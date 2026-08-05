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
        """The LLM gap fill is UNCONDITIONAL; the CSV loader only runs when a CSV exists.

        The gap fill used to be opt-in behind --extract-control, which left a third state the
        binary exists() check did not cover: CSV present but stale. The CSV covers only its
        export window, so a later rebuild crawls newer 13Ds it cannot match — and an
        unclassified edge has no percent_of_class, so it is silently absent from CONTROLS and
        INFLUENCES instead of erroring. Running the fill every time is what removes that state.
        """
        csv_path = tmp_path / "control_figures.csv"
        csv_path.write_text("accession_number\n")
        monkeypatch.setattr(p, "_CONTROL_FIGURES_CSV", csv_path)

        default = [s.script for s in p._steps("secgraph", refresh=False)]
        assert "load_control_figures.py" in default
        # The regression this guards: the gap fill must be present with no flag at all.
        assert "extract_control_edges.py" in default
        assert default.index("load_control_figures.py") < default.index(
            "extract_control_edges.py"
        ), "committed figures must be applied before the fill, or the fill re-does covered edges"

        monkeypatch.setattr(p, "_CONTROL_FIGURES_CSV", tmp_path / "absent.csv")
        no_csv = [s.script for s in p._steps("secgraph", refresh=False)]
        assert "extract_control_edges.py" in no_csv
        assert "load_control_figures.py" not in no_csv

    def test_gap_fill_is_incremental_unless_extract_control(self, monkeypatch, tmp_path):
        """Default fill is only-missing; --extract-control passes --all to redo everything.

        Cost depends on this: only-missing touches just the uncovered edges (cents), while --all
        re-extracts all ~10.6k. If the default ever gained --all, every build would pay full price.
        """
        csv_path = tmp_path / "control_figures.csv"
        csv_path.write_text("accession_number\n")
        monkeypatch.setattr(p, "_CONTROL_FIGURES_CSV", csv_path)

        def fill_args(**kwargs):
            steps = p._steps("secgraph", refresh=False, **kwargs)
            return next(s for s in steps if s.script == "extract_control_edges.py").extra_args

        assert "--all" not in fill_args()
        assert "--all" in fill_args(extract_control=True)

    def test_reexport_follows_the_gap_fill(self, monkeypatch, tmp_path):
        """Figures are re-exported after the fill, so the NEXT rebuild is LLM-free.

        Without this the loop never closes: the fill labels new edges in Neo4j but the committed
        CSV still ends at the old window, so every later rebuild re-extracts the same edges.
        """
        csv_path = tmp_path / "control_figures.csv"
        csv_path.write_text("accession_number\n")
        monkeypatch.setattr(p, "_CONTROL_FIGURES_CSV", csv_path)

        scripts = [s.script for s in p._steps("secgraph", refresh=False)]
        assert "export_control_figures.py" in scripts
        assert scripts.index("extract_control_edges.py") < scripts.index(
            "export_control_figures.py"
        )
        # And the export must not land after the materializer that consumes the figures.
        assert scripts.index("export_control_figures.py") < scripts.index(
            "materialize_control_edges.py"
        )


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


