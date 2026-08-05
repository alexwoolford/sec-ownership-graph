#!/usr/bin/env python3
"""
Phase 0 — create the standalone SEC ownership database + constraints.

The ownership graph is a fresh, fully independent Neo4j database (Enterprise
``CREATE DATABASE``, run against the ``system`` database). It shares nothing with
the prior project's graph — only the repo's loader/constraint techniques.

Usage:
    python scripts/ownership_create_database.py --database secgraph            # dry-run
    python scripts/ownership_create_database.py --database secgraph --execute  # create
"""

import argparse
import re
import sys

from neo4j.exceptions import ClientError

from secgraph.cli import (
    add_database_argument,
    add_execute_argument,
    get_driver_and_database,
    setup_logging,
    verify_neo4j_connection,
)
from secgraph.neo4j import create_ownership_constraints
from secgraph.neo4j.constraints import create_company_constraints


def _validate_database_name(name: str) -> str | None:
    """Return an error message if ``name`` is not a legal Neo4j database name, else None.

    Neo4j accepts ascii letters, numbers, dots and dashes, must start with a letter, and is
    2-63 characters. Underscores are *not* allowed — a natural choice like ``secgraph_repro``
    fails, and it used to fail as a raw driver stack trace from inside phase 0.
    """
    if not (2 <= len(name) <= 63):
        return f"database name {name!r} must be 2-63 characters (got {len(name)})"
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9.\-]*", name):
        return (
            f"database name {name!r} is not valid for Neo4j. Use ascii letters, numbers, "
            "dots and dashes, starting with a letter — underscores are not allowed "
            f"(try {name.replace('_', '-')!r})."
        )
    return None


def _is_unsupported_admin_command(exc: ClientError) -> bool:
    """True when the server refused CREATE DATABASE because the edition lacks it.

    Community raises ``Neo.ClientError.Statement.UnsupportedAdministrationCommand``; Aura has
    used both that and a plain forbidden/unsupported message. Matched loosely (code *and*
    message) so a wording change degrades into a raised error rather than a wrong diagnosis.
    """
    code = (exc.code or "").lower()
    message = str(exc).lower()
    return (
        "unsupportedadministrationcommand" in code
        or "unsupported administration command" in message
        or ("createdatabase" in code and "unsupported" in code)
    )


def main():
    parser = argparse.ArgumentParser(description="Create the SEC ownership database + constraints")
    add_execute_argument(parser)
    add_database_argument(parser)
    args = parser.parse_args()

    if not args.database:
        print("ERROR: --database NAME is required (the new ownership DB, e.g. secgraph)")
        sys.exit(1)

    name_error = _validate_database_name(args.database)
    if name_error:
        print(f"ERROR: {name_error}")
        sys.exit(1)

    logger = setup_logging("ownership_create_database", execute=args.execute)
    logger.info("=" * 80)
    logger.info(f"Phase 0 — create ownership database: {args.database}")
    logger.info("=" * 80)

    # Connect via the default database to run system-level DDL.
    driver, _ = get_driver_and_database(logger)
    try:
        if not verify_neo4j_connection(driver, "system", logger):
            sys.exit(1)

        if not args.execute:
            logger.info("DRY RUN — would:")
            logger.info(f"  CREATE DATABASE {args.database} IF NOT EXISTS")
            logger.info("  create Company + ownership constraints/indexes")
            logger.info("Run with --execute to create.")
            return

        logger.info(f"Creating database {args.database} (if not exists)...")
        try:
            with driver.session(database="system") as session:
                # WAIT blocks until the database is online; without it a freshly
                # created database is not yet routable and the constraint sessions
                # below fail with "Unable to retrieve routing information".
                #
                # The name is backtick-quoted: Neo4j allows dashes and dots in database names,
                # but a bare dashed identifier is a Cypher syntax error. `args.database` is
                # validated by _validate_database_name above, so it cannot smuggle a backtick.
                session.run(f"CREATE DATABASE `{args.database}` IF NOT EXISTS WAIT")
        except ClientError as exc:
            # Community Edition and Aura reject CREATE DATABASE outright: Community has no
            # named databases, Aura exposes exactly one (`neo4j`). Raw driver errors here read
            # as a connectivity problem, so name the actual cause and the two ways out.
            if not _is_unsupported_admin_command(exc):
                raise
            logger.error("=" * 80)
            logger.error(f"✗ This server cannot CREATE DATABASE '{args.database}'.")
            logger.error("  Named databases require Neo4j Enterprise (local or Docker).")
            logger.error("  Community Edition and Aura both expose a single database.")
            logger.error("")
            logger.error("  Either:")
            logger.error("    - use Neo4j Enterprise / the neo4j:enterprise Docker image, or")
            logger.error("    - target the server's existing database:")
            logger.error("        NEO4J_DATABASE=neo4j make build-exec DB=neo4j")
            logger.error("")
            logger.error(f"  Underlying error: {exc.code}")
            logger.error("=" * 80)
            sys.exit(1)
        logger.info("✓ Database ready")

        logger.info("Creating constraints + indexes...")
        create_company_constraints(driver, database=args.database, logger=logger)
        create_ownership_constraints(driver, database=args.database, logger=logger)
        logger.info("✓ Constraints created")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
