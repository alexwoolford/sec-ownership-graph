"""
Materialize ``(:BeneficialOwner)-[:INFLUENCES]->(:Company)`` at the Fed presumption tiers.

**Why this exists.** ``CONTROLS`` fires only on a single stake of >=50%, and that bar turns out to
be **structurally anti-selected for large caps**: an issuer with a majority holder has little free
float, so it is largely absent from major indices and from the size range a finance professional
recognizes. Measured on the built graph, median issuer size rises *monotonically* as the bar falls:

    >=50%  825 issuers   median $35.7M   max  $92.6B
    >=25% 1676 issuers   median $79.8M   max $479.9B   (Berkshire, Walmart, Charter, Ferrari)

So the >=50% filter was finding illiquidity as much as control. The fix is not to loosen
``CONTROLS`` — that would make the word "control" mean a 25% stake, which invites a correct
objection and costs the room. It is to add a **separately named** edge at the thresholds US bank
regulation actually uses.

**The legal basis, cited precisely.** 12 CFR 225.2(e) sets the Federal Reserve's control
presumptions, with tiers at 5/10/15/25 percent of a class of voting securities plus a separate
board-control limb. We tier at 10/15/25/50 and set ``board_seat`` from Form 3/4/5. We deliberately
do **not** cite SCCL (12 CFR 252.76): that rule aggregates a large bank's *credit exposures* to a
counterparty and merely borrows 225.2's definition for that narrow purpose, so invoking it to
justify a general corporate-influence map is a category error a regulatory lawyer would catch.

**Two limits stated at the source, because they bound what the edge can claim:**

- ``percent_of_class`` is percent of the class covered by the filing, **not voting power**.
  225.2(e)'s test is 25% of a class of *voting* securities. Several of the largest names here are
  dual-class (Berkshire A/B, the Liberty tracking-stock complex, Carvana, Sea), where economic and
  voting stakes diverge sharply — so the voting test cannot be evaluated cleanly from 13D alone.
- **13D/G carries no exit obligation** once a holder drops below 5%, so a percent is *last known*,
  not current. ``filing_date`` therefore travels with every edge, and half the underlying 13D
  population predates 2020. An edge is evidence of a filing, not proof of a present-day stake.

The ``board_seat`` limb is the interesting half: a stake plus a current board seat is the
conjunction nobody argues with, and it is the one thing here that is genuinely fresh (Form 3/4/5
board activity runs to 2026 even where the 13D is a decade old).

Writes only to the target database (intended: ``secgraph``); MERGE-idempotent and
dry-run-by-default (``--execute`` to write, ``--replace`` to rebuild).
"""

from __future__ import annotations

import logging
from typing import Any

from secgraph.core.config.constants import BATCH_SIZE_LARGE

logger = logging.getLogger(__name__)

_SOURCE = "sec13d_influence_tiers"

# 12 CFR 225.2(e) control presumptions. 5% is omitted deliberately: it is the 13D *filing*
# trigger, so every edge in the source would qualify and the tier would carry no information.
INFLUENCE_TIERS = (10.0, 15.0, 25.0, 50.0)

# The lowest tier materialized — below this an edge says only "this filer had to file".
MIN_INFLUENCE_PCT = min(INFLUENCE_TIERS)

# Board seats are read from Form 3/4/5 activity, which is a keep-latest snapshot rather than a
# roster. "Has this owner been on the board recently" is answerable; "does this owner hold a
# majority of the board" is NOT (see the module docstring of interlock.py and CLAUDE.md), so only
# the boolean is derived here. Default recency window for "current".
DEFAULT_BOARD_SEAT_SINCE = "2025-06-01"

# Read-only enumeration. Self-filings (owner CIK == company CIK) excluded exactly as
# control_edges.py does — an entity filing on itself adds a bogus self-loop.
#
# The board-seat limb joins on CIK identity: an Insider and a BeneficialOwner sharing a CIK are the
# same legal person, which is the same hard-key discipline SAME_ENTITY_AS uses. No name matching.
_COMPUTE_QUERY = """
    MATCH (b:BeneficialOwner)-[r:BENEFICIAL_OWNER_OF {filing_type:'13D'}]->(c:Company)
    WHERE r.percent_of_class >= $min_pct
      AND b.cik IS NOT NULL AND c.cik IS NOT NULL
      AND b.cik <> c.cik
    OPTIONAL MATCH (i:Insider)-[dir:DIRECTOR_OF]->(c)
      WHERE i.cik = b.cik AND dir.last_seen >= date($seat_since)
    WITH b, c, r, max(dir.last_seen) AS seat_last_seen
    RETURN elementId(b) AS b_eid, elementId(c) AS c_eid,
           b.cik AS owner_cik, b.name AS owner_name,
           c.cik AS company_cik, c.name AS company_name,
           r.percent_of_class AS percent_of_class,
           r.accession_number AS accession_number,
           toString(r.filing_date) AS filing_date,
           seat_last_seen IS NOT NULL AS board_seat,
           toString(seat_last_seen) AS board_seat_last_seen
"""

