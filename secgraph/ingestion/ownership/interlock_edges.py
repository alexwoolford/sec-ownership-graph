"""
Materialize the derived board-interlock edge ``(:Company)-[:SHARES_DIRECTOR]->(:Company)``.

Every interlock query today — the GDS betweenness/Louvain projections in :mod:`.interlock`,
every ``shortestPath`` in the graph-native proof — re-derives the 2-hop join
``(a)<-[:DIRECTOR_OF]-(person)-[:DIRECTOR_OF]->(b)`` at runtime. This module computes that
join once (under the two validated scrubs) and persists it as a first-class relationship, so
path traversal runs over ``(a)-[:SHARES_DIRECTOR*..N]-(b)`` directly and the GDS projection
reads a stored edge instead of an expensive live pattern.

Two scrubs, identical to the validated interlock proof (:mod:`.interlock`):

- **Human directors only** — funds/LPs/advisors file Form 3/4 as "directors" of the
  companies they control; an interlock must be a real *person* on both boards.
- **Operating companies only** — closed-end funds / muni-income trusts / ETF families share
  trustees across a fund complex, which is not a corporate interlock.

The edge is stored **undirected, once per pair** (``id(a) < id(b)``), matching how the GDS
projection already treats it. Traversal must therefore be direction-agnostic
(``-[:SHARES_DIRECTOR]-``, no arrow). Properties: ``director_count`` (how many distinct human
directors the two boards share), ``via_ciks`` (those directors' CIKs, capped), ``source``,
``computed_at``.

Writes only to the target database (intended: ``secgraph``); MERGE-idempotent and
dry-run-by-default (``--execute`` to write, ``--replace`` to rebuild).
"""

from __future__ import annotations

import logging
from typing import Any

from secgraph.core.config.constants import BATCH_SIZE_LARGE
from secgraph.ingestion.ownership.interlock import (
    _HUMAN_DIRECTOR_FILTER,
    _not_fund_company,
)

logger = logging.getLogger(__name__)

# Cap the director-CIK provenance list stored on each edge. A handful is enough to show
# *who* links the two boards; a pair sharing dozens of directors (rare) does not need every
# one persisted, and an unbounded list bloats the edge.
_MAX_VIA_CIKS = 10

_SOURCE = "form345_director_interlock"

# Read-only pattern that enumerates scrubbed interlock pairs with their shared-director count
# and provenance CIKs. id(a) < id(b) stores each unordered pair exactly once.
_COMPUTE_QUERY = f"""
    MATCH (a:Company)<-[:DIRECTOR_OF]-(i:Insider)-[:DIRECTOR_OF]->(b:Company)
    WHERE id(a) < id(b)
      AND {_HUMAN_DIRECTOR_FILTER}
      AND {_not_fund_company("a")} AND {_not_fund_company("b")}
      AND i.cik IS NOT NULL
    WITH a, b, collect(DISTINCT i.cik) AS via
    RETURN elementId(a) AS a_eid, elementId(b) AS b_eid,
           size(via) AS director_count, via[0..{_MAX_VIA_CIKS}] AS via_ciks
"""

_WRITE_QUERY = """
    UNWIND $batch AS row
    MATCH (a:Company) WHERE elementId(a) = row.a_eid
    MATCH (b:Company) WHERE elementId(b) = row.b_eid
    MERGE (a)-[r:SHARES_DIRECTOR]->(b)
    SET r.director_count = row.director_count,
        r.via_ciks = row.via_ciks,
        r.source = $source,
        r.computed_at = datetime()
    RETURN count(r) AS n
"""

_DELETE_QUERY = "MATCH (:Company)-[r:SHARES_DIRECTOR]->(:Company) DELETE r"


# --------------------------------------------------------------------------- #
# Pure helpers — the unit-test surface.
# --------------------------------------------------------------------------- #
def batches(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    """Split rows into write batches of at most ``size`` (last batch may be short)."""
    if size <= 0:
        raise ValueError("batch size must be positive")
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def summarize_pairs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Coverage stats over computed interlock pairs (for the plan/verify output)."""
    total = len(rows)
    multi = sum(1 for r in rows if (r.get("director_count") or 0) >= 2)
    max_shared = max((r.get("director_count") or 0 for r in rows), default=0)
    return {"pairs": total, "pairs_multi_director": multi, "max_shared_directors": max_shared}


# --------------------------------------------------------------------------- #
# DB-bound compute + write.
# --------------------------------------------------------------------------- #
def _compute_pairs(session) -> list[dict[str, Any]]:
    """Enumerate scrubbed interlock pairs (read-only)."""
    return session.run(_COMPUTE_QUERY).data()


def _write_pairs(session, rows: list[dict[str, Any]], batch_size: int) -> int:
    written = 0
    for batch in batches(rows, batch_size):
        written += session.run(_WRITE_QUERY, batch=batch, source=_SOURCE).single()["n"]
    return written


def materialize_interlock_edges(
    driver,
    database: str | None = None,
    replace: bool = False,
    batch_size: int = BATCH_SIZE_LARGE,
    execute: bool = False,
    logger_instance: logging.Logger | None = None,
) -> dict[str, Any]:
    """Compute and persist ``SHARES_DIRECTOR`` interlock edges. Dry-run unless ``execute``.

    Args:
        driver: Neo4j driver.
        database: Target database (``secgraph``).
        replace: Delete existing ``SHARES_DIRECTOR`` edges before writing (full rebuild).
        batch_size: Relationship-write batch size.
        execute: If False, compute the plan and report counts without writing.
        logger_instance: Optional logger.

    Returns:
        Summary dict (pairs / pairs_multi_director / max_shared_directors), plus
        ``written`` and ``deleted`` when executed.
    """
    log = logger_instance or logger

    with driver.session(database=database) as session:
        rows = _compute_pairs(session)
    summary = summarize_pairs(rows)
    log.info(
        f"interlock pairs (human directors, operating companies): {summary['pairs']:,} "
        f"({summary['pairs_multi_director']:,} share >=2 directors; "
        f"max {summary['max_shared_directors']} shared)"
    )

    if not execute:
        log.info("")
        log.info(
            f"DRY RUN — would MERGE {summary['pairs']:,} SHARES_DIRECTOR edges "
            f"(undirected, one per pair){' after deleting existing' if replace else ''}."
        )
        log.info("Run with --execute to write.")
        summary["dry_run"] = True
        return summary

    with driver.session(database=database) as session:
        if replace:
            summary["deleted"] = session.run(_DELETE_QUERY).consume().counters.relationships_deleted
            log.info(f"deleted {summary.get('deleted', 0):,} existing SHARES_DIRECTOR edges")
        summary["written"] = _write_pairs(session, rows, batch_size)

    log.info(f"✓ Materialized {summary['written']:,} SHARES_DIRECTOR interlock edges")
    return summary
