#!/usr/bin/env python3
"""Serve the curated read-only ownership MCP server over ``secgraph`` (stdio transport).

Thin wrapper: build the Neo4j driver via the standard accessors, hand it to
:func:`secgraph.mcp.create_ownership_mcp_server`, and run the FastMCP stdio
loop. Curated tools only — no raw Cypher, no write path. Neo4j credentials come from the
existing ``get_settings()`` accessors (``.env`` / shell env); nothing is logged or echoed.

Run (defaults to the ``secgraph`` database):
  python scripts/serve_ownership_mcp.py
  python scripts/serve_ownership_mcp.py --database secgraph

Drop into an MCP client (e.g. Claude Desktop) with a ``.mcp.json`` entry:
  {
    "mcpServers": {
      "secgraph-ownership": {
        "command": "python",
        "args": ["scripts/serve_ownership_mcp.py", "--database", "secgraph"]
      }
    }
  }
"""

from __future__ import annotations

import argparse
import logging
import sys

from secgraph.cli import get_driver_and_database
from secgraph.mcp import create_ownership_mcp_server


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the curated ownership MCP server")
    parser.add_argument(
        "--database",
        default="secgraph",
        help="Neo4j database to serve (default: secgraph).",
    )
    args = parser.parse_args()

    # Log to stderr ONLY — stdout is the MCP stdio channel and must carry protocol frames.
    # (The shared setup_logging dry-run path uses basicConfig(stream=stdout), which would
    # corrupt the protocol, so we configure logging directly here.)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr, force=True)
    logger = logging.getLogger("serve_ownership_mcp")
    driver, database = get_driver_and_database(logger, database=args.database)
    server = create_ownership_mcp_server(driver, database=database, read_only=True)
    logger.info("Serving curated ownership MCP tools over database '%s' (stdio).", database)
    try:
        server.run()  # stdio transport by default
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
