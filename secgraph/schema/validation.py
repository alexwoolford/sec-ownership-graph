"""
Runtime validation helpers for graph schema compliance.

Provides functions to:
- Validate Cypher queries reference existing relationships
- Warn when querying sparse relationships (<40% coverage)
- Check properties use coalesce() when nullable
- Find similar relationship names (typo detection)

Usage:
    from secgraph.schema.validation import validate_cypher_query, warn_if_sparse

    warnings = validate_cypher_query(my_query)
    if warnings:
        for w in warnings:
            logger.warning(w)

    if warn_if_sparse("HAS_SUPPLIER", logger):
        # Logged warning about sparse relationship
        pass
"""

import logging
import re
from difflib import get_close_matches

from secgraph.schema.contract import (
    NODES,
    RELATIONSHIPS,
    Coverage,
    get_property_default,
    get_property_info,
    get_relationship_info,
)

logger = logging.getLogger(__name__)


class SchemaValidationError(Exception):
    """Raised when schema validation fails."""

    pass


def extract_relationships_from_cypher(cypher: str) -> list[str]:
    """
    Extract relationship names from Cypher query.

    Matches patterns like:
    - [:REL_NAME]
    - [:REL_NAME|OTHER_REL]
    - [r:REL_NAME]
    - [r:REL_NAME {prop: 'value'}]

    Returns:
        List of relationship names found in query
    """
    # Pattern: [:REL_NAME] or [var:REL_NAME] or [:REL1|REL2]
    pattern = r"\[(?:[a-z_]\w*)?:([A-Z_][A-Z_0-9|]*)\]"
    matches = re.findall(pattern, cypher)

    # Split on | for multiple relationship types
    rel_names = []
    for match in matches:
        rel_names.extend(match.split("|"))

    return list(set(rel_names))  # Deduplicate


def find_similar_relationships(name: str, max_suggestions: int = 3) -> list[str]:
    """
    Find relationship names similar to the given name (typo detection).

    Args:
        name: Relationship name to find matches for
        max_suggestions: Maximum number of suggestions to return

    Returns:
        List of similar relationship names from contract
    """
    all_rel_names = list(RELATIONSHIPS.keys())
    matches = get_close_matches(name, all_rel_names, n=max_suggestions, cutoff=0.6)
    return matches


def validate_relationship_exists(name: str) -> bool:
    """Check if relationship exists in schema contract."""
    return name in RELATIONSHIPS


def validate_cypher_query(cypher: str, raise_on_error: bool = False) -> list[str]:
    """
    Validate that Cypher query only references existing relationships.

    Args:
        cypher: Cypher query string to validate
        raise_on_error: If True, raise SchemaValidationError on validation failure

    Returns:
        List of warning messages (empty if valid)

    Raises:
        SchemaValidationError: If raise_on_error=True and validation fails

    Example:
        >>> warnings = validate_cypher_query("MATCH (c:Company)-[:HAS_CHUNK]->(chunk) RETURN c")
        >>> if warnings:
        ...     for w in warnings:
        ...         logger.warning(w)
        WARNING: Relationship 'HAS_CHUNK' not in schema contract
        WARNING:   Did you mean: PART_OF_DOCUMENT?
    """
    rel_names = extract_relationships_from_cypher(cypher)
    warnings = []

    for rel_name in rel_names:
        if not validate_relationship_exists(rel_name):
            warnings.append(f"Relationship '{rel_name}' not in schema contract")

            # Suggest similar names
            similar = find_similar_relationships(rel_name)
            if similar:
                warnings.append(f"  Did you mean: {', '.join(similar)}?")

    if warnings and raise_on_error:
        raise SchemaValidationError("\n".join(warnings))

    return warnings


