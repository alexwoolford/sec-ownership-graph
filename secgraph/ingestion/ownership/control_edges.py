"""
Materialize the derived control edge ``(:BeneficialOwner)-[:CONTROLS]->(:Company)``.

**Why this exists.** The transitive control chain is the headline graph-native win, but it was
being computed the wrong way: pull *every* verified control edge out of Neo4j with a flat
``MATCH``, then walk the chain in Python (``graph_native_proof.build_control_adjacency`` →
``enumerate_control_chains``). That is a ``SELECT``-then-loop, which is exactly what a
warehouse plus a ``while`` loop reproduces — so the "SQL cannot express this" claim was not
actually earned by the database. Persisting the edge lets the chain run as a real
variable-depth Cypher traversal (``(root)-[:CONTROLS*1..N]->(target)``), which is the query no
single SQL statement can write.

Two rules baked in at materialize time, so no caller can forget them:

- **Verified control only** — derived from ``BENEFICIAL_OWNER_OF {filing_type:'13D'}`` where
  ``control_class = 'control'`` (the >=50% stake confirmed by ``control_extraction.py``). A
  ``stake``/``unknown`` edge is never promoted to ``CONTROLS``.
- **No self-filings** — 25 of the 927 control edges have ``owner.cik == company.cik`` (an
  entity filing 13D on itself). Left in, they add bogus repeated hops to every chain
  (``TRANSCONTINENTAL -> TRANSCONTINENTAL -> ...``). Excluded here, leaving 902 real edges.

**How transitivity works.** The edge deliberately runs ``BeneficialOwner -> Company`` rather
than ``Company -> Company``: only 134 of 927 controllers exist as a ``Company`` node, so a
Company-to-Company edge would silently discard ~85% of the control graph (a truth-in-inclusion
violation). Chains continue instead via **CIK identity** — a controlled ``Company`` whose CIK
also exists as a ``BeneficialOwner`` is the same legal entity, so it can control the next link
down. That bridge is what turns 902 single edges into multi-hop pyramids, and it works only
because the graph is hard-keyed on CIK rather than name-matched.

Writes only to the target database (intended: ``secgraph``); MERGE-idempotent and
dry-run-by-default (``--execute`` to write, ``--replace`` to rebuild).
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

from secgraph.core.config.constants import BATCH_SIZE_LARGE

logger = logging.getLogger(__name__)

_SOURCE = "sec13d_verified_control"

# Columns of reference/control_figures.csv — the committed, LLM-free control layer.
CONTROL_FIGURE_COLUMNS = (
    "accession_number",
    "company_cik",
    "owner_key",
    "percent_of_class",
    "control_class",
    "pct_verified",
    # Which rule produced the percent. Carried through the CSV so a cloner's rebuild keeps the
    # provenance rather than getting a bare number with no way to audit it.
    "pct_source",
)

# Apply committed figures onto the 13D ownership edge, keyed on (company, accession). This is
# the same property set control_extraction.py writes, so materialize_control_edges cannot tell
# the difference — but it is deterministic and needs no API key.
_APPLY_FIGURES_QUERY = """
    UNWIND $batch AS row
    MATCH (b:BeneficialOwner {owner_key: row.owner_key})
          -[r:BENEFICIAL_OWNER_OF {filing_type:'13D'}]->(c:Company {cik: row.company_cik})
    WHERE r.accession_number = row.accession_number
    SET r.percent_of_class = row.percent_of_class,
        r.control_class = row.control_class,
        r.pct_verified = row.pct_verified,
        r.pct_source = row.pct_source,
        r.control_extracted_at = datetime(),
        r.control_source = 'reference_csv'
    RETURN count(r) AS n
