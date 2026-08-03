"""
Unit tests for the SEC ownership-graph parsers (no live SEC, no Neo4j).

Covers the pure fetch/parse layer of ``ingestion/ownership/``: bulk-TSV date/CIK
normalization, dataset-period key ordering, TSV iteration over a real in-memory
zip, the Form 3/4/5 submission/reporting-owner join, the CUSIP→CIK crosswalk
matcher, the 13D/13G SGML header parser, and the SIC→sector bucketer. Loaders
that touch Neo4j are exercised only through their pure row-builders.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from secgraph.ingestion.ownership import (
    beneficial,
    bulk_datasets,
    cusip_crosswalk,
    insiders,
    institutional,
    universe,
)
from secgraph.ingestion.ownership.sic_sectors import sector_for_sic


def _make_zip(path: Path, tables: dict[str, list[str]]) -> None:
    """Write a zip with each ``table`` as ``<TABLE>.tsv`` (rows are pre-joined)."""
    with zipfile.ZipFile(path, "w") as archive:
        for table, lines in tables.items():
            archive.writestr(f"{table}.tsv", "\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# sic_sectors.sector_for_sic
# --------------------------------------------------------------------------- #
class TestSectorForSic:
    @pytest.mark.parametrize(
        "code,expected",
        [
            ("3571", "Manufacturing"),
            (6021, "Finance, Insurance & Real Estate"),
            ("1040", "Mining"),
            ("7372", "Services"),
            ("100", "Agriculture, Forestry & Fishing"),
            ("9995", "Nonclassifiable"),
        ],
    )
    def test_maps_known_ranges(self, code, expected):
        assert sector_for_sic(code) == expected

    @pytest.mark.parametrize("bad", ["", None, "abc", "99", "0"])
    def test_unmappable_returns_none(self, bad):
        assert sector_for_sic(bad) is None


# --------------------------------------------------------------------------- #
# bulk_datasets: date / CIK normalization
# --------------------------------------------------------------------------- #
class TestParseSecDate:
    def test_valid(self):
        assert bulk_datasets.parse_sec_date("31-MAR-2025") == "2025-03-31"

    def test_single_digit_day_padded(self):
        assert bulk_datasets.parse_sec_date("1-JAN-2024") == "2024-01-01"

    @pytest.mark.parametrize("bad", ["", None, "2025-03-31", "31-XXX-2025", "garbage"])
    def test_malformed_returns_none(self, bad):
        assert bulk_datasets.parse_sec_date(bad) is None


class TestNormalizeCik:
    def test_zero_pads(self):
        assert bulk_datasets.normalize_cik("320193") == "0000320193"

    def test_already_padded(self):
        assert bulk_datasets.normalize_cik("0001840502") == "0001840502"

    @pytest.mark.parametrize("bad", ["", None, "abc", "12x"])
    def test_non_numeric_returns_none(self, bad):
        assert bulk_datasets.normalize_cik(bad) is None


# --------------------------------------------------------------------------- #
# bulk_datasets: period key + ordering (two filename schemes)
# --------------------------------------------------------------------------- #
class TestPeriodKeys:
    def test_quarter_scheme(self):
        assert bulk_datasets._period_key("2025q1_form345.zip") == "2025q1"

    def test_daterange_scheme(self):
        key = bulk_datasets._period_key("01sep2025-30nov2025_form13f.zip")
        assert key == "01sep2025-30nov2025"

    def test_no_period_returns_none(self):
        assert bulk_datasets._period_key("random.zip") is None

    def test_select_quarters_newest_first(self):
        available = {
            "2024q3": "u1",
            "2025q1": "u2",
            "2024q4": "u3",
        }
        assert bulk_datasets.select_quarters(available, 2) == ["2025q1", "2024q4"]

    def test_daterange_sorts_by_end_date(self):
        available = {
            "01mar2025-31may2025": "u1",
            "01sep2025-30nov2025": "u2",
            "01jun2025-31aug2025": "u3",
        }
        assert bulk_datasets.select_quarters(available, 1) == ["01sep2025-30nov2025"]


# --------------------------------------------------------------------------- #
# bulk_datasets: TSV iteration + staged discovery over a real zip
# --------------------------------------------------------------------------- #
class TestIterTsvRows:
    def test_reads_rows_as_dicts(self, tmp_path):
        zpath = tmp_path / "q.zip"
        _make_zip(
            zpath,
            {
                "SUBMISSION": [
                    "ACCESSION_NUMBER\tISSUERCIK\tFILING_DATE",
                    "0001-25-000001\t320193\t31-MAR-2025",
                ]
            },
        )
        rows = list(bulk_datasets.iter_tsv_rows(zpath, "SUBMISSION"))
        assert rows == [
            {
                "ACCESSION_NUMBER": "0001-25-000001",
                "ISSUERCIK": "320193",
                "FILING_DATE": "31-MAR-2025",
            }
        ]

    def test_ragged_trailing_fields_tolerated(self, tmp_path):
        zpath = tmp_path / "q.zip"
        _make_zip(zpath, {"T": ["a\tb\tc", "1\t2"]})  # short row
        rows = list(bulk_datasets.iter_tsv_rows(zpath, "T"))
        assert rows[0] == {"a": "1", "b": "2"}

    def test_missing_table_raises(self, tmp_path):
        zpath = tmp_path / "q.zip"
        _make_zip(zpath, {"T": ["a", "1"]})
        with pytest.raises(KeyError):
            list(bulk_datasets.iter_tsv_rows(zpath, "NOPE"))

    def test_nested_folder_layout_found(self, tmp_path):
        """SEC 13F zips nest tables under a per-period folder — match by basename."""
        zpath = tmp_path / "13f.zip"
        with zipfile.ZipFile(zpath, "w") as archive:
            archive.writestr("01jun2025-31aug2025_form13f/SUBMISSION.tsv", "a\tb\n1\t2\n")
        rows = list(bulk_datasets.iter_tsv_rows(zpath, "SUBMISSION"))
        assert rows == [{"a": "1", "b": "2"}]

    def test_staged_zip_paths_newest_first(self, tmp_path, monkeypatch):
        cache = tmp_path / "form345"
        cache.mkdir()
        (cache / "2024q4_form345.zip").write_bytes(b"x")
        (cache / "2025q1_form345.zip").write_bytes(b"x")
        (cache / "2024q3_form345.zip").write_bytes(b"x")
        monkeypatch.setattr(bulk_datasets, "get_ownership_data_dir", lambda subdir: cache)
        names = [p.name for p in bulk_datasets.staged_zip_paths("form345")]
        assert names == [
            "2025q1_form345.zip",
            "2024q4_form345.zip",
            "2024q3_form345.zip",
        ]


# --------------------------------------------------------------------------- #
# insiders: relationship parse + submission/owner join
# --------------------------------------------------------------------------- #
class TestInsiderParsing:
    def test_parse_relationship_multi(self):
        assert insiders.parse_relationship("Director,Officer") == [
            "DIRECTOR_OF",
            "OFFICER_OF",
        ]

    def test_parse_relationship_dedupes_and_ignores_unknown(self):
        assert insiders.parse_relationship("Director,Director,Other") == ["DIRECTOR_OF"]

    def test_parse_relationship_ten_pct(self):
        assert insiders.parse_relationship("TenPercentOwner") == ["TEN_PCT_OWNER_OF"]

    def test_build_submission_index_filters_bad_rows(self, tmp_path):
        zpath = tmp_path / "s.zip"
        _make_zip(
            zpath,
            {
                "SUBMISSION": [
                    "ACCESSION_NUMBER\tISSUERCIK\tFILING_DATE",
                    "acc-1\t320193\t31-MAR-2025",
                    "\t320193\t31-MAR-2025",  # no accession
                    "acc-2\tnotacik\t31-MAR-2025",  # bad cik
                    "acc-3\t320193\tbad-date",  # bad date
                ]
            },
        )
        index = insiders.build_submission_index([zpath])
        assert set(index) == {"acc-1"}
        assert index["acc-1"] == {"company_cik": "0000320193", "filing_date": "2025-03-31"}

    def test_build_edge_rows_join_and_universe_filter(self, tmp_path):
        zpath = tmp_path / "s.zip"
        _make_zip(
            zpath,
            {
                "SUBMISSION": [
                    "ACCESSION_NUMBER\tISSUERCIK\tFILING_DATE",
                    "acc-1\t320193\t31-MAR-2025",  # in universe
                    "acc-2\t999999\t31-MAR-2025",  # out of universe
                ],
                "REPORTINGOWNER": [
                    "ACCESSION_NUMBER\tRPTOWNERCIK\tRPTOWNERNAME"
                    "\tRPTOWNER_RELATIONSHIP\tRPTOWNER_TITLE",
                    "acc-1\t111\tDoe John\tDirector,Officer\tCEO",
                    "acc-2\t222\tRoe Jane\tDirector\t",  # dropped: out of universe
                ],
            },
        )
        edges = insiders.build_edge_rows([zpath], universe_ciks={"0000320193"})
        assert len(edges["DIRECTOR_OF"]) == 1
        assert len(edges["OFFICER_OF"]) == 1
        assert edges["TEN_PCT_OWNER_OF"] == []
        director = edges["DIRECTOR_OF"][0]
        assert director["owner_cik"] == "0000000111"
        assert director["company_cik"] == "0000320193"
        assert director["filing_date"] == "2025-03-31"
        # officer_title only attached on the OFFICER_OF edge
        assert edges["OFFICER_OF"][0]["officer_title"] == "CEO"
        assert "officer_title" not in director


# --------------------------------------------------------------------------- #
# cusip_crosswalk: key-based CUSIP-9 → FTD symbol → ticker → CIK
# --------------------------------------------------------------------------- #
def _make_ftd_zip(path: Path, rows: list[str]) -> None:
    """Write a fails-to-deliver zip: one pipe-delimited, latin-1 member."""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("cnsfails.txt", "\n".join(rows) + "\n")


class TestCusipCrosswalk:
    def test_cusip6_and_cusip9(self):
        assert cusip_crosswalk.cusip6("037833100") == "037833"
        assert cusip_crosswalk.cusip6("123") is None
        assert cusip_crosswalk.cusip9("037833100") == "037833100"
        assert cusip_crosswalk.cusip9(" 037833100 ") == "037833100"
        assert cusip_crosswalk.cusip9("03783") is None

    def test_normalize_ticker_strips_punctuation(self):
        assert cusip_crosswalk.normalize_ticker("BRK.B") == "BRKB"
        assert cusip_crosswalk.normalize_ticker("brk-b") == "BRKB"

    def test_collect_ftd_symbols_most_common(self, tmp_path):
        zpath = tmp_path / "ftd.zip"
        _make_ftd_zip(
            zpath,
            [
                "SETTLEMENT DATE|CUSIP|SYMBOL|QUANTITY (FAILS)|DESCRIPTION|PRICE",
                "20260615|037833100|AAPL|10|APPLE INC|200.0",
                "20260616|037833100|AAPL|5|APPLE INC|201.0",
                "20260617|037833100|AAPLX|1|APPLE INC (typo)|201.0",  # minority
                "20260615|345370860|F|3|FORD MOTOR CO|14.0",
            ],
        )
        symbols = cusip_crosswalk.collect_ftd_symbols([zpath])
        assert symbols == {"037833100": "AAPL", "345370860": "F"}

    def test_build_ticker_index_exact_and_normalized(self):
        index = cusip_crosswalk.build_ticker_index(
            [
                {"cik": "0000320193", "ticker": "AAPL"},
                {"cik": "0001067983", "ticker": "BRK.B"},
            ]
        )
        assert index["AAPL"] == "0000320193"
        assert index["BRK.B"] == "0001067983"
        assert index["BRKB"] == "0001067983"  # punctuation-insensitive fallback

    def test_match_cusips_key_based(self):
        # AAPL exact, BRKB→BRK.B normalized, IWM (an ETF) unmatched.
        cusip_symbols = {"037833100": "AAPL", "084670702": "BRKB", "464287655": "IWM"}
        ticker_index = cusip_crosswalk.build_ticker_index(
            [
                {"cik": "0000320193", "ticker": "AAPL"},
                {"cik": "0001067983", "ticker": "BRK.B"},
            ]
        )
        matched, unmatched = cusip_crosswalk.match_cusips(cusip_symbols, ticker_index)
        assert matched == {"037833100": "0000320193", "084670702": "0001067983"}
        assert unmatched == {"464287655": "IWM"}


# --------------------------------------------------------------------------- #
# beneficial: 13D/13G SGML header parsing
# --------------------------------------------------------------------------- #
_HEADER = """\
<SEC-HEADER>
CONFORMED SUBMISSION TYPE:	SC 13D/A
FILED AS OF DATE:	20250115
SUBJECT COMPANY:
	COMPANY CONFORMED NAME:	GAMESTOP CORP
	CENTRAL INDEX KEY:	0001326380
