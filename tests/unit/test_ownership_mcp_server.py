"""Unit tests for the curated ownership MCP server construction.

No live DB and no MCP client: asserts the server wires exactly the curated read-only tool
catalog, marks every tool read-only, refuses a writable configuration, and that the envelope
helper carries the engine's structured payload plus a human rendering. Skipped entirely when
the optional ``mcp`` extra (fastmcp) is not installed, so the default CI lane stays green.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastmcp", reason="optional 'mcp' extra (fastmcp) not installed")

from secgraph.ingestion.ownership.intelligence import (  # noqa: E402
    OwnershipIntelligenceResult,
)
from secgraph.mcp.ownership_server import (  # noqa: E402
    _envelope,
    create_ownership_mcp_server,
)

_EXPECTED_TOOLS = {
    "activist_convergence",
    "campaign_timeline",
    "control_chain",
    "board_interlock_path",
    "activist_coalition",
    "ownership_snapshot",
    "get_secgraph_schema",
}

# Tool order shapes which tool an agent reaches for, so the timing questions lead — they are what
# an event-driven desk asks first. (Not because control_chain is weak: it reaches large caps.)
_PRIMARY_TOOLS_FIRST = ["activist_convergence", "campaign_timeline"]


def _tools(server):
    return asyncio.run(server.get_tools())


def test_exposes_exactly_the_curated_catalog():
    server = create_ownership_mcp_server(MagicMock(), database="secgraph", read_only=True)
    assert set(_tools(server).keys()) == _EXPECTED_TOOLS


def test_every_tool_is_read_only():
    server = create_ownership_mcp_server(MagicMock(), read_only=True)
    for name, tool in _tools(server).items():
        ann = tool.annotations
        assert ann is not None, name
        assert ann.readOnlyHint is True, name
        assert ann.destructiveHint is False, name


def test_refuses_writable_configuration():
    with pytest.raises(ValueError, match="read-only"):
        create_ownership_mcp_server(MagicMock(), read_only=False)


def test_timing_tools_are_offered_first():
    server = create_ownership_mcp_server(MagicMock(), read_only=True)
    names = list(_tools(server).keys())
    assert names[: len(_PRIMARY_TOOLS_FIRST)] == _PRIMARY_TOOLS_FIRST


def test_schema_declares_the_material_limits():
    """The honest limits are part of the pitch; a regression here misleads the caller."""
    from secgraph.mcp.ownership_server import _SECGRAPH_SCHEMA

    limits = _SECGRAPH_SCHEMA["honest_limits"]
    # Size is now available but only as a proxy. Each caveat must survive, because an agent
    # that reads "size" without them will over-trust the figure: it is free-float-based, has a
    # ~25% coverage hole, and is not a market cap.
    assert "PROXY" in limits
    assert "free float" in limits
    assert "25%" in limits
    assert "market cap" in limits
    assert "franchise" in limits  # activist screens trade recall for precision
    assert "4 hops" in limits  # interlock existence is not informative


def test_control_chain_is_not_described_as_small_cap_only():
    """Regression guard on an agent-facing correctness bug.

    The tool catalog and the control_chain docstring both used to assert the verified chains were
    "all micro/nano-cap", which told an agent to deprioritize a tool that returns Deutsche
    Telekom's 74.3% of T-Mobile US. 20 of 825 controlled issuers carry >=$10B. The caveat is
    true of the MULTI-HOP pyramids only, and must stay scoped to them.
    """
    from secgraph.mcp.ownership_server import _SECGRAPH_SCHEMA

    catalog = " ".join(_SECGRAPH_SCHEMA["curated_tools"])
    assert "control_chain — PRIMARY" in catalog
    assert "micro/nano-cap issuers, so treat this as a small-cap" not in catalog
    # The surviving caveat must be attached to depth, not to control generally.
    assert "MULTI-HOP" in catalog


def test_envelope_accepts_a_custom_renderer():
    """The campaign-timing engine renders differently but shares the envelope."""
    from secgraph.ingestion.ownership.campaign_timeline import (
        CampaignTimelineEngine,
        CampaignTimelineResult,
    )

    result = CampaignTimelineResult(
        anchor="MONRO, INC.",
        task_type="campaign_timeline",
        abstained=True,
        result={"reason": "no_dated_ownership_filings"},
    )
    env = _envelope(result, None, renderer=CampaignTimelineEngine.format_answer)
    assert env["task_type"] == "campaign_timeline"
    assert "no_dated_ownership_filings" in env["rendered"]


def test_envelope_carries_payload_and_rendering():
    result = OwnershipIntelligenceResult(
        anchor="AAPL → JPM",
        task_type="board_path",
        abstained=False,
        result={
            "chain": [{"cik": "1", "name": "Apple", "ticker": "AAPL"}],
            "via_directors": ["BELL JAMES A"],
            "hops": 1,
        },
        evidence=[],
        metadata={},
    )
    env = _envelope(result, freshness={"as_of": "2026-07-27"})
    assert env["task_type"] == "board_path"
    assert env["abstained"] is False
    assert "rendered" in env and isinstance(env["rendered"], str)
    assert env["as_of"] == "2026-07-27"


def test_envelope_omits_as_of_without_manifest():
    result = OwnershipIntelligenceResult(
        anchor="Foo",
        task_type="control_chain",
        abstained=True,
        result={"reason": "company_not_found"},
        evidence=[],
        metadata={},
    )
    env = _envelope(result, freshness=None)
    assert "as_of" not in env
    assert env["abstained"] is True
