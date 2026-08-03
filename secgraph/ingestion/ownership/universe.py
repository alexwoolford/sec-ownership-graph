"""
Company universe loader — the in-scope set of SEC filers with a ticker.

Source: ``https://www.sec.gov/files/company_tickers.json`` (~10.4k ticker rows,
~8.0k distinct CIKs; dual-class share a CIK). Loaded live at build time so a
re-run picks up new/delisted filers.

Creates ``Company {cik}`` nodes (the MERGE identity for the whole ownership
graph). Ownership loaders only ever ``MATCH`` Company, so the universe must load
first — that bounds every downstream edge to an in-scope issuer.

Optional SIC/sector enrichment pulls ``data.sec.gov/submissions/CIK*.json`` per
CIK (cached on disk), bucketed via :mod:`.sic_sectors`. It is not required for
the Phase-1 density gate, so it is opt-in.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from pathlib import Path

from secgraph.core.config.constants import BATCH_SIZE_SMALL, SEC_EDGAR_RATE_LIMIT
from secgraph.core.rate_limiting import get_rate_limiter
from secgraph.ingestion.ownership.bulk_datasets import (
    get_ownership_data_dir,
    normalize_cik,
)
from secgraph.ingestion.ownership.sic_sectors import sector_for_sic
from secgraph.neo4j.utils import clean_properties_batch

logger = logging.getLogger(__name__)

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_SEC_USER_AGENT = "public-company-graph research contact@example.com"


def _user_agent() -> str:
    import os

    return os.environ.get("SEC_USER_AGENT", _SEC_USER_AGENT)


def _http_get_json(url: str) -> dict:
    limiter = get_rate_limiter("sec_edgar", requests_per_second=SEC_EDGAR_RATE_LIMIT)
    if limiter is not None:
        limiter()
    req = urllib.request.Request(url, headers={"User-Agent": _user_agent()})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - fixed https host
        return json.load(resp)


def fetch_company_universe() -> list[dict[str, str]]:
    """Fetch company_tickers.json → deduplicated list of {cik, name, ticker}.

    Dual-class tickers share a CIK; the first ticker seen per CIK wins (the file
    is roughly ordered by size, so this keeps the most prominent listing).
    """
    raw = _http_get_json(COMPANY_TICKERS_URL)
    seen: dict[str, dict[str, str]] = {}
    for row in raw.values():
        cik = normalize_cik(str(row.get("cik_str", "")))
        if cik is None or cik in seen:
            continue
        seen[cik] = {
            "cik": cik,
            "name": (row.get("title") or "").strip(),
            "ticker": (row.get("ticker") or "").strip(),
        }
    return list(seen.values())


def _fetch_sic_metadata(cik: str, cache_dir: Path, refresh: bool) -> dict[str, str]:
    """Fetch (cached) SIC/state metadata for one CIK from the submissions API."""
    cache_file = cache_dir / f"{cik}.json"
    if cache_file.exists() and not refresh:
        try:
            data = json.loads(cache_file.read_text())
        except (ValueError, OSError):
            data = {}
    else:
        try:
            sub = _http_get_json(_SUBMISSIONS_URL.format(cik=cik))
            data = {
                "sic_code": str(sub.get("sic", "") or "").strip(),
                "state_of_incorp": str(sub.get("stateOfIncorporation", "") or "").strip(),
            }
            cache_file.write_text(json.dumps(data))
        except Exception as exc:  # noqa: BLE001 - one bad CIK must not abort the run
            logger.debug(f"SIC fetch failed for {cik}: {exc}")
            data = {}
    return data


def enrich_universe_with_sic(
    companies: list[dict[str, str]],
    refresh: bool = False,
    log: logging.Logger | None = None,
) -> None:
    """In-place add sic_code/sector/state_of_incorp to each company (cached).

    Per-CIK EDGAR submissions crawl (~8k calls, throttled). Failures leave the
    company without the enrichment fields rather than aborting.
    """
    log = log or logger
    cache_dir = get_ownership_data_dir("submissions")
    t0 = time.time()
    for i, company in enumerate(companies):
        meta = _fetch_sic_metadata(company["cik"], cache_dir, refresh)
        sic = meta.get("sic_code") or ""
        if sic:
            company["sic_code"] = sic
            sector = sector_for_sic(sic)
            if sector:
                company["sector"] = sector
        state = meta.get("state_of_incorp") or ""
        if state:
            company["state_of_incorp"] = state
        if (i + 1) % 500 == 0:
            log.info(f"  SIC enrichment {i + 1}/{len(companies)} ({time.time() - t0:.0f}s)")


def load_company_universe(
    driver,
    database: str | None = None,
    enrich_sic: bool = False,
    refresh: bool = False,
    batch_size: int = BATCH_SIZE_SMALL,
    execute: bool = False,
    logger_instance: logging.Logger | None = None,
) -> dict:
    """Load the Company universe from company_tickers.json.

    Args:
        driver: Neo4j driver
        database: Target Neo4j database
        enrich_sic: If True, crawl the submissions API for SIC/sector/state
        refresh: If True, ignore any cached submissions metadata
        batch_size: Node MERGE batch size
        execute: If False, only report the plan
        logger_instance: Optional logger

    Returns:
        Dict of statistics.
    """
    log = logger_instance or logger

    log.info("Fetching company universe (company_tickers.json)...")
    companies = fetch_company_universe()
    log.info(f"  {len(companies):,} distinct CIKs with a ticker")

    if enrich_sic:
        log.info("Enriching with SIC/sector/state (submissions API, cached)...")
        enrich_universe_with_sic(companies, refresh=refresh, log=log)
        with_sector = sum(1 for c in companies if c.get("sector"))
        log.info(f"  sector assigned for {with_sector:,}/{len(companies):,} companies")

    if not execute:
        log.info("")
        log.info("DRY RUN — would MERGE Company nodes:")
        log.info(f"  count: {len(companies):,}")
        log.info(f"  enrich_sic: {enrich_sic}")
        log.info("Run with --execute to load.")
        return {"dry_run": True, "companies": len(companies)}

    log.info(f"Loading {len(companies):,} Company nodes...")
    written = 0
    with driver.session(database=database) as session:
        for start in range(0, len(companies), batch_size):
            batch = clean_properties_batch(companies[start : start + batch_size])
            result = session.run(
                """
                UNWIND $batch AS row
                MERGE (c:Company {cik: row.cik})
                SET c.name = row.name,
                    c.ticker = row.ticker,
                    c.sic_code = coalesce(row.sic_code, c.sic_code),
                    c.sector = coalesce(row.sector, c.sector),
                    c.state_of_incorp = coalesce(row.state_of_incorp, c.state_of_incorp),
                    c.loaded_at = datetime()
                RETURN count(c) AS n
                """,
                batch=batch,
            )
            written += result.single()["n"]

    log.info(f"✓ Loaded {written:,} Company nodes")
    return {
        "companies": len(companies),
        "nodes_written": written,
        "enrich_sic": enrich_sic,
    }
