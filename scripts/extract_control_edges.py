#!/usr/bin/env python3
"""
Extract control-vs-stake figures for every Schedule 13D edge.

Thin wrapper around ingestion.ownership.control_extraction. For each
``BENEFICIAL_OWNER_OF {filing_type:'13D'}`` edge it fetches the filing body,
reads the cover-page percent-of-class (regex first, mini-LLM fallback for the
messy majority), verifies the figure against the source text (fail-closed), and
writes ``percent_of_class`` / ``sole_voting`` / ``shared_voting`` / ``control_class``
onto the edge. Full coverage — every edge is attempted; what cannot be verified is
labelled ``unknown`` and counted, never dropped.

Usage:
    python scripts/extract_control_edges.py --database secgraph                # dry-run
    python scripts/extract_control_edges.py --database secgraph --execute
    python scripts/extract_control_edges.py --database secgraph --execute --limit 50
    python scripts/extract_control_edges.py --database secgraph --execute --all   # re-do all
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
from secgraph.core.config.settings import ModelConfig, get_openai_api_key
from secgraph.ingestion.ownership.control_extraction import extract_control_edges


def main():
    parser = argparse.ArgumentParser(description="Extract 13D control-vs-stake figures")
    add_execute_argument(parser)
    add_database_argument(parser)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Re-extract every 13D edge (default: only edges not yet labelled).",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-fetch filing bodies instead of using the disk cache.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap on edges processed (for a bounded trial run).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Concurrent EDGAR-fetch threads (shared 10 req/s limiter caps rate).",
    )
    args = parser.parse_args()

    logger = setup_logging("extract_control_edges", execute=args.execute)
    logger.info("=" * 80)
    logger.info("13D control-vs-stake extraction")
    logger.info("=" * 80)

    driver, database = get_driver_and_database(logger, database=args.database)
    try:
        if not verify_neo4j_connection(driver, database, logger):
            sys.exit(1)

        llm_client = None
        if args.execute:
            from openai import OpenAI

            llm_client = OpenAI(api_key=get_openai_api_key())
        extract_control_edges(
            driver,
            llm_client=llm_client,
            model=ModelConfig.LLM_MINI_MODEL,
            database=database,
            only_missing=not args.all,
            refresh=args.refresh,
            workers=args.workers,
            execute=args.execute,
            limit=args.limit,
            logger_instance=logger,
        )
    finally:
        driver.close()


if __name__ == "__main__":
    main()
