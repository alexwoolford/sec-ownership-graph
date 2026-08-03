"""Schema contract module for graph reliability.

``schema/graph_schema.yaml`` at the repo root is the single source of truth for node labels,
relationship types, properties, constraints and indexes. :mod:`secgraph.schema.contract` loads it
at import time into ``NODES`` / ``RELATIONSHIPS`` / ``PROPERTIES``;
:mod:`secgraph.schema.validation` lints Cypher against it. The rule the whole design rests on:
**do not reference a label or relationship type in Cypher that is not declared in the YAML** —
``tests/unit/test_schema_consistency.py`` scans every ``.py`` file and fails the build otherwise.
"""

from secgraph.schema.contract import (
    NODES,
    PROPERTIES,
    RELATIONSHIPS,
    Coverage,
    NodeInfo,
    PropertyInfo,
    RelationshipInfo,
    get_property_default,
    get_property_info,
    get_relationship_info,
    is_property_nullable,
    is_relationship_sparse,
)
from secgraph.schema.validation import (
    SchemaValidationError,
    check_property_has_coalesce,
    extract_relationships_from_cypher,
    find_similar_relationships,
    get_coverage_warning_message,
    suggest_coalesce_pattern,
    validate_cypher_query,
    validate_property_access,
    validate_relationship_exists,
    warn_if_sparse,
)

__all__ = [
    # Contract types and data
    "NODES",
    "PROPERTIES",
    "RELATIONSHIPS",
    "Coverage",
    "NodeInfo",
    "PropertyInfo",
    "RelationshipInfo",
    # Contract helpers
    "get_property_default",
    "get_property_info",
    "get_relationship_info",
    "is_property_nullable",
    "is_relationship_sparse",
    # Validation
    "SchemaValidationError",
    "check_property_has_coalesce",
    "extract_relationships_from_cypher",
    "find_similar_relationships",
    "get_coverage_warning_message",
    "suggest_coalesce_pattern",
    "validate_cypher_query",
    "validate_property_access",
    "validate_relationship_exists",
    "warn_if_sparse",
]
