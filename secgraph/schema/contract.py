"""
Graph Schema Contract - Single Source of Truth.

This module defines the canonical schema for the Public Company Graph.
All definitions are loaded from schema/graph_schema.yaml at import time.

**This module describes STRUCTURE, not density.** It knows which labels, relationship types and
properties are declared, and (from whether a property is required or optional) whether each is
nullable. It deliberately does **not** know coverage — that requires the database, and the previous
attempt to carry it as hand-maintained statistics drifted into reporting the 6.7M-edge ``HOLDS``
relationship as ``count=0, SPARSE``. See the "COVERAGE STATISTICS — REMOVED" block below.

To measure anything about the live graph — coverage, counts, or whether the declared schema is
actually the schema the database has — run::

    python scripts/validate_graph_schema.py --database secgraph

Coverage levels are retained on the dataclasses for callers that compute them from a live
connection: DENSE (>80%), MODERATE (40-80%), SPARSE (<40%), UNKNOWN (not measured — the default
here).
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class Coverage(Enum):
    """Coverage level for relationships and properties."""

    DENSE = "DENSE"  # >80% coverage
    MODERATE = "MODERATE"  # 40-80% coverage
    SPARSE = "SPARSE"  # <40% coverage
    # Not measured. A STATIC contract parsed from YAML cannot know coverage — that requires the
    # database. This value exists because the alternative was defaulting to SPARSE, which is a
    # claim, and a false one: it made is_relationship_sparse('HOLDS') return True for 6.7M edges.
    # Measure with scripts/validate_graph_schema.py.
    UNKNOWN = "UNKNOWN"


@dataclass
class NodeInfo:
    """Information about a node type."""

    label: str
    description: str
    count: int
    unique_key: str  # Property that uniquely identifies the node


@dataclass
class PropertyInfo:
    """Information about a node property."""

    name: str
    node_label: str
    type: str  # "String", "Long", "Double", "Boolean", "StringArray", "DoubleArray", etc.
    nullable: bool
    coverage_pct: float  # % of nodes that have this property populated
    coverage: Coverage
    default_value: Any  # Default to use in coalesce()
    description: str = ""


@dataclass
class RelationshipInfo:
    """Information about a relationship type."""

    name: str
    pattern: str  # e.g., "(Company)-[:HAS_DOMAIN]->(Domain)"
    description: str
    coverage: Coverage
    coverage_pct: float  # % of source nodes with >=1 relationship
    count: int  # Total relationship instances
    required_properties: list[str] = field(default_factory=list)
    optional_properties: list[str] = field(default_factory=list)
    source_label: str = ""
    target_label: str = ""


# ==============================================================================
# YAML SCHEMA LOADER
# ==============================================================================


def _find_schema_yaml() -> Path:
    """Find graph_schema.yaml relative to this package."""
    # Try multiple paths to find the schema file
    candidates = [
        Path(__file__).parent.parent.parent / "schema" / "graph_schema.yaml",
        Path.cwd() / "schema" / "graph_schema.yaml",
    ]
    for path in candidates:
        if path.exists():
            return path
    msg = f"Cannot find graph_schema.yaml. Tried: {candidates}"
    raise FileNotFoundError(msg)


def _load_yaml_schema() -> dict:
    """Load and return the YAML schema."""
    schema_path = _find_schema_yaml()
    with schema_path.open() as f:
        return yaml.safe_load(f)


# ==============================================================================
# COVERAGE STATISTICS — REMOVED DELIBERATELY. DO NOT REINTRODUCE.
# ==============================================================================
# This module used to carry hand-maintained _NODE_COUNTS, _RELATIONSHIP_STATS and
# _PROPERTY_STATS dicts. They drifted into describing a different graph, and because this file is
# named "Single Source of Truth" that drift read as authoritative:
#
#   * _RELATIONSHIP_STATS held 23 entries and not ONE current relationship type (all from a prior
#     tech-graph project: HAS, USES, TNIC_COMPETITOR...). Every lookup missed and fell back to
#     SPARSE, so is_relationship_sparse('HOLDS') returned True for a 6.7M-edge relationship and
#     the warning read "Only 0 relationships exist in graph" for the densest layer in the graph.
#   * _NODE_COUNTS said Company: 5_410 (actual 8,000) and listed five labels that no longer exist.
#   * _PROPERTY_STATS asserted market_cap at 18% and revenue at 15% coverage while the schema's own
#     Company description says the graph has neither. It had no entries at all for Insider,
#     InstitutionalManager or BeneficialOwner, and none for the newest Company properties, so
#     get_property_default('Company','size_usd') silently returned an invented figure.
#
# The fix is not to re-enter the numbers by hand — that is how it got this bad. Coverage cannot be
# known by a static contract parsed from YAML; it requires the database. So it is MEASURED:
#
#     python scripts/validate_graph_schema.py --database secgraph
#
# which walks every declared label, relationship type, property, constraint, index and provenance
# claim against the live graph, fails hard on missing structure, and reports coverage as a fact
# rather than asserting it as a guess.


# ==============================================================================
# BUILD NODES/RELATIONSHIPS/PROPERTIES FROM YAML
# ==============================================================================


def _build_nodes(schema: dict) -> dict[str, NodeInfo]:
    """Build NODES from the YAML schema.

    ``count`` is 0 because a *static* contract cannot know it. The previous version read a
    hand-maintained ``_NODE_COUNTS`` dict, which claimed ``Company: 5_410`` against an actual 8,000
    and listed five labels that no longer exist. Reporting 0 and pointing at the live validator is
    honest; reporting a stale number that looks authoritative is not.
    """
    nodes: dict[str, NodeInfo] = {}
    for label, node_def in schema.get("nodes", {}).items():
        nodes[label] = NodeInfo(
            label=label,
            description=node_def.get("description", ""),
            count=0,  # see docstring — measure with scripts/validate_graph_schema.py
            unique_key=node_def.get("unique_key", ""),
        )
    return nodes


def _build_relationships(schema: dict) -> dict[str, RelationshipInfo]:
    """Build RELATIONSHIPS from the YAML schema.

    Coverage is ``UNKNOWN``, not ``SPARSE``. The old ``_RELATIONSHIP_STATS`` dict held 23 entries
    from a prior project and not one current relationship type, so every lookup missed and defaulted
    to ``SPARSE`` — which made ``is_relationship_sparse('HOLDS')`` return True for a **6.7M-edge**
    relationship, and would have emitted "Only 0 relationships exist in graph" for the densest layer
    in the graph. A wrong answer stated confidently is worse than no answer, so the class of
    lookup is gone and ``scripts/validate_graph_schema.py`` measures it instead.
    """
    relationships: dict[str, RelationshipInfo] = {}
    for name, rel_def in schema.get("relationships", {}).items():
        relationships[name] = RelationshipInfo(
            name=name,
            pattern=rel_def.get("pattern", ""),
            description=rel_def.get("description", ""),
            coverage=Coverage.UNKNOWN,
            coverage_pct=0.0,
            count=0,
            required_properties=rel_def.get("required_properties", []),
            optional_properties=rel_def.get("optional_properties", []),
            source_label=rel_def.get("source", ""),
            target_label=rel_def.get("target", ""),
        )
    return relationships


def _build_properties(schema: dict) -> dict[str, dict[str, PropertyInfo]]:
    """Build PROPERTIES from the YAML schema.

    ``nullable`` is derived structurally — required properties are non-nullable, optional ones are —
    which is a fact the YAML actually encodes. ``coverage`` is ``UNKNOWN`` and ``default_value`` is
    always None: both used to come from ``_PROPERTY_STATS``, which had no entries at all for three
    of the four labels and none for any of the newest ``Company`` properties, so it silently
    returned invented figures (0.5 coverage) for real data. It also still asserted ``market_cap`` at
    18% and ``revenue`` at 15% while the schema's own ``Company`` description said the graph has
    neither.
    """
    properties: dict[str, dict[str, PropertyInfo]] = {}
    for label, node_def in schema.get("nodes", {}).items():
        properties[label] = {}
        for kind, nullable in (("required_properties", False), ("optional_properties", True)):
            for prop_name, prop_def in (node_def.get(kind) or {}).items():
                spec = prop_def if isinstance(prop_def, dict) else {}
                properties[label][prop_name] = PropertyInfo(
                    name=prop_name,
                    node_label=label,
                    type=spec.get("type", "String"),
                    nullable=nullable,
                    coverage_pct=0.0,
                    coverage=Coverage.UNKNOWN,
                    default_value=None,
                    description=spec.get("description", ""),
                )
    return properties


# ==============================================================================
# LOAD SCHEMA AT IMPORT TIME
# ==============================================================================

_SCHEMA = _load_yaml_schema()

NODES: dict[str, NodeInfo] = _build_nodes(_SCHEMA)
RELATIONSHIPS: dict[str, RelationshipInfo] = _build_relationships(_SCHEMA)
PROPERTIES: dict[str, dict[str, PropertyInfo]] = _build_properties(_SCHEMA)


# ==============================================================================
# HELPER FUNCTIONS (unchanged API)
# ==============================================================================


def get_relationship_info(name: str) -> RelationshipInfo | None:
    """Get relationship info by name."""
    return RELATIONSHIPS.get(name)


def get_property_info(node_label: str, property_name: str) -> PropertyInfo | None:
    """Get property info by node label and property name."""
    if node_label not in PROPERTIES:
        return None
    return PROPERTIES[node_label].get(property_name)


def is_relationship_sparse(name: str) -> bool:
    """Check if relationship is sparse (<40% coverage)."""
    rel_info = get_relationship_info(name)
    if rel_info is None:
        return True  # Unknown relationships assumed sparse
    return rel_info.coverage == Coverage.SPARSE


def is_property_nullable(node_label: str, property_name: str) -> bool:
    """Check if property is nullable."""
    prop_info = get_property_info(node_label, property_name)
    if prop_info is None:
        return True  # Unknown properties assumed nullable
    return prop_info.nullable


def get_property_default(node_label: str, property_name: str) -> Any:
    """Get default value for property (for use in coalesce())."""
    prop_info = get_property_info(node_label, property_name)
    if prop_info is None:
        return None
    return prop_info.default_value


def get_all_node_labels() -> set[str]:
    """Get all node labels defined in the schema."""
    return set(NODES.keys())


def get_all_relationship_types() -> set[str]:
    """Get all relationship types defined in the schema."""
    return set(RELATIONSHIPS.keys())


def get_schema_yaml_path() -> Path:
    """Get the path to the schema YAML file."""
    return _find_schema_yaml()
