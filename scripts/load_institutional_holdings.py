#!/usr/bin/env python3
"""
Phase 2b — load InstitutionalManager nodes + HOLDS edges from Form 13F.

Thin wrapper around ingestion.ownership.institutional. Reads the staged Form-13F
zips, joins SUBMISSION + COVERPAGE + INFOTABLE, maps CUSIP → CIK via the crosswalk
(build_cusip_crosswalk.py first), drops out-of-universe holdings, and MERGEs
aggregated HOLDS edges to in-universe issuers.

Usage:
    python scripts/load_institutional_holdings.py --database secgraph              # dry-run
    python scripts/load_institutional_holdings.py --database secgraph --execute
    python scripts/load_institutional_holdings.py --database secgraph --replace --execute
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
from secgraph.ingestion.ownership.institutional import load_institutional_holdings


def main():
    parser = argparse.ArgumentParser(description="Load SEC Form 13F institutional holdings")
    add_execute_argument(parser)
    add_database_argument(parser)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete all existing HOLDS edges before loading (needed after a "
        "crosswalk change so stale/misattributed edges don't survive)",
    )
    args = parser.parse_args()

    logger = setup_logging("load_institutional_holdings", execute=args.execute)
    logger.info("=" * 80)
    logger.info("Phase 2b — load institutional holdings (Form 13F)")
    logger.info("=" * 80)

    zip_paths = staged_zip_paths("form13f")
    if not zip_paths:
        logger.error(
            "No Form-13F zips found. Run: python scripts/download_ownership_data.py --form 13f"
        )
        sys.exit(1)
    logger.info(f"Found {len(zip_paths)} staged quarter(s)")

    driver, database = get_driver_and_database(logger, database=args.database)
    try:
        if not verify_neo4j_connection(driver, database, logger):
            sys.exit(1)

        load_institutional_holdings(
            driver,
            zip_paths,
            database=database,
            replace=args.replace,
            execute=args.execute,
            logger_instance=logger,
        )
    finally:
        driver.close()


if __name__ == "__main__":
    main()
