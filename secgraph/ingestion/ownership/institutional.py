"""
Institutional-holdings loader — SEC Form 13F (the HOLDS edge; Phase 2b).

Reads the quarterly 13F bulk tables and produces ``InstitutionalManager {cik}``
nodes plus ``HOLDS`` edges to in-universe issuer ``Company`` nodes:

- ``SUBMISSION``  → accession → filer CIK + period + filing date
- ``COVERPAGE``   → accession → filer (manager) name
- ``INFOTABLE``   → accession → per-holding CUSIP + value + shares

CUSIP-9 is mapped to CIK via the key-based crosswalk (:mod:`.cusip_crosswalk`,
CUSIP→FTD symbol→ticker→CIK); holdings whose CUSIP is unmatched or out-of-universe
are dropped at parse time (the loader ``MATCH``es Company, so they cannot leak
in). Multiple holdings of the same issuer by one manager within a batch are
aggregated (summed value/shares, latest period kept) before the MERGE.
"""

from __future__ import annotations

import logging
from pathlib import Path

from secgraph.core.config.constants import BATCH_SIZE_LARGE
from secgraph.ingestion.ownership.bulk_datasets import (
    iter_tsv_rows,
    normalize_cik,
    parse_sec_date,
)
from secgraph.ingestion.ownership.cusip_crosswalk import cusip9, load_crosswalk
from secgraph.neo4j.utils import clean_properties_batch

logger = logging.getLogger(__name__)

_HOLDS_QUERY = """
    UNWIND $batch AS row
    MATCH (c:Company {cik: row.company_cik})
    MERGE (m:InstitutionalManager {cik: row.manager_cik})
      ON CREATE SET m.name = row.manager_name, m.loaded_at = datetime()
    SET m.name = coalesce(m.name, row.manager_name)
    MERGE (m)-[r:HOLDS {report_period: date(row.report_period)}]->(c)
    SET r.source = 'sec_form13f',
        r.filing_date = date(row.filing_date),
        // value_usd / shares are SHARE OWNERSHIP only. Option notional is reported separately
        // because it is not ownership: a $14.8B put on a utility is a position, not a stake.
        r.value_usd = row.value_usd,
        r.shares = row.shares,
        r.call_notional_usd = row.call_notional_usd,
        r.put_notional_usd = row.put_notional_usd,
        r.cusip = row.cusip,
        r.accession_number = row.accession_number,
        r.loaded_at = datetime()
    RETURN count(r) AS n
"""


def _to_int(value: str | None) -> int | None:
    if not value:
        return None
    value = value.strip().replace(",", "")
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def build_holdings_rows(
    zip_paths: list[Path],
    crosswalk: dict[str, str],
    universe_ciks: set[str],
    log: logging.Logger | None = None,
) -> list[dict]:
    """Join SUBMISSION+COVERPAGE+INFOTABLE → aggregated HOLDS edge rows.

    Aggregates on (manager_cik, company_cik, report_period): sums value/shares
    across a manager's multiple CUSIP lines for the same issuer *within one
    quarter*, but keeps each quarter as a distinct row so HOLDS is a
    quarter-over-quarter position time series, not a latest-only snapshot.
    """
    log = log or logger

    # accession → filer metadata
    submissions: dict[str, dict] = {}
    for zip_path in zip_paths:
        for row in iter_tsv_rows(zip_path, "SUBMISSION"):
            accession = (row.get("ACCESSION_NUMBER") or "").strip()
            manager_cik = normalize_cik(row.get("CIK"))
            filing_date = parse_sec_date(row.get("FILING_DATE"))
            report_period = parse_sec_date(row.get("PERIODOFREPORT"))
            if not accession or manager_cik is None or filing_date is None:
                continue
            submissions[accession] = {
                "manager_cik": manager_cik,
                "filing_date": filing_date,
                "report_period": report_period or filing_date,
            }
    for zip_path in zip_paths:
        for row in iter_tsv_rows(zip_path, "COVERPAGE"):
            accession = (row.get("ACCESSION_NUMBER") or "").strip()
            if accession in submissions:
                submissions[accession]["manager_name"] = (
                    row.get("FILINGMANAGER_NAME") or ""
                ).strip()
    log.info(f"  indexed {len(submissions):,} 13F submissions")

    # (manager_cik, company_cik, report_period) → aggregated holding.
    # report_period is part of the key so each quarter is its own edge; within a
    # quarter, a manager's multiple CUSIP lines for one issuer are summed.
    agg: dict[tuple[str, str, str], dict] = {}
    holding_rows = 0
    for zip_path in zip_paths:
        for row in iter_tsv_rows(zip_path, "INFOTABLE"):
            accession = (row.get("ACCESSION_NUMBER") or "").strip()
            sub = submissions.get(accession)
            if sub is None:
                continue
            cusip = cusip9(row.get("CUSIP", ""))
            company_cik = crosswalk.get(cusip) if cusip else None
            if company_cik is None or company_cik not in universe_ciks:
                continue
            holding_rows += 1
            period = sub["report_period"]
            key = (sub["manager_cik"], company_cik, period)
            value = _to_int(row.get("VALUE")) or 0
            shares = _to_int(row.get("SSHPRNAMT")) or 0
            # PUTCALL splits share ownership from option notional. Reading VALUE without it
            # counted puts and calls as stock: Belvedere Trading appeared as the largest holder
            # of Eversource at $18.68B while owning 67 actual shares (the rest was a $3.9B call
            # and a $14.8B put). ~7% of all reported 13F dollars are options, and because they
            # concentrate in market-maker books they distort specific issuers badly.
            #
            # Options are kept, not dropped — a put is a true reported fact, and truth-in-
            # inclusion says label rather than delete. They just are not ownership, so they get
            # their own properties and stay out of value_usd/shares.
            putcall = (row.get("PUTCALL") or "").strip().upper()
            existing = agg.get(key)
            if existing is None:
                existing = agg[key] = {
                    "manager_cik": sub["manager_cik"],
                    "manager_name": sub.get("manager_name", ""),
                    "company_cik": company_cik,
                    "report_period": period,
                    "filing_date": sub["filing_date"],
                    "value_usd": 0,
                    "shares": 0,
                    "call_notional_usd": 0,
                    "put_notional_usd": 0,
                    "cusip": cusip,
                    "accession_number": accession,
                }
            if putcall == "CALL":
                existing["call_notional_usd"] += value
            elif putcall == "PUT":
                existing["put_notional_usd"] += value
            else:
                existing["value_usd"] += value
                existing["shares"] += shares
            # A later amendment for the same period supersedes the filing date.
            if sub["filing_date"] > existing["filing_date"]:
                existing["filing_date"] = sub["filing_date"]
                existing["accession_number"] = accession
    log.info(
        f"  {holding_rows:,} in-universe holdings → {len(agg):,} "
        f"(manager, company, quarter) HOLDS edges"
    )
    return list(agg.values())