"""

# Minimum verified stake that counts as control. Mirrors
# graph_native_proof.CONTROL_THRESHOLD_PCT; control_extraction.py already encodes this as
# control_class='control', so this constant documents the contract rather than re-filtering.
CONTROL_THRESHOLD_PCT = 50.0

# Read-only pattern enumerating verified control edges, excluding self-filings. elementId is
# used for the write-back join so the MERGE targets the exact nodes we computed against.
_COMPUTE_QUERY = """
    MATCH (b:BeneficialOwner)-[r:BENEFICIAL_OWNER_OF {filing_type:'13D'}]->(c:Company)
    WHERE r.control_class = 'control'
      AND b.cik IS NOT NULL AND c.cik IS NOT NULL
      AND b.cik <> c.cik
    RETURN elementId(b) AS b_eid, elementId(c) AS c_eid,
           b.cik AS owner_cik, b.name AS owner_name,
           c.cik AS company_cik, c.name AS company_name,
           r.percent_of_class AS percent_of_class,
           r.accession_number AS accession_number,
           toString(r.filing_date) AS filing_date
"""

_WRITE_QUERY = """
    UNWIND $batch AS row
    MATCH (b:BeneficialOwner) WHERE elementId(b) = row.b_eid
    MATCH (c:Company) WHERE elementId(c) = row.c_eid
    MERGE (b)-[r:CONTROLS]->(c)
    SET r.percent_of_class = row.percent_of_class,
        r.accession_number = row.accession_number,
        r.filing_date = row.filing_date,
        r.source = $source,
        r.computed_at = datetime()
    RETURN count(r) AS n
"""

_DELETE_QUERY = "MATCH (:BeneficialOwner)-[r:CONTROLS]->(:Company) DELETE r"

# The CIK-identity bridge. A holding company that is both controlled and controlling exists as
# two nodes (a Company and a BeneficialOwner) sharing one CIK; without a traversable edge
# between them Cypher stops at one hop, because a variable-length pattern cannot "jump" between
# nodes on a property equality. Materializing it is what makes the chain a real traversal.
# Restricted to CIKs that actually participate in CONTROLS, so we don't bridge the whole graph.
_BRIDGE_WRITE_QUERY = """
    MATCH (c:Company)<-[:CONTROLS]-()
    MATCH (b:BeneficialOwner) WHERE b.cik = c.cik
    MERGE (c)-[r:SAME_ENTITY_AS]->(b)
    SET r.cik = c.cik,
        r.source = $source,
        r.computed_at = datetime()
    RETURN count(r) AS n
"""

_BRIDGE_DELETE_QUERY = "MATCH (:Company)-[r:SAME_ENTITY_AS]->(:BeneficialOwner) DELETE r"

# How many controlled companies are themselves BeneficialOwners — i.e. how many CIK-identity
# bridges exist. This is the number that makes multi-hop chains possible at all, so it is
# reported in the plan: if it drops to zero, every chain collapses to one hop.
_BRIDGE_QUERY = """
    MATCH (b:BeneficialOwner)-[:CONTROLS]->(c:Company)
    WHERE EXISTS { MATCH (nb:BeneficialOwner) WHERE nb.cik = c.cik }
    RETURN count(DISTINCT c.cik) AS bridges
"""


# --------------------------------------------------------------------------- #
# Pure helpers — the unit-test surface.
# --------------------------------------------------------------------------- #
def batches(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    """Split rows into write batches of at most ``size`` (last batch may be short)."""
    if size <= 0:
        raise ValueError("batch size must be positive")
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def summarize_control_edges(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Coverage stats over computed control edges (for the plan/verify output)."""
    total = len(rows)
    controllers = len({r.get("owner_cik") for r in rows if r.get("owner_cik")})
    controlled = len({r.get("company_cik") for r in rows if r.get("company_cik")})
    pcts = [r["percent_of_class"] for r in rows if r.get("percent_of_class") is not None]
    return {
        "control_edges": total,
        "controllers": controllers,
        "controlled_companies": controlled,
        "min_percent_of_class": min(pcts) if pcts else None,
        "max_percent_of_class": max(pcts) if pcts else None,
    }


