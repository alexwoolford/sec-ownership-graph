#!/usr/bin/env python3
"""
Phase 0 — create the standalone SEC ownership database + constraints.

The ownership graph is a fresh, fully independent Neo4j database (Enterprise
``CREATE DATABASE``, run against the ``system`` database). It shares nothing with
the prior project's graph — only the repo's loader/constraint techniques.

Usage:
    python scripts/ownership_create_database.py --database secgraph            # dry-run
    python scripts/ownership_create_database.py --database secgraph --execute  # create
"""

import argparse
import sys

from secgraph.cli import (
    add_database_argument,
    add_execute_argument,
    get_driver_and_database,
    setup_logging,
    verify_neo4j_connection,
)
from secgraph.neo4j import create_ownership_constraints
from secgraph.neo4j.constraints import create_company_constraints


def main():
    parser = argparse.ArgumentParser(description="Create the SEC ownership database + constraints")
    add_execute_argument(parser)
    add_database_argument(parser)
    args = parser.parse_args()

    if not args.database:
        print("ERROR: --database NAME is required (the new ownership DB, e.g. secgraph)")
        sys.exit(1)

    logger = setup_logging("ownership_create_database", execute=args.execute)
    logger.info("=" * 80)
    logger.info(f"Phase 0 — create ownership database: {args.database}")
    logger.info("=" * 80)

    # Connect via the default database to run system-level DDL.
    driver, _ = get_driver_and_database(logger)
    try:
        if not verify_neo4j_connection(driver, "system", logger):
            sys.exit(1)

        if not args.execute:
            logger.info("DRY RUN — would:")
            logger.info(f"  CREATE DATABASE {args.database} IF NOT EXISTS")
            logger.info("  create Company + ownership constraints/indexes")
            logger.info("Run with --execute to create.")
            return

        logger.info(f"Creating database {args.database} (if not exists)...")
        with driver.session(database="system") as session:
            # WAIT blocks until the database is online; without it a freshly
            # created database is not yet routable and the constraint sessions
            # below fail with "Unable to retrieve routing information".
            session.run(f"CREATE DATABASE {args.database} IF NOT EXISTS WAIT")
        logger.info("✓ Database ready")

        logger.info("Creating constraints + indexes...")
        create_company_constraints(driver, database=args.database, logger=logger)
        create_ownership_constraints(driver, database=args.database, logger=logger)
        logger.info("✓ Constraints created")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
