#!/usr/bin/env python3
"""
Materialize the board-interlock edge ``(:Company)-[:SHARES_DIRECTOR]->(:Company)``.

Computes the human-director / operating-company interlock join once (the two validated
scrubs from the interlock proof) and persists it as a first-class relationship, so
``shortestPath`` and GDS projections traverse a stored edge instead of re-deriving the
2-hop ``DIRECTOR_OF`` pattern at query time. Stored undirected, once per pair.

Dry-run by default (prints the plan); ``--execute`` writes; ``--replace`` rebuilds.
Intended for the ``secgraph`` database.

Run:
    python scripts/materialize_interlock_edges.py --database secgraph            # dry-run
    python scripts/materialize_interlock_edges.py --database secgraph --execute
    python scripts/materialize_interlock_edges.py --database secgraph --execute --replace
"""

from __future__ import annotations

import argparse
import sys

from secgraph.cli import (
    add_database_argument,
    add_execute_argument,
    get_driver_and_database,
    setup_logging,
    verify_neo4j_connection,
)
from secgraph.ingestion.ownership.interlock_edges import materialize_interlock_edges


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize SHARES_DIRECTOR board-interlock edges"
    )
    add_execute_argument(parser)
    add_database_argument(parser)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete existing SHARES_DIRECTOR edges before writing (full rebuild).",
    )
    args = parser.parse_args()

    logger = setup_logging("materialize_interlock_edges", execute=args.execute)
    driver, database = get_driver_and_database(logger, database=args.database)
    try:
        if not verify_neo4j_connection(driver, database, logger):
            return 1
        materialize_interlock_edges(
            driver,
            database=database,
            replace=args.replace,
            execute=args.execute,
            logger_instance=logger,
        )
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