def identity_bridge_ciks(rows: list[dict[str, Any]]) -> set[str]:
    """CIKs that are both controlled (a target) and a controller (a source).

    These are the hinges: the legal entities that make a chain transitive. Computed purely
    from the edge rows so the hinge count can be reported without a second query.
    """
    owners = {r["owner_cik"] for r in rows if r.get("owner_cik")}
    companies = {r["company_cik"] for r in rows if r.get("company_cik")}
    return owners & companies


# --------------------------------------------------------------------------- #
# Committed control figures — the reproducible, LLM-free path.
# --------------------------------------------------------------------------- #
def parse_control_figures(path: Path) -> list[dict[str, Any]]:
    """Read reference/control_figures.csv into typed rows.

    The committed alternative to running ``extract_control_edges.py``: one LLM call per 13D
    edge, unseeded, against an unpinned model alias. Reading verified figures from a file makes
    ``CONTROLS`` — and therefore the control-chain count — identical on every rebuild, and drops
    the OpenAI dependency from the critical path.

    Raises:
        ValueError: if the header is missing a required column, or a row is malformed. Fails
            loudly rather than silently importing a partial control layer.
    """
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(CONTROL_FIGURE_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} is missing required column(s): {sorted(missing)}")

        rows: list[dict[str, Any]] = []
        for lineno, raw in enumerate(reader, start=2):
            pct_raw = (raw["percent_of_class"] or "").strip()
            try:
                pct = float(pct_raw) if pct_raw else None
            except ValueError as exc:
                raise ValueError(
                    f"{path}:{lineno}: percent_of_class {pct_raw!r} is not a number"
                ) from exc
            control_class = (raw["control_class"] or "").strip()
            if control_class not in {"control", "stake", "unknown"}:
                raise ValueError(
                    f"{path}:{lineno}: control_class {control_class!r} must be "
                    "'control', 'stake' or 'unknown'"
                )
            rows.append(
                {
                    "accession_number": (raw["accession_number"] or "").strip(),
                    "company_cik": (raw["company_cik"] or "").strip(),
                    "owner_key": (raw["owner_key"] or "").strip(),
                    "percent_of_class": pct,
                    "control_class": control_class,
                    "pct_verified": str(raw["pct_verified"]).strip().lower()
                    in {"true", "1", "yes"},
                    "pct_source": (raw.get("pct_source") or "").strip() or None,
                }
            )
    return rows


def load_control_figures(
    driver,
    csv_path: Path,
    database: str | None = None,
    batch_size: int = BATCH_SIZE_LARGE,
    execute: bool = False,
    logger_instance: logging.Logger | None = None,
) -> dict[str, Any]:
    """Apply committed control figures onto 13D ownership edges. Dry-run unless ``execute``.

    Coverage is *reported, never silently absorbed*: a cloner whose EDGAR window differs from
    the one the CSV was exported from will legitimately have figures for accessions they did not
    crawl, and edges the CSV does not cover. Both counts are surfaced so the gap is visible
    rather than looking like a clean run.
    """
    log = logger_instance or logger
    rows = parse_control_figures(csv_path)
    control_rows = sum(1 for r in rows if r["control_class"] == "control")
    log.info(
        f"control figures from {csv_path}: {len(rows):,} rows "
        f"({control_rows:,} classified 'control', i.e. >={CONTROL_THRESHOLD_PCT:.0f}%)"
    )

    summary: dict[str, Any] = {"csv_rows": len(rows), "csv_control_rows": control_rows}

    if not execute:
        log.info(f"DRY RUN — would apply {len(rows):,} control figures to 13D edges")
        log.info("Run with --execute to write.")
        summary["dry_run"] = True
        return summary

    applied = 0
    with driver.session(database=database) as session:
        for batch in batches(rows, batch_size):
            applied += session.run(_APPLY_FIGURES_QUERY, batch=batch).single()["n"]
        uncovered = session.run(
            """
            MATCH ()-[r:BENEFICIAL_OWNER_OF {filing_type:'13D'}]->()
            WHERE r.control_class IS NULL
            RETURN count(r) AS n
            """
        ).single()["n"]

    summary["applied"] = applied
    summary["unmatched_csv_rows"] = len(rows) - applied
    summary["uncovered_13d_edges"] = uncovered
    log.info(f"✓ Applied {applied:,}/{len(rows):,} control figures")
    if summary["unmatched_csv_rows"]:
        log.info(
            f"  {summary['unmatched_csv_rows']:,} CSV rows matched no edge "
            f"(expected when your EDGAR window differs from the export's)"
        )
    if uncovered:
        log.info(
            f"  {uncovered:,} 13D edges have no figures — newer filings than the CSV. "
            f"Run extract_control_edges.py to fill them (needs an OpenAI key)."
        )
    return summary


