#!/usr/bin/env python3
"""Smoke-test the curated ownership MCP surface against a live ``secgraph`` database.

Read-only. Asserts:

1. ``create_ownership_mcp_server`` exposes the seven curated tool names.
2. Demo engines answer without unexpected abstention:
   ``activist_convergence(since=2023-01-01)``, ``campaign_timeline(MNRO)``,
   ``activist_coalition(ICAHN CARL C)``.

Does not speak MCP stdio — use this before wiring a client, or as ``make smoke-mcp``.

Run:
  python scripts/smoke_ownership_mcp.py --database secgraph
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from secgraph.cli import (
    add_database_argument,
    get_driver_and_database,
    setup_logging,
    verify_neo4j_connection,
)
from secgraph.ingestion.ownership.campaign_timeline import CampaignTimelineEngine
from secgraph.ingestion.ownership.intelligence import OwnershipIntelligenceEngine
from secgraph.mcp import create_ownership_mcp_server

_EXPECTED_TOOLS = frozenset(
    {
        "activist_convergence",
        "campaign_timeline",
        "control_chain",
        "board_interlock_path",
        "activist_coalition",
        "ownership_snapshot",
        "get_secgraph_schema",
    }
)


def _check_tool_catalog(driver, database: str, log: logging.Logger) -> None:
    server = create_ownership_mcp_server(driver, database=database, read_only=True)
    tools = asyncio.run(server.get_tools())
    names = frozenset(tools.keys())
    if names != _EXPECTED_TOOLS:
        missing = sorted(_EXPECTED_TOOLS - names)
        extra = sorted(names - _EXPECTED_TOOLS)
        raise SystemExit(f"tool catalog mismatch: missing={missing} extra={extra}")
    log.info("tool catalog OK (%d tools)", len(names))


def _check_demo_queries(driver, database: str, log: logging.Logger) -> None:
    timing = CampaignTimelineEngine(driver, database=database)
    intel = OwnershipIntelligenceEngine(driver, database=database)

    conv = timing.convergence_scan(since="2023-01-01")
    if conv.abstained:
        raise SystemExit(f"activist_convergence abstained: {conv.result}")
    hits = conv.result.get("targets") or []
    if not hits:
        raise SystemExit("activist_convergence returned no hits since 2023-01-01")
    tickers = {h.get("company", {}).get("ticker") for h in hits}
    if "MNRO" not in tickers:
        raise SystemExit(
            f"expected MNRO in convergence hits; got {sorted(t for t in tickers if t)}"
        )
    log.info("activist_convergence OK (%d issuers, includes MNRO)", len(hits))

    timeline = timing.campaign_timeline("MNRO")
    if timeline.abstained:
        raise SystemExit(f"campaign_timeline(MNRO) abstained: {timeline.result}")
    seq = timeline.result.get("sequence") or {}
    first = (seq.get("first_mover") or {}).get("filer") or ""
    if "GAMCO" not in first.upper():
        raise SystemExit(f"expected GAMCO first mover on MNRO; got {first!r}")
    followers = seq.get("followers") or []
    if not any("ICAHN" in str(f.get("filer", "")).upper() for f in followers):
        raise SystemExit(f"expected Icahn follower on MNRO; followers={followers}")
    log.info(
        "campaign_timeline(MNRO) OK (%d filings; first mover %s)",
        timeline.result.get("filing_count", 0),
        first,
    )

    coalition = intel.coalition("ICAHN CARL C")
    if coalition.abstained:
        raise SystemExit(f"activist_coalition(ICAHN) abstained: {coalition.result}")
    member_count = int(coalition.result.get("member_count") or 0)
    if member_count < 2:
        raise SystemExit(f"expected multi-member Icahn coalition; member_count={member_count}")
    names = " ".join(str(n) for n in (coalition.result.get("members") or [])).upper()
    if "GAMCO" not in names:
        raise SystemExit("expected GAMCO in Icahn coalition members")
    log.info(
        "activist_coalition(ICAHN) OK (%d members, diameter_hops=%s)",
        member_count,
        coalition.result.get("diameter_hops"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test curated ownership MCP + demo queries")
    add_database_argument(parser)
    args = parser.parse_args()

    logger = setup_logging("smoke_ownership_mcp", execute=False)
    driver, database = get_driver_and_database(logger, database=args.database)
    try:
        verify_neo4j_connection(driver, database, logger)
        _check_tool_catalog(driver, database, logger)
        _check_demo_queries(driver, database, logger)
        logger.info("smoke-mcp PASSED against database '%s'", database)
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
