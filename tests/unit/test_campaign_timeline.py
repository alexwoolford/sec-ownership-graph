"""Unit tests for the activist campaign-timing pure helpers.

No live DB. The load-bearing behaviours under test are the ones that keep the pillar
demo-grade: filer classification (activist vs index money vs custodian), affiliated-entity
collapsing (so one manager filing through seven vehicles is not reported as seven activists),
sequence ordering, and the abstain render.
"""

from __future__ import annotations

import pytest

from secgraph.ingestion.ownership.campaign_timeline import (
    ACTIVIST_FRANCHISES,
    DEFAULT_MIN_ACTIVISTS,
    DEFAULT_WINDOW_DAYS,
    CampaignTimelineEngine,
    CampaignTimelineResult,
    classify_filer,
    days_between,
    distinct_franchises,
    is_activist_franchise,
    order_filings,
    summarize_sequence,
)


class TestClassifyFiler:
    @pytest.mark.parametrize(
        "name",
        ["ICAHN CARL C", "GAMCO INVESTORS, INC. ET AL", "Saba Capital Management, L.P."],
    )
    def test_known_franchises_are_activists(self, name):
        assert classify_filer(name, "13D") == "activist"

    def test_index_money_is_not_an_activist(self):
        assert classify_filer("BlackRock, Inc.", "13G") == "passive_index"
        assert classify_filer("VANGUARD GROUP INC", "13G") == "passive_index"

    def test_custodian_is_labelled_separately_from_index(self):
        assert classify_filer("NOMURA HOLDINGS INC", "13G") == "custodian"

    def test_unknown_13d_filer_is_insider_or_other_not_activist(self):
        """Micro-cap founders crossing 5% file 13D but are not activists."""
        assert classify_filer("HU YINAN", "13D") == "insider_or_other"

    def test_unknown_13g_filer_is_other_holder(self):
        assert classify_filer("Cooper Creek Partners Management LLC", "13G") == "other_holder"

    def test_activist_classification_wins_over_bank_like_token(self):
        # Ordering matters: the franchise list is the more specific signal.
        assert classify_filer("GABELLI FUNDS LLC", "13D") == "activist"

    def test_handles_none(self):
        assert classify_filer(None, None) == "other_holder"


class TestIsActivistFranchise:
    def test_case_insensitive(self):
        assert is_activist_franchise("icahn carl c") is True

    def test_rejects_unknown(self):
        assert is_activist_franchise("Some Family Office LLC") is False

    def test_rejects_none(self):
        assert is_activist_franchise(None) is False


class TestDistinctFranchises:
    def test_collapses_affiliated_entities_of_one_manager(self):
        """Bulldog Investors + Bulldog Investors, LLP is ONE franchise, not two."""
        filings = [{"filer": "Bulldog Investors"}, {"filer": "Bulldog Investors, LLP"}]
        assert distinct_franchises(filings) == ["BULLDOG INVESTORS"]

    def test_counts_genuinely_distinct_franchises(self):
        filings = [{"filer": "ICAHN CARL C"}, {"filer": "GAMCO INVESTORS, INC. ET AL"}]
        assert len(distinct_franchises(filings)) == 2

    def test_ignores_non_franchise_filers(self):
        filings = [{"filer": "BlackRock, Inc."}, {"filer": "HU YINAN"}]
        assert distinct_franchises(filings) == []

    def test_empty(self):
        assert distinct_franchises([]) == []


class TestOrderFilings:
    def test_sorts_oldest_first(self):
        out = order_filings([{"filing_date": "2025-11-05"}, {"filing_date": "2025-08-01"}])
        assert [f["filing_date"] for f in out] == ["2025-08-01", "2025-11-05"]

    def test_undated_sink_to_end(self):
        out = order_filings([{"filing_date": None}, {"filing_date": "2025-01-01"}])
        assert out[0]["filing_date"] == "2025-01-01"
        assert out[-1]["filing_date"] is None

    def test_empty(self):
        assert order_filings([]) == []


class TestDaysBetween:
    def test_computes_gap(self):
        assert days_between("2025-08-01", "2025-11-05") == 96

    def test_same_day_is_zero(self):
        assert days_between("2025-08-01", "2025-08-01") == 0

    def test_none_on_missing(self):
        assert days_between(None, "2025-01-01") is None
        assert days_between("2025-01-01", None) is None

    def test_none_on_unparseable(self):
        assert days_between("not-a-date", "2025-01-01") is None


