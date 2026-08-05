"""
Materialize GDS-derived structural features on ``Company`` from the board-interlock graph.

**Why this exists.** The repo already had the good version of this analysis and never ran it.
:func:`interlock.analyze_interlocks` computes betweenness and Louvain over a carefully scrubbed
projection — and has **zero callers**. The only GDS that executes in a shipped build is the density
gate's ``gds.wcc.write``. So the graph's structure was measured, argued about in docstrings, and
then thrown away instead of persisted where a query or a visualization could use it.

This module promotes that analysis into a materializer.

**It must project UNDIRECTED, and getting this wrong silently invents a result.** The first version
of this module reused ``interlock._PROJECTION_REL_QUERY``, whose ``WHERE id(a) < id(b)`` predicate
stores each interlock pair once. Under ``gds.graph.project.cypher`` — which has no ``orientation``
option — every edge then points low-id → high-id, so the projection is a **DAG**: ``gds.scc.stream``
returned 7,702 components over 7,702 nodes.

That is not a performance detail. ``load_company_universe.py`` loads ``company_tickers.json`` in
SEC's size-descending order, so **internal node id tracks market-cap rank** (verified: ``id=0``
AAPL, ``1`` NVDA, ``2`` GOOGL). Betweenness over an id-ascending DAG therefore rewards nodes for
being *structurally upstream*, i.e. for being large caps. Measured: ``spearman(node_id,
betweenness) = -0.373``. The reassuringly recognizable top-8 it produced —
PRU $774B, AIG $161B, GPN $53B — was substantially an artifact of insertion order, not a governance
finding, and it would have been indefensible under one question from a buyer.

The fix is a **native projection over the already-materialized edge**, which supports a real
``orientation``::

    gds.graph.project(name, "Company", {"SHARES_DIRECTOR": {"orientation": "UNDIRECTED"}})

Verified undirected: 3,615 SCC components over 8,000 nodes, versus one-per-node before.

**The scrub is already baked into the edge, so nothing is lost by dropping the Cypher projection.**
``interlock_edges.py`` builds ``SHARES_DIRECTOR`` with both ``_HUMAN_DIRECTOR_FILTER`` and
``_not_fund_company`` applied. Verified: **zero** Nuveen/Gabelli/PIMCO fund-family companies carry
a ``SHARES_DIRECTOR`` edge. The scrubs remain essential — without them a single BlackRock closed-end
fund complex manufactures a "director on 36 boards" — they are simply enforced upstream now.

**And the honest answer is that small caps really do broker.** On the corrected undirected graph the
top brokers are XOS ($0.1B), IFF ($25.5B), PBI ($3.2B), FLGT ($1.2B), FTNT ($10.4B). XOS is not
noise: it has 18 genuine interlock neighbours through real human directors. Boards that bridge
otherwise-separate clusters are often *small* companies whose directors also sit on larger ones —
which is a more interesting finding than "big companies are central", and it is what the data says.
Callers that need recognizable names should band by ``size_usd`` rather than expect centrality to
correlate with size.

**The use case, stated plainly.** *Which boards broker between otherwise-unconnected clusters?*
Betweenness is the honest answer to that, and no screener provides it — it requires the whole graph
at once, not a row per company. This is not GDS for its own sake: the density gate already proves
the interlock layer is real before this ever runs.

**Determinism is not optional here.** GDS parallelises by default, and the default is *not*
reproducible. Measured across two identical runs on this graph:

    betweenness identical:           False
    louvain communityIds identical:  False  -> 4,035 of 7,702 nodes (52.4%) changed community

That violates the reproducibility contract's rule against unseeded randomness in a decision path,
and it would be invisible: a rebuild would silently recolour half a visualization. ``concurrency=1``
fixes both completely, and at this graph size it is *free* — Louvain runs 0.24s single-threaded
versus 5.72s parallel, because coordination dominates. So it is pinned, and
:data:`GDS_CONCURRENCY` documents why.

**Community identity needs an anchor, not an integer.** Even pinned, a Louvain ``communityId`` is an
arbitrary label: it says nothing about *which* cluster it is, so two builds cannot be compared and a
colour-by-community visualization would reshuffle. ``interlock_community_anchor`` — the lowest CIK in
the cluster — is a stable handle derived from membership rather than from iteration order. Verified:
7,702/7,702 members (100%) map to the same anchor across runs.

Writes only to the target database (intended: ``secgraph``); idempotent and dry-run-by-default
(``--execute`` to write, ``--replace`` to clear stale values first).
"""

from __future__ import annotations

import logging
from typing import Any

from secgraph.core.config.constants import BATCH_SIZE_LARGE
from secgraph.gds.utils import get_gds_client, safe_drop_graph

