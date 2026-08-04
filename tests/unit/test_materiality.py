"""Unit tests for the 13F institutional-value size proxy (no Neo4j, no network).

The proxy exists so structural results can be ranked — a $95B control relationship and a $30 one
are not the same finding, and before this they rendered as peer rows. The tests below pin the
properties that make the figure safe to publish: coverage is reported honestly, and absence is
never silently turned into zero.
"""

from __future__ import annotations

import pytest

from secgraph.ingestion.ownership.materiality import batches, summarize_materiality


def _rows(*values: float | None) -> list[dict]:
    return [{"cik": f"{i:010d}", "value_usd": v, "positions": 1} for i, v in enumerate(values)]


class TestSummarizeMateriality:
    def test_reports_coverage_and_the_gap(self):
        """The coverage hole is the honest headline: a quarter of the universe has no value."""
        summary = summarize_materiality(_rows(5e9, 2e9), universe_count=8)
        assert summary["companies_with_value"] == 2
        assert summary["companies_without_value"] == 6
        assert summary["coverage_pct"] == 25.0

    def test_buckets_are_cumulative_thresholds(self):
        summary = summarize_materiality(_rows(150e9, 50e9, 5e9, 50e6), universe_count=4)
        b = summary["buckets"]
        assert b["ge_100b"] == 1
        assert b["ge_10b"] == 2  # 150B and 50B
        assert b["ge_1b"] == 3  # ...plus 5B
        assert b["lt_100m"] == 1

    def test_nulls_are_excluded_not_counted_as_zero(self):
        """A null means "not institutionally held", not "worth nothing" — it must not be summed
        in as a 0, which would drag the median and imply a measured value where none exists."""
        summary = summarize_materiality(_rows(10e9, None, None), universe_count=3)
        assert summary["companies_with_value"] == 1
        assert summary["companies_without_value"] == 2
        assert summary["total_usd"] == 10e9

    def test_empty_input_does_not_divide_by_zero(self):
        summary = summarize_materiality([], universe_count=0)
        assert summary["coverage_pct"] == 0.0
        assert summary["max_usd"] is None
        assert summary["median_usd"] is None

    def test_never_reports_negative_missing_count(self):
        """Defensive: more rows than the universe count must not yield a negative gap."""
        summary = summarize_materiality(_rows(1e9, 2e9, 3e9), universe_count=1)
        assert summary["companies_without_value"] == 0


class TestBatches:
    def test_splits_with_a_short_final_batch(self):
        assert batches([{"i": i} for i in range(5)], 2) == [
            [{"i": 0}, {"i": 1}],
            [{"i": 2}, {"i": 3}],
            [{"i": 4}],
        ]

    def test_rejects_non_positive_size(self):
        with pytest.raises(ValueError, match="positive"):
            batches([{"i": 1}], 0)


class TestPublishedFigureProvenance:
    """Guards the arithmetic that produced a published figure.

    "20 of 825 controlled issuers carry >=$10B" must be computed from the *stored per-company*
    property, never by re-summing HOLDS in a query that also joins CONTROLS. 159 controlled
    companies have more than one CONTROLS edge (max 7), so that join fans each company's holdings
    out once per edge and inflates the sum — it reported 27 instead of 20. The materializer
    aggregates per company precisely to avoid this.
    """

    def test_compute_query_aggregates_per_company_only(self):
        from secgraph.ingestion.ownership.materiality import _COMPUTE_QUERY

        # The aggregation must touch only InstitutionalManager -> Company. Any CONTROLS or
        # BENEFICIAL_OWNER_OF pattern in the same MATCH would fan the holdings out.
        assert "CONTROLS" not in _COMPUTE_QUERY
        assert "BENEFICIAL_OWNER_OF" not in _COMPUTE_QUERY
        assert "sum(h.value_usd)" in _COMPUTE_QUERY
        # One report_period only: HOLDS is a per-quarter time series, so an unfiltered sum would
        # multiply-count the same position across quarters.
        assert "h.report_period = $period" in _COMPUTE_QUERY