def _clear_holds(session, batch_size: int, log: logging.Logger) -> int:
    """Delete all HOLDS edges (batched) — for a clean rebuild after a crosswalk change.

    HOLDS MERGEs are additive, so edges written under an earlier (partly wrong)
    crosswalk would survive a plain reload. Clearing first guarantees the graph
    reflects only the current crosswalk. Managers are left in place; any left with
    no HOLDS after the reload are pruned by the caller.
    """
    total = 0
    while True:
        n = session.run(
            "MATCH (:InstitutionalManager)-[r:HOLDS]->() "
            "WITH r LIMIT $limit DELETE r RETURN count(r) AS n",
            limit=batch_size,
        ).single()["n"]
        total += n
        if n == 0:
            break
        if total % (batch_size * 20) == 0:
            log.info(f"  cleared {total:,} HOLDS edges...")
    return total


def load_institutional_holdings(
    driver,
    zip_paths: list[Path],
    universe_ciks: set[str] | None = None,
    crosswalk: dict[str, str] | None = None,
    database: str | None = None,
    batch_size: int = BATCH_SIZE_LARGE,
    replace: bool = False,
    execute: bool = False,
    logger_instance: logging.Logger | None = None,
) -> dict:
    """Load InstitutionalManager nodes + HOLDS edges from 13F bulk TSVs.

    Args:
        driver: Neo4j driver
        zip_paths: Cached quarterly Form-13F zips
        universe_ciks: In-scope issuer CIKs; if None, read from Company nodes
        crosswalk: CUSIP-9→CIK map; if None, loaded from disk (build first)
        database: Target Neo4j database
        batch_size: Edge MERGE batch size
        replace: If True, delete all existing HOLDS edges before loading (needed
            after a crosswalk change so stale/misattributed edges don't survive)
        execute: If False, only report the plan
        logger_instance: Optional logger

    Returns:
        Dict of statistics.
    """
    log = logger_instance or logger

    if not zip_paths:
        log.error("No Form-13F zips provided; run download_ownership_data.py --form 13f first")
        return {"error": "no_input"}

    if crosswalk is None:
        crosswalk = load_crosswalk()
    if not crosswalk:
        log.error("Empty CUSIP crosswalk; run build_cusip_crosswalk.py --execute first")
        return {"error": "no_crosswalk"}
    log.info(f"CUSIP crosswalk: {len(crosswalk):,} entries")

    if universe_ciks is None:
        with driver.session(database=database) as session:
            universe_ciks = {r["cik"] for r in session.run("MATCH (c:Company) RETURN c.cik AS cik")}
    log.info(f"Universe: {len(universe_ciks):,} companies")

    log.info(f"Parsing {len(zip_paths)} quarter(s) of Form 13F...")
    rows = build_holdings_rows(zip_paths, crosswalk, universe_ciks, log=log)
    distinct_managers = len({r["manager_cik"] for r in rows})

    if not execute:
        log.info("")
        if replace:
            log.info("DRY RUN — would DELETE all existing HOLDS edges, then MERGE afresh:")
        else:
            log.info("DRY RUN — would MERGE InstitutionalManager nodes + HOLDS edges:")
        log.info(f"  managers: {distinct_managers:,}, HOLDS edges: {len(rows):,}")
        log.info("Run with --execute to load.")
        return {"dry_run": True, "managers": distinct_managers, "holds_edges": len(rows)}

    with driver.session(database=database) as session:
        if replace:
            log.info("Clearing existing HOLDS edges (--replace)...")
            cleared = _clear_holds(session, batch_size, log)
            log.info(f"  cleared {cleared:,} HOLDS edges")

        written = 0
        for start in range(0, len(rows), batch_size):
            batch = clean_properties_batch(rows[start : start + batch_size])
            written += session.run(_HOLDS_QUERY, batch=batch).single()["n"]

        pruned = 0
        if replace:
            pruned = session.run(
                "MATCH (m:InstitutionalManager) WHERE NOT (m)-[:HOLDS]->() "
                "DETACH DELETE m RETURN count(m) AS n"
            ).single()["n"]
            if pruned:
                log.info(f"  pruned {pruned:,} managers left with no holdings")

    log.info(f"✓ Loaded {distinct_managers:,} managers, {written:,} HOLDS edges")
    return {
        "managers": distinct_managers,
        "holds_edges": written,
        "quarters": len(zip_paths),
        "pruned_managers": pruned,
    }