def warn_if_sparse(rel_name: str, logger_instance: logging.Logger) -> bool:
    """
    Warn if relationship is sparse (<40% coverage).

    Args:
        rel_name: Relationship name to check
        logger_instance: Logger to use for warning

    Returns:
        True if warning was issued, False otherwise

    Example:
        >>> if warn_if_sparse("HAS_SUPPLIER", logger):
        ...     pass  # Warning logged
        WARNING: Using sparse relationship 'HAS_SUPPLIER' (3.3% coverage)
        WARNING:   Only 176 / 5,410 companies have this relationship
        WARNING:   Results will be limited. Consider adding fallback logic.
    """
    rel_info = get_relationship_info(rel_name)

    if rel_info is None:
        logger_instance.warning(f"Unknown relationship '{rel_name}' - cannot determine coverage")
        return True

    if rel_info.coverage == Coverage.SPARSE:
        logger_instance.warning(
            f"Using sparse relationship '{rel_name}' ({rel_info.coverage_pct:.1%} coverage)"
        )
        logger_instance.warning(f"  Only {rel_info.count:,} relationships exist in graph")
        logger_instance.warning("  Results will be limited. Consider adding fallback logic.")
        return True

    return False


def check_property_has_coalesce(cypher: str, node_label: str, property_name: str) -> bool:
    """
    Check if nullable property uses coalesce() in Cypher query.

    Args:
        cypher: Cypher query string
        node_label: Node label (e.g., "Company")
        property_name: Property name (e.g., "industry")

    Returns:
        True if property uses coalesce() or is not nullable, False otherwise

    Example:
        >>> good = "WHERE coalesce(company.industry, '') = 'Tech'"
        >>> check_property_has_coalesce(good, "Company", "industry")
        True

        >>> bad = "WHERE company.industry = 'Tech'"  # Missing coalesce!
        >>> check_property_has_coalesce(bad, "Company", "industry")
        False
    """
    prop_info = get_property_info(node_label, property_name)

    if prop_info is None:
        # Unknown property - assume it needs coalesce
        return False

    if not prop_info.nullable:
        # Property is not nullable, coalesce not required
        return True

    # Check if coalesce is used for this property
    # Pattern: coalesce(var.property_name, ...)
    pattern = rf"coalesce\s*\(\s*\w+\.{property_name}\s*,"
    return bool(re.search(pattern, cypher, re.IGNORECASE))


def validate_property_access(
    cypher: str, node_label: str, property_name: str, raise_on_error: bool = False
) -> list[str]:
    """
    Validate property access uses coalesce() if nullable.

    Args:
        cypher: Cypher query string
        node_label: Node label
        property_name: Property name
        raise_on_error: If True, raise SchemaValidationError on validation failure

    Returns:
        List of warning messages (empty if valid)

    Raises:
        SchemaValidationError: If raise_on_error=True and validation fails
    """
    prop_info = get_property_info(node_label, property_name)
    warnings = []

    if prop_info is None:
        warnings.append(f"Unknown property '{node_label}.{property_name}'")
        if raise_on_error:
            raise SchemaValidationError("\n".join(warnings))
        return warnings

    if not prop_info.nullable:
        # Not nullable, no coalesce needed
        return []

    # Check if property is accessed without coalesce
    if not check_property_has_coalesce(cypher, node_label, property_name):
        default = prop_info.default_value
        warnings.append(
            f"Nullable property '{node_label}.{property_name}' accessed without coalesce()"
        )
        warnings.append(f"  {prop_info.coverage_pct:.0%} of nodes have NULL value")
        warnings.append(
            f"  Recommended: coalesce({node_label.lower()}.{property_name}, {repr(default)})"
        )

    if warnings and raise_on_error:
        raise SchemaValidationError("\n".join(warnings))

    return warnings


def suggest_coalesce_pattern(node_label: str, property_name: str) -> str:
    """
    Generate coalesce() pattern for a nullable property.

    Args:
        node_label: Node label
        property_name: Property name

    Returns:
        Cypher coalesce() pattern string

    Example:
        >>> suggest_coalesce_pattern("Company", "industry")
        "coalesce(company.industry, '')"
    """
    default = get_property_default(node_label, property_name)
    var_name = node_label.lower()

    if default is None:
        return f"coalesce({var_name}.{property_name}, null)"
    elif isinstance(default, str):
        return f"coalesce({var_name}.{property_name}, {repr(default)})"
    else:
        return f"coalesce({var_name}.{property_name}, {default})"


