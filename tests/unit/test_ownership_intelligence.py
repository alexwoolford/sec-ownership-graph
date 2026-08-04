"""Unit tests for the ownership query-core pure helpers.

No live DB: exercises the anchor/chain/coalition selection functions and the
``format_answer`` renderer (including the abstain path) that are the unit-test surface of
:mod:`secgraph.ingestion.ownership.intelligence`. The DB-bound engine methods
and traversal math (reused from ``graph_native_proof``) are covered elsewhere / by their own
suite.
"""

from __future__ import annotations

from secgraph.ingestion.ownership.intelligence import (
    OwnershipIntelligenceEngine,
    OwnershipIntelligenceResult,
    chain_contains,
    chains_from_paths,
    chains_through_anchor,
    coalition_of,
    render_control_chain,
)


class TestChainsFromPaths:
    """The Cypher-path → rendered-chain adapter (replaces the old Python adjacency walk)."""

    def _row(self, names, pcts, accs=None, dates=None):
        return {
            "steps": [{"cik": str(i), "name": n} for i, n in enumerate(names)],
            "pcts": pcts,
            "accessions": accs if accs is not None else [f"acc-{i}" for i in range(len(pcts))],
            "filing_dates": dates if dates is not None else ["2020-01-01"] * len(pcts),
        }

    def test_root_carries_no_percentage(self):
        chains, _ = chains_from_paths([self._row(["Root", "Mid"], [62.0])])
        assert chains[0][0]["pct"] == 0.0
        assert chains[0][1]["pct"] == 62.0

    def test_multi_hop_chain_aligns_pct_to_controlled_step(self):
        chains, _ = chains_from_paths([self._row(["A", "B", "C", "D"], [62.0, 83.0, 85.0])])
        assert [s["name"] for s in chains[0]] == ["A", "B", "C", "D"]
        assert [s["pct"] for s in chains[0]] == [0.0, 62.0, 83.0, 85.0]

    def test_evidence_one_entry_per_control_hop(self):
        _, ev = chains_from_paths([self._row(["A", "B", "C"], [62.0, 83.0])])
        assert len(ev) == 2
        assert ev[0]["owner"] == "A" and ev[0]["company"] == "B"
        assert ev[0]["percent_of_class"] == 62.0
        assert ev[1]["owner"] == "B" and ev[1]["company"] == "C"

    def test_evidence_dedupes_filing_shared_by_two_chains(self):
        # Two chains ending in the same final hop must cite that filing once.
        rows = [
            self._row(["A", "B", "Z"], [62.0, 85.0], accs=["a1", "shared"]),
            self._row(["Q", "B", "Z"], [70.0, 85.0], accs=["q1", "shared"]),
        ]
        _, ev = chains_from_paths(rows)
        shared = [e for e in ev if e["accession_number"] == "shared"]
        assert len(shared) == 1

    def test_skips_degenerate_single_node_path(self):
        chains, ev = chains_from_paths([{"steps": [{"cik": "1", "name": "A"}], "pcts": []}])
        assert chains == [] and ev == []

    def test_handles_empty_rows(self):
        assert chains_from_paths([]) == ([], [])

    def test_tolerates_missing_accession_and_date(self):
        _, ev = chains_from_paths([self._row(["A", "B"], [51.0], accs=[None], dates=[None])])
        assert ev[0]["accession_number"] is None
        assert ev[0]["filing_date"] is None


class TestChainsThroughAnchor:
    def test_selects_only_chains_touching_anchor(self):
        chains = [
            [("A", 0.0), ("B", 90.0), ("C", 80.0)],
            [("X", 0.0), ("Y", 75.0)],
        ]
        hits = chains_through_anchor(chains, "C")
        assert len(hits) == 1
        assert hits[0][0][0] == "A"

    def test_longest_first(self):
        chains = [
            [("A", 0.0), ("B", 90.0)],
            [("A", 0.0), ("B", 90.0), ("C", 80.0)],
        ]
        hits = chains_through_anchor(chains, "B")
        assert [len(c) for c in hits] == [3, 2]

    def test_no_match_returns_empty(self):
        assert chains_through_anchor([[("A", 0.0), ("B", 90.0)]], "Z") == []


class TestChainContains:
    def test_true_when_present(self):
        rendered = [
            {"cik": "A", "name": "Alpha", "pct": 0.0},
            {"cik": "B", "name": "Beta", "pct": 90.0},
        ]
        assert chain_contains(rendered, "B") is True

    def test_false_when_absent(self):
        rendered = [{"cik": "A", "name": "Alpha", "pct": 0.0}]
        assert chain_contains(rendered, "Z") is False


class TestRenderControlChain:
    def test_maps_names_and_keeps_pct(self):
        chain = [("A", 0.0), ("B", 90.0)]
        names = {"A": "Alpha Corp", "B": "Beta Inc"}
        out = render_control_chain(chain, names)
        assert out == [
            {"cik": "A", "name": "Alpha Corp", "pct": 0.0},
            {"cik": "B", "name": "Beta Inc", "pct": 90.0},
        ]

    def test_falls_back_to_cik_when_name_missing(self):
        out = render_control_chain([("A", 0.0)], {})
        assert out[0]["name"] == "A"


class TestCoalitionOf:
    def test_finds_component_with_anchor(self):
        comps = [{"a", "b", "c"}, {"x", "y"}]
        assert coalition_of(comps, "y") == {"x", "y"}

    def test_empty_when_anchor_absent(self):
        assert coalition_of([{"a", "b"}], "z") == set()


