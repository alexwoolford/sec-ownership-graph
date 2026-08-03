#!/usr/bin/env python3
"""
Phase 2a — load BeneficialOwner nodes + BENEFICIAL_OWNER_OF edges (13D/13G).

Thin wrapper around ingestion.ownership.beneficial. Schedule 13D/13G has no bulk
dataset, so this crawls EDGAR per in-universe subject company and parses only the
SGML submission header (subject CIK + filer CIK/name + form type + date). The
crawl is throttled to the SEC fair-access limit and cached per accession.

Usage:
    python scripts/load_beneficial_owners.py --database secgraph              # dry-run
    python scripts/load_beneficial_owners.py --database secgraph --execute
    python scripts/load_beneficial_owners.py --database secgraph --execute --max-filings 20
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
from secgraph.ingestion.ownership.beneficial import load_beneficial_owners


def main():
    parser = argparse.ArgumentParser(description="Load SEC 13D/13G beneficial owners")
    add_execute_argument(parser)
    add_database_argument(parser)
    parser.add_argument(
        "--max-filings",
        type=int,
        default=40,
        help="Cap on 13D/G filings crawled per subject company (default: 40)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-fetch the per-subject submissions index (cheap; needed after a "
        "form-code/filter change). Newly-discovered accessions are always fetched.",
    )
    parser.add_argument(
        "--refresh-headers",
        action="store_true",
        help="Also re-fetch cached SGML headers (rarely needed — headers are "
        "immutable by accession). Implies a full re-crawl.",
    )
    args = parser.parse_args()

    logger = setup_logging("load_beneficial_owners", execute=args.execute)
    logger.info("=" * 80)
    logger.info("Phase 2a — load beneficial owners (13D/13G)")
    logger.info("=" * 80)

    driver, database = get_driver_and_database(logger, database=args.database)
    try:
        if not verify_neo4j_connection(driver, database, logger):
            sys.exit(1)

        load_beneficial_owners(
            driver,
            database=database,
            refresh=args.refresh or args.refresh_headers,
            refresh_headers=args.refresh_headers,
            max_filings_per_subject=args.max_filings,
            execute=args.execute,
            logger_instance=logger,
        )
    finally:
        driver.close()


if __name__ == "__main__":
    main()
