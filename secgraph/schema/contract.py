"""
Graph Schema Contract - Single Source of Truth.

This module defines the canonical schema for the Public Company Graph.
All definitions are loaded from schema/graph_schema.yaml at import time.

Coverage levels indicate data density:
- DENSE (>80%): Safe to use in queries, most nodes have this relationship
- MODERATE (40-80%): Use OPTIONAL MATCH, many nodes have this relationship
- SPARSE (<40%): Warn users, few nodes have this relationship

Coverage statistics are maintained separately and may lag behind schema changes.
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
# COVERAGE STATISTICS (supplemental - may lag behind schema)
# ==============================================================================
# These are approximate counts/percentages from the live graph.
# They are NOT authoritative - the YAML schema is the source of truth for structure.

_NODE_COUNTS: dict[str, int] = {
    "Company": 5_410,
    "Domain": 4_337,
    "Technology": 827,
    "Document": 5_410,
    "Chunk": 2_850_489,
    "CommunitySummary": 11,
}

_RELATIONSHIP_STATS: dict[str, dict[str, Any]] = {
    "HAS": {"coverage": Coverage.DENSE, "coverage_pct": 1.0, "count": 5_410},
    "PART_OF_DOCUMENT": {"coverage": Coverage.DENSE, "coverage_pct": 1.0, "count": 2_850_489},
    "NEXT_CHUNK": {"coverage": Coverage.DENSE, "coverage_pct": 0.998, "count": 2_845_079},
    "HAS_DOMAIN": {"coverage": Coverage.MODERATE, "coverage_pct": 0.692, "count": 3_745},
    "USES": {"coverage": Coverage.DENSE, "coverage_pct": 0.85, "count": 46_081},
    "LIKELY_TO_ADOPT": {"coverage": Coverage.MODERATE, "coverage_pct": 0.70, "count": 41_250},
    "CO_OCCURS_WITH": {"coverage": Coverage.MODERATE, "coverage_pct": 0.60, "count": 41_220},
    "HAS_COMPETITOR": {"coverage": Coverage.MODERATE, "coverage_pct": 0.607, "count": 3_282},
    "HAS_PARTNER": {"coverage": Coverage.SPARSE, "coverage_pct": 0.135, "count": 733},
    "HAS_CUSTOMER": {"coverage": Coverage.SPARSE, "coverage_pct": 0.062, "count": 337},
    "HAS_SUPPLIER": {"coverage": Coverage.SPARSE, "coverage_pct": 0.033, "count": 176},
    "CANDIDATE_COMPETITOR": {"coverage": Coverage.SPARSE, "coverage_pct": 0.025, "count": 134},
    "CANDIDATE_PARTNER": {"coverage": Coverage.SPARSE, "coverage_pct": 0.166, "count": 899},
    "CANDIDATE_CUSTOMER": {"coverage": Coverage.SPARSE, "coverage_pct": 0.021, "count": 114},
    "CANDIDATE_SUPPLIER": {"coverage": Coverage.SPARSE, "coverage_pct": 0.028, "count": 150},
    "SIMILAR_DESCRIPTION": {"coverage": Coverage.DENSE, "coverage_pct": 0.95, "count": 438_039},
    "SIMILAR_SIZE": {"coverage": Coverage.MODERATE, "coverage_pct": 0.18, "count": 419_282},
    "SIMILAR_INDUSTRY": {"coverage": Coverage.DENSE, "coverage_pct": 0.95, "count": 519_000},
    "SIMILAR_RISK": {"coverage": Coverage.DENSE, "coverage_pct": 0.95, "count": 395_152},
    "SIMILAR_TECHNOLOGY": {"coverage": Coverage.MODERATE, "coverage_pct": 0.69, "count": 124_584},
    "SIMILAR_KEYWORD": {"coverage": Coverage.SPARSE, "coverage_pct": 0.0001, "count": 71},
    "TNIC_COMPETITOR": {"coverage": Coverage.SPARSE, "coverage_pct": 0.25, "count": 22_000},
    "IN_COMMUNITY": {"coverage": Coverage.DENSE, "coverage_pct": 1.0, "count": 5_410},
}

_PROPERTY_STATS: dict[str, dict[str, dict[str, Any]]] = {
    "Company": {
        "cik": {"coverage_pct": 1.0, "coverage": Coverage.DENSE, "nullable": False, "default": ""},
        "name": {"coverage_pct": 1.0, "coverage": Coverage.DENSE, "nullable": False, "default": ""},
        "loaded_at": {
            "coverage_pct": 1.0,
            "coverage": Coverage.DENSE,
            "nullable": False,
            "default": None,
        },
        "ticker": {
            "coverage_pct": 1.0,
            "coverage": Coverage.DENSE,
            "nullable": True,
            "default": "",
        },
        "description": {
            "coverage_pct": 0.9985,
            "coverage": Coverage.DENSE,
            "nullable": True,
            "default": "",
        },
        "description_embedding": {
            "coverage_pct": 0.9985,
            "coverage": Coverage.DENSE,
            "nullable": True,
            "default": None,
        },
        "risk_factors": {
            "coverage_pct": 0.99,
            "coverage": Coverage.DENSE,
            "nullable": True,
            "default": "",
        },
        "risk_factors_embedding": {
            "coverage_pct": 0.99,
            "coverage": Coverage.DENSE,
            "nullable": True,
            "default": None,
        },
        "industry": {
            "coverage_pct": 0.18,
            "coverage": Coverage.SPARSE,
            "nullable": True,
            "default": "",
        },
        "sector": {
            "coverage_pct": 0.18,
            "coverage": Coverage.SPARSE,
            "nullable": True,
            "default": "",
        },
        "market_cap": {
            "coverage_pct": 0.18,
            "coverage": Coverage.SPARSE,
            "nullable": True,
            "default": 0,
        },
        "revenue": {
            "coverage_pct": 0.15,
            "coverage": Coverage.SPARSE,
            "nullable": True,
            "default": 0,
        },
        "employees": {
            "coverage_pct": 0.15,
            "coverage": Coverage.SPARSE,
            "nullable": True,
            "default": 0,
        },
        "sic_code": {
            "coverage_pct": 0.50,
            "coverage": Coverage.MODERATE,
            "nullable": True,
            "default": "",
        },
        "community_id": {
            "coverage_pct": 1.0,
            "coverage": Coverage.DENSE,
            "nullable": True,
            "default": None,
        },
        "centrality_score": {
            "coverage_pct": 1.0,
            "coverage": Coverage.DENSE,
            "nullable": True,
            "default": 0.0,
        },
        "risk_categories": {
            "coverage_pct": 1.0,
            "coverage": Coverage.DENSE,
            "nullable": True,
            "default": [],
        },
        "primary_risks": {
            "coverage_pct": 1.0,
            "coverage": Coverage.DENSE,
            "nullable": True,
            "default": [],
        },
    },
    "Domain": {
        "final_domain": {
            "coverage_pct": 1.0,
            "coverage": Coverage.DENSE,
            "nullable": False,
            "default": "",
        },
        "title": {
            "coverage_pct": 0.90,
            "coverage": Coverage.DENSE,
            "nullable": True,
            "default": "",
        },
        "description": {
            "coverage_pct": 0.85,
            "coverage": Coverage.DENSE,
            "nullable": True,
            "default": "",
        },
        "http_status": {
            "coverage_pct": 1.0,
            "coverage": Coverage.DENSE,
            "nullable": True,
            "default": 0,
        },
    },
    "Chunk": {
        "chunk_id": {
            "coverage_pct": 1.0,
            "coverage": Coverage.DENSE,
            "nullable": False,
            "default": "",
        },
        "text": {"coverage_pct": 1.0, "coverage": Coverage.DENSE, "nullable": False, "default": ""},
        "embedding": {
            "coverage_pct": 1.0,
            "coverage": Coverage.DENSE,
            "nullable": False,
            "default": None,
        },
        "chunk_index": {
            "coverage_pct": 1.0,
            "coverage": Coverage.DENSE,
            "nullable": False,
            "default": 0,
        },
    },
}


# ==============================================================================
# BUILD NODES/RELATIONSHIPS/PROPERTIES FROM YAML
# ==============================================================================


def _build_nodes(schema: dict) -> dict[str, NodeInfo]:
    """Build NODES dict from YAML schema."""
    nodes: dict[str, NodeInfo] = {}
    for label, node_def in schema.get("nodes", {}).items():
        nodes[label] = NodeInfo(
            label=label,
            description=node_def.get("description", ""),
            count=_NODE_COUNTS.get(label, 0),
            unique_key=node_def.get("unique_key", ""),
        )
    return nodes


def _build_relationships(schema: dict) -> dict[str, RelationshipInfo]:
    """Build RELATIONSHIPS dict from YAML schema."""
    relationships: dict[str, RelationshipInfo] = {}
    for name, rel_def in schema.get("relationships", {}).items():
        stats = _RELATIONSHIP_STATS.get(name, {})
        relationships[name] = RelationshipInfo(
            name=name,
            pattern=rel_def.get("pattern", ""),
            description=rel_def.get("description", ""),
            coverage=stats.get("coverage", Coverage.SPARSE),
            coverage_pct=stats.get("coverage_pct", 0.0),
            count=stats.get("count", 0),
            required_properties=rel_def.get("required_properties", []),
            optional_properties=rel_def.get("optional_properties", []),
            source_label=rel_def.get("source", ""),
            target_label=rel_def.get("target", ""),
        )
    return relationships


def _build_properties(schema: dict) -> dict[str, dict[str, PropertyInfo]]:
    """Build PROPERTIES dict from YAML schema."""
    properties: dict[str, dict[str, PropertyInfo]] = {}
    for label, node_def in schema.get("nodes", {}).items():
        properties[label] = {}
        label_stats = _PROPERTY_STATS.get(label, {})

        # Required properties
        for prop_name, prop_def in node_def.get("required_properties", {}).items():
            prop_stats = label_stats.get(prop_name, {})
            properties[label][prop_name] = PropertyInfo(
                name=prop_name,
                node_label=label,
                type=prop_def.get("type", "String"),
                nullable=prop_stats.get("nullable", False),
                coverage_pct=prop_stats.get("coverage_pct", 1.0),
                coverage=prop_stats.get("coverage", Coverage.DENSE),
                default_value=prop_stats.get("default", None),
                description=prop_def.get("description", ""),
            )

        # Optional properties
        for prop_name, prop_def in node_def.get("optional_properties", {}).items():
            prop_stats = label_stats.get(prop_name, {})
            properties[label][prop_name] = PropertyInfo(
                name=prop_name,
                node_label=label,
                type=prop_def.get("type", "String"),
                nullable=prop_stats.get("nullable", True),
                coverage_pct=prop_stats.get("coverage_pct", 0.5),
                coverage=prop_stats.get("coverage", Coverage.MODERATE),
                default_value=prop_stats.get("default", None),
                description=prop_def.get("description", ""),
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
