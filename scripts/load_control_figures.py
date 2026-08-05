#!/usr/bin/env python3
"""
Apply committed 13D control figures from ``reference/control_figures.csv``.

The deterministic alternative to ``extract_control_edges.py``: instead of one ``gpt-4o-mini``
call per Schedule 13D edge (unseeded, unpinned model alias, no response cache), read verified
figures from a committed file. Same properties written — ``percent_of_class``,
``control_class``, ``pct_verified`` — so ``materialize_control_edges.py`` downstream cannot tell
the difference, but the result is identical on every rebuild and needs no OpenAI key.

Coverage is reported, not hidden: CSV rows that match no edge (your EDGAR window differs from
the export's) and 13D edges with no figures (filings newer than the CSV) are both counted. Run
``extract_control_edges.py`` afterwards to fill the latter — ``only_missing`` means it touches
only the uncovered edges.

The CSV is produced by ``scripts/export_control_figures.py`` against a populated graph.

Dry-run by default; ``--execute`` writes. Depends on ``load_beneficial_owners.py``.

Run:
    python scripts/load_control_figures.py --database secgraph            # dry-run
    python scripts/load_control_figures.py --database secgraph --execute
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from secgraph.cli import (
    add_database_argument,
    add_execute_argument,
    get_driver_and_database,
    setup_logging,
    verify_neo4j_connection,
)
from secgraph.ingestion.ownership.control_edges import load_control_figures

_DEFAULT_CSV = Path("reference/control_figures.csv")


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply committed 13D control figures")
    add_execute_argument(parser)
    add_database_argument(parser)
    parser.add_argument(
        "--csv",
        default=str(_DEFAULT_CSV),
        help=f"Control-figures CSV to load (default: {_DEFAULT_CSV})",
    )
    args = parser.parse_args()

    logger = setup_logging("load_control_figures", execute=args.execute)
    csv_path = Path(args.csv)
    if not csv_path.exists():
        logger.error(f"✗ {csv_path} not found.")
        logger.error("  Either commit it (scripts/export_control_figures.py --execute against a")
        logger.error("  populated graph), or run scripts/extract_control_edges.py instead")
        logger.error('  (needs pip install -e ".[dev,llm]" and OPENAI_API_KEY).')
        return 1

    driver, database = get_driver_and_database(logger, database=args.database)
    try:
        if not verify_neo4j_connection(driver, database, logger):
            return 1
        load_control_figures(
            driver,
            csv_path,
            database=database,
            execute=args.execute,
            logger_instance=logger,
        )
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
