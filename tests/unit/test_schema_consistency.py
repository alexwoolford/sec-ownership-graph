"""
Schema consistency test - ensures all Cypher references match the canonical schema.

This test scans all Python files in the codebase for Cypher relationship type
and node label references, validating them against schema/graph_schema.yaml.

When this test fails, it means someone introduced a reference to a relationship
or node label that doesn't exist in the canonical schema. Fix by either:
1. Correcting the typo in the code
2. Adding the new relationship/node to schema/graph_schema.yaml (if intentional)

This test runs in CI and prevents schema drift.
"""

import re
from pathlib import Path

import pytest
import yaml

# Directories to scan for Cypher references
SCAN_DIRECTORIES = [
    "secgraph",
    "scripts",
]

# Files to skip. This file names undeclared types on purpose (that is what it tests), so it
# cannot scan itself.
SKIP_FILES = {
    "test_schema_consistency.py",
}

# Allowlisted relationship types: false positives from the regex, not real graph references.
ALLOWLISTED_RELATIONSHIP_TYPES = {
    "DEBUG",  # logging level
    "ABC",  # Abstract Base Class
    "REL_NAME",  # placeholder in validation docstring examples
    "HAS_CHUNK",  # appears in a docstring showing what NOT to use
}

# Allowlisted node labels: same — regex artifacts, not graph labels.
ALLOWLISTED_NODE_LABELS: set[str] = set()


def get_project_root() -> Path:
    """Get the project root directory."""
    # Navigate from tests/unit/ to project root
    return Path(__file__).parent.parent.parent


def load_schema() -> dict:
    """Load the canonical schema from YAML."""
    schema_path = get_project_root() / "schema" / "graph_schema.yaml"
    with schema_path.open() as f:
        return yaml.safe_load(f)


def get_valid_relationship_types(schema: dict) -> set[str]:
    """Extract all valid relationship types from the schema."""
    return set(schema.get("relationships", {}).keys())


def get_valid_node_labels(schema: dict) -> set[str]:
    """Extract all valid node labels from the schema."""
    return set(schema.get("nodes", {}).keys())


def find_python_files(directories: list[str]) -> list[Path]:
    """Find all Python files in the specified directories."""
    project_root = get_project_root()
    python_files = []

    for dir_name in directories:
        dir_path = project_root / dir_name
        if dir_path.exists():
            python_files.extend(dir_path.rglob("*.py"))

    return python_files


def should_skip_file(file_path: Path) -> bool:
    """Check if a file should be skipped."""
    file_str = str(file_path)
    return any(skip_pattern in file_str for skip_pattern in SKIP_FILES)


def extract_relationship_types(content: str) -> set[str]:
    """
    Extract relationship type references from Cypher patterns in Python code.

    Matches patterns like:
    - [:HAS_DOMAIN]
    - [:SIMILAR_DESCRIPTION]
    - -[:HAS]->
    - ()-[r:USES]->()
    """
    # Pattern matches Cypher relationship syntax: [:REL_TYPE] or [var:REL_TYPE]
    # Only match UPPERCASE relationship type names (standard Cypher convention)
    # This excludes patterns like [cache_key] or [idx]
    pattern = r"\[\s*(?:\w+\s*:\s*)?([A-Z][A-Z0-9_]*)\s*(?:\{[^}]*\})?\s*\]"
    matches = re.findall(pattern, content)

    # Also match relationship types in query strings with patterns like ()-[:TYPE]->()
    cypher_pattern = r"-\s*\[\s*(?:\w+\s*:\s*)?([A-Z][A-Z0-9_]*)\s*(?:\{[^}]*\})?\s*\]\s*->"
    matches.extend(re.findall(cypher_pattern, content))

    # Filter: only keep relationship types (UPPER_SNAKE_CASE, at least 2 chars)
    filtered = set()
    for match in matches:
        # Must be fully uppercase (standard Cypher convention for relationships)
        if match.isupper() and len(match) >= 2:
            filtered.add(match)

    return filtered


