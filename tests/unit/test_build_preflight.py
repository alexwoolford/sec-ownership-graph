"""Unit tests for the build preflight and provenance (no Neo4j, no network).

The preflight exists because every precondition it checks used to surface only after
minutes-to-hours of EDGAR crawling, as a generic non-zero exit from a child script.
"""

from __future__ import annotations

import pytest

from secgraph.ingestion.ownership import pipeline


@pytest.fixture(autouse=True)
def _real_user_agent(monkeypatch):
    """Default to a valid SEC_USER_AGENT so each test isolates the check it targets."""
    monkeypatch.setenv("SEC_USER_AGENT", "Test Runner test@example.org")


def _failures_text(failures: list[str]) -> str:
    return "\n".join(failures)


class TestPreflightUserAgent:
    def test_placeholder_user_agent_fails(self, monkeypatch):
        monkeypatch.delenv("SEC_USER_AGENT", raising=False)
        failures = pipeline.preflight_checks(database="secgraph", driver=None)
        assert "SEC_USER_AGENT" in _failures_text(failures)

    def test_real_user_agent_passes_that_check(self):
        failures = pipeline.preflight_checks(database="secgraph", driver=None)
        assert "SEC_USER_AGENT" not in _failures_text(failures)


class TestPreflightControlFigures:
    def test_missing_csv_and_no_openai_fails(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pipeline, "_CONTROL_FIGURES_CSV", tmp_path / "absent.csv")
        monkeypatch.setattr(pipeline.importlib.util, "find_spec", lambda name: None)
        failures = pipeline.preflight_checks(database="secgraph", driver=None)
        text = _failures_text(failures)
        assert "openai" in text
        # The error must name the install command, since '[dev]' alone omits openai.
        assert '".[dev,llm]"' in text

    def test_committed_csv_satisfies_the_check(self, monkeypatch, tmp_path):
        csv_path = tmp_path / "control_figures.csv"
        csv_path.write_text("accession_number\n")
        monkeypatch.setattr(pipeline, "_CONTROL_FIGURES_CSV", csv_path)
        # No openai available, but the CSV makes it unnecessary.
        monkeypatch.setattr(pipeline.importlib.util, "find_spec", lambda name: None)
        failures = pipeline.preflight_checks(database="secgraph", driver=None)
        assert "openai" not in _failures_text(failures)


class TestPreflightDriverlessMode:
    def test_no_driver_skips_neo4j_checks_without_failing(self):
        """The dry run has no driver; it must report what it cannot check, not invent failures."""
        failures = pipeline.preflight_checks(database="secgraph", driver=None)
        text = _failures_text(failures)
        assert "Neo4j" not in text
        assert "GDS" not in text


class TestAnyUserDatabase:
    def test_prefers_neo4j(self):
        assert pipeline._any_user_database({"system", "neo4j", "secgraph"}) == "neo4j"

    def test_falls_back_to_first_non_system(self):
        assert pipeline._any_user_database({"system", "secgraph", "zebra"}) == "secgraph"

    def test_never_returns_system(self):
        # gds.version() is unavailable on `system`, so probing it there is a false failure.
        assert pipeline._any_user_database({"system"}) is None

    def test_empty_returns_none(self):
        assert pipeline._any_user_database(set()) is None


class TestProvenance:
    def test_records_pin_state_and_requests(self):
        prov = pipeline.collect_provenance(as_of="2026-06-30", quarters_345=12, quarters_13f=4)
        assert prov["as_of_requested"] == "2026-06-30"
        assert prov["as_of_pinned"] is True
        assert prov["quarters_345_requested"] == 12
        assert prov["quarters_13f_requested"] == 4
        assert set(prov["staged_periods"]) == {"form345", "form13f", "ftd"}

    def test_unpinned_build_is_flagged(self):
        prov = pipeline.collect_provenance(as_of=None, quarters_345=12, quarters_13f=4)
        assert prov["as_of_pinned"] is False

    def test_control_source_reflects_committed_csv(self, monkeypatch, tmp_path):
        csv_path = tmp_path / "control_figures.csv"
        csv_path.write_text("accession_number\n")
        monkeypatch.setattr(pipeline, "_CONTROL_FIGURES_CSV", csv_path)
        monkeypatch.setattr(pipeline, "_REPO_ROOT", tmp_path)
        prov = pipeline.collect_provenance(as_of=None, quarters_345=12, quarters_13f=4)
        assert prov["control_figures"]["source"] == "reference_csv"

    def test_control_source_names_the_model_when_extracting(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pipeline, "_CONTROL_FIGURES_CSV", tmp_path / "absent.csv")
        prov = pipeline.collect_provenance(as_of=None, quarters_345=12, quarters_13f=4)
        assert prov["control_figures"]["source"] == "llm_extraction"
        assert prov["control_figures"]["model"]  # model id is recorded, not just the source


class TestReportPreflight:
    def test_empty_failures_returns_true(self):
        import logging

        assert pipeline._report_preflight([], log=logging.getLogger("t")) is True

    def test_failures_return_false(self):
        import logging

        assert pipeline._report_preflight(["boom"], log=logging.getLogger("t")) is False