class TestFsdsWiring:
    """The balance-sheet layer must stage before it loads, and load before size is derived."""

    def test_staging_precedes_load_precedes_size(self, monkeypatch, tmp_path):
        csv_path = tmp_path / "control_figures.csv"
        csv_path.write_text("accession_number\n")
        monkeypatch.setattr(p, "_CONTROL_FIGURES_CSV", csv_path)

        steps = p._steps("secgraph", refresh=False)
        scripts = [s.script for s in steps]
        assert "load_company_financials.py" in scripts

        fsds_stage = next(
            i
            for i, s in enumerate(steps)
            if s.script == "download_ownership_data.py" and "fsds" in s.extra_args
        )
        load = scripts.index("load_company_financials.py")
        size = scripts.index("materialize_materiality.py")
        assert fsds_stage < load, "cannot load what has not been staged"
        # This one is a real data dependency, not a preference: materialize_materiality computes
        # size_usd = coalesce(total_assets_usd, institutional_value_usd), so running it first
        # would silently produce a float-only measure and hide the assets-only issuers.
        assert load < size, "total_assets_usd must exist before size_usd is derived from it"

    def test_as_of_is_passed_at_load_time_not_only_download(self, monkeypatch, tmp_path):
        """staged_zip_paths globs the WHOLE local cache, so a machine that once staged more
        quarters would load a wider window than a fresh clone from the same command. Pinning only
        the download leaves that hazard open."""
        csv_path = tmp_path / "control_figures.csv"
        csv_path.write_text("accession_number\n")
        monkeypatch.setattr(p, "_CONTROL_FIGURES_CSV", csv_path)

        steps = p._steps("secgraph", refresh=False, as_of="2026-06-30")
        load = next(s for s in steps if s.script == "load_company_financials.py")
        assert "--as-of" in load.extra_args
        assert "2026-06-30" in load.extra_args
        assert "--quarters" in load.extra_args

    def test_refresh_replaces_stale_figures(self, monkeypatch, tmp_path):
        """An issuer that stops filing must lose its figure; absence is meaningful here."""
        csv_path = tmp_path / "control_figures.csv"
        csv_path.write_text("accession_number\n")
        monkeypatch.setattr(p, "_CONTROL_FIGURES_CSV", csv_path)

        refresh = next(
            s
            for s in p._steps("secgraph", refresh=True)
            if s.script == "load_company_financials.py"
        )
        assert "--replace" in refresh.extra_args
        build = next(
            s
            for s in p._steps("secgraph", refresh=False)
            if s.script == "load_company_financials.py"
        )
        assert "--replace" not in build.extra_args

    def test_provenance_records_staged_fsds_periods(self, monkeypatch, tmp_path):
        """Without the resolved quarters a cloner comparing assets figures cannot tell drift
        from breakage."""
        monkeypatch.setattr(p, "_CONTROL_FIGURES_CSV", tmp_path / "absent.csv")
        prov = p.collect_provenance(as_of=None, quarters_345=16, quarters_13f=4)
        assert "fsds" in prov["staged_periods"]


class TestInterlockFeaturesWiring:
    def test_features_follow_the_interlock_edge_they_depend_on(self, monkeypatch, tmp_path):
        """A real data dependency: the GDS projection reads DIRECTOR_OF and the materializer's
        degree pass reads SHARES_DIRECTOR, so both must exist first."""
        csv_path = tmp_path / "control_figures.csv"
        csv_path.write_text("accession_number\n")
        monkeypatch.setattr(p, "_CONTROL_FIGURES_CSV", csv_path)

        scripts = [s.script for s in p._steps("secgraph", refresh=False)]
        assert "materialize_interlock_features.py" in scripts
        assert scripts.index("load_insiders.py") < scripts.index(
            "materialize_interlock_features.py"
        )
        assert scripts.index("materialize_interlock_edges.py") < scripts.index(
            "materialize_interlock_features.py"
        )

    def test_features_run_after_the_density_gate(self, monkeypatch, tmp_path):
        """A preference, not a dependency — and the reason is worth locking in. Keeping a
        GDS-plugin-dependent step after the fail-closed gate means a GDS problem cannot abort an
        expensive build before the gate has had a chance to run."""
        csv_path = tmp_path / "control_figures.csv"
        csv_path.write_text("accession_number\n")
        monkeypatch.setattr(p, "_CONTROL_FIGURES_CSV", csv_path)

        steps = p._steps("secgraph", refresh=False)
        scripts = [s.script for s in steps]
        gate = next(i for i, s in enumerate(steps) if s.gate)
        assert gate < scripts.index("materialize_interlock_features.py")

    def test_refresh_replaces_stale_features(self, monkeypatch, tmp_path):
        """A company that drops out of the scrubbed projection must LOSE its score: absence means
        'not in the interlock graph', which is a different claim from 'central with score 0'."""
        csv_path = tmp_path / "control_figures.csv"
        csv_path.write_text("accession_number\n")
        monkeypatch.setattr(p, "_CONTROL_FIGURES_CSV", csv_path)

        refresh = next(
            s
            for s in p._steps("secgraph", refresh=True)
            if s.script == "materialize_interlock_features.py"
        )
        assert "--replace" in refresh.extra_args
