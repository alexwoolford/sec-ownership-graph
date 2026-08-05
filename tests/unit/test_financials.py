"""Unit tests for the FSDS balance-sheet loader (no Neo4j, no network).

``total_assets_usd`` exists because the 13F float proxy is smallest exactly where ownership is
concentrated — EchoStar is $43B of assets, 51.8% controlled, and has no 13F coverage at all. Every
test here guards a way the figure goes silently wrong: a wrong row filter mixes segment values
into a consolidated series, and an unstable merge publishes a different number between runs over
identical inputs. Neither errors; both just produce the wrong answer.
"""

from __future__ import annotations

import pytest

from secgraph.ingestion.ownership.bulk_datasets import parse_sec_date
from secgraph.ingestion.ownership.financials import (
    is_consolidated_assets_row,
    keep_latest_by_ddate,
    merge_quarters,
    summarize_assets,
)


def _num_row(**overrides) -> dict[str, str]:
    """A minimal valid num.txt row; override one field per test."""
    row = {
        "adsh": "0000320193-26-000001",
        "tag": "Assets",
        "uom": "USD",
        "ddate": "20251231",
        "qtrs": "0",
        "segments": "",
        "coreg": "",
        "value": "1000000",
    }
    row.update(overrides)
    return row


class TestConsolidatedRowFilter:
    def test_accepts_a_consolidated_usd_balance(self):
        assert is_consolidated_assets_row(_num_row()) is True

    def test_rejects_other_tags(self):
        assert is_consolidated_assets_row(_num_row(tag="Revenues")) is False
        assert is_consolidated_assets_row(_num_row(tag="AssetsCurrent")) is False

    def test_rejects_non_usd(self):
        # A EUR-denominated filer would otherwise be ranked against USD figures directly.
        assert is_consolidated_assets_row(_num_row(uom="EUR")) is False

    @pytest.mark.parametrize("qtrs", ["1", "4", "2"])
    def test_rejects_duration_facts(self, qtrs):
        """A balance sheet is an instant (qtrs=0). Flow facts carry 1/4 and are a category error."""
        assert is_consolidated_assets_row(_num_row(qtrs=qtrs)) is False

    def test_rejects_segmented_rows(self):
        """The filter that silently corrupts the figure if dropped.

        A segmented row reports ONE business line. Including it mixes part-of-company values into
        a consolidated series — no error, just whichever row happened to sort last.
        """
        assert is_consolidated_assets_row(_num_row(segments="ProductOrService=Widgets")) is False

    def test_rejects_coregistrant_rows(self):
        """A coreg row is a subsidiary filing under the parent's accession — not the parent."""
        assert is_consolidated_assets_row(_num_row(coreg="SubsidiaryCo")) is False

    def test_missing_segment_columns_are_treated_as_consolidated(self):
        """Older FSDS quarters omit segments/coreg entirely; absence must not raise."""
        row = _num_row()
        del row["segments"]
        del row["coreg"]
        assert is_consolidated_assets_row(row) is True


class TestKeepLatestByDdate:
    def test_newer_ddate_wins(self):
        facts: dict = {}
        keep_latest_by_ddate(facts, "0000320193", "20240930", 100.0, "a")
        keep_latest_by_ddate(facts, "0000320193", "20251231", 200.0, "b")
        assert facts["0000320193"]["total_assets_usd"] == 200.0

    def test_older_ddate_does_not_overwrite(self):
        facts: dict = {}
        keep_latest_by_ddate(facts, "0000320193", "20251231", 200.0, "b")
        keep_latest_by_ddate(facts, "0000320193", "20240930", 100.0, "a")
        assert facts["0000320193"]["total_assets_usd"] == 200.0

    def test_same_ddate_highest_accession_wins_regardless_of_order(self):
        """The reproducibility guard, and a real case rather than a hypothetical.

        Measured on 2026q1: of 10,606 (cik, ddate) pairs, ONE reports two different values from
        two accessions — CIK 0001342916 at 20251031 gives 1,731,020 under ...-26-000008 and
        1,731,038 under ...-26-000016 (an original and an amendment). Without a total order,
        whichever row streamed last would win, so two builds over identical inputs could publish
        different assets figures. Silent, and the worst failure mode in this repo.
        """
        forward: dict = {}
        keep_latest_by_ddate(forward, "0001342916", "20251031", 1731020.0, "0001342916-26-000008")
        keep_latest_by_ddate(forward, "0001342916", "20251031", 1731038.0, "0001342916-26-000016")

        reverse: dict = {}
        keep_latest_by_ddate(reverse, "0001342916", "20251031", 1731038.0, "0001342916-26-000016")
        keep_latest_by_ddate(reverse, "0001342916", "20251031", 1731020.0, "0001342916-26-000008")

        assert forward == reverse, "the merge must not depend on row order"
        assert forward["0001342916"]["total_assets_usd"] == 1731038.0


