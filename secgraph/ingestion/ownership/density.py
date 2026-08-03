"""
Ownership-graph density measurement — the Phase-1 GO/NO-GO gate (read-only).

The vignette (``results/insider_interlock_proof.md``) proved multi-hop board
interlocks *exist* but only *extrapolated* their density from a 3.7% sample. This
module measures the real thing on the full graph and decides whether the build
is showable to a finance professional or still a vignette.

Three measurements:

1. **Connectivity (headline)** — collapse
   ``(Company)<-[role]-(Insider)-[role]->(Company)`` into an undirected
   company↔company projection and run ``gds.wcc.write`` (writing
   ``Company.ownership_component``). Report component count, giant-component
   size, and the % of companies in a size-≥2 component / the giant component.
2. **Path distribution** — sample company pairs inside the giant component and
   compute an in-DB ``shortestPath`` hop-count histogram; report the fraction of
   reachable pairs at distance ≥3 (the true multi-hop signal).
3. **Sanity** — count insiders sitting on ≥2 boards and surface one concrete
   ≥3-hop chain.

Writing ``ownership_component`` is the only mutation; everything else is
read-only. The projection follows the repo's create→use→drop convention.
"""

from __future__ import annotations

import logging

from secgraph.gds.utils import get_gds_client, safe_drop_graph

logger = logging.getLogger(__name__)

# Interlock relationship types that connect companies through a shared insider.
# TenPercentOwner is deliberately excluded: it is an ownership stake, not a
# board/governance tie, and dominant holders would create spurious hubs.
_INTERLOCK_RELS = ["DIRECTOR_OF", "OFFICER_OF"]

# Provisional gate thresholds (tunable; documented in the plan).
GATE_MIN_IN_COMPONENT_PCT = 60.0  # % of companies in a size-≥2 component
GATE_MIN_GIANT_PCT = 25.0  # giant component as % of universe
GATE_MIN_MULTIHOP_PAIRS = 1  # ≥1 concrete ≥3-hop chain


def _relationship_query() -> str:
    """Undirected company↔company projection via shared director/officer."""
    clauses = [
        f"MATCH (a:Company)<-[:{rel}]-(:Insider)-[:{rel2}]->(b:Company) "
        "WHERE id(a) < id(b) "
        "RETURN id(a) AS source, id(b) AS target"
        for rel in _INTERLOCK_RELS
        for rel2 in _INTERLOCK_RELS
    ]
    return " UNION ".join(clauses)


def _measure_components(gds, graph_name: str, universe_count: int, log: logging.Logger) -> dict:
    """Project, run WCC (write ownership_component), summarise connectivity."""
    safe_drop_graph(gds, graph_name)
    log.info("  projecting undirected company↔company interlock graph...")
    graph, result = gds.graph.project.cypher(
        graph_name,
        "MATCH (c:Company) RETURN id(c) AS id",
        _relationship_query(),
    )
    node_count = int(result["nodeCount"])
    rel_count = int(result["relationshipCount"])
    log.info(f"  projection: {node_count:,} nodes, {rel_count:,} interlock edges")

    if rel_count == 0:
        graph.drop()
        return {
            "interlock_edges": 0,
            "components": 0,
            "giant_component_size": 0,
            "pct_in_component": 0.0,
            "pct_in_giant": 0.0,
        }

    gds.wcc.write(graph, writeProperty="ownership_component")
    graph.drop()
    return {"interlock_edges": rel_count}


def _component_stats(driver, database: str | None, universe_count: int) -> dict:
    """Read component sizes back from the written ownership_component property."""
    with driver.session(database=database) as session:
        rows = session.run(
            """
            MATCH (c:Company)
            WHERE c.ownership_component IS NOT NULL
            WITH c.ownership_component AS comp, count(*) AS size
            RETURN comp, size ORDER BY size DESC
            """
        ).data()
    if not rows:
        return {
            "components": 0,
            "giant_component_size": 0,
            "pct_in_component": 0.0,
            "pct_in_giant": 0.0,
        }
    sizes = [r["size"] for r in rows]
    giant = sizes[0]
    in_multi = sum(s for s in sizes if s >= 2)
    return {
        "components": len(sizes),
        "components_size_ge2": sum(1 for s in sizes if s >= 2),
        "giant_component_size": giant,
        "giant_component_comp_id": rows[0]["comp"],
        "pct_in_component": round(in_multi / universe_count * 100, 2) if universe_count else 0.0,
        "pct_in_giant": round(giant / universe_count * 100, 2) if universe_count else 0.0,
    }


