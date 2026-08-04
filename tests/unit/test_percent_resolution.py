"""Unit tests for 13D percent-of-class resolution (no network, no Neo4j, no LLM).

Regression suite for a defect that reached the demo's headline output: the extractor printed
`GAMCO INVESTORS, INC. ET AL 4.0%` on a Schedule 13D. A 13D is triggered by crossing 5%, so a
sub-5% figure on an *original* filing is impossible on its face — the first thing a
filings-literate reader challenges.

Cause: a filing group's cover pages carry one row-13 percent *per affiliated vehicle*, none of
which is the group total. The real filing says:

    "the aggregate number of Securities to which this Schedule 13D relates is 1,502,130
     shares, representing 5.01% of the 29,978,942 shares outstanding"
    "GAMCO 1,211,530 shares 4.04%, Gabelli Funds 212,400 shares 0.71%, ..."

4.04 was taken instead of 5.01. `verify_percent` passed it because 4.04 *is* in the document —
the gate checked presence, not identity.
"""

from __future__ import annotations

import pytest

from secgraph.ingestion.ownership.beneficial import is_amendment
from secgraph.ingestion.ownership.control_extraction import (
    build_edge_result,
    parse_aggregate_percent,
    parse_percent_deterministic,
    resolve_percent,
    verify_percent,
)

# Faithful reduction of the GAMCO/MNRO 13D (accession 0000807249-25-000101).
_GROUP_FILING = (
    "SCHEDULE 13D CUSIP No. 610236101 1. NAME OF REPORTING PERSON GAMCO INVESTORS, INC. "
    "11. AGGREGATE AMOUNT BENEFICIALLY OWNED BY EACH REPORTING PERSON 1,211,530 "
    "13. PERCENT OF CLASS REPRESENTED BY AMOUNT IN ROW 11 4.04% "
    "The aggregate number of Securities to which this Schedule 13D relates is 1,502,130 "
    "shares, representing 5.01% of the 29,978,942 shares outstanding as reported by the "
    "Issuer. The Reporting Persons beneficially own those Securities as follows: "
    "GAMCO 1,211,530 shares 4.04%, Gabelli Funds 212,400 shares 0.71%, Foundation 0.15%."
)

# A single filer with no group aggregate — row 13 is the correct answer here.
_SINGLE_FILING = (
    "SCHEDULE 13D 11. AGGREGATE AMOUNT BENEFICIALLY OWNED 18,317,588 "
    "13. PERCENT OF CLASS REPRESENTED BY AMOUNT IN ROW 11 12.7% "
    "Item 5. The Reporting Person beneficially owns 12.7% of the outstanding shares."
)

# A 13D/A reporting an exit below the threshold — legitimately sub-5%.
_EXIT_AMENDMENT = (
    "SCHEDULE 13D/A Amendment No. 7 "
    "13. PERCENT OF CLASS REPRESENTED BY AMOUNT IN ROW 11 1.27% "
    "The Reporting Person has sold shares and now beneficially owns 1.27% of the class."
)


class TestParseAggregatePercent:
    def test_finds_the_group_aggregate(self):
        assert parse_aggregate_percent(_GROUP_FILING) == 5.01

    def test_none_when_no_aggregate_sentence(self):
        assert parse_aggregate_percent(_SINGLE_FILING) is None

    def test_tolerates_approximately_and_varied_wording(self):
        text = (
            "the aggregate amount of shares of Common Stock, par value $0.01, beneficially "
            "owned by the Reporting Persons is 4,102,900, representing approximately 7.8% of "
            "the outstanding shares"
        )
        assert parse_aggregate_percent(text) == 7.8

    def test_does_not_reach_across_a_sentence_boundary(self):
        """The `[^.]` bound is deliberate: without it the pattern would pair an aggregate
        clause with an unrelated percent from a later sentence."""
        text = (
            "the aggregate amount of Securities is 100 shares. "
            "Separately, the Issuer repurchased stock representing 42.0% of the class."
        )
        assert parse_aggregate_percent(text) is None


class TestParsePercentDeterministic:
    def test_prefers_aggregate_over_row13(self):
        """The whole bug in one assertion: 5.01 (group), not 4.04 (one vehicle)."""
        pct, source = parse_percent_deterministic(_GROUP_FILING)
        assert pct == 5.01
        assert source == "aggregate_prose"

    def test_falls_back_to_row13_for_a_single_filer(self):
        pct, source = parse_percent_deterministic(_SINGLE_FILING)
        assert pct == 12.7
        assert source == "row13"

    def test_returns_none_pair_when_nothing_matches(self):
        assert parse_percent_deterministic("no percentages here at all") == (None, None)