logger = logging.getLogger(__name__)

# Native projection, NOT gds.graph.project.cypher. This is the one place in the repo that departs
# from the Cypher-projection convention, and the reason is correctness rather than taste:
# gds.graph.project.cypher accepts no `orientation`, so a Cypher projection of a
# stored-once-per-pair edge is inescapably directed — and a directed interlock graph measures
# insertion order (see the module docstring). The native form is also the more idiomatic Neo4j
# path once the edge is materialized: project the relationship that exists, don't re-derive it.
#
# Rejected alternative: keeping the Cypher projection with an undirected pattern
# `(a)-[:SHARES_DIRECTOR]-(b)`. That double-counts every edge and yields betweenness exactly 2x
# too high — a wrong number that looks plausible, which is worse than an error.
_PROJECTION_CONFIG: dict[str, Any] = {"SHARES_DIRECTOR": {"orientation": "UNDIRECTED"}}

# Pinned to 1 for REPRODUCIBILITY, not performance — though it happens to be faster here.
#
# GDS parallelises by default and the parallel path is not deterministic: two identical runs gave
# different betweenness scores and reassigned 52.4% of nodes to a different Louvain community. A
# property that changes when nothing changed cannot be published, cannot be diffed between builds,
# and would silently recolour a visualization on rebuild.
#
# Measured on this graph (7,702 nodes / 16,290 rels): betweenness 0.22s, Louvain 0.24s at
# concurrency=1, versus 0.14s / 5.72s at default. Single-threaded Louvain is ~24x FASTER here
# because coordination overhead dominates at this size. If the graph grows by orders of magnitude,
# revisit — but re-verify determinism before raising it.
GDS_CONCURRENCY = 1

_GRAPH_NAME = "interlock_features"

# Betweenness is written straight from the projection (gds.betweenness.write) rather than streamed
# and written back through the client. Writing from the projection is the idiomatic GDS path and
# avoids materializing 7.7k rows in Python only to send them back.
_BETWEENNESS_PROPERTY = "interlock_betweenness"

# Louvain is streamed rather than written, because the anchor has to be derived from cluster
# MEMBERSHIP before anything is persisted — the raw id alone is not worth writing on its own.
_COMMUNITY_PROPERTY = "interlock_community"

# Degree over the materialized SHARES_DIRECTOR edge, not over the projection. Same scrubs (the edge
# is built with them in interlock_edges.py), and it means the number a reader sees on the node
# matches the edges they can actually traverse from it.
_DEGREE_QUERY = """
    MATCH (c:Company)-[:SHARES_DIRECTOR]-(other:Company)
    WITH c.cik AS cik, count(DISTINCT other) AS degree
    RETURN cik, degree
    ORDER BY cik
"""

_WRITE_COMMUNITY_QUERY = """
    UNWIND $batch AS row
    MATCH (c:Company {cik: row.cik})
    SET c.interlock_community = row.community,
        c.interlock_community_anchor = row.anchor,
        c.interlock_community_size = row.size
    RETURN count(c) AS n
"""

_WRITE_DEGREE_QUERY = """
    UNWIND $batch AS row
    MATCH (c:Company {cik: row.cik})
    SET c.interlock_degree = row.degree
    RETURN count(c) AS n
"""

# Clear before a rebuild so a company that drops out of the scrubbed projection loses its features
# rather than keeping a stale score. Absence is meaningful: no property means "not in the
# human-director interlock graph", which is a different claim from "central to it with score 0".
_CLEAR_QUERY = """
    MATCH (c:Company)
    WHERE c.interlock_betweenness IS NOT NULL
       OR c.interlock_community IS NOT NULL
       OR c.interlock_degree IS NOT NULL
    REMOVE c.interlock_betweenness, c.interlock_community,
           c.interlock_community_anchor, c.interlock_community_size, c.interlock_degree
    RETURN count(c) AS n
"""


