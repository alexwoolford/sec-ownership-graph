#!/usr/bin/env python3
"""
Phase 2b prereq — build the CUSIP-9 → CIK crosswalk (key-based, via FTD symbols).

Thin wrapper around ingestion.ownership.cusip_crosswalk. There is no single
authoritative public CUSIP→CIK map, but the SEC fails-to-deliver dataset publishes
``CUSIP | SYMBOL`` for essentially every traded equity, and ticker→CIK is already
trusted (it defines the universe). So this builds a pure key join —
CUSIP-9 → FTD symbol → in-universe ticker → CIK — with no fuzzy name matching.
Writes crosswalk.json + unmatched.json (auditable).

Usage:
    python scripts/build_cusip_crosswalk.py --database secgraph              # dry-run
    python scripts/build_cusip_crosswalk.py --database secgraph --execute
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
from secgraph.ingestion.ownership.cusip_crosswalk import build_cusip_crosswalk


def main():
    parser = argparse.ArgumentParser(description="Build the CUSIP-9 → CIK crosswalk")
    add_execute_argument(parser)
    add_database_argument(parser)
    args = parser.parse_args()

    logger = setup_logging("build_cusip_crosswalk", execute=args.execute)
    logger.info("=" * 80)
    logger.info("Phase 2b prereq — CUSIP → CIK crosswalk (key-based via FTD symbols)")
    logger.info("=" * 80)

    zip_paths = staged_zip_paths("ftd")
    if not zip_paths:
        logger.error(
            "No fails-to-deliver zips found. "
            "Run: python scripts/download_ownership_data.py --form ftd --quarters 14"
        )
        sys.exit(1)
    logger.info(f"Found {len(zip_paths)} staged fails-to-deliver period(s)")

    driver, database = get_driver_and_database(logger, database=args.database)
    try:
        if not verify_neo4j_connection(driver, database, logger):
            sys.exit(1)

        result = build_cusip_crosswalk(
            driver,
            zip_paths,
            database=database,
            execute=args.execute,
            logger_instance=logger,
        )
        if "match_rate_pct" in result:
            logger.info(
                f"✓ Crosswalk match rate: {result['match_rate_pct']}% "
                f"({result.get('matched', 0):,}/{result.get('distinct_cusip9', 0):,}) → "
                f"{result.get('distinct_companies', 0):,} companies"
            )
    finally:
        driver.close()


if __name__ == "__main__":
    main()