# --------------------------------------------------------------------------- #
# DB-bound compute + write.
# --------------------------------------------------------------------------- #
def _compute_control_edges(session) -> list[dict[str, Any]]:
    """Enumerate verified control edges, self-filings excluded (read-only)."""
    return session.run(_COMPUTE_QUERY).data()


def _write_control_edges(session, rows: list[dict[str, Any]], batch_size: int) -> int:
    written = 0
    for batch in batches(rows, batch_size):
        written += session.run(_WRITE_QUERY, batch=batch, source=_SOURCE).single()["n"]
    return written


def materialize_control_edges(
    driver,
    database: str | None = None,
    replace: bool = False,
    batch_size: int = BATCH_SIZE_LARGE,
    execute: bool = False,
    logger_instance: logging.Logger | None = None,
) -> dict[str, Any]:
    """Compute and persist ``CONTROLS`` edges. Dry-run unless ``execute``.

    Args:
        driver: Neo4j driver.
        database: Target database (``secgraph``).
        replace: Delete existing ``CONTROLS`` edges before writing (full rebuild).
        batch_size: Relationship-write batch size.
        execute: If False, compute the plan and report counts without writing.
        logger_instance: Optional logger.

    Returns:
        Summary dict (control_edges / controllers / controlled_companies / hinges / pct range),
        plus ``written``, ``deleted`` and ``bridges`` when executed.
    """
    log = logger_instance or logger

    with driver.session(database=database) as session:
        rows = _compute_control_edges(session)
    summary = summarize_control_edges(rows)
    hinges = identity_bridge_ciks(rows)
    summary["hinges"] = len(hinges)
    log.info(
        f"verified control edges (>={CONTROL_THRESHOLD_PCT:.0f}%, self-filings excluded): "
        f"{summary['control_edges']:,} · {summary['controllers']:,} controllers · "
        f"{summary['controlled_companies']:,} controlled · {summary['hinges']:,} hinges "
        f"(entities both controlled and controlling — these make chains transitive)"
    )

    if not execute:
        log.info("")
        log.info(
            f"DRY RUN — would MERGE {summary['control_edges']:,} CONTROLS edges "
            f"+ SAME_ENTITY_AS CIK-identity bridges on the {summary['hinges']:,} hinges"
        )
        if replace:
            log.info("  (after deleting existing CONTROLS and SAME_ENTITY_AS edges)")
        log.info("Run with --execute to write.")
        summary["dry_run"] = True
        return summary

    with driver.session(database=database) as session:
        if replace:
            summary["deleted"] = session.run(_DELETE_QUERY).consume().counters.relationships_deleted
            log.info(f"deleted {summary.get('deleted', 0):,} existing CONTROLS edges")
            session.run(_BRIDGE_DELETE_QUERY)
        summary["written"] = _write_control_edges(session, rows, batch_size)
        # The bridge must be written after CONTROLS exists — it is scoped to controlled companies.
        summary["bridge_edges"] = session.run(_BRIDGE_WRITE_QUERY, source=_SOURCE).single()["n"]
        summary["bridges"] = session.run(_BRIDGE_QUERY).single()["bridges"]

    log.info(
        f"✓ Materialized {summary['written']:,} CONTROLS edges + "
        f"{summary['bridge_edges']:,} SAME_ENTITY_AS bridges "
        f"({summary['bridges']:,} controlled companies are themselves controllers — "
        f"these are what make chains multi-hop)"
    )
    return summary
