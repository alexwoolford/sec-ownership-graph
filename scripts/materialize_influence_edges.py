#!/usr/bin/env python3
"""
Materialize ``INFLUENCES`` edges at the Fed control-presumption tiers (12 CFR 225.2(e)).

``CONTROLS`` fires only on a single >=50% stake, and that bar is structurally anti-selected for
large caps — an issuer with a majority holder has little free float. This adds a **separately
named** edge at the 10/15/25/50 percent presumption tiers, so "control" keeps meaning control while
the influence tiers reach the recognizable names (Berkshire, Walmart, Charter, Ferrari).

Each edge also carries ``board_seat``: true when the same CIK currently sits on the issuer's board
via Form 3/4/5. Stake *plus* a current board seat is the two-limb test that needs no relabelling.

See ``secgraph.ingestion.ownership.influence_edges`` for the caveats that travel with the edge —
notably that ``percent_of_class`` is not voting power, and that 13D percentages are last-known
rather than current.

Dry-run by default; ``--execute`` writes; ``--replace`` rebuilds. Depends on
``load_beneficial_owners.py`` and ``load_insiders.py``.

Run:
    python scripts/materialize_influence_edges.py --database secgraph            # dry-run
    python scripts/materialize_influence_edges.py --database secgraph --execute
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
from secgraph.ingestion.ownership.influence_edges import (
    DEFAULT_BOARD_SEAT_SINCE,
    MIN_INFLUENCE_PCT,
    materialize_influence_edges,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize INFLUENCES edges at the 12 CFR 225.2(e) presumption tiers"
    )
    add_execute_argument(parser)
    add_database_argument(parser)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete existing INFLUENCES edges before writing (full rebuild).",
    )
    parser.add_argument(
        "--min-pct",
        type=float,
        default=MIN_INFLUENCE_PCT,
        help=f"Lowest percent-of-class to materialize (default: {MIN_INFLUENCE_PCT:.0f}). 5%% is "
        "deliberately excluded — it is the 13D filing trigger, so every edge would qualify.",
    )
    parser.add_argument(
        "--board-seat-since",
        default=DEFAULT_BOARD_SEAT_SINCE,
        metavar="YYYY-MM-DD",
        help=f"A board seat counts as CURRENT if Form 3/4/5 activity is on/after this date "
        f"(default: {DEFAULT_BOARD_SEAT_SINCE}). Exposed rather than hidden because the cutoff "
        "materially changes the count.",
    )
    args = parser.parse_args()

    logger = setup_logging("materialize_influence_edges", execute=args.execute)
    driver, database = get_driver_and_database(logger, database=args.database)
    try:
        if not verify_neo4j_connection(driver, database, logger):
            return 1
        materialize_influence_edges(
            driver,
            database=database,
            replace=args.replace,
            min_pct=args.min_pct,
            board_seat_since=args.board_seat_since,
            execute=args.execute,
            logger_instance=logger,
        )
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
