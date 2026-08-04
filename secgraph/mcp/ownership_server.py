"""FastMCP server exposing the ``secgraph`` ownership graph-native wins as curated tools.

Design mirrors the upstream ``mcp-neo4j-cypher`` ``create_mcp_server`` pattern
(``FastMCP`` + ``@mcp.tool`` + ``ToolAnnotations``) but is **curated, not raw text2cypher**:
each tool wraps exactly one read-only
:class:`~secgraph.ingestion.ownership.intelligence.OwnershipIntelligenceEngine`
method. The engine never writes and there is no Cypher passthrough, so the graph cannot be
mutated through this surface regardless of the ``read_only`` flag — the flag is defence in
depth and is asserted, not merely trusted.

Every tool returns the engine's structured dict (``anchor`` / ``task_type`` / ``abstained``
/ ``result`` / ``evidence`` / ``metadata``) plus a ``rendered`` human-readable rendering and,
when available, the ``as_of`` freshness stamp — carrying the GraphRAG evidence/citation
contract onto the MCP surface.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast

from fastmcp.server import FastMCP
from mcp.types import ToolAnnotations

from secgraph.ingestion.ownership.campaign_timeline import (
    DEFAULT_MIN_ACTIVISTS,
    DEFAULT_WINDOW_DAYS,
    CampaignTimelineEngine,
)
from secgraph.ingestion.ownership.intelligence import (
    DEFAULT_MAX_HOPS,
    DEFAULT_MIN_SHARED_TARGETS,
    OwnershipIntelligenceEngine,
)

logger = logging.getLogger("secgraph.mcp.ownership")

# Freshness manifest emitted by the Layer-1 build/refresh orchestrator. Optional: absent on
# a graph built by hand, in which case the "as of" stamp is simply omitted.
_FRESHNESS_MANIFEST = Path("results/secgraph_freshness.json")

# Curated, hand-written schema summary for tool-use grounding. Deliberately NOT the full
# apoc.meta.schema dump — the point of a curated surface is to describe only what these tools
# traverse, in finance terms, so an agent picks the right tool.
_SECGRAPH_SCHEMA: dict[str, Any] = {
    "description": (
        "SEC ownership-relationship graph (secgraph). Read-only. Hard-keyed on CIK. "
        "Answers variable-depth reachability questions that flat SQL cannot express."
    ),
    "nodes": {
        "Company": "A public issuer. Keys: cik. Props: name, ticker.",
        "Insider": "A natural-person director/officer (Form 3/4/5 filer). Keys: cik. Props: name.",
        "BeneficialOwner": "A 13D/13G beneficial owner (activist or passive). Keys: cik. Props: name.",
        "InstitutionalManager": "A 13F filer. Keys: cik. Props: name.",
    },
    "relationships": {
        "BENEFICIAL_OWNER_OF": (
            "(BeneficialOwner)->(Company). Props: filing_type ('13D'|'13G'), "
            "percent_of_class, control_class ('control' when >=50%), accession_number, "
            "filing_date. 13D filing_date is real 1994->present history."
        ),
        "DIRECTOR_OF": "(Insider)->(Company). Keep-latest snapshot over the staged Form 3/4/5 window (NOT a time series).",
        "OFFICER_OF": "(Insider)->(Company). Keep-latest snapshot over the staged Form 3/4/5 window.",
        "SHARES_DIRECTOR": (
            "(Company)-(Company) derived board-interlock edge (undirected, one per pair). "
            "Props: director_count, via_ciks, source, computed_at."
        ),
    },
    "curated_tools": [
        # Ordered by demo value: the timing and coalition questions are what an event-driven "
        # desk actually asks. Control chains and interlock paths are supporting context.
        "activist_convergence — PRIMARY SCREEN: issuers where several known activist "
        "franchises filed 13D within a rolling window ('what is heating up')",
        "campaign_timeline — PRIMARY: every dated 13D/13G filing on one issuer in order, "
        "each filer classified activist/insider/passive-index/custodian; identifies who moved "
        "first and how many days later each follower arrived",
        "activist_coalition — PRIMARY: the custodial-scrubbed co-targeting coalition around a "
        "named activist, with its diameter",
        "ownership_snapshot — SUPPORTING: top holders / insiders / control status for an issuer",
        "control_chain — PRIMARY: transitive >=50% control chains. Works on large caps: 27 of "
        "825 controlled issuers carry >=$10B of institutional ownership (Deutsche Telekom holds "
        "74.3% of T-Mobile US; GE held 62.6% of Baker Hughes). Only the MULTI-HOP pyramids are "
        "small-cap. Results carry institutional_value_usd so they can be ranked by size",
        "board_interlock_path — SUPPORTING: shortest board-interlock path. The *existence* of a "
        "path is near-universal (every well-connected pair links within 4 hops), so the "
        "informative output is the NAMED BRIDGING DIRECTOR, not whether a path exists",
    ],
    "honest_limits": (
        "CIK-only (understates family structure, conservative). No prediction/alpha claims. "
        "Size is a PROXY only: institutional_value_usd sums one quarter of 13F holdings, so it "
        "measures free float (understating concentrated-ownership issuers), is null for ~25% of "
        "companies with no institutional coverage, and includes ETFs. No revenue, assets or true "
        "market cap. Board/insider edges are a keep-latest snapshot; only 13D "
        "filing_date is a real time series. Interlock path existence is not informative at "
        "<=4 hops. Activist screens are gated to a curated franchise list: precision over "
        "recall, so unlisted activists are missed by design. No raw Cypher."
    ),
}


def _load_freshness() -> dict[str, Any] | None:
    try:
        if _FRESHNESS_MANIFEST.is_file():
            return cast("dict[str, Any]", json.loads(_FRESHNESS_MANIFEST.read_text()))
    except (OSError, ValueError) as exc:  # pragma: no cover - manifest is best-effort
        logger.warning("could not read freshness manifest: %s", exc)
    return None


def _envelope(result, freshness: dict[str, Any] | None, renderer=None) -> dict[str, Any]:
    """Wrap an engine result in the served envelope: structured payload + rendering + as_of.

    ``renderer`` defaults to the ownership engine's ``format_answer``; the campaign-timing engine
    passes its own, since both result types share the same serialized shape but render differently.
    """
    payload: dict[str, Any] = result.to_dict()
    render = renderer or OwnershipIntelligenceEngine.format_answer
    payload["rendered"] = render(result)
    if freshness:
        payload["as_of"] = freshness.get("as_of") or freshness.get("generated_at")
    return payload


def create_ownership_mcp_server(
    driver,
    database: str = "secgraph",
    read_only: bool = True,
) -> FastMCP:
    """Build the curated read-only ownership MCP server over ``secgraph``.

    ``driver`` is a Neo4j driver (sync). ``database`` scopes every session. ``read_only`` is
    asserted True: this surface exposes no write path, and we refuse to construct a writable
    one to avoid a false sense of a mutation capability that does not exist here.
    """
    if not read_only:
        raise ValueError(
            "create_ownership_mcp_server exposes only read-only curated tools; "
            "read_only=False is not supported (there is no write path)."
        )

    engine = OwnershipIntelligenceEngine(driver, database=database)
    timing = CampaignTimelineEngine(driver, database=database)
    freshness = _load_freshness()
    mcp: FastMCP = FastMCP("secgraph-ownership")

    _read_only_annotations = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )

    def _timed(result) -> dict[str, Any]:
        return _envelope(result, freshness, renderer=CampaignTimelineEngine.format_answer)

    @mcp.tool(
        name="influence_map",
        annotations=_read_only_annotations,
    )
    def influence_map(
        min_tier: int = 25,
        min_value_usd: float = 1e9,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Issuers where a holder has a big stake AND currently sits on the board.

        The strongest single output here, and the best place to start a governance or
        counterparty question. It is the two-limb test from 12 CFR 225.2(e) — the Federal
        Reserve's control presumptions — and its force comes from the **conjunction of two
        independent filing types**: a Schedule 13D on one side, Form 3/4/5 board activity on the
        other. A screener can give you either list; the pairing is the finding.

        Requiring a *current* board seat also fixes a freshness problem. 13D carries no exit
        obligation below 5%, so half the stakes on file predate 2020 and are last-known rather
        than current — but board activity runs to the present, so a recent seat corroborates an
        old declaration. Liberty Broadband's 26.1% of Charter was declared in 2014; its director
        was seen in 2026.

        ``min_tier`` is a presumption tier (10/15/25/50). ``min_value_usd`` filters on the 13F
        size proxy so results are recognizable names rather than nano-caps.

        Caveat to state if asked: percent_of_class is percent of the class covered by the filing,
        NOT voting power — and several of the largest names are dual-class (Berkshire, the
        Liberty complex, Carvana, Sea), where economic and voting stakes diverge.

        Example: influence_map() returns Buffett/Berkshire 37% (seat 2026-05), Liberty
        Broadband/Charter 26.1% (seat 2026-06), Huffman/Reddit 61.5% (seat 2026-06).
        """
        return _envelope(
            engine.influence_map(
                min_tier=min_tier, min_value_usd=min_value_usd, limit=limit
            ),
            freshness,
        )

    @mcp.tool(
        name="activist_convergence",
        annotations=_read_only_annotations,
    )
    def activist_convergence(
        since: str | None = None,
        min_activists: int = DEFAULT_MIN_ACTIVISTS,
        window_days: int = DEFAULT_WINDOW_DAYS,
    ) -> dict[str, Any]:
        """Screen for issuers where several known activist franchises filed 13D close together.

        The "what is heating up" screen, and the best starting point for an event-driven
        question. ``since`` is an ISO date (e.g. '2025-01-01'). Returns each issuer with the
        franchises involved, the span in days, and the filing sequence with accession numbers.

        Gated to a curated list of recognised activist franchises (Icahn, GAMCO/Gabelli, Saba,
        Bulldog, Karpus, Elliott, Starboard, RA Capital, OrbiMed, ...) — precision over recall.
        Without that gate the output is dominated by micro-cap founders crossing 5% and by
        filing-group artifacts where one manager files through several affiliated entities.

        Example: activist_convergence(since='2023-01-01') surfaces MNRO (GAMCO then Icahn, 96
        days apart) and SION (OrbiMed then RA Capital, 5 days apart).
        """
        return _timed(
            timing.convergence_scan(
                since=since, min_activists=min_activists, window_days=window_days
            )
        )

    @mcp.tool(
        name="campaign_timeline",
        annotations=_read_only_annotations,
    )
    def campaign_timeline(company: str, since: str | None = None) -> dict[str, Any]:
        """Show who moved first on an issuer, who followed, and who is merely index money.

        Returns every dated 13D/13G filing on the issuer in chronological order, each filer
        classified as ``activist`` / ``insider_or_other`` / ``passive_index`` / ``custodian``,
        with percent_of_class and the accession number. Also reports the first mover and, for
        each follower, how many days later they arrived. Abstains when the issuer has no dated
        ownership filing.

        Only 13D/13G filing dates are used — they are the one trustworthy time series in this
        graph (board and 13F layers are snapshots).

        Example: campaign_timeline("MNRO") shows GAMCO at 4.0% on 2025-08-01, then
        ICAHN CARL C at 14.79% exactly 96 days later.
        """
        return _timed(timing.campaign_timeline(company, since=since))

    @mcp.tool(
        name="control_chain",
        annotations=_read_only_annotations,
    )
    def control_chain(
        company: str,
        direction: str = "up",
        max_hops: int = DEFAULT_MAX_HOPS,
    ) -> dict[str, Any]:
        """Trace the transitive >=50% ownership control chain through a public issuer.

        ``direction='up'`` = who ultimately controls this issuer; ``'down'`` = what this
        issuer controls. Each step carries percent_of_class and the 13D accession number as
        citation. Abstains (abstained=True) when the issuer has no verified control edge —
        never fabricates a chain from sub-50% or unclassified stakes.

        **Scope, precisely.** Single-hop control reaches large caps: 20 of 825 controlled
        issuers carry >=$10B of institutional ownership and 99 carry >=$1B. It is the
        MULTI-HOP pyramids that are small-cap — those top out around $1.5B — so treat chain
        *depth* as a small-cap governance signal while single-hop control is general-purpose.
        Most large caps still have no >=50% holder and correctly abstain.

        Each step carries institutional_value_usd (a 13F size proxy, null for the ~25% of
        issuers with no institutional coverage) so results can be ranked by materiality.

        Examples: control_chain("TMUS") returns Deutsche Telekom -> T-Mobile US (74.3%, $95B).
        control_chain("Income Opportunity Realty") returns the 3-hop pyramid
        Basic Capital -> American Realty (62%) -> Transcontinental (83%) -> Income Opportunity (85%).
        """
        return _envelope(
            engine.control_chain(company, direction=direction, max_hops=max_hops), freshness
        )

    @mcp.tool(
        name="board_interlock_path",
        annotations=_read_only_annotations,
    )
    def board_interlock_path(
        from_company: str,
        to_company: str,
        max_hops: int = DEFAULT_MAX_HOPS,
    ) -> dict[str, Any]:
        """Name the director(s) who bridge two companies' boards.

        Walks the persisted SHARES_DIRECTOR edge, returning the alternating company chain and
        the bridging director(s) per hop. Abstains when no shared-director path exists within
        max_hops.

        **Read the bridging director, not the path's existence.** In this dataset every
        well-connected pair of companies is linked within 4 hops (measured), so "are these
        boards connected?" is essentially always yes and carries no information. The useful
        output is *who* the named connector is, and how short the link is.

        Example: board_interlock_path("AAPL", "JPM") -> AAPL — JPM via BELL JAMES A (1 hop).
        """
        return _envelope(engine.board_path(from_company, to_company, max_hops=max_hops), freshness)

    @mcp.tool(
        name="activist_coalition",
        annotations=_read_only_annotations,
    )
    def activist_coalition(
        activist: str,
        min_shared_targets: int = DEFAULT_MIN_SHARED_TARGETS,
    ) -> dict[str, Any]:
        """Find the de-facto activist coalition (wolf-pack) around a named 13D filer.

        Two activists are linked when they co-target >=min_shared_targets of the same issuers
        via 13D. Custodial/broker hubs are scrubbed before the connected component is formed
        (precision discipline), so the coalition reflects coordinated activists, not shared
        custodians. Returns members + coalition diameter. Abstains when the filer sits in no
        multi-member coalition (or was scrubbed as a custodial hub).

        Example: activist_coalition("ICAHN CARL C") -> the ~22-member scrubbed cluster
        (Bulldog/Goldstein, GAMCO/Gabelli, Karpus, Icahn, Dolan family).
        """
        return _envelope(
            engine.coalition(activist, min_shared_targets=min_shared_targets), freshness
        )

    @mcp.tool(
        name="ownership_snapshot",
        annotations=_read_only_annotations,
    )
    def ownership_snapshot(company: str, top_n: int = 10) -> dict[str, Any]:
        """Supporting ownership context for one issuer: top holders, board size, control status.

        Grounds the headline chain/path/coalition answers with the surrounding facts — the
        largest 13D/13G beneficial owners (with percent_of_class), director/officer counts,
        and whether any verified >=50% control edge exists. Abstains only if the company can't
        be resolved.
        """
        return _envelope(engine.ownership_snapshot(company, top_n=top_n), freshness)

    @mcp.tool(
        name="get_secgraph_schema",
        annotations=_read_only_annotations,
    )
    def get_secgraph_schema() -> dict[str, Any]:
        """Return the curated secgraph ownership schema and tool catalog for grounding.

        A hand-curated summary (nodes, relationship semantics, temporal-trust caveats, and
        the curated tool catalog) — not a raw graph dump — so an agent can pick the right
        tool. Includes the honest limits (CIK-only, snapshot vs dated layers, no prediction).
        """
        out = dict(_SECGRAPH_SCHEMA)
        out["database"] = database
        if freshness:
            out["as_of"] = freshness.get("as_of") or freshness.get("generated_at")
            out["layer_freshness"] = freshness.get("layers")
        return out

    return mcp
