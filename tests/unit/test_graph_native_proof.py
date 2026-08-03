"""Unit tests for the three-win graph-native proof — pure functions, no DB/network.

The proof's credibility rests on pure graph logic: control-chain enumeration (variable
depth, cycle-safe), coalition components + diameter (the transitive structure SQL can't
build), and the custodial-hub scrub (the pillar-1 precision discipline). These pin all
three.
"""

from __future__ import annotations

from secgraph.ingestion.ownership import graph_native_proof as gp


class TestIsCustodialHub:
    def test_flags_brokers(self):
        assert gp.is_custodial_hub("JPMORGAN CHASE & CO") is True
        assert gp.is_custodial_hub("RBC Municipal Products") is True
        assert gp.is_custodial_hub("BANK OF AMERICA CORP /DE/") is True

    def test_keeps_real_activists(self):
        assert gp.is_custodial_hub("ICAHN CARL C") is False
        assert gp.is_custodial_hub("GAMCO INVESTORS, INC") is False
        assert gp.is_custodial_hub(None) is False


class TestBuildControlAdjacency:
    def test_keeps_only_control_edges(self):
        edges = [
            {"owner_cik": "A", "company_cik": "B", "pct": 60.0},
            {"owner_cik": "A", "company_cik": "C", "pct": 40.0},  # below threshold
            {"owner_cik": "X", "company_cik": "X", "pct": 90.0},  # self-loop
            {"owner_cik": "D", "company_cik": None, "pct": 80.0},  # missing endpoint
        ]
        adj = gp.build_control_adjacency(edges)
        assert adj == {"A": [("B", 60.0)]}

    def test_custom_threshold(self):
        edges = [{"owner_cik": "A", "company_cik": "B", "pct": 30.0}]
        assert gp.build_control_adjacency(edges, threshold=25.0) == {"A": [("B", 30.0)]}


class TestEnumerateControlChains:
    def test_finds_multi_hop_chain(self):
        # A -> B -> C -> D (3 hops), rooted at A
        adj = {"A": [("B", 94.0)], "B": [("C", 68.0)], "C": [("D", 85.0)]}
        chains = gp.enumerate_control_chains(adj, min_hops=2)
        assert len(chains) == 1
        assert [step[0] for step in chains[0]] == ["A", "B", "C", "D"]

    def test_ignores_single_hop(self):
        adj = {"A": [("B", 60.0)]}  # only 1 hop
        assert gp.enumerate_control_chains(adj, min_hops=2) == []

    def test_cycle_safe(self):
        # A -> B -> A cycle must not loop forever; no chain of >=2 hops exists
        adj = {"A": [("B", 60.0)], "B": [("A", 60.0)]}
        chains = gp.enumerate_control_chains(adj, min_hops=2)
        assert chains == []

    def test_branching_pyramid(self):
        # A controls B and C; B controls D. Chains: A-B-D (2h). A-C is 1 hop (dropped).
        adj = {"A": [("B", 90.0), ("C", 90.0)], "B": [("D", 90.0)]}
        chains = gp.enumerate_control_chains(adj, min_hops=2)
        assert len(chains) == 1
        assert [s[0] for s in chains[0]] == ["A", "B", "D"]


class TestCoalitionComponents:
    def test_transitive_component(self):
        # A-B, B-C, D-E : one 3-node component {A,B,C} and one 2-node {D,E}
        pairs = [("A", "B"), ("B", "C"), ("D", "E")]
        comps = gp.coalition_components(pairs)
        assert [len(c) for c in comps] == [3, 2]
        assert comps[0] == {"A", "B", "C"}

    def test_custodial_scrub_removes_bridge(self):
        # JPMorgan bridges two otherwise-separate activists; scrub splits them.
        pairs = [("ICAHN", "JPM"), ("JPM", "GAMCO")]
        names = {"ICAHN": "ICAHN CARL C", "JPM": "JPMORGAN CHASE", "GAMCO": "GAMCO INVESTORS"}
        raw = gp.coalition_components(pairs)
        assert len(raw[0]) == 3  # all linked through JPMorgan
        scrubbed = gp.coalition_components(pairs, scrub_custodial_names=names)
        assert scrubbed == []  # both edges dropped (each touches JPMorgan)


class TestComponentDiameter:
    def test_path_diameter(self):
        # A-B-C-D is a line: diameter 3
        pairs = [("A", "B"), ("B", "C"), ("C", "D")]
        comp = {"A", "B", "C", "D"}
        assert gp.component_diameter(comp, pairs) == 3

    def test_clique_diameter_one(self):
        # fully connected triangle: diameter 1 (all mutually linked = not transitive)
        pairs = [("A", "B"), ("B", "C"), ("A", "C")]
        assert gp.component_diameter({"A", "B", "C"}, pairs) == 1

    def test_singleton(self):
        assert gp.component_diameter({"A"}, []) == 0
