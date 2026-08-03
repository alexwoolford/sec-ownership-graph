#!/usr/bin/env python3
"""
Materialize the verified control edge ``(:BeneficialOwner)-[:CONTROLS]->(:Company)``.

Promotes ``BENEFICIAL_OWNER_OF {filing_type:'13D', control_class:'control'}`` edges (the
>=50% stakes confirmed by ``extract_control_edges.py``) into a first-class relationship,
excluding self-filings. This is what lets the transitive control chain run as a real
variable-depth Cypher traversal (``(root)-[:CONTROLS*1..N]->(target)``) instead of pulling
every edge into Python and walking it there.

Dry-run by default (prints the plan); ``--execute`` writes; ``--replace`` rebuilds.
Intended for the ``secgraph`` database. Depends on ``extract_control_edges.py`` having run.

Run:
    python scripts/materialize_control_edges.py --database secgraph            # dry-run
    python scripts/materialize_control_edges.py --database secgraph --execute
    python scripts/materialize_control_edges.py --database secgraph --execute --replace
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
from secgraph.ingestion.ownership.control_edges import materialize_control_edges


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize CONTROLS verified-control edges")
    add_execute_argument(parser)
    add_database_argument(parser)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete existing CONTROLS edges before writing (full rebuild).",
    )
    args = parser.parse_args()

    logger = setup_logging("materialize_control_edges", execute=args.execute)
    driver, database = get_driver_and_database(logger, database=args.database)
    try:
        if not verify_neo4j_connection(driver, database, logger):
            return 1
        materialize_control_edges(
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