def get_coverage_warning_message(rel_name: str) -> str | None:
    """
    Get warning message for sparse relationship, or None if not sparse.

    Args:
        rel_name: Relationship name

    Returns:
        Warning message string, or None if relationship is not sparse

    Example:
        >>> msg = get_coverage_warning_message("HAS_SUPPLIER")
        >>> print(msg)
        Using sparse relationship 'HAS_SUPPLIER' (3.3% coverage).
        Only 176 relationships exist. Results will be limited.
    """
    rel_info = get_relationship_info(rel_name)

    if rel_info is None:
        return f"Unknown relationship '{rel_name}'"

    if rel_info.coverage != Coverage.SPARSE:
        return None

    return (
        f"Using sparse relationship '{rel_name}' ({rel_info.coverage_pct:.1%} coverage). "
        f"Only {rel_info.count:,} relationships exist. Results will be limited."
    )


# =============================================================================
# CYPHER QUERY VALIDATION FOR LLM-GENERATED QUERIES (Phase 3.3)
# =============================================================================
#
# These functions validate Cypher queries generated by Text2CypherRetriever
# to ensure they are safe and schema-compliant before execution.


def extract_node_labels_from_cypher(cypher: str) -> list[str]:
    """
    Extract node labels from Cypher query.

    Matches patterns like:
    - (n:Company)
    - (:BeneficialOwner)
    - (c:Company:Insider)  # Multiple labels
    - (n:Company {ticker: 'MNRO'})

    Args:
        cypher: Cypher query string

    Returns:
        List of unique node labels found in query

    Example:
        >>> extract_node_labels_from_cypher(
        ...     "MATCH (b:BeneficialOwner)-[:CONTROLS]->(c:Company) RETURN c"
        ... )
        ['BeneficialOwner', 'Company']
    """
    # Matches Cypher node patterns: named nodes (c:Company), anonymous nodes,
    # and multi-label combinations. Extracts labels after the colon.
    pattern = r"\(\s*(?:[a-z_]\w*)?\s*:([A-Za-z_][A-Za-z_0-9:]*)"
    matches = re.findall(pattern, cypher)

    # Split on : for multiple labels
    labels = []
    for match in matches:
        labels.extend(match.split(":"))

    # Filter out empty strings and deduplicate
    return list({label for label in labels if label})


def validate_node_labels(labels: list[str]) -> list[str]:
    """
    Validate that node labels exist in schema contract.

    Args:
        labels: List of node labels to validate

    Returns:
        List of invalid (unknown) labels
    """
    known_labels = set(NODES.keys())
    return [label for label in labels if label not in known_labels]


def extract_properties_from_cypher(cypher: str) -> list[tuple[str, str]]:
    """
    Extract property accesses from Cypher query.

    Matches patterns like:
    - n.ticker
    - company.name
    - c.cik
    - n.`property with spaces`

    Args:
        cypher: Cypher query string

    Returns:
        List of (variable, property) tuples

    Example:
        >>> extract_properties_from_cypher("WHERE c.ticker = 'AAPL' AND c.name CONTAINS 'Apple'")
        [('c', 'ticker'), ('c', 'name')]
    """
    # Pattern: variable.property or variable.`property`
    # Match word.word but exclude common keywords like node.id()
    pattern = r"\b([a-z_]\w*)\.([a-zA-Z_][a-zA-Z_0-9]*)\b"
    matches = re.findall(pattern, cypher)

    # Filter out function calls like collect(), count(), etc.
    # These typically appear as word.word() where second word is lowercase
    # Also filter out Neo4j internal properties like .elementId
    filtered = []
    for var, prop in matches:
        # Skip if it looks like a function call (would have parentheses after)
        func_pattern = rf"\b{var}\.{prop}\s*\("
        if re.search(func_pattern, cypher):
            continue
        # Skip Neo4j internal properties
        if prop.startswith("element") or prop in ("id", "labels", "type"):
            continue
        filtered.append((var, prop))

    return filtered


