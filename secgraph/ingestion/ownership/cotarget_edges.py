"""
Materialize the activist co-targeting edge ``(:BeneficialOwner)-[:CO_TARGETS]->(:BeneficialOwner)``.

**Why this exists.** The wolf-pack coalition is the third graph-native win, but like the control
chain it was being computed the wrong way: flat-``MATCH`` the 156 co-targeting pairs out of
Neo4j, then run connected-components in Python (``graph_native_proof.coalition_components``).
A ``SELECT``-then-loop is reproducible in any warehouse, so the graph wasn't earning the claim.
Persisting the pair as an edge lets the coalition come from a real graph algorithm — GDS WCC
over a projection, or variable-depth Cypher — where the *component* is what the database
computes rather than the client.

Two disciplines baked in at materialize time:

- **Threshold at write.** Two activists are linked only when they co-target
  ``>= min_shared_targets`` (default 2) of the same issuers via 13D. A single shared target is
  too weak to imply coordination, and putting the threshold in the materializer means no
  caller can accidentally query a 1-target graph.
- **Custodial scrub by labelling, not deletion.** Broker/custodian/index complexes
  (JPMorgan, RBC, State Street, …) co-target hundreds of issuers as a *custody* artifact and
  bridge otherwise-unrelated activists into one giant fake coalition. Per truth-in-inclusion we
  do **not** delete those edges — the co-targeting fact is true. Instead the hub node is flagged
  ``is_custodial = true`` and excluded at *projection* time, so the fact survives in the graph
  and the precision decision is explicit and reversible. Reuses
  :func:`~.graph_native_proof.is_custodial_hub` — the same predicate the validated proof used.

Stored **undirected, once per pair** (``a.cik < b.cik``), matching the ``SHARES_DIRECTOR``
convention; traversal must be direction-agnostic (``-[:CO_TARGETS]-``, no arrow).

Writes only to the target database (intended: ``secgraph``); MERGE-idempotent and
dry-run-by-default (``--execute`` to write, ``--replace`` to rebuild).
"""

from __future__ import annotations

import logging
from typing import Any

from secgraph.core.config.constants import BATCH_SIZE_LARGE
from secgraph.ingestion.ownership.graph_native_proof import is_custodial_hub

logger = logging.getLogger(__name__)

_SOURCE = "sec13d_cotargeting"

# Minimum shared 13D targets to link two activists. Same threshold as the validated proof.
DEFAULT_MIN_SHARED_TARGETS = 2

# Cap the stored provenance list of shared target CIKs. Enough to show *which* issuers link the
# pair without bloating the edge when a custodian shares dozens.
_MAX_SHARED_CIKS = 20

# Read-only pattern enumerating co-targeting pairs at threshold. a.cik < b.cik stores each
# unordered pair exactly once.
_COMPUTE_QUERY = f"""
    MATCH (a:BeneficialOwner)-[:BENEFICIAL_OWNER_OF {{filing_type:'13D'}}]->(c:Company)
          <-[:BENEFICIAL_OWNER_OF {{filing_type:'13D'}}]-(b:BeneficialOwner)
    WHERE a.cik < b.cik AND a.cik IS NOT NULL AND b.cik IS NOT NULL
    // Sort before collecting: shared is truncated to _MAX_SHARED_CIKS and surfaces as the
    // coalition's cited evidence, so an unordered collect() changed which accessions were
    // shown between runs on an identical graph.
    WITH a, b, c.cik AS cik ORDER BY cik
    WITH a, b, collect(DISTINCT cik) AS shared
    WHERE size(shared) >= $min_shared
    RETURN elementId(a) AS a_eid, elementId(b) AS b_eid,
           a.cik AS a_cik, a.name AS a_name,
           b.cik AS b_cik, b.name AS b_name,
           size(shared) AS shared_target_count,
           shared[0..{_MAX_SHARED_CIKS}] AS shared_target_ciks
"""

_WRITE_QUERY = """
    UNWIND $batch AS row
    MATCH (a:BeneficialOwner) WHERE elementId(a) = row.a_eid
    MATCH (b:BeneficialOwner) WHERE elementId(b) = row.b_eid
    MERGE (a)-[r:CO_TARGETS]->(b)
    SET r.shared_target_count = row.shared_target_count,
        r.shared_target_ciks = row.shared_target_ciks,
        r.source = $source,
        r.computed_at = datetime()
    RETURN count(r) AS n
"""

# Label (never delete) custodial hubs. Written on the node so a projection can exclude them
# while the underlying co-targeting edges remain queryable.
_LABEL_CUSTODIAL_QUERY = """
    UNWIND $ciks AS cik
    MATCH (b:BeneficialOwner {cik: cik})
    SET b.is_custodial = true
    RETURN count(b) AS n
"""

# Clear stale flags so a re-run with an updated hub list cannot leave a node wrongly flagged.
_CLEAR_CUSTODIAL_QUERY = """
    MATCH (b:BeneficialOwner) WHERE b.is_custodial IS NOT NULL
    REMOVE b.is_custodial
    RETURN count(b) AS n
"""

