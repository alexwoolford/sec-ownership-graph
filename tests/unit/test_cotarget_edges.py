"""Unit tests for the CO_TARGETS co-targeting materializer's pure helpers.

No live DB: covers batching, the coverage summary, and the custodial-hub detection that drives
the precision scrub (labelled on the node, excluded at projection, never deleted).
"""

from __future__ import annotations

import pytest

from secgraph.ingestion.ownership.cotarget_edges import (
    DEFAULT_MIN_SHARED_TARGETS,
    batches,
    custodial_ciks,
    summarize_cotarget_pairs,
)
from secgraph.ingestion.ownership.graph_native_proof import custodial_category


def _pair(a_cik, a_name, b_cik, b_name, shared=2):
    return {
        "a_cik": a_cik,
        "a_name": a_name,
        "b_cik": b_cik,
        "b_name": b_name,
        "shared_target_count": shared,
    }


class TestBatches:
    def test_splits_and_keeps_short_tail(self):
        assert batches([1, 2, 3], 2) == [[1, 2], [3]]

    def test_rejects_nonpositive(self):
        with pytest.raises(ValueError):
            batches([1], -1)


class TestCustodialCiks:
    def test_flags_known_hub_by_name(self):
        rows = [_pair("1", "ICAHN CARL C", "2", "JPMORGAN CHASE & CO")]
        assert custodial_ciks(rows) == {"2"}

    def test_flags_hub_on_either_side(self):
        rows = [_pair("9", "STATE STREET CORP", "8", "GAMCO INVESTORS")]
        assert custodial_ciks(rows) == {"9"}

    def test_no_hubs_among_real_activists(self):
        rows = [_pair("1", "ICAHN CARL C", "2", "GOLDSTEIN PHILLIP")]
        assert custodial_ciks(rows) == set()

    def test_case_insensitive(self):
        rows = [_pair("1", "jpmorgan securities", "2", "Bulldog Investors")]
        assert custodial_ciks(rows) == {"1"}

    def test_ignores_missing_cik(self):
        rows = [_pair(None, "JPMORGAN", "2", "Bulldog Investors")]
        assert custodial_ciks(rows) == set()

    def test_dedupes_hub_appearing_in_many_pairs(self):
        rows = [
            _pair("1", "JPMORGAN", "2", "Bulldog Investors"),
            _pair("1", "JPMORGAN", "3", "Karpus Management, Inc."),
        ]
        assert custodial_ciks(rows) == {"1"}


class TestExpandedLegalNamesAreCaught:
    """Regression: substring tokens miss expanded legal names.

    `"RBC"` does not appear in `"ROYAL BANK OF CANADA"`, so RBC and Toronto Dominion leaked into
    the Icahn coalition and inflated it from 16 to 22 members. Each of these must be caught.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "ROYAL BANK OF CANADA",
            "Toronto Dominion Investments, Inc.",
            "LAZARD ASSET MANAGEMENT LLC",
            "NOMURA HOLDINGS INC",
            "J.P. MORGAN SECURITIES LLC",
        ],
    )
    def test_broker_names_are_caught(self, name):
        assert custodial_ciks([_pair("1", name, "2", "ICAHN CARL C")]) == {"1"}
        assert custodial_category(name) == "broker"

    @pytest.mark.parametrize(
        "name",
        [
            "CITY OF LONDON INVESTMENT GROUP PLC",
            "SIT INVESTMENT ASSOCIATES INC",
            "PUBLIC EMPLOYEES RETIREMENT SYSTEM OF OHIO",
            "VANGUARD GROUP INC",
            "BlackRock, Inc.",
            "Adage Capital Management, L.P.",
        ],
    )
    def test_passive_index_holders_are_caught(self, name):
        assert custodial_ciks([_pair("1", name, "2", "ICAHN CARL C")]) == {"1"}
        assert custodial_category(name) == "passive"

    @pytest.mark.parametrize(
        "name",
        [
            "ICAHN CARL C",
            "GAMCO INVESTORS, INC. ET AL",
            "Saba Capital Management, L.P.",
            "Bulldog Investors, LLP",
            "Karpus Management, Inc.",
            "GOLDSTEIN PHILLIP",
            "CANNELL CAPITAL LLC",
            "ROYCE CHARLES M",
            "MALONE JOHN C",
        ],
    )
    def test_real_activists_are_never_scrubbed(self, name):
        """The scrub must not cost us a single genuine activist — that would be worse noise."""
        assert custodial_ciks([_pair("1", name, "2", "SOME OTHER FILER")]) == set()
        assert custodial_category(name) is None


class TestSummarizeCotargetPairs:
    def test_counts_pairs_participants_and_max(self):
        rows = [
            _pair("1", "A", "2", "B", shared=3),
            _pair("2", "B", "3", "C", shared=7),
        ]
        s = summarize_cotarget_pairs(rows)
        assert s["cotarget_pairs"] == 2
        assert s["participants"] == 3
        assert s["max_shared_targets"] == 7

    def test_reports_custodial_hub_count(self):
        rows = [_pair("1", "RBC CAPITAL MARKETS", "2", "ICAHN CARL C")]
        assert summarize_cotarget_pairs(rows)["custodial_hubs"] == 1

    def test_empty(self):
        s = summarize_cotarget_pairs([])
        assert s["cotarget_pairs"] == 0
        assert s["participants"] == 0
        assert s["max_shared_targets"] == 0


def test_default_threshold_is_two():
    """A single shared target is too weak to imply coordination; pin the contract."""
    assert DEFAULT_MIN_SHARED_TARGETS == 2
