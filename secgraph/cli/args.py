"""
Argument parsing utilities for secgraph CLI.

Provides standard argument patterns used across scripts.
"""


def add_execute_argument(parser):
    """
    Add standard --execute argument to an ArgumentParser.

    Args:
        parser: argparse.ArgumentParser instance
    """
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually execute the operation (default is dry-run)",
    )


def add_database_argument(parser):
    """
    Add a standard --database argument to an ArgumentParser.

    Defaults to None so callers can fall back to get_neo4j_database() when the
    flag is omitted (via get_driver_and_database). Set it to target a specific
    database, e.g. the standalone SEC ownership graph.

    Args:
        parser: argparse.ArgumentParser instance
    """
    parser.add_argument(
        "--database",
        default=None,
        help="Neo4j database to target (default: NEO4J_DATABASE from settings)",
    )
