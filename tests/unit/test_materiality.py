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


class TestCombinedSizeMeasure:
    """size_usd coalesces a filed balance sheet over the 13F float proxy.

    The precedence is the whole point: float is smallest exactly where ownership is concentrated,
    so a float-first measure would keep hiding the issuers the graph exists to describe.
    """

    def test_assets_take_precedence_over_float(self):
        from secgraph.ingestion.ownership.materiality import _SIZE_QUERY

        # coalesce order is load-bearing, not stylistic — reversing it reinstates the bug.
        assert "coalesce(c.total_assets_usd, c.institutional_value_usd)" in _SIZE_QUERY
        assets_pos = _SIZE_QUERY.index("total_assets_usd")
        float_pos = _SIZE_QUERY.index("institutional_value_usd")
        assert assets_pos < float_pos

    def test_size_source_labels_which_measure_applied(self):
        """Without the label a $43B balance sheet and $43B of float are indistinguishable in
        output, despite being different claims about different quantities."""
        from secgraph.ingestion.ownership.materiality import _SIZE_QUERY

        assert "'dera_assets'" in _SIZE_QUERY
        assert "'institutional_13f'" in _SIZE_QUERY
        # The label must be decided by the same condition that drives the coalesce, or the two
        # can disagree — a row could report float while carrying the assets figure.
        assert "WHEN c.total_assets_usd IS NOT NULL THEN 'dera_assets'" in _SIZE_QUERY

    def test_nulls_are_never_coerced_to_zero(self):
        """coalesce(..., 0) here would be catastrophic in a way rule 10 does not cover.

        For a *boolean* nullable, coalesce(x, false) is correct. For a size it inverts the
        meaning: 0 both ranks an unknown issuer as the smallest AND silently passes any
        `>= 0` threshold. schema/validation.py flags the bare comparison and is wrong to.
        """
        from secgraph.ingestion.ownership.materiality import _SIZE_QUERY

        assert ", 0)" not in _SIZE_QUERY
        assert ", 0.0)" not in _SIZE_QUERY

    def test_only_sizable_companies_are_touched(self):
        """A company with neither input must be left with no size_usd at all, so that
        size-filtered queries exclude it rather than ranking it."""
        from secgraph.ingestion.ownership.materiality import _SIZE_QUERY

        assert "IS NOT NULL OR" in _SIZE_QUERY

    def test_clear_query_removes_both_properties(self):
        """Recomputing without clearing would leave a stale figure that outranks live ones, and
        would strand size_source on a company that lost its input."""
        from secgraph.ingestion.ownership.materiality import _SIZE_CLEAR_QUERY

        assert "REMOVE c.size_usd, c.size_source" in _SIZE_CLEAR_QUERY
