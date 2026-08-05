"""Unit tests for the INFLUENCES presumption tiers (no Neo4j, no network).

`CONTROLS` fires only on a single >=50% stake, which is structurally anti-selected for large caps —
an issuer with a majority holder has little free float. These edges tier at the Federal Reserve's
control presumptions (12 CFR 225.2(e)) so the recognizable names become reachable, *without*
relabelling "control" to mean a 25% stake.
"""

from __future__ import annotations

import pytest

from secgraph.ingestion.ownership.influence_edges import (
    INFLUENCE_TIERS,
    batches,
    presumption_tier,
    summarize_influence,
)


class TestPresumptionTier:
    @pytest.mark.parametrize(
        "pct,expected",
        [
            (74.3, 50),  # Deutsche Telekom / T-Mobile
            (50.0, 50),  # boundary is inclusive
            (49.9, 25),
            (37.0, 25),  # Buffett / Berkshire
            (26.1, 25),  # Liberty Broadband / Charter
            (25.0, 25),
            (24.9, 15),
            (15.0, 15),
            (10.0, 10),
            (9.9, None),  # below the lowest tier
            (5.1, None),
        ],
    )
    def test_assigns_the_highest_tier_reached(self, pct, expected):
        assert presumption_tier(pct) == expected

    def test_none_percent_has_no_tier(self):
        """An unverified percent must not be assigned a regulatory presumption."""
        assert presumption_tier(None) is None

    def test_five_percent_is_deliberately_not_a_tier(self):
        """5% is the 13D *filing* trigger, so every edge would qualify and the tier would carry
        no information. Excluded on purpose."""
        assert 5.0 not in INFLUENCE_TIERS
        assert presumption_tier(6.0) is None

    def test_returns_an_int_label_not_a_float(self):
        """The tier is a label for which presumption applies; the exact percent is on the edge."""
        assert presumption_tier(37.0) == 25
        assert isinstance(presumption_tier(37.0), int)


class TestSummarizeInfluence:
    def _row(self, tier, seat=False, owner="o", company="c"):
        return {"tier": tier, "board_seat": seat, "owner_cik": owner, "company_cik": company}

    def test_counts_by_tier_and_the_board_conjunction(self):
        rows = [
            self._row(50, True, "o1", "c1"),
            self._row(25, True, "o2", "c2"),
            self._row(25, False, "o3", "c3"),
            self._row(10, False, "o4", "c4"),
        ]
        s = summarize_influence(rows)
        assert s["edges"] == 4
        assert s["by_tier"] == {10: 1, 25: 2, 50: 1}
        # The conjunction is the headline: far rarer than either limb alone.
        assert s["with_board_seat"] == 2
        assert s["board_seat_by_tier"] == {25: 1, 50: 1}

    def test_counts_distinct_owners_and_companies(self):
        rows = [self._row(25, False, "o1", "c1"), self._row(50, False, "o1", "c2")]
        s = summarize_influence(rows)
        assert s["owners"] == 1  # one owner, two issuers
        assert s["companies"] == 2

    def test_untiered_rows_are_ignored(self):
        rows = [self._row(None), self._row(25)]
        s = summarize_influence(rows)
        assert s["by_tier"] == {25: 1}

    def test_empty(self):
        s = summarize_influence([])
        assert s["edges"] == 0
        assert s["by_tier"] == {}
        assert s["with_board_seat"] == 0


class TestBatches:
    def test_splits_with_short_final_batch(self):
        assert batches([{"i": i} for i in range(3)], 2) == [[{"i": 0}, {"i": 1}], [{"i": 2}]]

    def test_rejects_non_positive_size(self):
        with pytest.raises(ValueError, match="positive"):
            batches([{"i": 1}], 0)


class TestQueryContract:
    """Guards the two things that make the edge defensible."""

    def test_self_filings_are_excluded(self):
        """An entity filing 13D on itself adds a bogus self-loop; control_edges.py excludes them
        and this must match."""
        from secgraph.ingestion.ownership.influence_edges import _COMPUTE_QUERY

        assert "b.cik <> c.cik" in _COMPUTE_QUERY

    def test_board_seat_joins_on_cik_not_name(self):
        """Hard-key discipline: an Insider and a BeneficialOwner sharing a CIK are the same legal
        person. Name matching would invent relationships."""
        from secgraph.ingestion.ownership.influence_edges import _COMPUTE_QUERY

        assert "i.cik = b.cik" in _COMPUTE_QUERY

    def test_board_seat_is_recency_filtered(self):
        """A seat from 2014 is not a current seat. The cutoff is what makes the claim fresh."""
        from secgraph.ingestion.ownership.influence_edges import _COMPUTE_QUERY

        assert "dir.last_seen >= date($seat_since)" in _COMPUTE_QUERY

    def test_controls_is_not_touched(self):
        """`CONTROLS` must keep meaning >=50% — the whole point of a separate edge type is that
        'control' does not get relabelled to mean a 25% stake."""
        from secgraph.ingestion.ownership import influence_edges

        src = influence_edges._WRITE_QUERY + influence_edges._DELETE_QUERY
        assert "CONTROLS" not in src
        assert "INFLUENCES" in src
