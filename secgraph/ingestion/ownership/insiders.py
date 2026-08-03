"""
Insider loader — SEC Form 3/4/5 reporting owners (the Phase-1 density gate).

Reads the quarterly bulk TSV tables (``SUBMISSION`` + ``REPORTINGOWNER``) staged
by :mod:`.bulk_datasets`, joins them on ``ACCESSION_NUMBER``, and produces
CIK-keyed ``Insider`` nodes plus role edges to the issuer ``Company``:

- ``Director``         → ``DIRECTOR_OF``
- ``Officer``          → ``OFFICER_OF``
- ``TenPercentOwner``  → ``TEN_PCT_OWNER_OF``

Entity resolution is *solved at the source*: every reporting owner carries a CIK,
so there is no free-text name to resolve (the failure mode that made the prior
narrative graph unusable). Edges only attach to issuers already in the universe
(loader ``MATCH``es Company), so out-of-universe filings are dropped for free.

MERGE keeps the most-recent ``filing_date`` per (insider, company, role) and
tracks ``first_seen``/``last_seen`` for point-in-time reasoning.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from secgraph.core.config.constants import BATCH_SIZE_LARGE
from secgraph.ingestion.ownership.bulk_datasets import (
    iter_tsv_rows,
    normalize_cik,
    parse_sec_date,
)
from secgraph.neo4j.utils import clean_properties_batch

logger = logging.getLogger(__name__)

# Reporting-owner relationship token → (relationship type, edge props builder key)
_ROLE_TO_REL = {
    "Director": "DIRECTOR_OF",
    "Officer": "OFFICER_OF",
    "TenPercentOwner": "TEN_PCT_OWNER_OF",
}

# Rel type → the Cypher MERGE+SET block. Kept explicit (no dynamic rel-type
# interpolation) so schema-consistency scanning sees each literal.
_MERGE_QUERIES = {
    "DIRECTOR_OF": """
        UNWIND $batch AS row
        MATCH (c:Company {cik: row.company_cik})
        MERGE (i:Insider {cik: row.owner_cik})
          ON CREATE SET i.name = row.owner_name, i.loaded_at = datetime()
        SET i.name = coalesce(i.name, row.owner_name)
        MERGE (i)-[r:DIRECTOR_OF]->(c)
          ON CREATE SET r.first_seen = date(row.filing_date), r.loaded_at = datetime()
        SET r.source = 'sec_form345',
            r.accession_number = row.accession_number,
            r.last_seen = CASE WHEN r.last_seen IS NULL OR date(row.filing_date) > r.last_seen
                               THEN date(row.filing_date) ELSE r.last_seen END,
            r.first_seen = CASE WHEN r.first_seen IS NULL OR date(row.filing_date) < r.first_seen
                                THEN date(row.filing_date) ELSE r.first_seen END,
            r.filing_date = CASE WHEN r.filing_date IS NULL OR date(row.filing_date) > r.filing_date
                                 THEN date(row.filing_date) ELSE r.filing_date END
        RETURN count(r) AS n
    """,
    "OFFICER_OF": """
        UNWIND $batch AS row
        MATCH (c:Company {cik: row.company_cik})
        MERGE (i:Insider {cik: row.owner_cik})
          ON CREATE SET i.name = row.owner_name, i.loaded_at = datetime()
        SET i.name = coalesce(i.name, row.owner_name)
        MERGE (i)-[r:OFFICER_OF]->(c)
          ON CREATE SET r.first_seen = date(row.filing_date), r.loaded_at = datetime()
        SET r.source = 'sec_form345',
            r.accession_number = row.accession_number,
            r.officer_title = row.officer_title,
            r.last_seen = CASE WHEN r.last_seen IS NULL OR date(row.filing_date) > r.last_seen
                               THEN date(row.filing_date) ELSE r.last_seen END,
            r.first_seen = CASE WHEN r.first_seen IS NULL OR date(row.filing_date) < r.first_seen
                                THEN date(row.filing_date) ELSE r.first_seen END,
            r.filing_date = CASE WHEN r.filing_date IS NULL OR date(row.filing_date) > r.filing_date
                                 THEN date(row.filing_date) ELSE r.filing_date END
        RETURN count(r) AS n
    """,
    "TEN_PCT_OWNER_OF": """
        UNWIND $batch AS row
        MATCH (c:Company {cik: row.company_cik})
        MERGE (i:Insider {cik: row.owner_cik})
          ON CREATE SET i.name = row.owner_name, i.loaded_at = datetime()
        SET i.name = coalesce(i.name, row.owner_name)
        MERGE (i)-[r:TEN_PCT_OWNER_OF]->(c)
          ON CREATE SET r.first_seen = date(row.filing_date), r.loaded_at = datetime()
        SET r.source = 'sec_form345',
            r.accession_number = row.accession_number,
            r.last_seen = CASE WHEN r.last_seen IS NULL OR date(row.filing_date) > r.last_seen
                               THEN date(row.filing_date) ELSE r.last_seen END,
            r.first_seen = CASE WHEN r.first_seen IS NULL OR date(row.filing_date) < r.first_seen
                                THEN date(row.filing_date) ELSE r.first_seen END,
            r.filing_date = CASE WHEN r.filing_date IS NULL OR date(row.filing_date) > r.filing_date
                                 THEN date(row.filing_date) ELSE r.filing_date END
        RETURN count(r) AS n
    """,
}


def parse_relationship(token: str) -> list[str]:
    """Split a ``RPTOWNER_RELATIONSHIP`` value into known relationship types.

    The bulk table encodes multiple roles comma-joined, e.g.
    ``"Director,Officer,TenPercentOwner"``. Unknown tokens (e.g. ``"Other"``)
    are ignored — they don't map to a schema relationship.

    Examples:
        >>> parse_relationship("Director,Officer")
        ['DIRECTOR_OF', 'OFFICER_OF']
        >>> parse_relationship("Other")
        []
    """
    rels = []
    for part in token.split(","):
        rel = _ROLE_TO_REL.get(part.strip())
        if rel and rel not in rels:
            rels.append(rel)
    return rels


def build_submission_index(zip_paths: Iterable[Path]) -> dict[str, dict[str, str]]:
    """Map ACCESSION_NUMBER → {company_cik, filing_date} across all quarters."""
    index: dict[str, dict[str, str]] = {}
    for zip_path in zip_paths:
        for row in iter_tsv_rows(zip_path, "SUBMISSION"):
            accession = (row.get("ACCESSION_NUMBER") or "").strip()
            company_cik = normalize_cik(row.get("ISSUERCIK"))
            filing_date = parse_sec_date(row.get("FILING_DATE"))
            if not accession or company_cik is None or filing_date is None:
                continue
            index[accession] = {"company_cik": company_cik, "filing_date": filing_date}
    return index


def build_edge_rows(
    zip_paths: list[Path],
    universe_ciks: set[str],
    log: logging.Logger | None = None,
) -> dict[str, list[dict[str, str]]]:
    """Join SUBMISSION + REPORTINGOWNER → per-relationship-type edge row lists.

    Only issuers in ``universe_ciks`` are kept. Returns
    ``{"DIRECTOR_OF": [row, ...], "OFFICER_OF": [...], "TEN_PCT_OWNER_OF": [...]}``.
    """
    log = log or logger
    submissions = build_submission_index(zip_paths)
    log.info(f"  indexed {len(submissions):,} submissions")

    edges: dict[str, list[dict[str, str]]] = {rel: [] for rel in _MERGE_QUERIES}
    owner_rows = 0
    for zip_path in zip_paths:
        for row in iter_tsv_rows(zip_path, "REPORTINGOWNER"):
            accession = (row.get("ACCESSION_NUMBER") or "").strip()
            sub = submissions.get(accession)
            if sub is None or sub["company_cik"] not in universe_ciks:
                continue
            owner_cik = normalize_cik(row.get("RPTOWNERCIK"))
            if owner_cik is None:
                continue
            owner_rows += 1
            rels = parse_relationship(row.get("RPTOWNER_RELATIONSHIP") or "")
            base = {
                "owner_cik": owner_cik,
                "owner_name": (row.get("RPTOWNERNAME") or "").strip(),
                "company_cik": sub["company_cik"],
                "filing_date": sub["filing_date"],
                "accession_number": accession,
            }
            for rel in rels:
                edge = dict(base)
                if rel == "OFFICER_OF":
                    edge["officer_title"] = (row.get("RPTOWNER_TITLE") or "").strip()
                edges[rel].append(edge)
    log.info(f"  {owner_rows:,} in-universe owner rows")
    return edges


def _write_edges(
    driver,
    rel_type: str,
    rows: list[dict[str, str]],
    database: str | None,
    batch_size: int,
    log: logging.Logger,
) -> int:
    query = _MERGE_QUERIES[rel_type]
    written = 0
    with driver.session(database=database) as session:
        for start in range(0, len(rows), batch_size):
            batch = clean_properties_batch(rows[start : start + batch_size])
            written += session.run(query, batch=batch).single()["n"]
    log.info(f"  {rel_type}: {written:,} edges written")
    return written


def load_insiders(
    driver,
    zip_paths: list[Path],
    universe_ciks: set[str] | None = None,
    database: str | None = None,
    batch_size: int = BATCH_SIZE_LARGE,
    execute: bool = False,
    logger_instance: logging.Logger | None = None,
) -> dict:
    """Load Insider nodes + role edges from Form 3/4/5 bulk TSVs.

    Args:
        driver: Neo4j driver
        zip_paths: Cached quarterly Form-345 zips (from download_ownership_data)
        universe_ciks: In-scope issuer CIKs; if None, read from Company nodes
        database: Target Neo4j database
        batch_size: Edge MERGE batch size
        execute: If False, only report the plan
        logger_instance: Optional logger

    Returns:
        Dict of statistics.
    """
    log = logger_instance or logger

    if not zip_paths:
        log.error("No Form-345 zips provided; run download_ownership_data.py --form 345 first")
        return {"error": "no_input"}

    if universe_ciks is None:
        with driver.session(database=database) as session:
            universe_ciks = {r["cik"] for r in session.run("MATCH (c:Company) RETURN c.cik AS cik")}
    log.info(f"Universe: {len(universe_ciks):,} companies")

    log.info(f"Parsing {len(zip_paths)} quarter(s) of Form 3/4/5...")
    edges = build_edge_rows(zip_paths, universe_ciks, log=log)
    totals = {rel: len(rows) for rel, rows in edges.items()}
    distinct_owners = len({row["owner_cik"] for rows in edges.values() for row in rows})
    log.info(f"  edge rows: {totals}, distinct insiders: {distinct_owners:,}")

    if not execute:
        log.info("")
        log.info("DRY RUN — would MERGE Insider nodes + role edges:")
        for rel, n in totals.items():
            log.info(f"  {rel}: {n:,}")
        log.info("Run with --execute to load.")
        return {"dry_run": True, "edge_rows": totals, "distinct_insiders": distinct_owners}

    written = {}
    for rel, rows in edges.items():
        written[rel] = _write_edges(driver, rel, rows, database, batch_size, log)

    log.info(f"✓ Loaded {distinct_owners:,} insiders, {sum(written.values()):,} role edges")
    return {
        "distinct_insiders": distinct_owners,
        "edges_written": written,
        "quarters": len(zip_paths),
    }
