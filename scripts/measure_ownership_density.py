#!/usr/bin/env python3
"""
Phase 1 GO/NO-GO gate — measure ownership-graph density (read-only, WCC write).

Thin wrapper around ingestion.ownership.density. Collapses insider board
interlocks into an undirected company↔company graph, runs GDS WCC (writing
Company.ownership_component), and reports connectivity + a shortest-path hop
histogram. Prints PASS/FAIL against the provisional gate and writes a JSON
summary to results/ownership_graph_density.json.

Usage:
    python scripts/measure_ownership_density.py --database secgraph
    python scripts/measure_ownership_density.py --database secgraph --sample-pairs 1000
"""

import argparse
import json
import sys
from pathlib import Path

from secgraph.cli import (
    add_database_argument,
    get_driver_and_database,
    setup_logging,
    verify_neo4j_connection,
)
from secgraph.ingestion.ownership.density import measure_ownership_density


def main():
    parser = argparse.ArgumentParser(description="Measure ownership-graph density (GO/NO-GO gate)")
    add_database_argument(parser)
    parser.add_argument(
        "--sample-pairs",
        type=int,
        default=500,
        help="Company pairs to sample for the shortest-path histogram (default: 500)",
    )
    parser.add_argument(
        "--out",
        default="results/ownership_graph_density.json",
        help="Path to write the JSON summary (default: results/ownership_graph_density.json)",
    )
    args = parser.parse_args()

    # WCC writes Company.ownership_component; the projection follows create→use→drop.
    logger = setup_logging("measure_ownership_density", execute=True)
    logger.info("=" * 80)
    logger.info("Phase 1 density gate — ownership interlock connectivity")
    logger.info("=" * 80)

    driver, database = get_driver_and_database(logger, database=args.database)
    try:
        if not verify_neo4j_connection(driver, database, logger):
            sys.exit(1)

        stats = measure_ownership_density(
            driver,
            database=database,
            sample_pairs=args.sample_pairs,
            logger_instance=logger,
        )

        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(stats, indent=2, default=str))
        logger.info(f"✓ Wrote density summary → {out_path}")

        gate = stats.get("gate", {})
        if gate and not gate.get("passed", False):
            # Non-zero exit signals NO-GO to the pipeline / caller.
            sys.exit(2)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
