#!/usr/bin/env python3
"""
Load ``Company.total_assets_usd`` from staged SEC Financial Statement Data Sets.

Thin wrapper around ``ingestion.ownership.financials``. Reads the balance-sheet ``Assets`` fact
(consolidated, USD, point-in-time) out of each staged FSDS zip, keeps the newest per CIK across
quarters, and writes it onto the matching Company node.

**Why the graph needs this.** ``institutional_value_usd`` measures 13F *free float*, which is
smallest exactly where ownership is concentrated — so the property used to filter for materiality
understates the issuers this graph is about. EchoStar carries $60.9B of assets and is 51.8%
controlled, yet has no 13F coverage at all, so it was invisible to every size-filtered query.

Requires staged data first:
    python scripts/download_ownership_data.py --form fsds --quarters 4 --as-of YYYY-MM-DD

Usage:
    python scripts/load_company_financials.py --database secgraph                    # dry-run
    python scripts/load_company_financials.py --database secgraph --execute
    python scripts/load_company_financials.py --database secgraph --execute --replace
    python scripts/load_company_financials.py --database secgraph --execute --as-of 2026-06-30
"""

import argparse
import sys
from datetime import date

from secgraph.cli import (
    add_database_argument,
    add_execute_argument,
    get_driver_and_database,
    setup_logging,
    verify_neo4j_connection,
)
from secgraph.ingestion.ownership.bulk_datasets import staged_zip_paths
from secgraph.ingestion.ownership.financials import load_company_financials


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load Company.total_assets_usd from SEC Financial Statement Data Sets"
    )
    add_execute_argument(parser)
    add_database_argument(parser)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Clear existing total_assets_usd values before loading. Needed so an issuer that "
        "stopped filing does not keep a stale figure — absence is meaningful here.",
    )
    parser.add_argument(
        "--as-of",
        metavar="YYYY-MM-DD",
        default=None,
        help="Ignore staged periods ending after this date. Applied at LOAD time, not just "
        "download: staged_zip_paths globs the whole local cache, so a machine that once staged "
        "more quarters would otherwise load a wider window than a fresh clone.",
    )
    parser.add_argument(
        "--quarters",
        type=int,
        default=None,
        help="Cap on staged quarters to load, newest first (default: all staged). Applied after "
        "the as-of filter, never before.",
    )
    args = parser.parse_args()

    if args.as_of is not None:
        try:
            date.fromisoformat(args.as_of)
        except ValueError:
            print(f"error: --as-of must be YYYY-MM-DD, got {args.as_of!r}", file=sys.stderr)
            return 1

    logger = setup_logging("load_company_financials", execute=args.execute)
    logger.info("=" * 80)
    logger.info("Company financials — total assets from FSDS")
    logger.info("=" * 80)

    # as_of BEFORE the quarter cap: reversing them would give a pinned build *less* history than
    # an unpinned one, which is the trap documented in CLAUDE.md's reproducibility contract.
    zip_paths = staged_zip_paths("fsds", limit=args.quarters, as_of=args.as_of)
    if not zip_paths:
        logger.error(
            "No FSDS zips found. Run: "
            "python scripts/download_ownership_data.py --form fsds --quarters 4"
        )
        return 1
    logger.info(f"Found {len(zip_paths)} staged quarter(s): {[p.name for p in zip_paths]}")

    driver, database = get_driver_and_database(logger, database=args.database)
    try:
        if not verify_neo4j_connection(driver, database, logger):
            return 1

        load_company_financials(
            driver,
            zip_paths,
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
