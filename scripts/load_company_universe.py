#!/usr/bin/env python3
"""
Load the Company universe (SEC filers with a ticker) into the ownership graph.

Thin wrapper around ingestion.ownership.universe. Source is fetched live from
``company_tickers.json`` so a re-run refreshes the universe. Must run before any
ownership loader (edges only attach to in-universe Company nodes).

Usage:
    python scripts/load_company_universe.py --database secgraph                    # dry-run
    python scripts/load_company_universe.py --database secgraph --execute
    python scripts/load_company_universe.py --database secgraph --execute --enrich-sic
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
from secgraph.ingestion.ownership.universe import load_company_universe


def main():
    parser = argparse.ArgumentParser(description="Load SEC company universe (ticker filers)")
    add_execute_argument(parser)
    add_database_argument(parser)
    parser.add_argument(
        "--enrich-sic",
        action="store_true",
        help="Crawl the submissions API for SIC/sector/state (slow; cached)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore cached submissions metadata when enriching",
    )
    args = parser.parse_args()

    logger = setup_logging("load_company_universe", execute=args.execute)
    logger.info("=" * 80)
    logger.info("Load Company universe")
    logger.info("=" * 80)

    driver, database = get_driver_and_database(logger, database=args.database)
    try:
        if not verify_neo4j_connection(driver, database, logger):
            sys.exit(1)

        result = load_company_universe(
            driver,
            database=database,
            enrich_sic=args.enrich_sic,
            refresh=args.refresh,
            execute=args.execute,
            logger_instance=logger,
        )

        if args.execute and "nodes_written" in result:
            logger.info(f"✓ Loaded {result['nodes_written']:,} Company nodes")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
