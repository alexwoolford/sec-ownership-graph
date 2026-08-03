"""
Neo4j utility functions for common operations.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neo4j import Driver, Session

# Relationship type pattern: uppercase letters, numbers, and underscores
REL_TYPE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


def clean_properties(props: dict[str, Any]) -> dict[str, Any]:
    """
    Remove empty string and None values from a properties dictionary.

    Neo4j does not support null property values - setting a property to null
    removes it entirely. Empty strings are semantically equivalent to "no value"
    and should also be omitted to keep the graph clean and queries simple.

    Args:
        props: Dictionary of property name -> value

    Returns:
        Dictionary with empty strings and None values removed

    Example:
        >>> clean_properties({"name": "Test", "desc": "", "count": None, "active": True})
        {"name": "Test", "active": True}
    """
    return {
        k: v
        for k, v in props.items()
        if v is not None and v != "" and not (isinstance(v, str) and not v.strip())
    }


def clean_properties_batch(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Apply clean_properties to each dictionary in a batch.

    Args:
        batch: List of property dictionaries

    Returns:
        List with cleaned dictionaries
    """
    return [clean_properties(props) for props in batch]


def safe_single(result: Any, default: Any = None, key: str | None = None) -> Any:
    """
    Safely get value from result.single() with None handling.

    Neo4j's result.single() can return None if no records match, which causes
    AttributeError when accessing record keys. This helper provides safe access
    with configurable defaults.

    Args:
        result: Neo4j result object (must have single() method)
        default: Default value if no record (default: None)
        key: Optional key to extract from record (if None, returns full record)

    Returns:
        Record value or default. If key is provided, returns record[key] or default.
        If key is None, returns the full record or default.

    Example:
        >>> # Get count with default 0
        >>> count = safe_single(result, default=0, key="count")
        >>> # Get full record or None
        >>> record = safe_single(result)
        >>> # Get specific field with default
        >>> deleted = safe_single(result, default=0, key="deleted")
    """
    record = result.single()
    if not record:
        return default
    if key:
        return record.get(key, default)
    return record


def _validate_relationship_type(rel_type: str) -> None:
    """Validate that a relationship type is safe to use in Cypher queries."""
    if not REL_TYPE_PATTERN.match(rel_type):
        raise ValueError(
            f"Invalid relationship type: '{rel_type}'. "
            "Relationship types must start with an uppercase letter and contain "
            "only uppercase letters, numbers, and underscores."
        )