class TestFormatAnswer:
    def test_abstain_message(self):
        r = OwnershipIntelligenceResult(
            anchor="Foo Inc",
            task_type="control_chain",
            abstained=True,
            result={"reason": "no_verified_control_chain", "note": "No control edge."},
            evidence=[],
            metadata={},
        )
        msg = OwnershipIntelligenceEngine.format_answer(r)
        assert "No graph-grounded answer" in msg
        assert "no_verified_control_chain" in msg

    def test_control_chain_render_with_evidence(self):
        r = OwnershipIntelligenceResult(
            anchor="Income Opportunity Realty",
            task_type="control_chain",
            abstained=False,
            result={
                "chains": [
                    [
                        {"cik": "1", "name": "Basic Capital", "pct": 0.0},
                        {"cik": "2", "name": "American Realty", "pct": 62.0},
                    ]
                ],
                "chain_count": 1,
                "deepest_hops": 1,
            },
            evidence=[
                {
                    "owner": "Basic Capital",
                    "company": "American Realty",
                    "percent_of_class": 62.0,
                    "accession_number": "0001-23",
                    "filing_date": "2021-05-01",
                }
            ],
            metadata={},
        )
        msg = OwnershipIntelligenceEngine.format_answer(r)
        assert "Basic Capital" in msg
        assert "American Realty (62%)" in msg
        assert "0001-23" in msg
        assert "Evidence" in msg

    def test_board_path_render(self):
        r = OwnershipIntelligenceResult(
            anchor="AAPL → JPM",
            task_type="board_path",
            abstained=False,
            result={
                "chain": [
                    {"cik": "1", "name": "Apple", "ticker": "AAPL"},
                    {"cik": "2", "name": "JPMorgan", "ticker": "JPM"},
                ],
                "via_directors": ["BELL JAMES A"],
                "hops": 1,
            },
            evidence=[{"bridge_director": "BELL JAMES A", "shared_director_count": 1}],
            metadata={},
        )
        msg = OwnershipIntelligenceEngine.format_answer(r)
        assert "AAPL — JPM" in msg
        assert "BELL JAMES A" in msg

    def test_coalition_render(self):
        r = OwnershipIntelligenceResult(
            anchor="ICAHN CARL C",
            task_type="coalition",
            abstained=False,
            result={"members": ["GAMCO", "ICAHN CARL C"], "member_count": 2, "diameter_hops": 1},
            evidence=[],
            metadata={},
        )
        msg = OwnershipIntelligenceEngine.format_answer(r)
        assert "2 members" in msg
        assert "ICAHN CARL C" in msg

    def test_ownership_snapshot_render(self):
        r = OwnershipIntelligenceResult(
            anchor="Apple Inc.",
            task_type="ownership_snapshot",
            abstained=False,
            result={
                "company": {"cik": "320193", "name": "Apple Inc.", "ticker": "AAPL"},
                "top_holders": [
                    {"owner": "VANGUARD GROUP INC", "filing_type": "13G", "percent_of_class": 7.2},
                    {"owner": "BlackRock Inc.", "filing_type": "13G", "percent_of_class": None},
                ],
                "beneficial_owner_count": 4,
                "director_count": 16,
                "officer_count": 34,
                "has_verified_control_edge": False,
            },
            evidence=[],
            metadata={},
        )
        msg = OwnershipIntelligenceEngine.format_answer(r)
        assert "Apple Inc." in msg
        assert "VANGUARD GROUP INC" in msg
        assert "7.2%" in msg
        assert "control edge: no" in msg


class TestCollapseAffiliates:
    """One manager files through several CIKs; a roster of CIKs is not a roster of actors.

    The Icahn coalition returns 13 CIKs but 11 distinct actors: "Bulldog Investors" and
    "Bulldog Investors, LLP" are two CIKs of one firm, and Phillip Goldstein is that firm's
    principal filing personally. Quoting 13 to someone who knows the names invites the
    objection immediately.
    """

    def test_collapses_name_variants_of_one_firm(self):
        from secgraph.ingestion.ownership.intelligence import collapse_affiliates

        kept = collapse_affiliates(["Bulldog Investors", "Bulldog Investors, LLP"])
        assert kept == ["Bulldog Investors"]

    def test_collapses_a_principal_into_their_firm(self):
        """A franchise-token match cannot see this: the two names share no substring."""
        from secgraph.ingestion.ownership.intelligence import collapse_affiliates

        kept = collapse_affiliates(["Bulldog Investors", "GOLDSTEIN PHILLIP"])
        assert kept == ["Bulldog Investors"]

    def test_keeps_genuinely_distinct_filers(self):
        """GAMCO and Marc Gabelli have separate 13D histories — a family tie is not one actor."""
        from secgraph.ingestion.ownership.intelligence import collapse_affiliates

        kept = collapse_affiliates(["GAMCO INVESTORS, INC. ET AL", "GABELLI MARC"])
        assert len(kept) == 2

    def test_passes_through_unknown_names(self):
        """Dropping a non-franchise filer would understate the coalition."""
        from secgraph.ingestion.ownership.intelligence import collapse_affiliates

        kept = collapse_affiliates(["ICAHN CARL C", "Some Unlisted Fund LP"])
        assert kept == ["ICAHN CARL C", "Some Unlisted Fund LP"]

    def test_empty_input(self):
        from secgraph.ingestion.ownership.intelligence import collapse_affiliates

        assert collapse_affiliates([]) == []
