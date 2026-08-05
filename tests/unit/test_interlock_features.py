"""Unit tests for the GDS interlock features (no Neo4j, no GDS).

Two properties are load-bearing and both are guarded here:

1. **Determinism.** GDS parallelises by default and the default is not reproducible — two identical
   runs reassigned 52.4% of Louvain communities. ``GDS_CONCURRENCY = 1`` is what fixes it, so a test
   pins the constant: raising it without re-verifying determinism would silently break the
   reproducibility contract, and nothing else would notice.
2. **Anchor stability.** A raw ``communityId`` is an arbitrary integer that carries no meaning across
   builds. The anchor must be derived from cluster *membership* so it survives a rebuild — which
   means it cannot depend on input order.
"""

from __future__ import annotations

import pytest

from secgraph.ingestion.ownership.interlock_features import (
    GDS_CONCURRENCY,
    batches,
    community_rows,
    summarize_features,
)


class TestDeterminismContract:
    def test_gds_concurrency_is_pinned_to_one(self):
        """The reproducibility guard. Do not raise this without re-verifying determinism.

        Measured on the live graph: at the default concurrency, two identical runs produced
        different betweenness scores and moved 4,035 of 7,702 nodes (52.4%) to a different Louvain
        community. At concurrency=1 both are byte-identical — and Louvain is ~24x faster at this
        size, so there is no speed argument for raising it either.
        """
        assert GDS_CONCURRENCY == 1


class TestCommunityRows:
    def test_anchor_is_the_lowest_cik_in_the_cluster(self):
        rows = community_rows([("0000000900", 7), ("0000000100", 7), ("0000000500", 7)])
        assert {r["anchor"] for r in rows} == {"0000000100"}
        assert all(r["size"] == 3 for r in rows)

    def test_anchor_is_independent_of_input_order(self):
        """The property that makes the anchor portable across rebuilds.

        GDS does not guarantee row order, so an anchor derived from "first seen" would change
        between runs even with concurrency pinned — and a colour-by-community visualization would
        reshuffle for no reason. Deriving it from membership (min) removes the dependency entirely.
        """
        forward = community_rows([("0000000100", 1), ("0000000200", 1), ("0000000300", 1)])
        reverse = community_rows([("0000000300", 1), ("0000000200", 1), ("0000000100", 1)])
        assert forward == reverse

    def test_separate_clusters_get_separate_anchors(self):
        rows = community_rows([("0000000100", 1), ("0000000200", 2)])
        anchors = {r["cik"]: r["anchor"] for r in rows}
        assert anchors["0000000100"] == "0000000100"
        assert anchors["0000000200"] == "0000000200"

    def test_output_is_cik_ordered(self):
        """Deterministic write order, so a batch — and any diff of the properties — is stable."""
        rows = community_rows([("0000000900", 1), ("0000000100", 1)])
        assert [r["cik"] for r in rows] == ["0000000100", "0000000900"]

    def test_empty_ciks_are_dropped_not_made_anchors(self):
        """A node with no CIK cannot be MATCHed on write, and must never become the anchor —
        min() over a list containing "" would silently name every member's cluster after nothing."""
        rows = community_rows([("", 1), ("0000000100", 1)])
        assert len(rows) == 1
        assert rows[0]["anchor"] == "0000000100"

    def test_size_reflects_only_kept_members(self):
        rows = community_rows([("", 1), ("0000000100", 1), ("0000000200", 1)])
        assert all(r["size"] == 2 for r in rows)


class TestSummarizeFeatures:
    def test_reports_coverage_gap_and_cluster_shape(self):
        rows = community_rows([("0000000100", 1), ("0000000200", 1), ("0000000300", 2)])
        s = summarize_features(3, rows, universe_count=10)
        assert s["companies_with_features"] == 3
        assert s["companies_without_features"] == 7
        assert s["coverage_pct"] == 30.0
        assert s["clusters"] == 2
        assert s["largest_cluster"] == 2

    def test_counts_clusters_above_the_meaningful_threshold(self):
        members = [(f"{i:010d}", 1) for i in range(1, 7)] + [("0000009999", 2)]
        s = summarize_features(7, community_rows(members), universe_count=7)
        assert s["clusters_ge_5"] == 1  # the 6-member cluster, not the singleton

    def test_empty_input_does_not_divide_by_zero(self):
        s = summarize_features(0, [], universe_count=0)
        assert s["coverage_pct"] == 0.0
        assert s["largest_cluster"] == 0


