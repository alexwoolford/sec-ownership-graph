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
from datetime import date
from pathlib import Path

from secgraph.cli import (
    add_database_argument,
    add_execute_argument,
    get_driver_and_database,
    setup_logging,
)
from secgraph.ingestion.ownership.pipeline import (
    _QUARTERS_13F as DEFAULT_QUARTERS_13F,
)
from secgraph.ingestion.ownership.pipeline import (
    _QUARTERS_345 as DEFAULT_QUARTERS_345,
)
from secgraph.ingestion.ownership.pipeline import (
    _report_preflight,
    build_secgraph,
    preflight_checks,
)


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
    parser.add_argument(
        "--quarters-345",
        type=int,
        default=DEFAULT_QUARTERS_345,
        help=f"Quarters of Form 3/4/5 bulk data to stage (default: {DEFAULT_QUARTERS_345}). "
        "This is what decides whether the density gate passes — raise it on a NO-GO.",
    )
    parser.add_argument(
        "--quarters-13f",
        type=int,
        default=DEFAULT_QUARTERS_13F,
        help=f"Quarters of Form 13F bulk data to stage (default: {DEFAULT_QUARTERS_13F}).",
    )
    parser.add_argument(
        "--as-of",
        default=None,
        metavar="YYYY-MM-DD",
        help="Pin every acquisition step to this date: bulk quarters ending on or before it, and "
        "13D/G filings dated on or before it. Without it, 'most recent N quarters' resolves "
        "against today, so two builds a week apart cannot agree. Required to reproduce a "
        "reference build.",
    )
    parser.add_argument(
        "--extract-control",
        action="store_true",
        help="Run LLM control extraction even when reference/control_figures.csv exists, to fill "
        "in filings newer than the CSV. Requires the [llm] extra and OPENAI_API_KEY.",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Check every build precondition (Neo4j edition, GDS, SEC_USER_AGENT, control "
        "figures, disk) and exit. Writes nothing and makes no network calls.",
    )
    args = parser.parse_args()

    if args.as_of:
        try:
            date.fromisoformat(args.as_of)
        except ValueError:
            print(f"ERROR: --as-of {args.as_of!r} must be YYYY-MM-DD")
            return 1

    logger = setup_logging("build_secgraph", execute=args.execute)
    database = args.database or "secgraph"

    # The preflight wants a driver even without --execute: its most valuable checks (edition,
    # GDS) need one, and it never writes.
    driver = None
    if args.execute or args.preflight_only:
        driver, database = get_driver_and_database(logger, database=database)
    try:
        if args.preflight_only:
            logger.info("=" * 70)
            logger.info("SECGRAPH PREFLIGHT — database '%s'", database)
            logger.info("=" * 70)
            failures = preflight_checks(
                database=database, driver=driver, refresh=args.refresh, log=logger
            )
            return 0 if _report_preflight(failures, log=logger) else 1

        ok = build_secgraph(
            database=database,
            refresh=args.refresh,
            execute=args.execute,
            driver=driver,
            freshness_path=Path(args.freshness_out),
            quarters_345=args.quarters_345,
            quarters_13f=args.quarters_13f,
            extract_control=args.extract_control,
            as_of=args.as_of,
            logger_instance=logger,
        )
    finally:
        if driver is not None:
            driver.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