def _path_distribution(
    driver, database: str | None, comp_id, sample_pairs: int, log: logging.Logger
) -> dict:
    """Shortest-path hop histogram over sampled pairs in the giant component."""
    with driver.session(database=database) as session:
        result = session.run(
            """
            MATCH (c:Company {ownership_component: $comp})
            WITH c ORDER BY rand() LIMIT $limit
            WITH collect(c) AS nodes
            UNWIND range(0, size(nodes) - 1) AS i
            UNWIND range(i + 1, size(nodes) - 1) AS j
            WITH nodes[i] AS a, nodes[j] AS b
            LIMIT $pairs
            MATCH p = shortestPath((a)-[:DIRECTOR_OF|OFFICER_OF*..8]-(b))
            RETURN length(p) AS hops
            """,
            comp=comp_id,
            limit=min(sample_pairs, 60),
            pairs=sample_pairs,
        )
        # length(p) counts relationships; a company↔company hop = 2 rels
        # (Company)-[role]-(Insider)-[role]-(Company). Convert to company-hops.
        histogram: dict[int, int] = {}
        reachable = 0
        for record in result:
            rels = record["hops"]
            company_hops = max(1, rels // 2)
            histogram[company_hops] = histogram.get(company_hops, 0) + 1
            reachable += 1
    ge3 = sum(v for k, v in histogram.items() if k >= 3)
    log.info(f"  path histogram (company-hops): {dict(sorted(histogram.items()))}")
    return {
        "reachable_pairs_sampled": reachable,
        "hop_histogram": {str(k): v for k, v in sorted(histogram.items())},
        "pairs_at_distance_ge3": ge3,
        "frac_ge3": round(ge3 / reachable, 3) if reachable else 0.0,
    }


def _sanity(driver, database: str | None) -> dict:
    """Insiders on ≥2 boards + one concrete ≥3-hop chain."""
    with driver.session(database=database) as session:
        multi = session.run(
            """
            MATCH (i:Insider)-[:DIRECTOR_OF|OFFICER_OF]->(c:Company)
            WITH i, count(DISTINCT c) AS boards
            WHERE boards >= 2
            RETURN count(i) AS multi_board_insiders
            """
        ).single()["multi_board_insiders"]

        chain_row = session.run(
            """
            MATCH (a:Company), (b:Company)
            WHERE a.ownership_component = b.ownership_component
              AND a.ownership_component IS NOT NULL
              AND id(a) < id(b)
            WITH a, b LIMIT 400
            MATCH p = shortestPath((a)-[:DIRECTOR_OF|OFFICER_OF*..16]-(b))
            WITH p, length(p) AS rels
            WHERE rels >= 6  // ≥3 company-hops (each hop = 2 relationships)
            RETURN [n IN nodes(p) WHERE n:Company | coalesce(n.ticker, n.name)] AS companies,
                   rels
            ORDER BY rels DESC LIMIT 1
            """
        ).single()
    chain = None
    if chain_row is not None:
        chain = {
            "companies": chain_row["companies"],
            "company_hops": max(1, chain_row["rels"] // 2),
        }
    return {"multi_board_insiders": multi, "example_chain": chain}


def measure_ownership_density(
    driver,
    database: str | None = None,
    sample_pairs: int = 500,
    logger_instance: logging.Logger | None = None,
) -> dict:
    """Measure connectivity + path distribution and apply the provisional gate.

    Args:
        driver: Neo4j driver
        database: Target Neo4j database
        sample_pairs: Company pairs to sample for the path histogram
        logger_instance: Optional logger

    Returns:
        Dict with all measurements plus a ``gate`` sub-dict (pass/fail + reasons).
    """
    log = logger_instance or logger

    with driver.session(database=database) as session:
        universe_count = session.run("MATCH (c:Company) RETURN count(c) AS n").single()["n"]
        insider_count = session.run("MATCH (i:Insider) RETURN count(i) AS n").single()["n"]
    log.info(f"Universe: {universe_count:,} companies, {insider_count:,} insiders")

    if universe_count == 0 or insider_count == 0:
        log.error("No companies/insiders loaded — run Phase 1 first.")
        return {"error": "empty_graph"}

    gds = get_gds_client(driver, database=database)
    graph_name = f"ownership_density_graph_{database or 'default'}"
    try:
        proj = _measure_components(gds, graph_name, universe_count, log)
    finally:
        safe_drop_graph(gds, graph_name)
        gds.close()

    stats = {"universe": universe_count, "insiders": insider_count, **proj}
    stats.update(_component_stats(driver, database, universe_count))

    paths = {}
    if stats.get("giant_component_comp_id") is not None:
        paths = _path_distribution(
            driver, database, stats["giant_component_comp_id"], sample_pairs, log
        )
    stats["paths"] = paths
    stats["sanity"] = _sanity(driver, database)

    # Apply the provisional gate.
    reasons = []
    pct_component = stats.get("pct_in_component", 0.0)
    pct_giant = stats.get("pct_in_giant", 0.0)
    multihop = paths.get("pairs_at_distance_ge3", 0)
    if pct_component < GATE_MIN_IN_COMPONENT_PCT:
        reasons.append(f"pct_in_component {pct_component}% < {GATE_MIN_IN_COMPONENT_PCT}%")
    if pct_giant < GATE_MIN_GIANT_PCT:
        reasons.append(f"pct_in_giant {pct_giant}% < {GATE_MIN_GIANT_PCT}%")
    if multihop < GATE_MIN_MULTIHOP_PAIRS:
        reasons.append(f"multi-hop(≥3) pairs {multihop} < {GATE_MIN_MULTIHOP_PAIRS}")
    stats["gate"] = {"passed": not reasons, "failing_reasons": reasons}

    log.info("")
    log.info("=" * 72)
    log.info(f"DENSITY GATE: {'PASS' if not reasons else 'FAIL'}")
    log.info(f"  % companies in size-≥2 component: {pct_component}%")
    log.info(f"  % companies in giant component:   {pct_giant}%")
    log.info(f"  sampled pairs at ≥3 company-hops: {multihop}")
    if reasons:
        for reason in reasons:
            log.info(f"  ✗ {reason}")
    log.info("=" * 72)
    return stats
