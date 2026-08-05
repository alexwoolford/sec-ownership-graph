"""
Load ``Company.total_assets_usd`` from the SEC Financial Statement Data Sets (FSDS).

**Why this exists.** ``institutional_value_usd`` (see :mod:`materiality`) made structural results
rankable, but it measures **free float** — and float is systematically smallest at exactly the
issuers this graph is about. A majority holder means less stock available to 13F filers, so the
one property used to filter for materiality understates concentrated ownership by construction.

Measured on the built graph, of the 825 issuers carrying a ``CONTROLS`` edge, **462 look
sub-$100M and 294 cannot be sized at all** by the 13F proxy. The worked example:

    ECHO  EchoStar   $60.9B total assets   51.8% controlled   13F float: NONE

A $60.9B balance sheet, majority-controlled, and invisible to every size-filtered query. That is
precisely the exposure a credit or compliance desk is asking about, and the reason a reviewer
called the graph "overwhelmed by micro-cap and nano-cap noise". More assets vs float, same graph:

    TMUS  T-Mobile        $208.0B assets  vs  $92.6B float   (74.3% Deutsche Telekom)
    IBKR  Int'l Brokers   $150.1B assets  vs  $25.8B float   (78.2% IBG Holdings)
    ET    Energy Transfer $125.4B assets  vs  $20.2B float   (50.0% Warren Kelcy L)

**Why assets and not revenue.** ``Assets`` is one XBRL tag, point-in-time, needing no period
normalization — 5,020 CIKs from a single quarter. Revenue needs a four-tag fallback chain *and*
annual-vs-quarterly normalization and still resolves fewer issuers (3,833), so it buys a worse
answer for materially more parsing risk. Assets answers the exposure question; revenue can be
added later behind the same staging step if anyone actually needs operating scale.

**What the number is, and is not.** Total consolidated assets as filed. Three limits:

- **~42% of the universe has no figure.** ETFs, funds and many foreign filers file no 10-K/10-Q,
  so a null means "not reported here", never zero — it must not be coerced, or unsized issuers
  sort as *smallest* rather than *unknown*.
- **It is not comparable across sectors.** A bank's assets *are* its balance sheet: JPMorgan's
  $4.0T is not "bigger than" a $200B industrial in any economically meaningful sense. Useful as a
  threshold within a peer set, misleading as a cross-sector ranking.
- **It is a point-in-time balance, not market value.** It says nothing about equity value or
  leverage, and a highly-levered issuer looks large on this measure.

**Extraction rules, and why each is load-bearing.** From ``num.txt``, keep only:
``tag='Assets'``, ``uom='USD'``, ``qtrs='0'`` (a balance is an instant, not a duration — flow
facts carry qtrs 1/4), and **empty ``segments`` and ``coreg``**. That last filter is the one that
silently corrupts the figure if omitted: segmented rows report a single business line, and a
co-registrant row reports a subsidiary, so including either would mix part-of-company values in
with consolidated ones and pick whichever happened to sort last.

Reads staged zips only (no network); writes only to the target database (intended: ``secgraph``);
idempotent and dry-run-by-default (``--execute`` to write, ``--replace`` to clear stale first).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from secgraph.core.config.constants import BATCH_SIZE_LARGE
from secgraph.ingestion.ownership.bulk_datasets import (
    iter_tsv_rows,
    normalize_cik,
    parse_sec_date,
)

logger = logging.getLogger(__name__)

# FSDS ships plain-text tab-separated tables, not the .tsv the Form 345/13F zips use.
_TABLE_EXTENSION = "txt"

# The balance-sheet fact we want. One tag, deliberately — see the module docstring on why the
# revenue tag-fallback chain was not worth its parsing risk.
ASSETS_TAG = "Assets"

# Only periodic reports carry a consolidated balance sheet worth keying on. 10-K/10-Q covers the
# universe; 8-K and S-1 exhibits would introduce duplicate and pre-IPO figures.
_ACCEPTED_FORMS = frozenset({"10-K", "10-Q", "10-K/A", "10-Q/A", "10-KT", "10-QT"})


# --------------------------------------------------------------------------- #
# Pure helpers — the unit-test surface. No Neo4j, no network.
# --------------------------------------------------------------------------- #
def batches(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    """Split rows into write batches of at most ``size`` (last batch may be short)."""
    if size <= 0:
        raise ValueError("batch size must be positive")
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def is_consolidated_assets_row(row: dict[str, str]) -> bool:
    """True if a ``num.txt`` row is a consolidated total-assets fact in USD.

    Every clause here excludes a specific way the figure goes wrong:

    - ``tag``/``uom`` — the fact we asked for, in dollars.
    - ``qtrs == "0"`` — a balance sheet is an instant. Flow facts (revenue, cash flow) carry
      1 or 4 and would be a category error here.
    - empty ``segments`` — a segmented row is one business line, not the company.
    - empty ``coreg`` — a co-registrant row is a subsidiary filing under the parent's accession.

    Dropping either of the last two mixes part-of-company values into a consolidated series,
    which does not error: it just silently returns whichever row sorted last.
    """
    if row.get("tag") != ASSETS_TAG or row.get("uom") != "USD":
        return False
    if (row.get("qtrs") or "").strip() != "0":
        return False
    return not (row.get("segments") or "").strip() and not (row.get("coreg") or "").strip()


def keep_latest_by_ddate(
    current: dict[str, dict[str, Any]],
    cik: str,
    ddate: str,
    value: float,
    accession: str = "",
) -> None:
    """Merge one fact into a CIK→latest-fact map, keeping the newest ``ddate``.

    Mutates ``current`` in place. The sort key is ``(ddate, accession)`` and the comparison is
    strictly greater-than, so the outcome is **independent of row and file order**.

    The ``accession`` tiebreak is not decoration. Measured on ``2026q1``: of 10,606 (cik, ddate)
    pairs, **one** carries two different values from two accessions — CIK 0001342916 at
    ``20251031`` reports 1,731,020 under ``…-26-000008`` and 1,731,038 under ``…-26-000016``
    (an original and a later amendment). Without a total order, whichever row streamed last
    would win, so two builds over identical inputs could publish different assets figures. That
    is the same defect class as the ``via_ciks[0]`` bug in the reproducibility contract, and the
    worst failure mode here because it is silent — it publishes a wrong number rather than
    erroring. Highest accession wins, which is the later filing.

    ``ddate`` is compared as a ``YYYYMMDD`` string, where lexical and chronological order
    coincide; the ISO conversion happens once at write time.
    """
    existing = current.get(cik)
    if existing is None or (ddate, accession) > (existing["ddate"], existing.get("accession", "")):
        current[cik] = {
            "cik": cik,
            "ddate": ddate,
            "total_assets_usd": value,
            "accession": accession,
        }


def parse_assets_from_zip(zip_path: Path) -> dict[str, dict[str, Any]]:
    """Extract CIK → newest consolidated total assets from one staged FSDS zip.

    Two streaming passes: ``sub.txt`` (small) builds the accession→CIK map for periodic reports,
    then ``num.txt`` is filtered against it. ``num.txt`` is ~530 MB uncompressed, so it is
    iterated row-by-row from inside the archive and never materialized.
    """
    accession_to_cik: dict[str, str] = {}
    for row in iter_tsv_rows(zip_path, "sub", extension=_TABLE_EXTENSION):
        if (row.get("form") or "").strip().upper() not in _ACCEPTED_FORMS:
            continue
        cik = normalize_cik(row.get("cik"))
        adsh = (row.get("adsh") or "").strip()
        if cik and adsh:
            accession_to_cik[adsh] = cik

    facts: dict[str, dict[str, Any]] = {}
    for row in iter_tsv_rows(zip_path, "num", extension=_TABLE_EXTENSION):
        if not is_consolidated_assets_row(row):
            continue
        cik = accession_to_cik.get((row.get("adsh") or "").strip())
        if cik is None:
            continue
        ddate = (row.get("ddate") or "").strip()
        if not ddate:
            continue
        try:
            value = float(row["value"])
        except (KeyError, TypeError, ValueError):
            # A blank or non-numeric value is a real occurrence in FSDS, not a parse failure.
            continue
        keep_latest_by_ddate(facts, cik, ddate, value, (row.get("adsh") or "").strip())

    return facts


def merge_quarters(per_quarter: list[dict[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Merge per-zip fact maps into one row list, newest ``ddate`` winning per CIK.

    Coverage saturates with depth rather than scaling linearly (5,020 CIKs from one quarter,
    5,989 from three), because a company appears in whichever quarters it filed in. Merging is
    what makes an issuer that skipped a quarter still sizable.

    Returns rows ordered by CIK so a write batch — and any diff of the resulting figures — is
    deterministic.
    """
    merged: dict[str, dict[str, Any]] = {}
    for quarter_facts in per_quarter:
        for cik, fact in quarter_facts.items():
            keep_latest_by_ddate(
                merged,
                cik,
                fact["ddate"],
                fact["total_assets_usd"],
                fact.get("accession", ""),
            )
    return [merged[cik] for cik in sorted(merged)]


