#!/usr/bin/env python3
"""Build (or refresh) the standalone ``secgraph`` SEC ownership-relationship graph.

Thin wrapper: parse args, hand off to
:func:`secgraph.ingestion.ownership.pipeline.build_secgraph`, which sequences the
per-phase scripts (create DB → universe → insiders → density GO/NO-GO gate → interlock edge →
beneficial owners → control extraction → CUSIP crosswalk → 13F holdings). Dry-run by default —
prints the phased plan and writes nothing; pass ``--execute`` to run it, ``--refresh`` for the
incremental re-pull. On a successful ``--execute`` run it writes ``results/secgraph_freshness.json``.

Run:
  python scripts/build_secgraph.py --database secgraph                 # dry-run plan
  python scripts/build_secgraph.py --database secgraph --execute       # full cold-start build
  python scripts/build_secgraph.py --database secgraph --refresh --execute   # incremental refresh
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from secgraph.cli import (
    add_database_argument,
    add_execute_argument,
    get_driver_and_database,
    setup_logging,
)
from secgraph.ingestion.ownership.pipeline import build_secgraph


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build or refresh the standalone secgraph ownership graph"
    )
    add_database_argument(parser)
    add_execute_argument(parser)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Incremental refresh (re-stage recent quarters, reload changed slices, "
        "re-materialize derived edges) instead of a full cold-start build.",
    )
    parser.add_argument(
        "--freshness-out",
        default="results/secgraph_freshness.json",
        help="Where to write the freshness manifest after a successful --execute run.",
    )
    args = parser.parse_args()

    logger = setup_logging("build_secgraph", execute=args.execute)
    database = args.database or "secgraph"

    driver = None
    if args.execute:
        driver, database = get_driver_and_database(logger, database=database)
    try:
        ok = build_secgraph(
            database=database,
            refresh=args.refresh,
            execute=args.execute,
            driver=driver,
            freshness_path=Path(args.freshness_out),
            logger_instance=logger,
        )
    finally:
        if driver is not None:
            driver.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
