"""
Board-interlock intelligence — the ownership graph's defensible graph-native win.

The density gate (``density.py``) proved multi-hop board interlocks exist at full
scale (57% of the universe in one connected network through ~7,400 human
directors). This module turns that connectivity into *intelligence* a finance
professional can act on — three things no single SEC filing and no SQL ``GROUP BY``
can produce:

1. **Systemic boardroom hubs** (GDS betweenness) — the companies whose boards
   *bridge* otherwise-separate corners of corporate America. High betweenness = a
   node that many shortest interlock paths run through: an information-contagion and
   governance-concentration signal. These are ranked, not extrapolated.
2. **"Six degrees of the boardroom" chains** (variable-depth ``shortestPath``) —
   concrete director-by-director paths between two companies that share *no* direct
   director. This is the canonical graph-only result: not a fixed join, not
   recoverable from any text embedding (director identity is CIK-keyed structured
   data), and genuinely relevant to conflict/independence analysis.
3. **Boardroom communities** (GDS Louvain) — clusters of companies bound tightly
   together by overlapping boards.

Why this layer and not the 13F/common-ownership layer: the interlock graph is a
*complete* bulk-dataset join (Form 3/4/5 → CIK-keyed insiders), so it has no
coverage hole. The 13F layer depends on a lossy CUSIP→CIK crosswalk (measured ~49%
of issuers with zero holders), which would make any common-ownership claim
misleading. We build on the layer that is trustworthy today.

The fund-entity scrub (``_HUMAN_DIRECTOR_FILTER``) is essential: closed-end fund
families (BlackRock's BST/BCAT/ECAT…) and investment advisors (RA Capital, Baker
Bros) file as "directors" of their portfolio/fund companies, which would otherwise
manufacture giant spurious hubs. We keep only *human* directors so an interlock
means two operating boards genuinely share a person.

READ-ONLY: uses GDS *stream* mode (no writes) and read-only Cypher. Nothing is
mutated in Neo4j — safe to run against a production database.
"""

from __future__ import annotations

import logging
from typing import Any

from secgraph.gds.utils import get_gds_client, safe_drop_graph

logger = logging.getLogger(__name__)

# Two scrubs are needed to make an "interlock" mean two *operating* boards sharing a
# real person — validated during probing, where their absence produced an
# embarrassing list (one director "on 36 boards" that were all one BlackRock
# closed-end-fund complex).
#
# 1. Entity filers: funds, LPs, advisors and holding companies file Form 3/4 as
#    "directors" of the companies they control. Scrubbed on the *insider* name.
#    "L.P." is handled separately from the word-boundary tokens because a trailing
#    "L.P." ends in a period, which has no `\b` after it — the naive `\bL\.?P\.?\b`
#    silently failed to catch "667, L.P." and "Baker Brothers ... LP".
_FUND_ENTITY_WORD_TOKENS = (
    r"LLC|INC|CORP|CAPITAL|ADVISORS?|ADVISERS?|FUND|PARTNERS|MANAGEMENT|"
    r"HOLDINGS|TRUST|GROUP|VENTURES|ASSOCIATES|MASTER|SICAV|SPC"
)
_FUND_ENTITY_REGEX = (
    rf"(?i)(.*\\b({_FUND_ENTITY_WORD_TOKENS})\\b.*|.*\\bL\\.?P\\.?\\s*$|.*,\\s*L\\.?P\\.?.*)"
)
_HUMAN_DIRECTOR_FILTER = f"NOT (i.name =~ '{_FUND_ENTITY_REGEX}')"

# 2. Investment vehicles as *companies*: closed-end funds, municipal-income trusts
#    and ETF families (PIMCO/Nuveen/BlackRock/Gabelli…) are SEC filers with boards,
#    but a shared trustee across a fund family is not a corporate interlock. ~5% of
#    the universe. Scrubbed on the *company* name; the filter is parameterized by the
#    node variable so it can be applied to a/b/n alike.
_FUND_COMPANY_TOKENS = (
    r"\bFUND\b|INCOME|MUNICIPAL|\bMUNI\b|DIVIDEND|YIELD|CLOSED.?END|TOTAL RETURN|"
    r"OPPORTUNIT|\bETF\b|NUVEEN|GABELLI|EATON VANCE|PIMCO DYNAMIC|BLACKROCK.*TRUST"
)


