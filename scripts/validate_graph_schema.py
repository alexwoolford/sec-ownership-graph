#!/usr/bin/env python3
"""
Verify the live database matches ``schema/graph_schema.yaml``.

Thin wrapper around ``secgraph.schema.live_validation``. **Fails hard** (exit 1) if a declared
label, relationship type, constraint or index is missing from the database, or if declared
provenance is wrong — a named writing module that does not exist, or a declared ``source_value``
that no edge actually carries. Property **coverage** is reported, never asserted, because coverage
legitimately moves with the staged EDGAR window.

**Why this exists.** ``docs/graph_schema.md`` has been telling readers to run this exact command,
with this exact contract, while the script did not exist. Everything else in the repo either scans
``.py`` source statically (`tests/unit/test_schema_consistency.py`) or compares live metadata
against a hand-mirrored Python tuple (`pipeline.py`) — so nothing asked the database whether the
schema it claims is the schema it has.

Read-only: no writes, no schema changes, safe against production. ``--execute`` is accepted and
ignored, for symmetry with the documented command and the rest of the repo's CLI.

Usage:
    python scripts/validate_graph_schema.py --database secgraph
    python scripts/validate_graph_schema.py --database secgraph --execute   # same; read-only
"""

import argparse
import sys

from secgraph.cli import (
    add_database_argument,
    get_driver_and_database,
    setup_logging,
    verify_neo4j_connection,
)
from secgraph.schema.live_validation import validate_live_schema


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail if the live database does not match schema/graph_schema.yaml"
    )
    add_database_argument(parser)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Accepted and ignored — this check is read-only. Present because the generated "
        "documentation references `--execute`, and silently rejecting it would be worse than "
        "honouring it as a no-op.",
    )
    args = parser.parse_args()

    logger = setup_logging("validate_graph_schema", execute=False)
    logger.info("=" * 80)
    logger.info("Graph schema validation — live database vs schema/graph_schema.yaml")
    logger.info("=" * 80)

    driver, database = get_driver_and_database(logger, database=args.database)
    try:
        if not verify_neo4j_connection(driver, database, logger):
            return 1

        report = validate_live_schema(driver, database=database, logger_instance=logger)

        failures = report["failures"]
        logger.info("")
        if failures:
            logger.error(f"✗ {len(failures)} schema violation(s):")
            for f in failures:
                logger.error(f"    {f}")
            logger.error("")
            logger.error(
                "The contract and the database disagree. Either the build is incomplete, or "
                "schema/graph_schema.yaml describes a graph this database is not."
            )
            return 1

        logger.info(
            f"✓ schema validated — {len(report['nodes'])} labels, "
            f"{len(report['relationships'])} relationship types, all declared constraints, "
            f"indexes and provenance verified against the live database"
        )
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