def validate_cypher_read_only_with_explain(
    session,
    cypher: str,
    params: dict | None = None,
) -> tuple[bool, list[str]]:
    """
    Canonical read-only check using Neo4j EXPLAIN.

    This is the recommended approach for validating read-only queries because:
    1. It's Neo4j's official mechanism
    2. Handles all Cypher syntax correctly
    3. No false positives from strings/comments
    4. Works with future Cypher versions

    Args:
        session: Neo4j session
        cypher: Cypher query string
        params: Query parameters (default: None)

    Returns:
        (is_read_only, errors) tuple where:
        - is_read_only: True if query is read-only, False if it contains writes
        - errors: List of error messages (empty if read-only)

    Example:
        >>> with driver.session() as session:
        ...     is_safe, errors = validate_cypher_read_only_with_explain(
        ...         session, "MATCH (n) WHERE n.x = 'CREATE' RETURN n"
        ...     )
        ...     assert is_safe  # No false positive!
    """
    errors = []

    try:
        # Run EXPLAIN to get query plan without executing
        result = session.run(f"EXPLAIN {cypher}", params or {})
        summary = result.consume()

        # Check execution plan for write operators
        plan = summary.plan
        if not plan:
            # No plan means read-only (shouldn't happen but safe)
            return True, []

        write_operators = {
            "CreateNode",
            "CreateRelationship",
            "SetProperty",
            "DeleteNode",
            "DeleteRelationship",
            "Merge",
            "SetLabels",
            "RemoveLabels",
            "DeleteExpression",
            "DetachDelete",
            "SetNodeProperty",
            "SetRelationshipProperty",
        }

        def find_write_ops(plan_node, path: str = "root") -> list[str]:
            """Recursively search plan tree for write operators."""
            found = []
            if plan_node.operator_type in write_operators:
                found.append(f"{plan_node.operator_type} at {path}")
            for i, child in enumerate(plan_node.children):
                found.extend(find_write_ops(child, f"{path}.child[{i}]"))
            return found

        write_ops_found = find_write_ops(plan.root)
        if write_ops_found:
            errors.append(f"Query contains write operations: {', '.join(write_ops_found)}")
            return False, errors

        return True, []

    except Exception as e:
        errors.append(f"EXPLAIN failed: {e}")
        return False, errors  # Safe default: reject if can't verify


def validate_cypher_read_only_static(cypher: str) -> list[str]:
    """
    Static regex-based read-only check (fallback for when no session available).

    WARNING: This has false positives but no false negatives.
    Use validate_cypher_read_only_with_explain() when possible.

    Known false positives:
    - Strings containing keywords: WHERE n.text = 'CREATE'
    - Property names: {CREATE: true}
    - Comments: /*CREATE comment*/

    Rejects queries containing:
    - CREATE
    - MERGE
    - SET
    - DELETE
    - REMOVE
    - DETACH DELETE

    Args:
        cypher: Cypher query string

    Returns:
        List of error messages (empty if read-only)

    Example:
        >>> validate_cypher_read_only_static("MATCH (n) RETURN n")
        []
        >>> validate_cypher_read_only_static("CREATE (n:Company {name: 'Test'})")
        ["Query contains 'CREATE' keyword (may be false positive - use validate_cypher_read_only_with_explain for accurate check)"]
    """
    errors = []

    # Write operation keywords (case-insensitive)
    write_operations = [
        "CREATE",
        "MERGE",
        "DELETE",
        "REMOVE",
        "SET",
        "DETACH",
    ]

    # Normalize query for checking
    upper_cypher = cypher.upper()

    for op in write_operations:
        # Check for whole word match (avoid matching "DELETE" in "DELETED")
        pattern = rf"\b{op}\b"
        if re.search(pattern, upper_cypher):
            errors.append(
                f"Query contains '{op}' keyword (may be false positive - "
                f"use validate_cypher_read_only_with_explain for accurate check)"
            )

    return errors


