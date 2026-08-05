"""Unit tests for the committed control-figures path (no Neo4j, no OpenAI).

``reference/control_figures.csv`` is what makes the CONTROLS layer reproducible without an LLM.
The parser fails loudly on malformed input rather than importing a partial control layer, since
a single wrong edge can add or delete an entire control chain.
"""

from __future__ import annotations

import pytest

from secgraph.ingestion.ownership.control_edges import (
    CONTROL_FIGURE_COLUMNS,
    parse_control_figures,
)

_HEADER = ",".join(CONTROL_FIGURE_COLUMNS)


def _write_csv(tmp_path, *body_lines):
    path = tmp_path / "control_figures.csv"
    path.write_text("\n".join([_HEADER, *body_lines]) + "\n")
    return path


class TestParseControlFigures:
    def test_parses_typed_rows(self, tmp_path):
        path = _write_csv(
            tmp_path,
            "0001234-25-000001,0000320193,0001822844,56.4,control,true",
            "0001234-25-000002,0000789019,0001326380,7.1,stake,false",
        )
        rows = parse_control_figures(path)
        assert len(rows) == 2
        assert rows[0] == {
            "accession_number": "0001234-25-000001",
            "company_cik": "0000320193",
            "owner_key": "0001822844",
            "percent_of_class": 56.4,  # float, not str
            "control_class": "control",
            "pct_verified": True,  # bool, not str
            "pct_source": None,  # absent in this fixture's header
        }
        assert rows[1]["percent_of_class"] == 7.1
        assert rows[1]["pct_verified"] is False

    def test_blank_percent_becomes_none(self, tmp_path):
        # 'unknown' rows legitimately carry no percent: verify_percent rejected the model's
        # figure because it did not appear in the filing text.
        path = _write_csv(tmp_path, "0001234-25-000003,0000320193,0001822844,,unknown,false")
        assert parse_control_figures(path)[0]["percent_of_class"] is None

    @pytest.mark.parametrize("truthy", ["true", "True", "1", "yes", "TRUE"])
    def test_pct_verified_truthy_spellings(self, tmp_path, truthy):
        path = _write_csv(
            tmp_path, f"0001234-25-000004,0000320193,0001822844,51.0,control,{truthy}"
        )
        assert parse_control_figures(path)[0]["pct_verified"] is True

    def test_missing_column_raises(self, tmp_path):
        path = tmp_path / "bad.csv"
        path.write_text("accession_number,company_cik\na,b\n")
        with pytest.raises(ValueError, match="missing required column"):
            parse_control_figures(path)

    def test_non_numeric_percent_raises_with_line_number(self, tmp_path):
        path = _write_csv(
            tmp_path,
            "0001234-25-000001,0000320193,0001822844,51.0,control,true",
            "0001234-25-000002,0000789019,0001326380,fifty,control,true",
        )
        with pytest.raises(ValueError, match=r":3: percent_of_class 'fifty'"):
            parse_control_figures(path)

    def test_unknown_control_class_raises(self, tmp_path):
        # A typo here would silently drop edges from CONTROLS at materialize time.
        path = _write_csv(tmp_path, "0001234-25-000001,0000320193,0001822844,51.0,controls,true")
        with pytest.raises(ValueError, match="control_class 'controls'"):
            parse_control_figures(path)

    def test_whitespace_is_stripped(self, tmp_path):
        path = _write_csv(
            tmp_path, " 0001234-25-000001 , 0000320193 , 0001822844 , 51.0 , control , true "
        )
        row = parse_control_figures(path)[0]
        assert row["accession_number"] == "0001234-25-000001"
        assert row["company_cik"] == "0000320193"
        assert row["control_class"] == "control"
        assert row["pct_verified"] is True


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return self._rows


class _Session:
    """Minimal session returning a fixed edge list for the 'needs extraction' query."""

    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def run(self, *_a, **_k):
        return _Result(self._rows)


class _Driver:
    def __init__(self, rows):
        self._rows = rows

    def session(self, database=None):
        return _Session(self._rows)


class TestGapFillClientResolution:
    """The build runs the gap fill unconditionally, so a no-op run must need no API key.

    This is what collapses the 'CSV present but stale' state: extraction always runs, but it is
    free and keyless when the committed figures already cover every edge.
    """

    def test_no_uncovered_edges_never_builds_a_client(self):
        from secgraph.ingestion.ownership.control_extraction import extract_control_edges

        calls = []

        def factory():
            calls.append(1)
            raise AssertionError("client must not be constructed when there is no work")

        result = extract_control_edges(
            _Driver([]),
            llm_client=None,
            model="gpt-4o-mini",
            database="secgraph",
            execute=True,
            client_factory=factory,
        )
        assert result["total"] == 0
        assert result["skipped_no_work"] is True
        assert calls == [], "an OPENAI_API_KEY must not be required for a fully-covered build"

    def test_uncovered_edges_with_no_client_raises(self):
        """Fail closed. An unclassified edge has no percent_of_class, so it is absent from
        CONTROLS and INFLUENCES entirely — a 'successful' build here would serve silently
        incomplete answers, violating the no-silent-fallbacks rule."""
        from secgraph.ingestion.ownership.control_extraction import extract_control_edges

        rows = [{"accession_number": "0001234-27-000001", "subject_cik": "0000320193"}]
        with pytest.raises(ValueError, match="no control figures and no LLM client"):
            extract_control_edges(
                _Driver(rows),
                llm_client=None,
                model="gpt-4o-mini",
                database="secgraph",
                execute=True,
                client_factory=None,
            )

    def test_missing_client_is_not_absorbed_as_unknown(self):
        """extract_with_llm must raise on a None client rather than return None.

        The per-edge error handler turns a failed extraction into an 'unknown' label. If a
        missing client fell into that path, an entire keyless run would label every edge
        unknown and exit 0 — a green build over an empty control layer.
        """
        from secgraph.ingestion.ownership.control_extraction import extract_with_llm

        with pytest.raises(ValueError, match="without an LLM client"):
            extract_with_llm("cover page text", None, "gpt-4o-mini")
