"""Unit tests for the SHARES_DIRECTOR interlock-edge materializer pure helpers.

No live DB: exercises the batching + coverage-summary functions that are the
unit-test surface of :mod:`secgraph.ingestion.ownership.interlock_edges`.
"""

from __future__ import annotations

import pytest

from secgraph.ingestion.ownership.interlock_edges import batches, summarize_pairs


class TestBatches:
    def test_splits_into_equal_batches(self):
        rows = [{"i": i} for i in range(10)]
        result = batches(rows, 4)
        assert [len(b) for b in result] == [4, 4, 2]
        # No row lost or duplicated.
        assert [r["i"] for b in result for r in b] == list(range(10))

    def test_exact_multiple(self):
        rows = [{"i": i} for i in range(6)]
        assert [len(b) for b in batches(rows, 3)] == [3, 3]

    def test_single_batch_when_size_exceeds_len(self):
        rows = [{"i": 0}, {"i": 1}]
        assert batches(rows, 100) == [rows]

    def test_empty_rows_yields_no_batches(self):
        assert batches([], 50) == []

    def test_nonpositive_size_raises(self):
        with pytest.raises(ValueError):
            batches([{"i": 0}], 0)
        with pytest.raises(ValueError):
            batches([{"i": 0}], -3)


class TestSummarizePairs:
    def test_counts_pairs_multi_and_max(self):
        rows = [
            {"director_count": 1},
            {"director_count": 2},
            {"director_count": 5},
        ]
        summary = summarize_pairs(rows)
        assert summary == {
            "pairs": 3,
            "pairs_multi_director": 2,
            "max_shared_directors": 5,
        }

    def test_empty_input(self):
        assert summarize_pairs([]) == {
            "pairs": 0,
            "pairs_multi_director": 0,
            "max_shared_directors": 0,
        }

    def test_missing_or_none_director_count_treated_as_zero(self):
        rows = [{"director_count": None}, {}, {"director_count": 3}]
        summary = summarize_pairs(rows)
        assert summary["pairs"] == 3
        assert summary["pairs_multi_director"] == 1
        assert summary["max_shared_directors"] == 3