def validate_cypher_read_only(cypher: str) -> list[str]:
    """
    Validate that Cypher query is read-only (no mutations).

    This is a convenience wrapper that uses the static regex-based check.
    For production use, prefer validate_cypher_read_only_with_explain() which
    uses Neo4j's EXPLAIN mechanism for accurate validation.

    Rejects queries containing:
    - CREATE
    - MERGE
    - SET
    - DELETE
    - REMOVE
    - DETACH DELETE

    Args:
        cypher: Cypher query string

    Returns:
        List of error messages (empty if read-only)

    Example:
        >>> validate_cypher_read_only("MATCH (n) RETURN n")
        []
        >>> validate_cypher_read_only("CREATE (n:Company {name: 'Test'})")
        ["Query contains 'CREATE' keyword (may be false positive - use validate_cypher_read_only_with_explain for accurate check)"]
    """
    return validate_cypher_read_only_static(cypher)


def validate_cypher_has_limit(cypher: str, max_limit: int = 100) -> list[str]:
    """
    Validate that Cypher query has a LIMIT clause within allowed range.

    Args:
        cypher: Cypher query string
        max_limit: Maximum allowed LIMIT value (default: 100)

    Returns:
        List of error messages (empty if valid)

    Example:
        >>> validate_cypher_has_limit("MATCH (n) RETURN n")
        ["Query must have a LIMIT clause"]
        >>> validate_cypher_has_limit("MATCH (n) RETURN n LIMIT 10")
        []
        >>> validate_cypher_has_limit("MATCH (n) RETURN n LIMIT 200", max_limit=100)
        ["LIMIT 200 exceeds maximum allowed (100)"]
    """
    errors = []

    # Check for LIMIT clause
    limit_pattern = r"\bLIMIT\s+(\d+)\b"
    limit_matches = re.findall(limit_pattern, cypher, re.IGNORECASE)

    if not limit_matches:
        errors.append("Query must have a LIMIT clause")
        return errors

    # Check that all LIMIT values are within range
    for limit_str in limit_matches:
        limit_val = int(limit_str)
        if limit_val > max_limit:
            errors.append(f"LIMIT {limit_val} exceeds maximum allowed ({max_limit})")

    return errors


def validate_generated_cypher(
    cypher: str,
    max_limit: int = 100,
    raise_on_error: bool = True,
) -> list[str]:
    """
    Full validation for LLM-generated Cypher queries.

    Combines all validation checks:
    1. Read-only (no CREATE, MERGE, SET, DELETE, REMOVE)
    2. LIMIT clause required and within bounds
    3. Node labels exist in schema
    4. Relationship types exist in schema

    Args:
        cypher: Cypher query string to validate
        max_limit: Maximum allowed LIMIT value (default: 100)
        raise_on_error: If True, raise SchemaValidationError on failure

    Returns:
        List of all error/warning messages

    Raises:
        SchemaValidationError: If raise_on_error=True and validation fails

    Example:
        >>> errors = validate_generated_cypher("MATCH (c:Company) RETURN c LIMIT 10")
        >>> if not errors:
        ...     print("Query is safe to execute")
    """
    all_errors = []

    # 1. Check read-only
    read_only_errors = validate_cypher_read_only(cypher)
    all_errors.extend(read_only_errors)

    # 2. Check LIMIT
    limit_errors = validate_cypher_has_limit(cypher, max_limit)
    all_errors.extend(limit_errors)

    # 3. Check node labels
    labels = extract_node_labels_from_cypher(cypher)
    invalid_labels = validate_node_labels(labels)
    for label in invalid_labels:
        all_errors.append(f"Unknown node label '{label}' - not in schema")

    # 4. Check relationship types (using existing function)
    rel_warnings = validate_cypher_query(cypher, raise_on_error=False)
    # Filter out suggestion lines (they start with spaces)
    rel_errors = [w for w in rel_warnings if not w.startswith(" ")]
    all_errors.extend(rel_errors)

    # Raise if requested
    if all_errors and raise_on_error:
        raise SchemaValidationError("\n".join(all_errors))

    return all_errors
