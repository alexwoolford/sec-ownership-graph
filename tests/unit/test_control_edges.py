"""Unit tests for the CONTROLS control-edge materializer's pure helpers.

No live DB: covers batching, the coverage summary, and the CIK-identity hinge detection that
determines whether a control chain can be multi-hop at all. The DB-bound compute/write path is
exercised against ``secgraph`` in the materialization run itself.
"""

from __future__ import annotations

import pytest

from secgraph.ingestion.ownership.control_edges import (
    CONTROL_THRESHOLD_PCT,
    batches,
    identity_bridge_ciks,
    summarize_control_edges,
)


def _edge(owner, company, pct=60.0):
    return {
        "owner_cik": owner,
        "company_cik": company,
        "owner_name": f"owner-{owner}",
        "company_name": f"co-{company}",
        "percent_of_class": pct,
    }


class TestBatches:
    def test_splits_evenly(self):
        assert batches([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]

    def test_last_batch_short(self):
        assert batches([1, 2, 3], 2) == [[1, 2], [3]]

    def test_empty(self):
        assert batches([], 10) == []

    def test_rejects_nonpositive_size(self):
        with pytest.raises(ValueError):
            batches([1], 0)


class TestSummarizeControlEdges:
    def test_counts_distinct_controllers_and_targets(self):
        rows = [_edge("A", "X"), _edge("A", "Y"), _edge("B", "X")]
        s = summarize_control_edges(rows)
        assert s["control_edges"] == 3
        assert s["controllers"] == 2
        assert s["controlled_companies"] == 2

    def test_percent_range(self):
        rows = [_edge("A", "X", 54.3), _edge("B", "Y", 92.1)]
        s = summarize_control_edges(rows)
        assert s["min_percent_of_class"] == 54.3
        assert s["max_percent_of_class"] == 92.1

    def test_handles_missing_percent(self):
        s = summarize_control_edges([_edge("A", "X", None)])
        assert s["min_percent_of_class"] is None
        assert s["control_edges"] == 1

    def test_empty_rows(self):
        s = summarize_control_edges([])
        assert s["control_edges"] == 0
        assert s["controllers"] == 0


class TestIdentityBridgeCiks:
    def test_finds_entity_that_is_both_controlled_and_controlling(self):
        # B controls X; A controls B -> B is the hinge that makes a 2-hop chain possible.
        rows = [_edge("A", "B"), _edge("B", "X")]
        assert identity_bridge_ciks(rows) == {"B"}

    def test_no_hinge_when_chains_are_flat(self):
        rows = [_edge("A", "X"), _edge("B", "Y")]
        assert identity_bridge_ciks(rows) == set()

    def test_multiple_hinges(self):
        rows = [_edge("A", "B"), _edge("B", "C"), _edge("C", "D")]
        assert identity_bridge_ciks(rows) == {"B", "C"}

    def test_ignores_null_ciks(self):
        rows = [{"owner_cik": None, "company_cik": None}]
        assert identity_bridge_ciks(rows) == set()


def test_control_threshold_is_fifty_percent():
    """The >=50% definition of control is the load-bearing contract; pin it."""
    assert CONTROL_THRESHOLD_PCT == 50.0