def extract_node_labels(content: str) -> set[str]:
    """
    Extract node label references from Cypher patterns in Python code.

    Matches patterns like:
    - (c:Company)
    - (d:Domain)
    - MATCH (n:Technology)
    - (:Chunk)
    """
    # Pattern matches (var:Label) or (:Label)
    pattern = r"\((?:\w+)?:(\w+)\)"
    matches = re.findall(pattern, content)

    # Filter: node labels are typically PascalCase
    filtered = set()
    for match in matches:
        # Node labels typically start with uppercase
        if match[0].isupper():
            filtered.add(match)

    return filtered


class TestSchemaConsistency:
    """Test that all Cypher references in the codebase match the canonical schema."""

    @pytest.fixture(scope="class")
    def schema(self):
        """Load the canonical schema."""
        return load_schema()

    @pytest.fixture(scope="class")
    def valid_relationship_types(self, schema):
        """Get all valid relationship types."""
        return get_valid_relationship_types(schema) | ALLOWLISTED_RELATIONSHIP_TYPES

    @pytest.fixture(scope="class")
    def valid_node_labels(self, schema):
        """Get all valid node labels."""
        return get_valid_node_labels(schema) | ALLOWLISTED_NODE_LABELS

    def test_relationship_types_in_schema(self, valid_relationship_types):
        """
        Verify all relationship type references in the codebase exist in the schema.

        This catches typos like [:HAS_COMPETTITOR] or references to removed relationships.
        """
        python_files = find_python_files(SCAN_DIRECTORIES)
        invalid_refs: dict[str, list[str]] = {}

        for file_path in python_files:
            if should_skip_file(file_path):
                continue

            try:
                content = file_path.read_text()
            except Exception:
                continue

            found_types = extract_relationship_types(content)
            invalid_in_file = found_types - valid_relationship_types

            if invalid_in_file:
                rel_path = file_path.relative_to(get_project_root())
                invalid_refs[str(rel_path)] = sorted(invalid_in_file)

        if invalid_refs:
            msg_parts = ["Invalid relationship type references found:"]
            for file, types in sorted(invalid_refs.items()):
                msg_parts.append(f"  {file}: {', '.join(types)}")
            msg_parts.append("")
            msg_parts.append(f"Valid relationship types: {sorted(valid_relationship_types)}")
            msg_parts.append("")
            msg_parts.append("Fix by correcting the typo or adding to schema/graph_schema.yaml")

            pytest.fail("\n".join(msg_parts))

    def test_node_labels_in_schema(self, valid_node_labels):
        """
        Verify all node label references in the codebase exist in the schema.

        This catches typos like (c:Companny) or references to removed node types.
        """
        python_files = find_python_files(SCAN_DIRECTORIES)
        invalid_refs: dict[str, list[str]] = {}

        for file_path in python_files:
            if should_skip_file(file_path):
                continue

            try:
                content = file_path.read_text()
            except Exception:
                continue

            found_labels = extract_node_labels(content)
            invalid_in_file = found_labels - valid_node_labels

            if invalid_in_file:
                rel_path = file_path.relative_to(get_project_root())
                invalid_refs[str(rel_path)] = sorted(invalid_in_file)

        if invalid_refs:
            msg_parts = ["Invalid node label references found:"]
            for file, labels in sorted(invalid_refs.items()):
                msg_parts.append(f"  {file}: {', '.join(labels)}")
            msg_parts.append("")
            msg_parts.append(f"Valid node labels: {sorted(valid_node_labels)}")
            msg_parts.append("")
            msg_parts.append("Fix by correcting the typo or adding to schema/graph_schema.yaml")

            pytest.fail("\n".join(msg_parts))

    def test_schema_yaml_exists(self):
        """Verify the canonical schema file exists."""
        schema_path = get_project_root() / "schema" / "graph_schema.yaml"
        assert schema_path.exists(), f"Schema file not found at {schema_path}"

    def test_schema_has_required_sections(self, schema):
        """Verify the schema has all required sections."""
        required_sections = ["nodes", "relationships", "constraints", "indexes"]
        for section in required_sections:
            assert section in schema, f"Schema missing required section: {section}"

    def test_all_relationships_have_source_target(self, schema):
        """Verify all relationships have source and target defined."""
        for rel_name, rel_def in schema.get("relationships", {}).items():
            assert "source" in rel_def, f"Relationship {rel_name} missing 'source'"
            assert "target" in rel_def, f"Relationship {rel_name} missing 'target'"

    def test_all_nodes_have_unique_key(self, schema):
        """Verify all nodes have a unique_key defined."""
        for node_name, node_def in schema.get("nodes", {}).items():
            assert "unique_key" in node_def, f"Node {node_name} missing 'unique_key'"