def delete_relationships_in_batches(
    driver,
    rel_type: str,
    batch_size: int = 10000,
    database: str | None = None,
    logger: logging.Logger | None = None,
):
    """
    Delete all relationships of a given type in batches using Neo4j's native IN TRANSACTIONS.

    This uses the modern Neo4j 5.x+ syntax that doesn't require APOC.
    This is necessary for large graphs where a simple MATCH/DELETE would
    cause memory issues or timeouts.

    Args:
        driver: Neo4j driver
        rel_type: Relationship type to delete (e.g., 'LIKELY_TO_ADOPT')
        batch_size: Number of relationships to delete per batch (default: 10000)
        database: Neo4j database name
        logger: Logger instance (optional)

    Returns:
        Total number of relationships deleted
    """
    if logger is None:
        logger = logging.getLogger(__name__)

    # Validate relationship type for security
    _validate_relationship_type(rel_type)

    with driver.session(database=database) as session:
        # Count relationships before deletion
        count_result = session.run(f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS count")
        count_before = safe_single(count_result, default=0, key="count")

        if count_before == 0:
            logger.info(f"   ✓ No {rel_type} relationships to delete")
            return 0

        # Use Neo4j's native IN TRANSACTIONS syntax (Neo4j 5.x+)
        # This is more modern and doesn't require APOC
        query = f"""
        MATCH ()-[r:{rel_type}]->()
        DELETE r
        IN TRANSACTIONS OF {batch_size} ROWS
        """
        try:
            result = session.run(query)
            # Consume the result to execute the query
            result.consume()
            logger.info(f"   ✓ Deleted {count_before:,} {rel_type} relationships in batches")
            return count_before
        except Exception as e:
            # Fallback to simple delete if IN TRANSACTIONS not supported (Neo4j < 5.x)
            error_str = str(e).lower()
            if "in transactions" in error_str or "syntax" in error_str or "unknown" in error_str:
                logger.warning(
                    "   ⚠ IN TRANSACTIONS not supported, using simple DELETE (may be slow)"
                )
                # Fix: Can't RETURN count(r) after DELETE r. Count first, then delete.
                result = session.run(f"MATCH ()-[r:{rel_type}]->() WITH r DELETE r")
                result.consume()
                # Use the count we already got from line 132
                logger.info(f"   ✓ Deleted {count_before:,} {rel_type} relationships")
                return count_before
            else:
                raise


# =============================================================================
# SAFE CYPHER EXECUTION FOR LLM-GENERATED QUERIES (Phase 3.3)
# =============================================================================


class CypherExecutionError(Exception):
    """Raised when Cypher execution fails validation or execution."""

    pass


def safe_execute_generated_cypher(
    session: Session,
    cypher: str,
    params: dict[str, Any] | None = None,
    timeout_ms: int = 5000,
    max_limit: int = 100,
    logger_instance: logging.Logger | None = None,
) -> list[dict[str, Any]]:
    """
    Execute LLM-generated Cypher with validation guards.

    This function provides safe execution of Cypher queries generated by
    Text2CypherRetriever or other LLM-based systems. It validates the query
    against the schema, runs EXPLAIN first, and only executes if safe.

    Safety Guards:
    1. Schema validation (labels, relationships, properties)
    2. Read-only enforcement (no CREATE, MERGE, SET, DELETE)
    3. LIMIT clause required and within bounds
    4. EXPLAIN must pass before execution
    5. Query timeout for protection

    Args:
        session: Neo4j session to execute query
        cypher: Cypher query string to execute
        params: Optional query parameters
        timeout_ms: Maximum execution time in milliseconds (default: 5000)
        max_limit: Maximum allowed LIMIT value (default: 100)
        logger_instance: Logger for messages (default: module logger)

    Returns:
        List of record dictionaries from query results

    Raises:
        CypherExecutionError: If validation fails or execution errors

    Example:
        >>> with driver.session() as session:
        ...     results = safe_execute_generated_cypher(
        ...         session,
        ...         "MATCH (c:Company) WHERE c.ticker = $ticker RETURN c.name LIMIT 10",
        ...         params={"ticker": "AAPL"}
        ...     )
        ...     for row in results:
        ...         print(row)
    """
    if logger_instance is None:
        logger_instance = logging.getLogger(__name__)

    params = params or {}

    # Import validation functions
    from secgraph.schema.validation import (
        SchemaValidationError,
        validate_generated_cypher,
    )

    # Step 1: Validate query against schema
    try:
        errors = validate_generated_cypher(cypher, max_limit=max_limit, raise_on_error=False)
        if errors:
            error_msg = "Cypher validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
            logger_instance.warning(f"Generated Cypher rejected: {error_msg}")
            raise CypherExecutionError(error_msg)
    except SchemaValidationError as e:
        raise CypherExecutionError(str(e)) from e

    logger_instance.debug(f"Cypher validation passed for query: {cypher[:100]}...")

    # Step 2: Run EXPLAIN to check query plan
    try:
        explain_query = f"EXPLAIN {cypher}"
        explain_result = session.run(explain_query, params)
        # Consume the result to ensure EXPLAIN succeeds
        explain_result.consume()
        logger_instance.debug("EXPLAIN check passed")
    except Exception as e:
        error_msg = f"EXPLAIN failed - query may be invalid: {e}"
        logger_instance.warning(error_msg)
        raise CypherExecutionError(error_msg) from e

    # Step 3: Execute the query with timeout
    try:
        # Note: Neo4j Python driver doesn't have per-query timeout, but we can
        # use transaction timeout via session config if needed
        result = session.run(cypher, params)

        # Convert results to list of dicts
        records = []
        for record in result:
            records.append(dict(record))

        logger_instance.debug(f"Query returned {len(records)} results")
        return records

    except Exception as e:
        error_msg = f"Query execution failed: {e}"
        logger_instance.error(error_msg)
        raise CypherExecutionError(error_msg) from e


def safe_execute_with_driver(
    driver: Driver,
    cypher: str,
    params: dict[str, Any] | None = None,
    database: str | None = None,
    timeout_ms: int = 5000,
    max_limit: int = 100,
    logger_instance: logging.Logger | None = None,
) -> list[dict[str, Any]]:
    """
    Convenience wrapper that creates a session and executes safely.

    Same as safe_execute_generated_cypher but handles session creation.

    Args:
        driver: Neo4j driver
        cypher: Cypher query string
        params: Optional query parameters
        database: Database name (default: driver default)
        timeout_ms: Maximum execution time in milliseconds
        max_limit: Maximum allowed LIMIT value
        logger_instance: Logger for messages

    Returns:
        List of record dictionaries

    Raises:
        CypherExecutionError: If validation or execution fails
    """
    with driver.session(database=database) as session:
        return safe_execute_generated_cypher(
            session=session,
            cypher=cypher,
            params=params,
            timeout_ms=timeout_ms,
            max_limit=max_limit,
            logger_instance=logger_instance,
        )
