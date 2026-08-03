#!/usr/bin/env python3
"""
Materialize the activist co-targeting edge ``(:BeneficialOwner)-[:CO_TARGETS]->(:BeneficialOwner)``.

Persists the 13D co-targeting pair (two activists holding >=N of the same issuers) as a
first-class relationship, so the wolf-pack coalition comes from a real graph algorithm (GDS WCC
/ variable-depth Cypher) instead of a flat pull plus a Python connected-components loop.
Custodial/broker hubs are flagged ``is_custodial`` on the node and excluded at projection time
— labelled, never deleted, so the true co-targeting fact survives.

Dry-run by default (prints the plan); ``--execute`` writes; ``--replace`` rebuilds.
Intended for the ``secgraph`` database. Depends on ``load_beneficial_owners.py`` having run.

Run:
    python scripts/materialize_cotarget_edges.py --database secgraph            # dry-run
    python scripts/materialize_cotarget_edges.py --database secgraph --execute
    python scripts/materialize_cotarget_edges.py --database secgraph --execute --replace
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
from secgraph.ingestion.ownership.cotarget_edges import (
    DEFAULT_MIN_SHARED_TARGETS,
    materialize_cotarget_edges,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize CO_TARGETS activist co-target edges")
    add_execute_argument(parser)
    add_database_argument(parser)
    parser.add_argument(
        "--min-shared-targets",
        type=int,
        default=DEFAULT_MIN_SHARED_TARGETS,
        help="Minimum shared 13D issuers to link two activists (default: %(default)s).",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete existing CO_TARGETS edges before writing (full rebuild).",
    )
    args = parser.parse_args()

    logger = setup_logging("materialize_cotarget_edges", execute=args.execute)
    driver, database = get_driver_and_database(logger, database=args.database)
    try:
        if not verify_neo4j_connection(driver, database, logger):
            return 1
        materialize_cotarget_edges(
            driver,
            database=database,
            min_shared_targets=args.min_shared_targets,
            replace=args.replace,
            execute=args.execute,
            logger_instance=logger,
        )
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