_DELETE_QUERY = "MATCH (:BeneficialOwner)-[r:CO_TARGETS]->(:BeneficialOwner) DELETE r"


# --------------------------------------------------------------------------- #
# Pure helpers — the unit-test surface.
# --------------------------------------------------------------------------- #
def batches(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    """Split rows into write batches of at most ``size`` (last batch may be short)."""
    if size <= 0:
        raise ValueError("batch size must be positive")
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def custodial_ciks(rows: list[dict[str, Any]]) -> set[str]:
    """CIKs of co-targeting participants whose name marks them a custodial/broker hub.

    These get flagged ``is_custodial`` on the node and excluded at projection time — the
    precision scrub that turned a 28-member artifact into the real 22-activist cluster.
    """
    found: set[str] = set()
    for row in rows:
        for cik_key, name_key in (("a_cik", "a_name"), ("b_cik", "b_name")):
            cik, name = row.get(cik_key), row.get(name_key)
            if cik and is_custodial_hub(name):
                found.add(cik)
    return found


def summarize_cotarget_pairs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Coverage stats over computed co-targeting pairs (for the plan/verify output)."""
    participants = {r[k] for r in rows for k in ("a_cik", "b_cik") if r.get(k)}
    counts = [r.get("shared_target_count") or 0 for r in rows]
    return {
        "cotarget_pairs": len(rows),
        "participants": len(participants),
        "max_shared_targets": max(counts, default=0),
        "custodial_hubs": len(custodial_ciks(rows)),
    }


# --------------------------------------------------------------------------- #
# DB-bound compute + write.
# --------------------------------------------------------------------------- #
def _compute_pairs(session, min_shared: int) -> list[dict[str, Any]]:
    """Enumerate co-targeting pairs at threshold (read-only)."""
    return session.run(_COMPUTE_QUERY, min_shared=min_shared).data()


def _write_pairs(session, rows: list[dict[str, Any]], batch_size: int) -> int:
    written = 0
    for batch in batches(rows, batch_size):
        written += session.run(_WRITE_QUERY, batch=batch, source=_SOURCE).single()["n"]
    return written


def materialize_cotarget_edges(
    driver,
    database: str | None = None,
    min_shared_targets: int = DEFAULT_MIN_SHARED_TARGETS,
    replace: bool = False,
    batch_size: int = BATCH_SIZE_LARGE,
    execute: bool = False,
    logger_instance: logging.Logger | None = None,
) -> dict[str, Any]:
    """Compute and persist ``CO_TARGETS`` edges + custodial labels. Dry-run unless ``execute``.

    Args:
        driver: Neo4j driver.
        database: Target database (``secgraph``).
        min_shared_targets: Minimum shared 13D issuers to link two activists.
        replace: Delete existing ``CO_TARGETS`` edges before writing (full rebuild).
        batch_size: Relationship-write batch size.
        execute: If False, compute the plan and report counts without writing.
        logger_instance: Optional logger.

    Returns:
        Summary dict (cotarget_pairs / participants / max_shared_targets / custodial_hubs),
        plus ``written``, ``deleted`` and ``custodial_labelled`` when executed.
    """
    log = logger_instance or logger

    with driver.session(database=database) as session:
        rows = _compute_pairs(session, min_shared_targets)
    summary = summarize_cotarget_pairs(rows)
    summary["min_shared_targets"] = min_shared_targets
    log.info(
        f"co-targeting pairs (>={min_shared_targets} shared 13D issuers): "
        f"{summary['cotarget_pairs']:,} · {summary['participants']:,} activists · "
        f"max {summary['max_shared_targets']} shared targets · "
        f"{summary['custodial_hubs']:,} custodial hubs to flag (labelled, not deleted)"
    )

    if not execute:
        log.info("")
        log.info(
            f"DRY RUN — would MERGE {summary['cotarget_pairs']:,} CO_TARGETS edges "
            f"(undirected, one per pair) and flag {summary['custodial_hubs']:,} "
            f"BeneficialOwner nodes is_custodial=true"
        )
        if replace:
            log.info("  (after deleting existing CO_TARGETS edges)")
        log.info("Run with --execute to write.")
        summary["dry_run"] = True
        return summary

    hubs = sorted(custodial_ciks(rows))
    with driver.session(database=database) as session:
        if replace:
            summary["deleted"] = session.run(_DELETE_QUERY).consume().counters.relationships_deleted
            log.info(f"deleted {summary.get('deleted', 0):,} existing CO_TARGETS edges")
        summary["written"] = _write_pairs(session, rows, batch_size)
        # Clear then re-apply so an updated hub list cannot leave stale flags behind.
        session.run(_CLEAR_CUSTODIAL_QUERY)
        summary["custodial_labelled"] = (
            session.run(_LABEL_CUSTODIAL_QUERY, ciks=hubs).single()["n"] if hubs else 0
        )

    log.info(
        f"✓ Materialized {summary['written']:,} CO_TARGETS edges; "
        f"flagged {summary['custodial_labelled']:,} custodial hubs "
        f"(excluded at projection time, not deleted)"
    )
    return summary
