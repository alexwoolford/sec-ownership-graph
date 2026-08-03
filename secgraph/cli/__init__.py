"""
Shared CLI utilities for the scripts in ``scripts/``.

Every script follows the same shape: argparse (with ``--execute`` / ``--database`` from
:mod:`secgraph.cli.args`) → :func:`setup_logging` → :func:`get_driver_and_database` → delegate the
real work to the library. Scripts stay thin; the logic lives in ``secgraph/``.

**Dry-run by default.** A script without ``--execute`` prints its plan and writes nothing.
"""

from secgraph.cli.args import add_database_argument, add_execute_argument
from secgraph.cli.connection import (
    get_driver_and_database,
    verify_neo4j_connection,
)
from secgraph.cli.logging import (
    print_dry_run_header,
    print_execute_header,
    setup_logging,
)

__all__ = [
    # Arguments
    "add_database_argument",
    "add_execute_argument",
    # Connection
    "get_driver_and_database",
    # Logging
    "print_dry_run_header",
    "print_execute_header",
    "setup_logging",
    "verify_neo4j_connection",
]
