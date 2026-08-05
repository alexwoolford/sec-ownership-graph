#!/usr/bin/env python3
"""
Materialize GDS structural features on Company from the board-interlock graph.

Thin wrapper around ``ingestion.ownership.interlock_features``. Projects the *scrubbed*
human-director company↔company graph, then writes betweenness centrality, Louvain community (with a
stable anchor), and interlock degree onto each Company node.

**Why it matters.** "Which boards broker between otherwise-unconnected clusters?" needs the whole
graph at once — it is not a row-per-company question, so no screener answers it. The analysis
already existed in ``interlock.py`` and was never called; this persists it where a query or a
visualization can use it.

**The projection must be UNDIRECTED, or the result is fiction.** It projects ``SHARES_DIRECTOR``
natively with ``orientation: UNDIRECTED``. An earlier version used a Cypher projection of a
stored-once-per-pair edge, which is inescapably directed — and because node ids track market-cap
rank (``id=0`` AAPL), betweenness over that DAG rewarded being a large cap. It produced a
comfortable PRU/AIG/GPN top-8 that was substantially insertion order. See the module docstring.

**Expect mixed-cap brokers.** The honest top of the list is XOS $0.1B (18 real interlock
neighbours), IFF $25.5B, PBI $3.2B, FLGT $1.2B. Boards that bridge otherwise-separate clusters are
often small companies whose directors also sit on larger ones. Band by ``size_usd`` if you need
recognizable names — do not expect centrality to track size.

**Deterministic by construction.** Every GDS call is pinned to ``concurrency=1``: the default
parallel path reassigned 52.4% of Louvain communities between two identical runs, which would
silently recolour a rebuild. At this graph size single-threaded is also faster.

**Nulls are meaningful.** A company with no interlock ends with NO feature properties rather than a
betweenness of 0.0 — "not measured" and "measured, brokers nothing" are different claims, and 1,129
companies legitimately carry a measured 0.0.

Requires Form 3/4/5 insiders and the SHARES_DIRECTOR edge to be loaded, plus the GDS plugin.

Usage:
    python scripts/materialize_interlock_features.py --database secgraph              # dry-run
    python scripts/materialize_interlock_features.py --database secgraph --execute
    python scripts/materialize_interlock_features.py --database secgraph --execute --replace
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
from secgraph.ingestion.ownership.interlock_features import materialize_interlock_features


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize interlock centrality + community features on Company (GDS)"
    )
    add_execute_argument(parser)
    add_database_argument(parser)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Clear existing interlock features before writing. Use when the insider layer "
        "changed: a company that drops out of the scrubbed projection must LOSE its score, "
        "since absence means 'not in the interlock graph', not 'central with score 0'.",
    )
    args = parser.parse_args()

    logger = setup_logging("materialize_interlock_features", execute=args.execute)
    logger.info("=" * 80)
    logger.info("Interlock structural features (GDS betweenness + Louvain, deterministic)")
    logger.info("=" * 80)

    driver, database = get_driver_and_database(logger, database=args.database)
    try:
        if not verify_neo4j_connection(driver, database, logger):
            return 1

        materialize_interlock_features(
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
