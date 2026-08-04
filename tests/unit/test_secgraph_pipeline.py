"""Unit tests for the secgraph build/refresh orchestrator planning logic.

No subprocess and no live DB: exercises the pure phase-planning (``_steps``) and the freshness
manifest assembly (``collect_freshness`` with a fake session). The subprocess-running and
gate-abort paths are integration concerns; here we lock the plan's shape, ordering, mode
filtering, and the --execute/--replace/--refresh flag threading.
"""

from __future__ import annotations

from secgraph.ingestion.ownership import pipeline as p


class TestSteps:
    def test_build_includes_db_create_and_gate(self):
        steps = p._steps("secgraph", refresh=False)
        scripts = [s.script for s in steps]
        assert scripts[0] == "ownership_create_database.py"
        assert "measure_ownership_density.py" in scripts
        gate = next(s for s in steps if s.gate)
        assert gate.script == "measure_ownership_density.py"
        assert gate.adds_execute is False

    def test_refresh_drops_build_only_steps(self):
        steps = p._steps("secgraph", refresh=True)
        scripts = [s.script for s in steps]
        # Phase 0 (create DB) and the one-time density gate are build-only.
        assert "ownership_create_database.py" not in scripts
        assert "measure_ownership_density.py" not in scripts

    def test_refresh_adds_replace_and_refresh_flags(self):
        steps = p._steps("secgraph", refresh=True)
        interlock = next(s for s in steps if s.script == "materialize_interlock_edges.py")
        holdings = next(s for s in steps if s.script == "load_institutional_holdings.py")
        universe = next(s for s in steps if s.script == "load_company_universe.py")
        assert "--replace" in interlock.extra_args
        assert "--replace" in holdings.extra_args
        assert "--refresh" in universe.extra_args

    def test_build_has_no_replace_flags(self):
        steps = p._steps("secgraph", refresh=False)
        for s in steps:
            assert "--replace" not in s.extra_args
            assert "--refresh" not in s.extra_args

    def test_database_threaded_onto_db_steps(self):
        steps = p._steps("mygraph", refresh=False)
        loaders = [s for s in steps if s.script == "load_insiders.py"]
        assert loaders and loaders[0].extra_args == ["--database", "mygraph"]

    def test_ordering_insiders_before_gate_before_interlock(self):
        scripts = [s.script for s in p._steps("secgraph", refresh=False)]
        assert scripts.index("load_insiders.py") < scripts.index("measure_ownership_density.py")
        assert scripts.index("measure_ownership_density.py") < scripts.index(
            "materialize_interlock_edges.py"
        )
        # Beneficial owners must precede whichever control step is active — the committed-CSV
        # loader or the LLM extractor. Exactly one is present unless --extract-control asks for
        # both, so assert on the control step generically rather than naming one.
        control_steps = [
            i
            for i, s in enumerate(scripts)
            if s in {"load_control_figures.py", "extract_control_edges.py"}
        ]
        assert control_steps, "the plan must include a control-figures step"
        assert scripts.index("load_beneficial_owners.py") < min(control_steps)
        # ...and control figures must precede the CONTROLS materializer, which reads them.
        assert max(control_steps) < scripts.index("materialize_control_edges.py")
        # CUSIP crosswalk must precede the 13F holdings load.
        assert scripts.index("build_cusip_crosswalk.py") < scripts.index(
            "load_institutional_holdings.py"
        )

    def test_control_step_selection(self, monkeypatch, tmp_path):
        """CSV present → deterministic loader only; --extract-control → CSV then LLM fill."""
        csv_path = tmp_path / "control_figures.csv"
        csv_path.write_text("accession_number\n")
        monkeypatch.setattr(p, "_CONTROL_FIGURES_CSV", csv_path)

        default = [s.script for s in p._steps("secgraph", refresh=False)]
        assert "load_control_figures.py" in default
        assert "extract_control_edges.py" not in default

        both = [s.script for s in p._steps("secgraph", refresh=False, extract_control=True)]
        assert both.index("load_control_figures.py") < both.index("extract_control_edges.py")

        monkeypatch.setattr(p, "_CONTROL_FIGURES_CSV", tmp_path / "absent.csv")
        no_csv = [s.script for s in p._steps("secgraph", refresh=False)]
        assert "extract_control_edges.py" in no_csv
        assert "load_control_figures.py" not in no_csv


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, *_a, **_k):
        rows = self._rows

        class _Res:
            def data(self_inner):
                return rows

        return _Res()


class _FakeDriver:
    def __init__(self, rows):
        self._rows = rows

    def session(self, database=None):
        return _FakeSession(self._rows)


class TestCollectFreshness:
    def test_as_of_is_max_beneficial_owner_filing_date(self):
        rows = [
            {"k": "Company", "c": 8046, "max_date": None},
            {"k": "BENEFICIAL_OWNER_OF", "c": 57449, "max_date": "2026-07-24"},
            {"k": "HOLDS", "c": 12595519, "max_date": "2026-05-29"},
        ]
        m = p.collect_freshness(_FakeDriver(rows), "secgraph")
        assert m["database"] == "secgraph"
        assert m["as_of"] == "2026-07-24"
        assert m["layers"]["Company"]["count"] == 8046
        assert m["layers"]["HOLDS"]["max_filing_date"] == "2026-05-29"

    def test_as_of_none_when_no_beneficial_owner_layer(self):
        m = p.collect_freshness(_FakeDriver([{"k": "Company", "c": 1, "max_date": None}]), "x")
        assert m["as_of"] is None