FILED BY:
	COMPANY CONFORMED NAME:	RC VENTURES LLC
	CENTRAL INDEX KEY:	0001822844
</SEC-HEADER>
"""

_HEADER_NAME_ONLY_FILER = """\
<SEC-HEADER>
CONFORMED SUBMISSION TYPE:	SC 13G
FILED AS OF DATE:	20250201
SUBJECT COMPANY:
	COMPANY CONFORMED NAME:	ACME CORP
	CENTRAL INDEX KEY:	0000012345
FILED BY:
	COMPANY CONFORMED NAME:	SOME PERSON GROUP
</SEC-HEADER>
"""


class TestBeneficialHeader:
    def test_name_slug(self):
        assert beneficial.name_slug("RC Ventures LLC") == "rc-ventures-llc"

    def test_classify_filing(self):
        assert beneficial._classify_filing("SC 13D/A") == "13D"
        assert beneficial._classify_filing("SC 13G") == "13G"

    def test_parse_header_full(self):
        parsed = beneficial.parse_header(_HEADER)
        assert parsed["subject_cik"] == "0001326380"
        assert parsed["filer_cik"] == "0001822844"
        assert parsed["filer_name"] == "RC VENTURES LLC"
        assert parsed["form_type"] == "SC 13D/A"
        assert parsed["filing_date"] == "2025-01-15"

    def test_parse_header_name_only_filer(self):
        parsed = beneficial.parse_header(_HEADER_NAME_ONLY_FILER)
        assert parsed["subject_cik"] == "0000012345"
        assert parsed["filer_cik"] is None
        assert parsed["filer_name"] == "SOME PERSON GROUP"

    def test_parse_header_no_filed_by_returns_none(self):
        assert beneficial.parse_header("<SEC-HEADER>\njunk\n</SEC-HEADER>") is None

    def test_build_edge_rows_resolution_and_fallback(self, tmp_path, monkeypatch):
        # Two subjects: one filer resolves to a CIK, one falls back to name-slug.
        def fake_accessions(subject_cik, cache_dir, refresh=False):
            return [{"accession": f"{subject_cik}-acc", "form": "SC 13D", "date": "2025-01-15"}]

        def fake_header(subject_cik, accession, cache_dir, refresh=False):
            if subject_cik == "0001326380":
                return _HEADER
            return _HEADER_NAME_ONLY_FILER

        monkeypatch.setattr(beneficial, "fetch_13dg_accessions", fake_accessions)
        monkeypatch.setattr(beneficial, "fetch_submission_header", fake_header)

        rows = beneficial.build_edge_rows(["0001326380", "0000012345"])
        by_owner = {r["owner_key"]: r for r in rows}
        assert "0001822844" in by_owner  # resolved via CIK
        assert by_owner["0001822844"]["resolved"] is True
        assert "some-person-group" in by_owner  # name-slug fallback
        assert by_owner["some-person-group"]["resolved"] is False

    def test_isolated_fetch_failure_is_tolerated(self, monkeypatch):
        """One unreachable subject must not lose a multi-hour crawl."""
        from secgraph.ingestion.ownership.edgar_client import SubmissionsFetchError

        def flaky(subject_cik, cache_dir, refresh=False):
            if subject_cik == "0000012345":
                raise SubmissionsFetchError("boom")
            return [{"accession": "a", "form": "SC 13D", "date": "2025-01-15"}]

        monkeypatch.setattr(beneficial, "fetch_13dg_accessions", flaky)
        monkeypatch.setattr(beneficial, "fetch_submission_header", lambda *a, **k: _HEADER)
        # 1 of 10 failing is under the threshold: return what we have.
        subjects = ["0001326380"] * 9 + ["0000012345"]
        rows = beneficial.build_edge_rows(subjects)
        assert rows, "surviving subjects should still produce edges"

    def test_systemic_fetch_failure_aborts(self, monkeypatch):
        """Mass failure (e.g. SEC 403-ing every request) must abort, not half-build."""
        from secgraph.ingestion.ownership.edgar_client import SubmissionsFetchError

        def always_blocked(subject_cik, cache_dir, refresh=False):
            raise SubmissionsFetchError("403 Forbidden")

        monkeypatch.setattr(beneficial, "fetch_13dg_accessions", always_blocked)
        with pytest.raises(SubmissionsFetchError, match="aborting"):
            beneficial.build_edge_rows([f"{i:010d}" for i in range(10)])

    def test_zero_edges_universe_wide_aborts(self, monkeypatch):
        """A green build over an empty 13D layer is the failure this prevents."""
        from secgraph.ingestion.ownership.edgar_client import SubmissionsFetchError

        monkeypatch.setattr(beneficial, "build_edge_rows", lambda *a, **k: [])
        with pytest.raises(SubmissionsFetchError, match="0 Schedule 13D/13G edges"):
            beneficial.load_beneficial_owners(
                driver=None,
                subject_ciks=["0001326380", "0000012345"],
                database="secgraph",
                execute=False,
            )


# --------------------------------------------------------------------------- #
# universe: company_tickers.json dedup by CIK
# --------------------------------------------------------------------------- #
class TestFetchCompanyUniverse:
    def test_dedup_first_ticker_wins(self, monkeypatch):
        payload = {
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            "1": {"cik_str": 320193, "ticker": "AAPL2", "title": "Apple Inc."},  # dual-class
            "2": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
        }
        monkeypatch.setattr(universe, "_http_get_json", lambda url: payload)
        companies = universe.fetch_company_universe()
        assert len(companies) == 2
        apple = next(c for c in companies if c["cik"] == "0000320193")
        assert apple["ticker"] == "AAPL"  # first wins
        assert apple["name"] == "Apple Inc."


# --------------------------------------------------------------------------- #
# edgar_client: 13D/G accession filtering (cached index shortcut)
# --------------------------------------------------------------------------- #
class TestEdgarClient:
    def test_fetch_accessions_uses_cache(self, tmp_path):
        from secgraph.ingestion.ownership import edgar_client

        cached = [{"accession": "a-1", "form": "SC 13D", "date": "2025-01-15"}]
        (tmp_path / "0001326380_index.json").write_text(json.dumps(cached))
        result = edgar_client.fetch_13dg_accessions("0001326380", tmp_path)
        assert result == cached

    def test_fetch_accessions_filters_forms(self, tmp_path, monkeypatch):
        from secgraph.ingestion.ownership import edgar_client

        payload = {
            "filings": {
                "recent": {
                    # mix legacy (SC 13*) and post-2024 regime (SCHEDULE 13*) codes
                    "form": ["SC 13D", "10-K", "SC 13G/A", "4", "SCHEDULE 13D/A", "SCHEDULE 13G"],
                    "accessionNumber": ["a", "b", "c", "d", "e", "f"],
                    "filingDate": [
                        "2024-01-01",
                        "2024-01-02",
                        "2024-01-03",
                        "2024-01-04",
                        "2025-01-22",
                        "2025-02-14",
                    ],
                }
            }
        }
        monkeypatch.setattr(
            edgar_client, "_throttled_get", lambda url: json.dumps(payload).encode()
        )
        result = edgar_client.fetch_13dg_accessions("0000000001", tmp_path)
        forms = [r["form"] for r in result]
        assert forms == ["SC 13D", "SC 13G/A", "SCHEDULE 13D/A", "SCHEDULE 13G"]

    # --- the poison-cache contract ------------------------------------------------------- #
    # A 403 (SEC rejecting the User-Agent) once cached "[]" for every subject, so the build
    # completed green over an empty graph. Only a 404 is a real answer; nothing else may be
    # cached, or a transient failure becomes permanent data loss.

    def test_404_caches_empty_result(self, tmp_path, monkeypatch):
        import urllib.error

        from secgraph.ingestion.ownership import edgar_client

        def not_found(url):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        monkeypatch.setattr(edgar_client, "_throttled_get", not_found)
        assert edgar_client.fetch_13dg_accessions("0000000002", tmp_path) == []
        # Cached, so a resume skips this subject rather than re-fetching forever.
        assert (tmp_path / "0000000002_index.json").read_text() == "[]"

    @pytest.mark.parametrize("status", [403, 429, 500, 503])
    def test_non_404_raises_and_does_not_cache(self, tmp_path, monkeypatch, status):
        import urllib.error

        from secgraph.ingestion.ownership import edgar_client

        def blocked(url):
            raise urllib.error.HTTPError(url, status, "blocked", {}, None)

        monkeypatch.setattr(edgar_client, "_throttled_get", blocked)
        with pytest.raises(edgar_client.SubmissionsFetchError):
            edgar_client.fetch_13dg_accessions("0000000003", tmp_path)
        assert not (tmp_path / "0000000003_index.json").exists()

    def test_transient_error_does_not_cache(self, tmp_path, monkeypatch):
        from secgraph.ingestion.ownership import edgar_client

        def timeout(url):
            raise TimeoutError("read timed out")

        monkeypatch.setattr(edgar_client, "_throttled_get", timeout)
        with pytest.raises(edgar_client.SubmissionsFetchError):
            edgar_client.fetch_13dg_accessions("0000000004", tmp_path)
        assert not (tmp_path / "0000000004_index.json").exists()

    @pytest.mark.parametrize(
        "ua,is_placeholder",
        [
            ("public-company-graph research contact@example.com", True),
            ("Example Corp research team@EXAMPLE.COM", True),
            ("Alex Woolford alex@realdomain.io", False),
        ],
    )
    def test_placeholder_user_agent_detection(self, ua, is_placeholder):
        from secgraph.ingestion.ownership import edgar_client

        assert edgar_client.is_placeholder_user_agent(ua) is is_placeholder

    @pytest.mark.parametrize(
        "form,expected",
        [
            ("SC 13D", True),
            ("SC 13G/A", True),
            ("SCHEDULE 13D", True),  # post-2024 regime
            ("SCHEDULE 13G/A", True),
            ("schedule 13d", True),  # case-insensitive
            ("SC 13E3", False),  # going-private, not beneficial ownership
            ("10-K", False),
            ("", False),
        ],
    )
    def test_is_schedule_13dg_both_regimes(self, form, expected):
        from secgraph.ingestion.ownership import edgar_client

        assert edgar_client.is_schedule_13dg(form) is expected


# --------------------------------------------------------------------------- #
# institutional: 13F HOLDS aggregation — the temporal (per-quarter) contract
# --------------------------------------------------------------------------- #
class TestInstitutionalHoldings:
    def _zip(self, path: Path, subs: list[str], holdings: list[str]) -> None:
        _make_zip(
            path,
            {
                "SUBMISSION": [
                    "ACCESSION_NUMBER\tCIK\tFILING_DATE\tPERIODOFREPORT",
                    *subs,
                ],
                "COVERPAGE": [
                    "ACCESSION_NUMBER\tFILINGMANAGER_NAME",
                    "acc-q1\tCITADEL ADVISORS LLC",
                    "acc-q2\tCITADEL ADVISORS LLC",
                ],
                "INFOTABLE": ["ACCESSION_NUMBER\tCUSIP\tVALUE\tSSHPRNAMT", *holdings],
            },
        )

    def test_report_period_kept_as_distinct_edges(self, tmp_path):
        """Same manager+issuer across two quarters → two edges, not one."""
        zpath = tmp_path / "13f.zip"
        self._zip(
            zpath,
            subs=[
                "acc-q1\t0001423053\t15-FEB-2025\t31-DEC-2024",
                "acc-q2\t0001423053\t15-MAY-2025\t31-MAR-2025",
            ],
            holdings=[
                "acc-q1\t037833100\t1000\t500",
                "acc-q2\t037833100\t1800\t700",
            ],
        )
        crosswalk = {"037833100": "0000320193"}
        rows = institutional.build_holdings_rows([zpath], crosswalk, universe_ciks={"0000320193"})
        periods = sorted(r["report_period"] for r in rows)
        assert periods == ["2024-12-31", "2025-03-31"]  # two distinct quarters
        by_period = {r["report_period"]: r for r in rows}
        assert by_period["2024-12-31"]["value_usd"] == 1000
        assert by_period["2025-03-31"]["value_usd"] == 1800  # accumulation is visible

    def test_multiple_cusip_lines_summed_within_a_quarter(self, tmp_path):
        """A manager's split lines for one issuer in one quarter aggregate to one edge."""
        zpath = tmp_path / "13f.zip"
        self._zip(
            zpath,
            subs=["acc-q1\t0001423053\t15-FEB-2025\t31-DEC-2024"],
            holdings=[
                "acc-q1\t037833100\t600\t300",
                "acc-q1\t037833100\t400\t200",
            ],
        )
        rows = institutional.build_holdings_rows(
            [zpath], {"037833100": "0000320193"}, universe_ciks={"0000320193"}
        )
        assert len(rows) == 1
        assert rows[0]["value_usd"] == 1000
        assert rows[0]["shares"] == 500

    def test_out_of_universe_holdings_dropped(self, tmp_path):
        zpath = tmp_path / "13f.zip"
        self._zip(
            zpath,
            subs=["acc-q1\t0001423053\t15-FEB-2025\t31-DEC-2024"],
            holdings=["acc-q1\t594918104\t900\t100"],  # CUSIP not in crosswalk
        )
        rows = institutional.build_holdings_rows(
            [zpath], {"037833100": "0000320193"}, universe_ciks={"0000320193"}
        )
        assert rows == []