_WRITE_QUERY = """
    UNWIND $batch AS row
    MATCH (b:BeneficialOwner) WHERE elementId(b) = row.b_eid
    MATCH (c:Company) WHERE elementId(c) = row.c_eid
    MERGE (b)-[r:INFLUENCES]->(c)
    SET r.percent_of_class = row.percent_of_class,
        r.tier = row.tier,
        r.accession_number = row.accession_number,
        r.filing_date = date(row.filing_date),
        r.board_seat = row.board_seat,
        r.board_seat_last_seen = CASE
              WHEN row.board_seat_last_seen IS NULL THEN NULL
              ELSE date(row.board_seat_last_seen) END,
        r.source = $source,
        r.computed_at = datetime()
    RETURN count(r) AS n
"""

_DELETE_QUERY = "MATCH (:BeneficialOwner)-[r:INFLUENCES]->(:Company) DELETE r"


# --------------------------------------------------------------------------- #
# Pure helpers — the unit-test surface.
# --------------------------------------------------------------------------- #
def batches(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    """Split rows into write batches of at most ``size`` (last batch may be short)."""
    if size <= 0:
        raise ValueError("batch size must be positive")
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def presumption_tier(pct: float | None, tiers: tuple[float, ...] = INFLUENCE_TIERS) -> int | None:
    """The highest 225.2(e) tier a percent reaches, or None below the lowest.

    Returns an int so the tier reads as a label (``25``) rather than a measurement — the exact
    percent is already on the edge, and the tier's job is to say which regulatory presumption
    applies.
    """
    if pct is None:
        return None
    reached = [t for t in tiers if pct >= t]
    return int(max(reached)) if reached else None


def summarize_influence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-tier counts plus the board-seat conjunction — the numbers worth reporting.

    ``with_board_seat`` is the headline: a stake *and* a current board seat is the two-limb test
    that needs no relabelling, and it is far rarer than either limb alone.
    """
    by_tier: dict[int, int] = {}
    seat_by_tier: dict[int, int] = {}
    for r in rows:
        tier = r.get("tier")
        if tier is None:
            continue
        by_tier[tier] = by_tier.get(tier, 0) + 1
        if r.get("board_seat"):
            seat_by_tier[tier] = seat_by_tier.get(tier, 0) + 1
    return {
        "edges": len(rows),
        "by_tier": dict(sorted(by_tier.items())),
        "with_board_seat": sum(1 for r in rows if r.get("board_seat")),
        "board_seat_by_tier": dict(sorted(seat_by_tier.items())),
        "owners": len({r.get("owner_cik") for r in rows if r.get("owner_cik")}),
        "companies": len({r.get("company_cik") for r in rows if r.get("company_cik")}),
    }


# --------------------------------------------------------------------------- #
# DB-bound compute + write.
# --------------------------------------------------------------------------- #
def materialize_influence_edges(
    driver,
    database: str | None = None,
    replace: bool = False,
    min_pct: float = MIN_INFLUENCE_PCT,
    board_seat_since: str = DEFAULT_BOARD_SEAT_SINCE,
    batch_size: int = BATCH_SIZE_LARGE,
    execute: bool = False,
    logger_instance: logging.Logger | None = None,
) -> dict[str, Any]:
    """Compute and persist ``INFLUENCES`` edges. Dry-run unless ``execute``."""
    log = logger_instance or logger

    with driver.session(database=database) as session:
        rows = session.run(_COMPUTE_QUERY, min_pct=min_pct, seat_since=board_seat_since).data()

    for r in rows:
        r["tier"] = presumption_tier(r.get("percent_of_class"))
    rows = [r for r in rows if r["tier"] is not None]

    summary = summarize_influence(rows)
    summary["board_seat_since"] = board_seat_since
    log.info(
        f"influence edges at 12 CFR 225.2(e) tiers (>= {min_pct:.0f}%, self-filings excluded): "
        f"{summary['edges']:,} across {summary['owners']:,} owners / "
        f"{summary['companies']:,} issuers"
    )
    log.info(f"  by tier: {summary['by_tier']}")
    log.info(
        f"  with a current board seat (since {board_seat_since}): "
        f"{summary['with_board_seat']:,} — {summary['board_seat_by_tier']}"
    )

    if not execute:
        log.info("")
        log.info(f"DRY RUN — would MERGE {summary['edges']:,} INFLUENCES edges")
        if replace:
            log.info("  (after deleting existing INFLUENCES edges)")
        log.info("Run with --execute to write.")
        summary["dry_run"] = True
        return summary

    with driver.session(database=database) as session:
        if replace:
            summary["deleted"] = session.run(_DELETE_QUERY).consume().counters.relationships_deleted
            log.info(f"deleted {summary['deleted']:,} existing INFLUENCES edges")
        written = 0
        for batch in batches(rows, batch_size):
            written += session.run(_WRITE_QUERY, batch=batch, source=_SOURCE).single()["n"]
        summary["written"] = written

    log.info(
        f"✓ Materialized {written:,} INFLUENCES edges "
        f"({summary['with_board_seat']:,} carry a current board seat)"
    )
    return summary