class TestSummarizeSequence:
    def _f(self, filer, date, pct=None, cls="activist"):
        return {
            "filer": filer,
            "filing_date": date,
            "percent_of_class": pct,
            "filer_class": cls,
        }

    def test_identifies_first_mover_and_follower_gap(self):
        s = summarize_sequence(
            [
                self._f("GAMCO INVESTORS, INC. ET AL", "2025-08-01", 4.0),
                self._f("ICAHN CARL C", "2025-11-05", 14.79),
            ]
        )
        assert s["activist_count"] == 2
        assert s["first_mover"]["filer"] == "GAMCO INVESTORS, INC. ET AL"
        assert s["followers"][0]["filer"] == "ICAHN CARL C"
        assert s["followers"][0]["days_after_first"] == 96

    def test_ignores_non_activists_when_sequencing(self):
        s = summarize_sequence(
            [
                self._f("BlackRock, Inc.", "2025-01-01", cls="passive_index"),
                self._f("ICAHN CARL C", "2025-06-01"),
            ]
        )
        assert s["activist_count"] == 1
        assert s["first_mover"]["filer"] == "ICAHN CARL C"

    def test_no_activists_reports_zero(self):
        s = summarize_sequence([self._f("BlackRock, Inc.", "2025-01-01", cls="passive_index")])
        assert s["activist_count"] == 0
        assert s["first_mover"] is None

    def test_empty(self):
        assert summarize_sequence([])["activist_count"] == 0


class TestFormatAnswer:
    def test_abstain_states_reason(self):
        r = CampaignTimelineResult(
            anchor="Foo Inc",
            task_type="campaign_timeline",
            abstained=True,
            result={"reason": "no_dated_ownership_filings", "note": "No dated 13D."},
        )
        msg = CampaignTimelineEngine.format_answer(r)
        assert "No graph-grounded answer" in msg
        assert "no_dated_ownership_filings" in msg

    def test_timeline_render_shows_sequence_and_citations(self):
        r = CampaignTimelineResult(
            anchor="MONRO, INC.",
            task_type="campaign_timeline",
            abstained=False,
            result={
                "filing_count": 2,
                "timeline": [
                    {
                        "filing_date": "2025-08-01",
                        "filing_type": "13D",
                        "percent_of_class": 4.0,
                        "filer": "GAMCO INVESTORS, INC. ET AL",
                        "filer_class": "activist",
                        "accession_number": "0000807249-25-000101",
                    },
                    {
                        "filing_date": "2025-11-05",
                        "filing_type": "13D",
                        "percent_of_class": 14.79,
                        "filer": "ICAHN CARL C",
                        "filer_class": "activist",
                        "accession_number": "0001539497-25-002847",
                    },
                ],
                "sequence": {
                    "activist_count": 2,
                    "first_mover": {
                        "filer": "GAMCO INVESTORS, INC. ET AL",
                        "filing_date": "2025-08-01",
                        "percent_of_class": 4.0,
                    },
                    "followers": [
                        {
                            "filer": "ICAHN CARL C",
                            "filing_date": "2025-11-05",
                            "percent_of_class": 14.79,
                            "days_after_first": 96,
                        }
                    ],
                },
                "franchises": ["GAMCO", "ICAHN"],
            },
        )
        msg = CampaignTimelineEngine.format_answer(r)
        assert "First mover: GAMCO" in msg
        assert "96 days later" in msg
        assert "0001539497-25-002847" in msg

    def test_convergence_render(self):
        r = CampaignTimelineResult(
            anchor="convergence since 2023-01-01",
            task_type="convergence_scan",
            abstained=False,
            result={
                "target_count": 1,
                "targets": [
                    {
                        "company": {"cik": "1", "name": "MONRO, INC.", "ticker": "MNRO"},
                        "franchises": ["GAMCO", "ICAHN"],
                        "franchise_count": 2,
                        "span_days": 96,
                        "filings": [
                            {
                                "filing_date": "2025-08-01",
                                "filer": "GAMCO",
                                "percent_of_class": 4.0,
                            }
                        ],
                        "sequence": {},
                    }
                ],
            },
        )
        msg = CampaignTimelineEngine.format_answer(r)
        assert "MNRO" in msg
        assert "2 franchises within 96 days" in msg


def test_defaults_are_pinned():
    """These thresholds define what counts as a convergence; pin the contract."""
    assert DEFAULT_WINDOW_DAYS == 180
    assert DEFAULT_MIN_ACTIVISTS == 2
    assert "ICAHN" in ACTIVIST_FRANCHISES