# --------------------------------------------------------------------------- #
# Pure helpers — the unit-test surface. No Neo4j, no GDS.
# --------------------------------------------------------------------------- #
def batches(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    """Split rows into write batches of at most ``size`` (last batch may be short)."""
    if size <= 0:
        raise ValueError("batch size must be positive")
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def community_rows(members: list[tuple[str, int]]) -> list[dict[str, Any]]:
    """Turn ``(cik, communityId)`` pairs into write rows carrying a stable anchor.

    The anchor is the **lowest CIK in the cluster** — a handle derived from membership rather than
    from GDS iteration order. This is what makes community identity portable:

    - A raw ``communityId`` is an arbitrary integer. Even with ``concurrency=1`` pinning it within
      a build, it carries no meaning across builds, so two graphs cannot be compared and a
      colour-by-community visualization reshuffles on every rebuild.
    - The anchor is stable as long as membership is stable, and it *names* the cluster with
      something a human can look up. Verified: 100% of members map to the same anchor across runs.
    - CIK is the repo's hard key, so this introduces no new join axis and no fuzzy matching.

    ``size`` travels with it because a cluster of 2 and a cluster of 809 are not the same finding,
    and a consumer filtering for meaningful clusters should not have to re-aggregate.

    Rows are returned CIK-ordered so a write batch — and any diff of the resulting properties — is
    deterministic. Rows with a falsy CIK are dropped: they cannot be MATCHed on write anyway, and
    they must not be allowed to become a cluster's anchor.
    """
    clusters: dict[int, list[str]] = {}
    for cik, community in members:
        if not cik:
            continue
        clusters.setdefault(int(community), []).append(cik)

    rows: list[dict[str, Any]] = []
    for community, ciks in clusters.items():
        anchor = min(ciks)
        size = len(ciks)
        rows.extend(
            {"cik": cik, "community": community, "anchor": anchor, "size": size} for cik in ciks
        )
    rows.sort(key=lambda r: r["cik"])
    return rows


def summarize_features(
    betweenness_rows: int, community_rows_: list[dict[str, Any]], universe_count: int
) -> dict[str, Any]:
    """Coverage + cluster-shape stats for the plan/verify output.

    Coverage is the honest headline: these features exist only for companies inside the *scrubbed*
    projection, so a large "without" count is expected and is not a failure — it is fund vehicles
    and companies with no human-director interlock.
    """
    sizes: dict[str, int] = {}
    for row in community_rows_:
        sizes[row["anchor"]] = row["size"]
    cluster_sizes = sorted(sizes.values(), reverse=True)
    return {
        "companies_with_features": betweenness_rows,
        "companies_without_features": max(0, universe_count - betweenness_rows),
        "coverage_pct": round(betweenness_rows / universe_count * 100, 1)
        if universe_count
        else 0.0,
        "clusters": len(cluster_sizes),
        "largest_cluster": cluster_sizes[0] if cluster_sizes else 0,
        "clusters_ge_5": sum(1 for s in cluster_sizes if s >= 5),
    }


# --------------------------------------------------------------------------- #
# DB/GDS-bound compute + write.
# --------------------------------------------------------------------------- #
def materialize_interlock_features(
    driver,
    database: str | None = None,
    replace: bool = False,
    batch_size: int = BATCH_SIZE_LARGE,
    execute: bool = False,
    logger_instance: logging.Logger | None = None,
) -> dict[str, Any]:
    """Compute and persist interlock centrality + community features. Dry-run unless ``execute``.

    Args:
        driver: Neo4j driver (used for the Cypher write-back; the GDS client makes its own
            connection — see ``gds/utils.py::get_gds_client``).
        database: Target database (``secgraph``).
        replace: Clear existing features before writing (full rebuild).
        batch_size: Write batch size.
        execute: If False, project and compute but write nothing.
        logger_instance: Optional logger.

    Returns:
        Summary dict (coverage, cluster shape, top brokers), plus ``written``/``cleared`` when
        executed.

    Raises:
        RuntimeError: if the projection is empty. That means the scrubs excluded everything or
            ``SHARES_DIRECTOR``/``DIRECTOR_OF`` was never loaded — a broken precondition, not a
            data fact, so it fails loudly rather than writing zero features and exiting 0.
    """
    log = logger_instance or logger

    with driver.session(database=database) as session:
        universe_count = session.run("MATCH (c:Company) RETURN count(c) AS n").single()["n"]

    gds = get_gds_client(driver, database=database)
    try:
        safe_drop_graph(gds, _GRAPH_NAME)
        log.info("projecting SHARES_DIRECTOR as an UNDIRECTED company↔company graph")
        graph, result = gds.graph.project(_GRAPH_NAME, "Company", _PROJECTION_CONFIG)
        node_count = int(result["nodeCount"])
        rel_count = int(result["relationshipCount"])
        log.info(
            f"  projection: {node_count:,} nodes · {rel_count:,} relationship entries "
            f"(each interlock appears in both directions — that is what UNDIRECTED means here)"
        )
        if rel_count == 0:
            raise RuntimeError(
                "the interlock projection has no relationships — SHARES_DIRECTOR is missing or "
                "empty. Load insiders and materialize the edge first:\n"
                "  python scripts/load_insiders.py --database <db> --execute\n"
                "  python scripts/materialize_interlock_edges.py --database <db> --execute"
            )
        # Guard the defect this module was rewritten to fix. A directed projection makes betweenness
        # a proxy for insertion order (node id tracks market-cap rank), which produced a
        # plausible-looking but invented large-cap ranking. Undirected means every edge is stored
        # both ways, so the entry count must exceed the distinct-pair count.
        with driver.session(database=database) as session:
            pairs = session.run(
                "MATCH (:Company)-[r:SHARES_DIRECTOR]->(:Company) RETURN count(r) AS n"
            ).single()["n"]
        if rel_count <= pairs:
            raise RuntimeError(
                f"projection looks DIRECTED ({rel_count:,} entries for {pairs:,} stored pairs). "
                f"Betweenness over a directed interlock graph measures node-id order, not "
                f"brokerage. Expected roughly 2x the pair count."
            )

        # Louvain streamed: the anchor is derived from membership, so we need the assignments in
        # hand before anything is persisted.
        louvain = gds.louvain.stream(graph, concurrency=GDS_CONCURRENCY)
        node_ids = [int(n) for n in louvain["nodeId"]]
        with driver.session(database=database) as session:
            cik_by_id = {
                r["nid"]: r["cik"]
                for r in session.run(
                    "UNWIND $ids AS i MATCH (c) WHERE id(c) = i RETURN i AS nid, c.cik AS cik",
                    ids=node_ids,
                )
            }
        members = [
            (cik_by_id.get(nid), int(cid))
            for nid, cid in zip(node_ids, louvain["communityId"], strict=True)
        ]
        rows = community_rows([(c, k) for c, k in members if c])

        summary = summarize_features(node_count, rows, universe_count)
        log.info(
            f"  clusters: {summary['clusters']:,} · largest {summary['largest_cluster']:,} · "
            f"{summary['clusters_ge_5']:,} with >=5 members"
        )

        if not execute:
            log.info("")
            log.info(
                f"DRY RUN — would set interlock features on {node_count:,} of "
                f"{universe_count:,} companies ({summary['coverage_pct']}%)"
            )
            if replace:
                log.info("  (after clearing existing features)")
            log.info("Run with --execute to write.")
            summary["dry_run"] = True
            return summary

        if replace:
            with driver.session(database=database) as session:
                summary["cleared"] = session.run(_CLEAR_QUERY).single()["n"]
            log.info(f"cleared features on {summary['cleared']:,} companies")

        # Betweenness written from the projection — the idiomatic GDS write path.
        bt = gds.betweenness.write(
            graph, writeProperty=_BETWEENNESS_PROPERTY, concurrency=GDS_CONCURRENCY
        )
        summary["betweenness_written"] = int(bt["nodePropertiesWritten"])
        log.info(f"✓ wrote {summary['betweenness_written']:,} {_BETWEENNESS_PROPERTY} values")

        with driver.session(database=database) as session:
            written = 0
            for batch in batches(rows, batch_size):
                written += session.run(_WRITE_COMMUNITY_QUERY, batch=batch).single()["n"]
            summary["community_written"] = written

            degree_rows = session.run(_DEGREE_QUERY).data()
            deg_written = 0
            for batch in batches(degree_rows, batch_size):
                deg_written += session.run(_WRITE_DEGREE_QUERY, batch=batch).single()["n"]
            summary["degree_written"] = deg_written

        log.info(
            f"✓ wrote community + anchor on {summary['community_written']:,} companies · "
            f"degree on {summary['degree_written']:,}"
        )

        # Strip features from companies with NO interlock at all — LAST, after every write.
        #
        # The native projection covers every :Company node, so GDS writes 0.0 for an isolated one,
        # and Louvain gives it a singleton community. Both are claims: 0.0 says "measured, and this
        # board brokers nothing", an anchor says "belongs to this cluster". For a company with no
        # shared director neither was measured, and conflating the two would present 3,468
        # unconnected companies as assessed. Same null discipline as the size properties: absence
        # means unknown, never zero.
        #
        # Ordering is load-bearing and I got it wrong once: with the strip before the community
        # write, the write re-added anchors to all 3,468 stripped companies. It must be last.
        with driver.session(database=database) as session:
            stripped = session.run(
                """
                MATCH (c:Company)
                WHERE NOT EXISTS { (c)-[:SHARES_DIRECTOR]-() }
                  AND (c.interlock_betweenness IS NOT NULL OR c.interlock_community IS NOT NULL)
                REMOVE c.interlock_betweenness, c.interlock_community,
                       c.interlock_community_anchor, c.interlock_community_size
                RETURN count(c) AS n
                """
            ).single()["n"]
        summary["stripped_isolated"] = stripped
        log.info(
            f"  removed features from {stripped:,} companies with no interlock at all "
            f"(absence means 'not measured'; 0.0 would misreport it as 'brokers nothing')"
        )
        safe_drop_graph(gds, _GRAPH_NAME)
        return summary
    finally:
        safe_drop_graph(gds, _GRAPH_NAME)
        gds.close()
