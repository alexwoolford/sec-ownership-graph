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
