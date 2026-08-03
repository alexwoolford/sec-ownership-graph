"""
GDS utility functions.

Provides helper functions for Graph Data Science operations.
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def safe_drop_graph(gds, graph_name: str) -> bool:
    """
    Safely drop a graph projection if it exists.

    Args:
        gds: GDS client instance
        graph_name: Name of the graph to drop

    Returns:
        True if graph was dropped, False if it didn't exist
    """
    try:
        gds.graph.drop(graph_name)
        return True
    except Exception:
        # Graph doesn't exist or couldn't be dropped - that's fine
        return False


def cleanup_leftover_graphs(
    gds, database: str | None = None, logger: logging.Logger | None = None
) -> None:
    """
    Drop any leftover graph projections from previous runs.

    Args:
        gds: GDS client instance
        database: Database name suffix to match
        logger: Optional logger instance
    """
    if logger is None:
        logger = logging.getLogger(__name__)

    try:
        graph_list = gds.graph.list()
        if hasattr(graph_list, "graphName"):
            if isinstance(graph_list, pd.DataFrame):
                for _, row in graph_list.iterrows():
                    graph_name = row["graphName"]
                    if database and graph_name.endswith(f"_{database}"):
                        safe_drop_graph(gds, graph_name)
            else:
                for graph_name in graph_list:
                    if database and graph_name.endswith(f"_{database}"):
                        safe_drop_graph(gds, graph_name)
    except Exception as e:
        logger.warning(f"⚠ Warning: Could not clean up leftover graphs: {e}")


def get_gds_client(driver, database: str | None = None):
    """
    Get GraphDataScience client connection from existing driver.

    Args:
        driver: Neo4j driver instance (already created)
        database: Database name

    Returns:
        GraphDataScience client instance

    Raises:
        ImportError: If graphdatascience is not installed
    """
    try:
        from graphdatascience import GraphDataScience
    except ImportError as err:
        raise ImportError(
            "graphdatascience not available. Install with: pip install graphdatascience"
        ) from err

    from secgraph.core.config.settings import (
        get_neo4j_password,
        get_neo4j_uri,
        get_neo4j_user,
    )

    uri = get_neo4j_uri()
    user = get_neo4j_user()
    password = get_neo4j_password()
    gds = GraphDataScience(uri, auth=(user, password), database=database)
    return gds
