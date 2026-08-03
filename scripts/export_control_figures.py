#!/usr/bin/env python3
"""
Export verified 13D control figures to ``reference/control_figures.csv``.

**Why.** Control extraction is the only LLM step in the build: one ``gpt-4o-mini`` call per
Schedule 13D edge, reading percent-of-class off the cover page. It is also the least
reproducible — no ``seed``, an unpinned model alias, and no response cache — while the figures
it produces decide the ``CONTROLS`` edges, and only ~10 hinge edges support the published
control-chain count. A single flipped edge can delete a whole chain.

Committing the extracted figures makes that layer deterministic: a cloner loads the CSV, gets
identical ``CONTROLS`` edges, and needs no OpenAI key at all. Live extraction remains available
to fill in filings newer than the CSV.

This is the *producer* side, run once by a maintainer against a populated graph. The consumer
side is ``control_edges.load_control_figures``, wired into the build as a phase.

Read-only against Neo4j. Dry-run by default: prints what it would write.

Run:
    python scripts/export_control_figures.py --database secgraph              # dry-run
    python scripts/export_control_figures.py --database secgraph --execute
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from secgraph.cli import (
    add_database_argument,
    add_execute_argument,
    get_driver_and_database,
    setup_logging,
    verify_neo4j_connection,
)
from secgraph.ingestion.ownership.control_edges import CONTROL_FIGURE_COLUMNS

_DEFAULT_OUT = Path("reference/control_figures.csv")

# Every 13D edge whose control figures have been extracted — including 'stake' and 'unknown'.
# Exporting only 'control' rows would make a cloner's build re-extract every non-control edge,
# defeating the point; and the stake/unknown classifications are themselves verified results.
# Ordered so the committed file has a stable diff.
_EXPORT_QUERY = """
    MATCH (b:BeneficialOwner)-[r:BENEFICIAL_OWNER_OF {filing_type:'13D'}]->(c:Company)
    WHERE r.control_class IS NOT NULL
      AND r.accession_number IS NOT NULL
    RETURN r.accession_number AS accession_number,
           c.cik            AS company_cik,
           b.owner_key      AS owner_key,
           r.percent_of_class AS percent_of_class,
           r.control_class  AS control_class,
           coalesce(r.pct_verified, false) AS pct_verified
    ORDER BY company_cik, accession_number, owner_key
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export verified 13D control figures for committing to the repo"
    )
    add_execute_argument(parser)
    add_database_argument(parser)
    parser.add_argument(
        "--out",
        default=str(_DEFAULT_OUT),
        help=f"Output CSV path (default: {_DEFAULT_OUT}). Must be committed, so keep it "
        "outside data/, which is gitignored.",
    )
    args = parser.parse_args()

    logger = setup_logging("export_control_figures", execute=args.execute)
    driver, database = get_driver_and_database(logger, database=args.database)
    try:
        if not verify_neo4j_connection(driver, database, logger):
            return 1

        with driver.session(database=database) as session:
            rows = session.run(_EXPORT_QUERY).data()

        if not rows:
            logger.error(
                "✗ No extracted control figures found. Run extract_control_edges.py first — "
                "this script exports what that step produced."
            )
            return 1

        by_class: dict[str, int] = {}
        for row in rows:
            by_class[row["control_class"]] = by_class.get(row["control_class"], 0) + 1
        verified = sum(1 for r in rows if r["pct_verified"])
        logger.info(f"{len(rows):,} extracted 13D edges")
        for name in sorted(by_class):
            logger.info(f"  {name}: {by_class[name]:,}")
        logger.info(f"  percent verified against filing text: {verified:,}")

        out_path = Path(args.out)
        if not args.execute:
            logger.info("")
            logger.info(f"DRY RUN — would write {len(rows):,} rows to {out_path}")
            logger.info(f"  columns: {', '.join(CONTROL_FIGURE_COLUMNS)}")
            logger.info("Run with --execute to write.")
            return 0

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(CONTROL_FIGURE_COLUMNS))
            writer.writeheader()
            writer.writerows(rows)
        logger.info(f"✓ Wrote {len(rows):,} rows to {out_path}")
        logger.info("  Commit this file — it is what makes CONTROLS reproducible without an LLM.")
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