class TestResolvePercent:
    def test_sub5_on_original_13d_is_corrected_to_the_aggregate(self):
        assert resolve_percent(4.04, _GROUP_FILING, filing_type="13D", is_amendment=False) == (
            5.01,
            "aggregate_prose",
        )

    def test_sub5_on_an_amendment_is_left_alone(self):
        """A 13D/A can legitimately report an exit below 5% — half the sub-5% figures in the
        graph were exactly this, so blanket-rejecting them would destroy real data."""
        pct, source = resolve_percent(1.27, _EXIT_AMENDMENT, filing_type="13D", is_amendment=True)
        assert pct == 1.27

    def test_sub5_original_with_no_aggregate_is_rejected_not_published(self):
        """A missing percent is honest; an impossible one is not."""
        text = "SCHEDULE 13D 13. PERCENT OF CLASS 3.0% and nothing else."
        pct, source = resolve_percent(3.0, text, filing_type="13D", is_amendment=False)
        assert pct is None
        assert source == "rejected_below_13d_threshold"

    def test_figure_absent_from_the_document_is_still_rejected(self):
        """The original presence gate must survive the new identity rule."""
        assert resolve_percent(88.8, _GROUP_FILING, filing_type="13D") == (None, None)

    def test_plausible_percent_passes_through_with_provenance(self):
        pct, source = resolve_percent(12.7, _SINGLE_FILING, filing_type="13D", source="row13")
        assert (pct, source) == (12.7, "row13")

    def test_13g_is_exempt_from_the_5pct_rule(self):
        """13G thresholds vary by filer class, so the bright line does not apply."""
        text = "SCHEDULE 13G 13. PERCENT OF CLASS 4.2% of the class."
        pct, _ = resolve_percent(4.2, text, filing_type="13G", is_amendment=False)
        assert pct == 4.2

    def test_none_input_is_none_output(self):
        assert resolve_percent(None, _GROUP_FILING) == (None, None)


class TestBuildEdgeResult:
    def test_group_filing_yields_the_aggregate_and_records_why(self):
        r = build_edge_result(
            "0000807249-25-000101",
            {"percent_of_class": 4.04},
            _GROUP_FILING,
            filing_type="13D",
            is_amendment=False,
        )
        assert r["percent_of_class"] == 5.01
        assert r["pct_source"] == "aggregate_prose"
        assert r["pct_verified"] is True
        assert r["control_class"] == "stake"  # 5.01% is a stake, not control

    def test_rejected_percent_collapses_to_unknown(self):
        r = build_edge_result(
            "x", {"percent_of_class": 3.0}, "SCHEDULE 13D 3.0% only.", is_amendment=False
        )
        assert r["percent_of_class"] is None
        assert r["control_class"] == "unknown"
        assert r["pct_verified"] is False
        assert r["pct_source"] == "rejected_below_13d_threshold"

    def test_control_classification_survives_the_new_gate(self):
        text = "SCHEDULE 13D 13. PERCENT OF CLASS 62.0% of the class."
        r = build_edge_result("x", {"percent_of_class": 62.0}, text, is_amendment=False)
        assert r["control_class"] == "control"


class TestIsAmendment:
    @pytest.mark.parametrize(
        "form,expected",
        [
            ("SC 13D", False),
            ("SCHEDULE 13D", False),
            ("SC 13D/A", True),
            ("SCHEDULE 13D/A", True),
            ("SC 13G/A", True),
            ("sc 13d/a", True),  # case-insensitive
            ("SC 13D/A ", True),  # trailing whitespace
        ],
    )
    def test_detects_amendments(self, form, expected):
        assert is_amendment(form) is expected


class TestVerifyPercentStillGuardsPresence:
    """The presence check is necessary-but-insufficient; it must not have regressed."""

    def test_present_number_verifies(self):
        assert verify_percent(5.01, _GROUP_FILING) is True

    def test_absent_number_does_not(self):
        assert verify_percent(77.7, _GROUP_FILING) is False

    def test_none_does_not(self):
        assert verify_percent(None, _GROUP_FILING) is False
