"""Curated read-only MCP surface over the ``secgraph`` ownership graph.

Exposes the three *proven* graph-native wins (control chain / board-interlock path /
activist coalition) — plus supporting ``ownership_snapshot`` and ``get_secgraph_schema``
tools — as curated FastMCP tools that finance professionals can call from Claude Desktop
or any MCP-speaking agent. Each tool wraps one
:class:`~secgraph.ingestion.ownership.intelligence.OwnershipIntelligenceEngine`
method: read-only by construction, evidence-returning, abstain-on-no-support. There is no
raw Cypher passthrough — the graph cannot be mutated through this surface.
"""

from __future__ import annotations

from secgraph.mcp.ownership_server import create_ownership_mcp_server

__all__ = ["create_ownership_mcp_server"]