class TestMergeQuarters:
    def test_newest_ddate_wins_across_quarters_not_newest_zip(self):
        """Precedence is the balance-sheet date, not which zip it came from.

        A 10-K filed in a later quarter can restate an EARLIER period, so ordering by zip would
        pick the older balance sheet.
        """
        newer_zip = {
            "0000320193": {"ddate": "20240630", "total_assets_usd": 50.0, "accession": "z"}
        }
        older_zip = {
            "0000320193": {"ddate": "20251231", "total_assets_usd": 99.0, "accession": "y"}
        }
        rows = merge_quarters([newer_zip, older_zip])
        assert len(rows) == 1
        assert rows[0]["total_assets_usd"] == 99.0

    def test_union_across_quarters_lifts_coverage(self):
        """Coverage saturates because a company appears only in quarters it filed in."""
        q1 = {"0000000001": {"ddate": "20251231", "total_assets_usd": 1.0, "accession": "a"}}
        q2 = {"0000000002": {"ddate": "20251231", "total_assets_usd": 2.0, "accession": "b"}}
        assert len(merge_quarters([q1, q2])) == 2

    def test_output_is_cik_ordered(self):
        """Deterministic order, so a write batch and any diff of figures is stable."""
        q = {
            "0000000009": {"ddate": "20251231", "total_assets_usd": 9.0, "accession": "i"},
            "0000000001": {"ddate": "20251231", "total_assets_usd": 1.0, "accession": "a"},
        }
        assert [r["cik"] for r in merge_quarters([q])] == ["0000000001", "0000000009"]


class TestSummarizeAssets:
    def test_reports_the_coverage_gap_honestly(self):
        rows = [{"total_assets_usd": 5e9}, {"total_assets_usd": 2e11}]
        s = summarize_assets(rows, universe_count=100)
        assert s["companies_with_assets"] == 2
        assert s["coverage_pct"] == 2.0
        assert s["buckets"]["ge_100b"] == 1

    def test_nulls_are_not_counted_as_zero(self):
        """A missing figure means "not reported", never zero — coercing it would rank an
        unsized issuer as the SMALLEST rather than as unknown. Different claims."""
        rows = [{"total_assets_usd": None}, {"total_assets_usd": 1e9}]
        s = summarize_assets(rows, universe_count=10)
        assert s["companies_with_assets"] == 1
        assert s["buckets"]["lt_100m"] == 0

    def test_empty_input_does_not_divide_by_zero(self):
        assert summarize_assets([], universe_count=0)["coverage_pct"] == 0.0


class TestFsdsDateParsing:
    def test_compact_yyyymmdd_is_parsed(self):
        """FSDS ddate is YYYYMMDD. Before this branch existed parse_sec_date returned None for
        EVERY row, which would have been a green run over zero coverage — the same shape as the
        403-caching bug that once produced a passing build over an empty graph."""
        assert parse_sec_date("20241231") == "2024-12-31"

    def test_legacy_format_still_works(self):
        assert parse_sec_date("31-MAR-2025") == "2025-03-31"

    @pytest.mark.parametrize("bad", ["20241345", "20230229", "notadate", "", None])
    def test_malformed_values_return_none(self, bad):
        """Validated, not sliced: "20241345" is 8 digits and would otherwise become the string
        "2024-13-45", failing later inside Cypher's date() far from the offending row."""
        assert parse_sec_date(bad) is None

    def test_leap_day_is_accepted_when_real(self):
        assert parse_sec_date("20240229") == "2024-02-29"