class TestProvenanceIsDeclared:
    """Every property must say where it came from, and the claim must be checkable.

    The user's requirement was: "for each property on each node/relationship, exactly how it got
    there and where it came from." Before this, 24 of 28 node properties and ALL 40 relationship
    properties named no writer, and the runtime `source` string values existed only in Python — a
    reader could see that a `source` property was declared but never learn what value it holds.

    These tests keep the declarations honest. scripts/validate_graph_schema.py additionally checks
    them against the live database.
    """

    def _schema(self):
        import yaml

        with open(get_project_root() / "schema" / "graph_schema.yaml") as f:
            return yaml.safe_load(f)

    def test_every_node_label_names_its_writer(self):
        schema = self._schema()
        missing = [label for label, nd in schema["nodes"].items() if not nd.get("written_by")]
        assert not missing, f"labels with no declared writer: {missing}"

    def test_every_declared_writer_exists_on_disk(self):
        """A renamed module must fail here, not leave the schema quietly fictional."""
        schema = self._schema()
        root = get_project_root()
        bad = []
        for label, nd in schema["nodes"].items():
            writers = [nd.get("written_by"), *(nd.get("property_writers") or {}).values()]
            bad += [(label, w) for w in writers if w and not (root / w).exists()]
        for rel, rd in schema["relationships"].items():
            w = rd.get("written_by")
            if w and not (root / w).exists():
                bad.append((rel, w))
        assert not bad, f"declared writers that do not exist: {bad}"

    def test_every_relationship_declares_writer_and_source_value(self):
        """`source_value` is the literal string the edge's `source` property carries. Declaring it
        is what lets the validator prove the schema matches the graph rather than describing it."""
        schema = self._schema()
        missing = [
            rel
            for rel, rd in schema["relationships"].items()
            if not rd.get("written_by") or not rd.get("source_value")
        ]
        assert not missing, f"relationships missing written_by/source_value: {missing}"

    def test_no_phantom_properties(self):
        """A declared property that no code writes is worse than an undocumented one — it invites
        a query that always returns null. `coalition_id` was exactly that: declared as "GDS WCC over
        CO_TARGETS" with zero .py references, attributed to an algorithm the repo does not run."""
        schema = self._schema()
        props = set()
        for nd in schema["nodes"].values():
            props |= set((nd.get("optional_properties") or {}).keys())
        assert "coalition_id" not in props, (
            "coalition_id is a phantom property — nothing writes it. If it is reintroduced, "
            "something must actually populate it."
        )

    def test_fulltext_index_is_declared(self):
        """Bloom's search bar and any name-based entry point need this. It was absent, so entity
        lookup was a full label scan — out-of-the-box Neo4j the repo had coded around."""
        schema = self._schema()
        ft = (schema.get("indexes") or {}).get("fulltext") or []
        assert ft, "no fulltext index declared"
        labels = {lbl for idx in ft for lbl in idx.get("labels", [])}
        assert {"Company", "BeneficialOwner", "Insider", "InstitutionalManager"} <= labels