def summarize_assets(rows: list[dict[str, Any]], universe_count: int) -> dict[str, Any]:
    """Coverage + distribution stats over the parsed figures.

    Coverage is the honest headline: a measure that silently omits 42% of the universe would
    otherwise read as complete. Buckets are reported because the *shape* is the argument — that
    large controlled issuers exist at all is the point of the property.
    """
    values = sorted(
        (r["total_assets_usd"] for r in rows if r.get("total_assets_usd")), reverse=True
    )
    covered = len(values)
    return {
        "companies_with_assets": covered,
        "coverage_pct": round(covered / universe_count * 100, 1) if universe_count else 0.0,
        "max_usd": values[0] if values else None,
        "median_usd": values[covered // 2] if covered else None,
        "buckets": {
            "ge_100b": sum(1 for v in values if v >= 100e9),
            "ge_10b": sum(1 for v in values if v >= 10e9),
            "ge_1b": sum(1 for v in values if v >= 1e9),
            "ge_100m": sum(1 for v in values if v >= 100e6),
            "lt_100m": sum(1 for v in values if v < 100e6),
        },
    }


# --------------------------------------------------------------------------- #
# DB-bound write.
# --------------------------------------------------------------------------- #
# MATCH, not MERGE: an FSDS filer outside the tickered Company universe is expected (private
# debt issuers, funds), and creating a bare Company node for one would invent an in-universe
# issuer with no ticker that every other loader would then attach to.
_WRITE_QUERY = """
    UNWIND $batch AS row
    MATCH (c:Company {cik: row.cik})
    SET c.total_assets_usd = row.total_assets_usd,
        // The BALANCE-SHEET date (num.txt ddate), not the zip's submission quarter. 2026q1.zip
        // holds filings submitted in 2026 Q1 whose balance sheets are mostly 2025-09-30/12-31;
        // conflating the two is how a published figure ends up describing the wrong instant.
        c.total_assets_period = date(row.period),
        c.total_assets_accession = row.accession
    RETURN count(c) AS n
"""

# Clear before a --replace rebuild so an issuer that stops filing does not keep a stale figure.
# Absence is meaningful here ("not reported"), so a stale value is worse than none.
_CLEAR_QUERY = """
    MATCH (c:Company)
    WHERE c.total_assets_usd IS NOT NULL
    REMOVE c.total_assets_usd, c.total_assets_period, c.total_assets_accession
    RETURN count(c) AS n
"""

_UNIVERSE_QUERY = "MATCH (c:Company) RETURN count(c) AS n"


def load_company_financials(
    driver,
    zip_paths: list[Path],
    database: str | None = None,
    replace: bool = False,
    batch_size: int = BATCH_SIZE_LARGE,
    execute: bool = False,
    logger_instance: logging.Logger | None = None,
) -> dict[str, Any]:
    """Parse staged FSDS zips and write ``total_assets_usd``. Dry-run unless ``execute``.

    Args:
        driver: Neo4j driver.
        zip_paths: Staged FSDS zips, newest period first (from ``staged_zip_paths``). The caller
            resolves these so the as-of window is applied at *load* time, not just download.
        database: Target database (``secgraph``).
        replace: Clear existing figures before writing (full rebuild).
        batch_size: Write batch size.
        execute: If False, parse and report without writing.
        logger_instance: Optional logger.

    Returns:
        Summary dict: parsed row count, coverage stats, and ``written``/``cleared`` when executed.
    """
    log = logger_instance or logger

    if not zip_paths:
        raise ValueError(
            "no staged FSDS zips to load. Run: "
            "python scripts/download_ownership_data.py --form fsds --quarters 4"
        )

    per_quarter: list[dict[str, dict[str, Any]]] = []
    for path in zip_paths:
        facts = parse_assets_from_zip(path)
        log.info(f"  {path.name}: {len(facts):,} CIKs with consolidated total assets")
        per_quarter.append(facts)

    rows = merge_quarters(per_quarter)
    log.info(f"merged across {len(zip_paths)} quarter(s): {len(rows):,} distinct CIKs")

    with driver.session(database=database) as session:
        universe = session.run(_UNIVERSE_QUERY).single()["n"]
    summary: dict[str, Any] = {"parsed_ciks": len(rows), "universe": universe}
    summary.update(summarize_assets(rows, universe))

    buckets = summary["buckets"]
    log.info(
        f"  ≥$100B: {buckets['ge_100b']:,} · ≥$10B: {buckets['ge_10b']:,} · "
        f"≥$1B: {buckets['ge_1b']:,} · <$100M: {buckets['lt_100m']:,}"
    )

    if not execute:
        log.info("")
        log.info(f"DRY RUN — would set total_assets_usd on up to {len(rows):,} Company nodes")
        if replace:
            log.info("  (after clearing existing figures)")
        log.info("Run with --execute to write.")
        summary["dry_run"] = True
        return summary

    # ISO-convert once, at the boundary. parse_sec_date validates, so a malformed ddate is
    # dropped here rather than reaching date() inside Cypher, where the error would name a batch
    # rather than a row.
    write_rows = []
    skipped_dates = 0
    for row in rows:
        period = parse_sec_date(row["ddate"])
        if period is None:
            skipped_dates += 1
            continue
        write_rows.append(
            {
                "cik": row["cik"],
                "total_assets_usd": row["total_assets_usd"],
                "period": period,
                "accession": row.get("accession") or "",
            }
        )
    if skipped_dates:
        log.warning(f"  skipped {skipped_dates:,} row(s) with an unparseable ddate")
    summary["skipped_bad_dates"] = skipped_dates

    written = 0
    with driver.session(database=database) as session:
        if replace:
            summary["cleared"] = session.run(_CLEAR_QUERY).single()["n"]
            log.info(f"cleared {summary['cleared']:,} existing total_assets_usd values")
        for batch in batches(write_rows, batch_size):
            written += session.run(_WRITE_QUERY, batch=batch).single()["n"]

    summary["written"] = written
    summary["unmatched_ciks"] = len(write_rows) - written
    log.info(
        f"✓ Set total_assets_usd on {written:,} of {universe:,} companies "
        f"({100 * written / universe:.1f}% coverage)"
    )
    if summary["unmatched_ciks"]:
        log.info(
            f"  {summary['unmatched_ciks']:,} FSDS filers are outside the tickered universe "
            f"(expected: private debt issuers, funds, foreign filers)"
        )
    return summary
