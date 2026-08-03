#!/usr/bin/env python3
"""
Phase 1 — load Insider nodes + role edges from Form 3/4/5 bulk TSVs.

Thin wrapper around ingestion.ownership.insiders. Reads the staged Form-345 zips
(download_ownership_data.py --form 345), joins SUBMISSION + REPORTINGOWNER, and
MERGEs Insider nodes with DIRECTOR_OF / OFFICER_OF / TEN_PCT_OWNER_OF edges to
in-universe issuers. This is the density-gate layer.

Usage:
    python scripts/load_insiders.py --database secgraph              # dry-run
    python scripts/load_insiders.py --database secgraph --execute
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
from secgraph.ingestion.ownership.bulk_datasets import staged_zip_paths
from secgraph.ingestion.ownership.insiders import load_insiders


def main():
    parser = argparse.ArgumentParser(description="Load SEC Form 3/4/5 insiders")
    add_execute_argument(parser)
    add_database_argument(parser)
    args = parser.parse_args()

    logger = setup_logging("load_insiders", execute=args.execute)
    logger.info("=" * 80)
    logger.info("Phase 1 — load insiders (Form 3/4/5)")
    logger.info("=" * 80)

    zip_paths = staged_zip_paths("form345")
    if not zip_paths:
        logger.error(
            "No Form-345 zips found. Run: python scripts/download_ownership_data.py --form 345"
        )
        sys.exit(1)
    logger.info(f"Found {len(zip_paths)} staged quarter(s)")

    driver, database = get_driver_and_database(logger, database=args.database)
    try:
        if not verify_neo4j_connection(driver, database, logger):
            sys.exit(1)

        load_insiders(
            driver,
            zip_paths,
            database=database,
            execute=args.execute,
            logger_instance=logger,
        )
    finally:
        driver.close()


if __name__ == "__main__":
    main()
