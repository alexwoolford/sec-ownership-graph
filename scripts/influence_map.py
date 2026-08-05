#!/usr/bin/env python3
"""
Show issuers where a holder has a presumption-tier stake AND currently sits on the board.

The two-limb test from 12 CFR 225.2(e), and the strongest single output in the demo. Its force is
the **conjunction of two independent filing types**: the stake comes from a Schedule 13D, the board
seat from Form 3/4/5. A screener gives you either list; the pairing is the finding.

It also fixes a freshness problem. 13D carries no exit obligation below 5%, so roughly half the
stakes on file predate 2020 and are last-known rather than current — but board activity runs to the
present, so a recent seat corroborates an old declaration. Liberty Broadband declared 26.1% of
Charter in 2014; its director was on file in 2026.

Read-only. Nothing is written to the graph.

Run:
    python scripts/influence_map.py --database secgraph
    python scripts/influence_map.py --database secgraph --min-tier 10 --min-value-usd 1e10
"""

from __future__ import annotations

import argparse
import sys

from secgraph.cli import (
    add_database_argument,
    get_driver_and_database,
    setup_logging,
    verify_neo4j_connection,
)
from secgraph.ingestion.ownership.intelligence import OwnershipIntelligenceEngine


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Issuers where a big holder also sits on the board (12 CFR 225.2(e))"
    )
    add_database_argument(parser)
    parser.add_argument(
        "--min-tier",
        type=int,
        default=25,
        choices=[10, 15, 25, 50],
        help="Minimum Fed control-presumption tier (default: 25).",
    )
    parser.add_argument(
        "--min-value-usd",
        type=float,
        default=1e9,
        help="Minimum institutional size proxy, so results are recognizable names "
        "(default: 1e9). Issuers with no 13F coverage are excluded, not ranked last.",
    )
    parser.add_argument("--limit", type=int, default=25, help="Maximum rows to show (default: 25).")
    args = parser.parse_args()

    logger = setup_logging("influence_map", execute=False)
    driver, database = get_driver_and_database(logger, database=args.database)
    try:
        if not verify_neo4j_connection(driver, database, logger):
            return 1
        engine = OwnershipIntelligenceEngine(driver, database=database)
        result = engine.influence_map(
            min_tier=args.min_tier,
            min_value_usd=args.min_value_usd,
            limit=args.limit,
        )
        print()
        print(OwnershipIntelligenceEngine.format_answer(result))
        print()
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