def _not_fund_company(var: str) -> str:
    """Predicate: the given Company variable is not an investment vehicle."""
    return f"NOT ({var}.name =~ '(?i).*({_FUND_COMPANY_TOKENS}).*')"


# Operating-company nodes only (fund vehicles excluded from the projection).
_PROJECTION_NODE_QUERY = f"""
    MATCH (c:Company) WHERE {_not_fund_company("c")}
    RETURN id(c) AS id
"""

# Company↔company projection: two *operating* boards linked by a shared *human*
# director (both scrubs applied).
_PROJECTION_REL_QUERY = f"""
    MATCH (a:Company)<-[:DIRECTOR_OF]-(i:Insider)-[:DIRECTOR_OF]->(b:Company)
    WHERE id(a) < id(b) AND {_HUMAN_DIRECTOR_FILTER}
      AND {_not_fund_company("a")} AND {_not_fund_company("b")}
    RETURN id(a) AS source, id(b) AS target
"""


def _project_interlock_graph(gds, graph_name: str, log: logging.Logger):
    """Create the undirected human-director company↔company GDS projection."""
    safe_drop_graph(gds, graph_name)
    log.info("  projecting human-director company↔company interlock graph...")
    graph, result = gds.graph.project.cypher(
        graph_name,
        _PROJECTION_NODE_QUERY,
        _PROJECTION_REL_QUERY,
    )
    log.info(
        f"  projection: {int(result['nodeCount']):,} nodes, "
        f"{int(result['relationshipCount']):,} interlock edges"
    )
    return graph, int(result["relationshipCount"])


