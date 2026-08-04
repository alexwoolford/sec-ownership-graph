#!/usr/bin/env python3
"""
Materialize the ``Company.institutional_value_usd`` size proxy from 13F holdings.

Sums ``HOLDS.value_usd`` per issuer for the newest 13F quarter, so structural results can be
ranked by materiality. Without it a $95B control relationship (Deutsche Telekom → T-Mobile) and a
$30 one render as peer rows, and the recognizable findings sink below the noise.

It is a **proxy, not a market cap**: absent for ~25% of the universe, measures free float, and
includes ETFs. See ``secgraph.ingestion.ownership.materiality`` for the full caveats.

Dry-run by default (prints coverage and distribution); ``--execute`` writes; ``--replace`` clears
stale values first. Intended for the ``secgraph`` database. Depends on
``load_institutional_holdings.py`` having run.

Run:
    python scripts/materialize_materiality.py --database secgraph            # dry-run
    python scripts/materialize_materiality.py --database secgraph --execute
    python scripts/materialize_materiality.py --database secgraph --execute --replace
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
from secgraph.ingestion.ownership.materiality import materialize_materiality


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize the institutional-value size proxy on Company nodes"
    )
    add_execute_argument(parser)
    add_database_argument(parser)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Clear existing values before writing. Use when 13F coverage may have changed — a "
        "stale figure on an issuer that lost coverage is worse than none, since absence is "
        "itself meaningful here.",
    )
    args = parser.parse_args()

    logger = setup_logging("materialize_materiality", execute=args.execute)
    driver, database = get_driver_and_database(logger, database=args.database)
    try:
        if not verify_neo4j_connection(driver, database, logger):
            return 1
        materialize_materiality(
            driver,
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
