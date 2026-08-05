"""
Neo4j connection management.

Provides utilities for creating and managing Neo4j driver connections.
Implements singleton pattern with automatic cleanup to prevent resource leaks.
"""

import atexit
import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from neo4j import Driver

# Neo4j is a required dependency - import directly
from neo4j import GraphDatabase

from secgraph.core.config.settings import (
    get_neo4j_database,
    get_neo4j_password,
    get_neo4j_uri,
    get_neo4j_user,
)

logger = logging.getLogger(__name__)

# Global driver instance (singleton pattern)
_global_driver: "Driver | None" = None
_global_driver_uri: str | None = None


def _cleanup_global_driver() -> None:
    """
    Clean up global driver on program exit.

    This is a safety net registered with atexit to ensure the driver
    is always closed, even if the user forgot to call close() explicitly.
    """
    global _global_driver
    if _global_driver is not None:
        try:
            logger.debug("Closing global Neo4j driver (atexit handler)")
            _global_driver.close()
            _global_driver = None
        except Exception as e:
            logger.warning(f"Error closing global Neo4j driver: {e}")


# Register cleanup handler
atexit.register(_cleanup_global_driver)


def get_neo4j_driver(database: str | None = None, use_singleton: bool = True) -> "Driver":
    """
    Get Neo4j driver connection.

    By default, returns a singleton driver instance that's automatically cleaned up
    on program exit. Set use_singleton=False to create a new driver instance that
    must be manually closed.

    Args:
        database: Optional database name override (only used for logging)
        use_singleton: If True (default), return singleton instance with automatic
                      cleanup. If False, create new instance that must be manually closed.

    Returns:
        Neo4j driver instance

    Raises:
        ValueError: If NEO4J_PASSWORD is not set

    Example:
        # Singleton pattern (recommended) - automatic cleanup
        driver = get_neo4j_driver()
        # Use driver...
        # No need to call driver.close() - handled by atexit

        # Manual pattern - requires explicit cleanup
        driver = get_neo4j_driver(use_singleton=False)
        try:
            # Use driver...
        finally:
            driver.close()

        # Context manager pattern
        with neo4j_driver_context() as driver:
            # Use driver...
            # Automatically closed on exit
    """
    global _global_driver, _global_driver_uri

    uri = get_neo4j_uri()
    user = get_neo4j_user()
    password = get_neo4j_password()
    db = database or get_neo4j_database()

    # Singleton mode (default)
    if use_singleton:
        # Create global driver if needed or if connection params changed
        if _global_driver is None or _global_driver_uri != uri:
            logger.debug(f"Creating singleton Neo4j driver for {uri} (database: {db})")
            if _global_driver is not None:
                # Close old driver if URI changed
                _global_driver.close()
            _global_driver = GraphDatabase.driver(uri, auth=(user, password))
            _global_driver_uri = uri
        else:
            logger.debug(f"Reusing singleton Neo4j driver (database: {db})")
        return _global_driver

    # Non-singleton mode: create new driver instance
    logger.debug(f"Creating new Neo4j driver instance for {uri} (database: {db})")
    driver = GraphDatabase.driver(uri, auth=(user, password))
    return driver


@contextmanager
def neo4j_driver_context(database: str | None = None):
    """
    Context manager for Neo4j driver that ensures automatic cleanup.

    This is useful when you want explicit control over driver lifecycle
    without using the singleton pattern.

    Args:
        database: Optional database name override

    Yields:
        Neo4j driver instance

    Example:
        with neo4j_driver_context() as driver:
            with driver.session() as session:
                result = session.run("RETURN 1")
        # Driver is automatically closed here
    """
    driver = get_neo4j_driver(database=database, use_singleton=False)
    try:
        yield driver
    finally:
        driver.close()


def close_global_driver() -> None:
    """
    Explicitly close the global singleton driver.

    This is usually not necessary as the driver will be automatically closed
    on program exit via atexit. However, this can be useful in tests or when
    you want to force a reconnection.
    """
    global _global_driver, _global_driver_uri
    if _global_driver is not None:
        logger.debug("Closing global Neo4j driver (explicit call)")
        _global_driver.close()
        _global_driver = None
        _global_driver_uri = None


def verify_connection(driver: "Driver", database: str | None = None) -> bool:
    """
    Verify Neo4j connection is working.

    The probe query depends on the target: ``system`` rejects ordinary Cypher with "This Cypher
    command can only be executed in a user database", so it is probed with ``SHOW DATABASES``
    instead of ``RETURN 1``. Phase 0 needs the ``system`` path because it runs before the target
    database exists.

    Args:
        driver: Neo4j driver instance
        database: Optional database name (uses configured default if not provided)

    Returns:
        True if connection is valid, False otherwise
    """
    if database is None:
        database = get_neo4j_database()
    probe = "SHOW DATABASES YIELD name RETURN count(*) AS n" if database == "system" else "RETURN 1"
    try:
        with driver.session(database=database) as session:
            session.run(probe).consume()
        return True
    except Exception as e:
        logger.error(f"Neo4j connection verification failed: {e}")
        return False
