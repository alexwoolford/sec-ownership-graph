"""Neo4j connection, constraints and helpers.

Sessions are always scoped to a named database (``secgraph``), never the server default — see
:func:`secgraph.neo4j.connection.neo4j_driver_context` for one-off use and
:func:`get_neo4j_driver` for the ``atexit``-closed singleton.
"""

from secgraph.neo4j.connection import (
    close_global_driver,
    get_neo4j_driver,
    neo4j_driver_context,
    verify_connection,
)
from secgraph.neo4j.constraints import (
    create_all_constraints,
    create_company_constraints,
    create_ownership_constraints,
)
from secgraph.neo4j.utils import (
    clean_properties,
    clean_properties_batch,
    delete_relationships_in_batches,
)

__all__ = [
    "clean_properties",
    "clean_properties_batch",
    "close_global_driver",
    "create_all_constraints",
    "create_company_constraints",
    "create_ownership_constraints",
    "delete_relationships_in_batches",
    "get_neo4j_driver",
    "neo4j_driver_context",
    "verify_connection",
]