class TestBatches:
    def test_splits_with_a_short_final_batch(self):
        assert batches([{"i": i} for i in range(5)], 2) == [
            [{"i": 0}, {"i": 1}],
            [{"i": 2}, {"i": 3}],
            [{"i": 4}],
        ]

    def test_rejects_non_positive_size(self):
        with pytest.raises(ValueError, match="batch size must be positive"):
            batches([{"i": 1}], 0)


class TestProjectionMustBeUndirected:
    """Regression guard on a defect that nearly shipped a fabricated result.

    The first version of this module reused interlock.py's Cypher projection, whose
    `WHERE id(a) < id(b)` predicate stores each pair once. `gds.graph.project.cypher` accepts no
    `orientation`, so every edge pointed low-id -> high-id and the projection was a DAG
    (gds.scc.stream: 7,702 components over 7,702 nodes).

    That matters because load_company_universe.py loads company_tickers.json in SEC's
    size-descending order, so internal node id tracks market-cap rank (id=0 AAPL, 1 NVDA, 2 GOOGL).
    Betweenness over an id-ascending DAG rewards being structurally upstream — i.e. being a large
    cap. It produced a reassuring PRU/AIG/GPN top-8 that was substantially an artifact of insertion
    order rather than a governance finding.

    The native projection is not a style preference here; it is the difference between a measurement
    and an invention.
    """

    def test_projection_is_declared_undirected(self):
        from secgraph.ingestion.ownership.interlock_features import _PROJECTION_CONFIG

        assert _PROJECTION_CONFIG["SHARES_DIRECTOR"]["orientation"] == "UNDIRECTED"

    def test_does_not_call_the_directed_cypher_projection(self):
        """The id(a) < id(b) projection must not come back through the front door.

        Checks the actual CALL, parsed from the AST — not a substring of the source. The first
        version of this test split the source on a triple-quote and inspected only the last 424
        chars (2% of the file), so it passed while `project.cypher` was still present earlier in
        the module. A guard that cannot fail is worse than no guard: it reports safety it has not
        verified.
        """
        import ast
        import inspect

        from secgraph.ingestion.ownership import interlock_features

        tree = ast.parse(inspect.getsource(interlock_features))
        called = {
            ast.unparse(node.func)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        directed = [c for c in called if c.endswith("graph.project.cypher")]
        assert not directed, (
            f"found a directed Cypher projection: {directed}. gds.graph.project.cypher accepts no "
            "orientation option, so it cannot express an undirected interlock graph — and a "
            "directed one makes betweenness a proxy for node-insertion order. Use the native "
            "gds.graph.project(name, 'Company', {'SHARES_DIRECTOR': {'orientation': 'UNDIRECTED'}})."
        )
        assert any(c.endswith("graph.project") for c in called), (
            "expected a native gds.graph.project call; if the projection moved, update this guard "
            "rather than deleting it."
        )

    def test_guard_would_actually_catch_a_regression(self):
        """Meta-test: prove the AST guard fires on code that has the defect.

        Added because the previous version of the guard above silently inspected the wrong 2% of the
        file and passed regardless. A regression test nobody has seen fail is an assumption.
        """
        import ast

        offending = "gds.graph.project.cypher(name, node_q, rel_q)"
        tree = ast.parse(offending)
        called = {
            ast.unparse(node.func)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert [c for c in called if c.endswith("graph.project.cypher")], (
            "the detection logic itself is broken — it fails to recognise the defect it exists "
            "to catch"
        )