def _label_nodes(driver, database: str | None, node_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Map internal node ids → {ticker, name} for rendering."""
    if not node_ids:
        return {}
    with driver.session(database=database) as session:
        return {
            r["id"]: {"ticker": r["ticker"], "name": r["name"]}
            for r in session.run(
                """
                MATCH (c:Company) WHERE id(c) IN $ids
                RETURN id(c) AS id, c.ticker AS ticker, c.name AS name
                """,
                ids=node_ids,
            )
        }


def _top_hubs(gds, graph, driver, database, top_n, log) -> list[dict[str, Any]]:
    """GDS betweenness (stream) → the companies whose boards bridge the network."""
    log.info("  computing boardroom-bridge centrality (betweenness)...")
    scores = gds.betweenness.stream(graph).nlargest(top_n, "score")
    ids = [int(x) for x in scores["nodeId"].tolist()]
    labels = _label_nodes(driver, database, ids)
    hubs: list[dict[str, Any]] = []
    for _, row in scores.iterrows():
        info = labels.get(int(row["nodeId"]), {})
        hubs.append(
            {
                "ticker": info.get("ticker"),
                "company": info.get("name"),
                "betweenness": round(float(row["score"]), 1),
            }
        )
    return hubs


def _communities(gds, graph, driver, database, top_n, log) -> list[dict[str, Any]]:
    """GDS Louvain (stream) → the largest company clusters bound by shared boards."""
    log.info("  detecting boardroom communities (Louvain)...")
    df = gds.louvain.stream(graph)
    sizes = df["communityId"].value_counts().head(top_n)
    out: list[dict[str, Any]] = []
    for community_id, size in sizes.items():
        member_ids = [int(x) for x in df[df.communityId == community_id]["nodeId"].tolist()]
        labels = _label_nodes(driver, database, member_ids[:400])
        tickers = [v["ticker"] for v in labels.values() if v.get("ticker")][:12]
        out.append(
            {"community_id": int(community_id), "size": int(size), "sample_tickers": tickers}
        )
    return out


def _busiest_directors(driver, database: str | None, top_n: int) -> list[dict[str, Any]]:
    """Human directors sitting on the most operating boards (the interlock makers)."""
    with driver.session(database=database) as session:
        rows = session.run(
            f"""
            MATCH (i:Insider)-[:DIRECTOR_OF]->(c:Company)
            WHERE {_HUMAN_DIRECTOR_FILTER} AND {_not_fund_company("c")}
            WITH i, collect(DISTINCT c.ticker) AS tickers, count(DISTINCT c) AS boards
            WHERE boards >= 2
            RETURN i.name AS director, boards,
                   [t IN tickers WHERE t IS NOT NULL][0..10] AS sample_tickers
            ORDER BY boards DESC, director
            LIMIT $limit
            """,
            limit=top_n,
        ).data()
    return rows


def _interlock_chain(
    driver, database: str | None, from_ticker: str, to_ticker: str, max_hops: int = 8
) -> dict[str, Any] | None:
    """Shortest human-director interlock path between two companies (variable depth).

    Returns the alternating company/director chain, or None if unreachable within
    ``max_hops`` company-hops. This is the graph-only result: a fixed SQL join
    cannot express a path of unknown length, and director identity appears in no
    text embedding.
    """
    with driver.session(database=database) as session:
        row = session.run(
            f"""
            MATCH (a:Company {{ticker: $from_ticker}}), (z:Company {{ticker: $to_ticker}})
            MATCH p = shortestPath((a)-[:DIRECTOR_OF*..{max_hops * 2}]-(z))
            WHERE all(n IN nodes(p) WHERE
                CASE WHEN n:Company THEN {_not_fund_company("n")}
                     ELSE {_HUMAN_DIRECTOR_FILTER.replace("i.name", "n.name")} END)
            RETURN [n IN nodes(p) | coalesce(n.ticker, n.name)] AS chain,
                   length(p) / 2 AS director_hops
            """,
            from_ticker=from_ticker,
            to_ticker=to_ticker,
        ).single()
    if row is None:
        return None
    return {
        "from": from_ticker,
        "to": to_ticker,
        "director_hops": row["director_hops"],
        "chain": row["chain"],
    }


def analyze_interlocks(
    driver,
    database: str | None = None,
    top_hubs: int = 20,
    top_communities: int = 8,
    top_directors: int = 20,
    chain_pairs: list[tuple[str, str]] | None = None,
    logger_instance: logging.Logger | None = None,
) -> dict[str, Any]:
    """Compute board-interlock intelligence (read-only) for the ownership graph.

    Args:
        driver: Neo4j driver
        database: Target Neo4j database
        top_hubs: Number of top-betweenness bridge companies to return
        top_communities: Number of largest boardroom communities to return
        top_directors: Number of busiest interlocking directors to return
        chain_pairs: (from_ticker, to_ticker) pairs to trace interlock chains for
        logger_instance: Optional logger

    Returns:
        Dict with hubs, communities, busiest_directors, chains, and coverage stats.
    """
    log = logger_instance or logger

    with driver.session(database=database) as session:
        universe = session.run("MATCH (c:Company) RETURN count(c) AS n").single()["n"]
    log.info(f"Universe: {universe:,} companies")

    gds = get_gds_client(driver, database=database)
    graph_name = f"interlock_intelligence_graph_{database or 'default'}"
    try:
        graph, rel_count = _project_interlock_graph(gds, graph_name, log)
        if rel_count == 0:
            log.warning("No human-director interlock edges — nothing to analyze.")
            graph.drop()
            return {"universe": universe, "interlock_edges": 0}
        hubs = _top_hubs(gds, graph, driver, database, top_hubs, log)
        communities = _communities(gds, graph, driver, database, top_communities, log)
        graph.drop()
    finally:
        safe_drop_graph(gds, graph_name)
        gds.close()

    busiest = _busiest_directors(driver, database, top_directors)

    chains: list[dict[str, Any]] = []
    for from_ticker, to_ticker in chain_pairs or []:
        chain = _interlock_chain(driver, database, from_ticker, to_ticker)
        if chain is not None:
            chains.append(chain)

    return {
        "universe": universe,
        "interlock_edges": rel_count,
        "hubs": hubs,
        "communities": communities,
        "busiest_directors": busiest,
        "chains": chains,
    }
