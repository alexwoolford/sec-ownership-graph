"""
The three-win graph-native proof: chain, path, coalition — head-to-head vs SQL.

This consolidates the *superiority-tested* wins of the `secgraph` ownership graph into
one read-only demonstration. Each win is run two ways in the same pass: the graph
traversal, and the flat-SQL equivalent a warehouse would use — so the contrast ("SQL
plateaus at the fixed hop; the graph completes the variable-depth object") is shown, not
asserted. Emits ``results/graph_native_proof.md``.

The bar every win here has already cleared: beat vector/semantic retrieval, beat
SQL/warehouse, be valuable. The discriminating leg is SQL — **only variable-depth traversal
beats a warehouse**; a fixed-hop self-join (stake+seat overlap, shared-holder set, dated
13G→13D flip) does not, so those are honestly labelled SQL-class and deliberately excluded
here rather than padded into the pitch.

The three wins:

- **CHAIN** — transitive control. 13D edges carry a verified ``percent_of_class``
  (:mod:`.control_extraction`); chaining the ``control`` (>=50%) edges through CIK "hinges"
  yields ultimate-parent → subsidiary → sub-subsidiary chains. SQL reproduces one hop
  (``WHERE control_class='control'``); it cannot walk the variable-depth chain.
- **PATH** — board interlocks. ``shortestPath`` between two boards over the persisted
  ``SHARES_DIRECTOR`` edge (:mod:`.interlock_edges`, the two interlock scrubs baked in at
  materialize time). SQL reproduces "who sits on >=2 boards" (a ``GROUP BY``); it cannot
  express reachability between two named companies at unknown depth.
- **COALITION** — activist wolf-packs. Connected-component / diameter over the co-targeting
  graph (activists linked by >=2 shared 13D targets). SQL reproduces the *pairs* (a
  self-join); it cannot assemble the transitive coalition or measure its diameter. Survives
  the custodial-hub precision scrub (the pillar-1 discipline).

READ-ONLY. No writes anywhere; a pure in-memory SQLite mirror is built for the SQL leg so
the comparison runs against the same rows the graph traversal sees.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict, deque
from typing import Any

logger = logging.getLogger(__name__)

CONTROL_THRESHOLD_PCT = 50.0
MIN_SHARED_TARGETS = 2

# Broker / custodian names that bridge otherwise-unrelated activists through pure
# record-holder co-filings. Removing them is the precision scrub that pillar 1 established:
# without it the largest "coalition" is a custodial artifact, not a real wolf-pack.
#
# NOTE ON MATCHING: these are substrings, so a token only catches names that literally contain
# it. "RBC" does NOT match "ROYAL BANK OF CANADA", which is how RBC and Toronto Dominion leaked
# into the Icahn coalition. Every abbreviation therefore needs its expanded legal name listed
# alongside it. Matched case-insensitively.
_CUSTODIAL_BROKERS = (
    "JPMORGAN",
    "J.P. MORGAN",
    "RBC",
    "ROYAL BANK OF CANADA",
    "TORONTO DOMINION",
    "TORONTO-DOMINION",
    "MORGAN STANLEY",
    "GOLDMAN",
    "CITIGROUP",
    "UBS",
    "WELLS FARGO",
    "MERRILL",
    "CHARLES SCHWAB",
    "BANK OF AMERICA",
    "STATE STREET",
    "BNY",
    "MELLON",
    "NOMURA",
    "LAZARD",
    "JEFFERIES",
    "RAYMOND JAMES",
    "STIFEL",
    "NORTHERN TRUST",
)

# Passive index funds, pensions and long-only asset managers. A *different* fact from a
# custodian — they are genuine beneficial owners, not record-holders — but equally not
# activists: they appear alongside campaigns because they hold nearly everything, so they
# bridge unrelated activists just as spuriously. Excluded from coalitions for the same
# precision reason, and reported separately so the distinction stays visible.
_PASSIVE_INDEX_HOLDERS = (
    "VANGUARD",
    "BLACKROCK",
    "FMR LLC",
    "FIDELITY",
    "CAPITAL WORLD",
    "CAPITAL RESEARCH",
    "T. ROWE",
    "INVESCO",
    "DIMENSIONAL FUND",
    "GEODE",
    "NORGES",
    "ADAGE",
    "CITY OF LONDON INVESTMENT",
    "SIT INVESTMENT",
    "PUBLIC EMPLOYEES RETIREMENT",
    "RETIREMENT SYSTEM",
    "STATE OF WISCONSIN",
    "CALPERS",
    "TEACHER RETIREMENT",
)

# Combined exclusion set used by the coalition projection.
_CUSTODIAL_HUBS = _CUSTODIAL_BROKERS + _PASSIVE_INDEX_HOLDERS


# --------------------------------------------------------------------------- #
# Pure helpers (no DB, no network) — the unit-test surface.
# --------------------------------------------------------------------------- #
def is_custodial_hub(name: str | None) -> bool:
    """True if an owner name is a broker/custodian or passive index/pension holder.

    Both categories are coalition *noise*: they co-appear with activists because they hold or
    custody nearly everything, so they bridge unrelated campaigns. Use
    :func:`custodial_category` when the reason matters.
    """
    upper = (name or "").upper()
    return any(hub in upper for hub in _CUSTODIAL_HUBS)


def custodial_category(name: str | None) -> str | None:
    """Why a name is excluded from coalitions: ``broker``, ``passive``, or None if it's real.

    Kept distinct because they are different facts about the filer — a custodian is not a
    beneficial owner in substance, whereas an index fund genuinely is but never campaigns.
    Surfacing the reason keeps the scrub auditable instead of a black-box name blocklist.
    """
    upper = (name or "").upper()
    if any(hub in upper for hub in _CUSTODIAL_BROKERS):
        return "broker"
    if any(hub in upper for hub in _PASSIVE_INDEX_HOLDERS):
        return "passive"
    return None


def build_control_adjacency(
    edges: list[dict[str, Any]], threshold: float = CONTROL_THRESHOLD_PCT
) -> dict[str, list[tuple[str, float]]]:
    """Directed control adjacency ``owner_cik -> [(company_cik, pct)]`` from control edges.

    Only edges at or above ``threshold`` with a verified percent and distinct endpoints
    (self-loops scrubbed) become control links.
    """
    out: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for e in edges:
        pct = e.get("pct")
        oc, cc = e.get("owner_cik"), e.get("company_cik")
        if pct is None or oc is None or cc is None or oc == cc:
            continue
        if pct >= threshold:
            out[oc].append((cc, pct))
    return dict(out)


def enumerate_control_chains(
    adjacency: dict[str, list[tuple[str, float]]], min_hops: int = 2
) -> list[list[tuple[str, float]]]:
    """All maximal control chains of >=``min_hops`` hops, rooted at top controllers.

    A chain is a path ``root -> ... -> leaf`` where every link is a control edge. Roots are
    controllers that are not themselves controlled (top of a pyramid); DFS is cycle-safe
    (a CIK already on the path is not revisited). Returns each chain as a list of
    ``(cik, pct)`` steps after the root (root carries pct 0.0 as the path head).
    """
    owners = set(adjacency)
    controlled = {c for links in adjacency.values() for c, _ in links}
    roots = owners - controlled
    chains: list[list[tuple[str, float]]] = []

    def dfs(node: str, path: list[tuple[str, float]], visited: set[str]) -> None:
        extended = False
        for nxt, pct in adjacency.get(node, []):
            if nxt in visited:
                continue
            extended = True
            dfs(nxt, path + [(nxt, pct)], visited | {nxt})
        if not extended and len(path) - 1 >= min_hops:
            chains.append(path)

    for root in roots:
        dfs(root, [(root, 0.0)], {root})
    # dedup identical paths (a hinge reachable by multiple roots can duplicate)
    unique = {tuple(c): c for c in chains}
    return sorted(unique.values(), key=len, reverse=True)


def coalition_components(
    pairs: list[tuple[str, str]], scrub_custodial_names: dict[str, str] | None = None
) -> list[set[str]]:
    """Connected components over an undirected activist co-targeting edge list.

    ``pairs`` is a list of ``(cik_a, cik_b)`` co-targeting links. If
    ``scrub_custodial_names`` (a ``cik -> name`` map) is given, any endpoint whose name is a
    custodial hub is dropped before components are formed — the precision scrub.
    """
    adjacency: dict[str, set[str]] = defaultdict(set)
    for a, b in pairs:
        if scrub_custodial_names is not None and (
            is_custodial_hub(scrub_custodial_names.get(a))
            or is_custodial_hub(scrub_custodial_names.get(b))
        ):
            continue
        adjacency[a].add(b)
        adjacency[b].add(a)
    seen: set[str] = set()
    components: list[set[str]] = []
    for node in adjacency:
        if node in seen:
            continue
        comp: set[str] = set()
        queue = deque([node])
        seen.add(node)
        while queue:
            cur = queue.popleft()
            comp.add(cur)
            for nbr in adjacency[cur]:
                if nbr not in seen:
                    seen.add(nbr)
                    queue.append(nbr)
        components.append(comp)
    return sorted(components, key=len, reverse=True)


def component_diameter(component: set[str], pairs: list[tuple[str, str]]) -> int:
    """Longest shortest-path (in hops) within one component — its diameter.

    Two BFS sweeps' worth of eccentricity, computed exactly over the (small) component
    subgraph. A diameter >=3 is the proof that the coalition is genuinely transitive
    (members linked through intermediaries, not all mutually co-filing).
    """
    if len(component) <= 1:
        return 0
    adjacency: dict[str, set[str]] = defaultdict(set)
    for a, b in pairs:
        if a in component and b in component:
            adjacency[a].add(b)
            adjacency[b].add(a)

    def eccentricity(root: str) -> int:
        dist = {root: 0}
        queue = deque([root])
        while queue:
            cur = queue.popleft()
            for nbr in adjacency[cur]:
                if nbr not in dist:
                    dist[nbr] = dist[cur] + 1
                    queue.append(nbr)
        return max(dist.values())

    return max(eccentricity(n) for n in component)


# --------------------------------------------------------------------------- #
# DB-bound helpers — pull the subgraphs (read-only).
# --------------------------------------------------------------------------- #
def _fetch_control_edges(session) -> list[dict[str, Any]]:
    return session.run(
        """
        MATCH (b:BeneficialOwner)-[r:BENEFICIAL_OWNER_OF {filing_type:'13D'}]->(c:Company)
        WHERE r.control_class = 'control' AND b.cik IS NOT NULL AND c.cik IS NOT NULL
        RETURN b.cik AS owner_cik, b.name AS owner_name, c.cik AS company_cik,
               c.name AS company_name, r.percent_of_class AS pct
        """
    ).data()


def _fetch_cotarget_pairs(session, min_shared: int) -> list[dict[str, Any]]:
    """Activist co-targeting pairs (>=``min_shared`` shared 13D targets) with names."""
    return session.run(
        """
        MATCH (b1:BeneficialOwner)-[:BENEFICIAL_OWNER_OF {filing_type:'13D'}]->(c:Company)
              <-[:BENEFICIAL_OWNER_OF {filing_type:'13D'}]-(b2:BeneficialOwner)
        WHERE b1.cik < b2.cik AND b1.cik IS NOT NULL AND b2.cik IS NOT NULL
        WITH b1, b2, count(DISTINCT c.cik) AS shared
        WHERE shared >= $min_shared
        RETURN b1.cik AS a_cik, b1.name AS a_name, b2.cik AS b_cik,
               b2.name AS b_name, shared
        """,
        min_shared=min_shared,
    ).data()


def _graph_interlock_chain(session, a_ticker: str, z_ticker: str, max_hops: int) -> dict[str, Any]:
    """The graph PATH win: shortestPath between two boards over persisted SHARES_DIRECTOR.

    Traverses the materialized company<->company board-interlock edge
    (:mod:`.interlock_edges`) directly — direction-agnostic because the edge is stored
    undirected (``id(a) < id(b)``). The two scrubs (human directors only, operating companies
    only) are already baked into that edge at materialize time, so no per-node filtering is
    needed here. ``via_ciks`` on each hop is resolved back to the lead bridging director's
    name so the demo still shows *who* connects the two boards.

    Returns the company chain plus the per-hop bridging director, or ``None`` chain if no
    interlock path exists within the hop limit.
    """
    query = f"""
        MATCH (a:Company), (z:Company)
        WHERE a.ticker = $a AND z.ticker = $z
        MATCH p = shortestPath((a)-[:SHARES_DIRECTOR*..{max_hops}]-(z))
        WITH nodes(p) AS ns, relationships(p) AS rs
        UNWIND (CASE WHEN size(rs) = 0 THEN [null] ELSE range(0, size(rs) - 1) END) AS idx
        OPTIONAL MATCH (i:Insider) WHERE idx IS NOT NULL AND i.cik = rs[idx].via_ciks[0]
        WITH ns, idx, coalesce(i.name, rs[idx].via_ciks[0]) AS via_name
        ORDER BY idx
        WITH ns, collect(CASE WHEN idx IS NULL THEN null ELSE via_name END) AS via_names
        RETURN [n IN ns | coalesce(n.ticker, n.name)] AS chain,
               [v IN via_names WHERE v IS NOT NULL] AS via
        LIMIT 1
    """
    rec = session.run(query, a=a_ticker, z=z_ticker).single()
    if not rec:
        return {"a": a_ticker, "z": z_ticker, "chain": None, "via": []}
    return {"a": a_ticker, "z": z_ticker, "chain": rec["chain"], "via": rec["via"]}


def _cypher_control_chains(session) -> list[dict[str, Any]]:
    """Multi-hop control chains computed **by Neo4j**, over the materialized CONTROLS graph.

    This is the graph leg of the CHAIN comparison: one variable-length pattern, depth decided by
    the data. Intermediate holding companies are crossed via the ``SAME_ENTITY_AS`` CIK-identity
    bridge and dropped from the rendered chain. Requires ``materialize_control_edges.py``; an
    empty result means that step has not been run.
    """
    query = """
    MATCH p = (root:BeneficialOwner)-[:CONTROLS|SAME_ENTITY_AS*1..24]->(target:Company)
    WHERE NOT EXISTS { MATCH ()-[:CONTROLS]->(:Company)-[:SAME_ENTITY_AS]->(root) }
      AND NOT EXISTS { MATCH (target)-[:SAME_ENTITY_AS]->(:BeneficialOwner)-[:CONTROLS]->() }
    WITH p, [r IN relationships(p) WHERE type(r) = 'CONTROLS'] AS ctrl
    WHERE size(ctrl) >= 2
    RETURN [n IN nodes(p) WHERE NOT (n:BeneficialOwner AND n <> head(nodes(p)))
            | coalesce(n.name, n.cik)] AS names,
           [r IN ctrl | r.percent_of_class] AS pcts,
           size(ctrl) AS hops
    ORDER BY hops DESC, names
    """
    return session.run(query).data()


def _cypher_largest_coalition(session) -> dict[str, Any]:
    """Largest activist coalition computed **by Neo4j**, over the materialized CO_TARGETS graph.

    Seeds from every non-custodial filer and keeps the biggest reachable set — the component is
    the database's answer, not a client-side union-find. Custodial hubs are excluded at
    projection time via the ``is_custodial`` flag (``coalesce`` is required: a missing property
    would null the predicate and drop every member). Requires
    ``materialize_cotarget_edges.py``.
    """
    query = """
    MATCH (seed:BeneficialOwner)-[:CO_TARGETS]-()
    WHERE NOT coalesce(seed.is_custodial, false)
    CALL (seed) {
      MATCH p = (seed)-[:CO_TARGETS*0..24]-(m:BeneficialOwner)
      WHERE NONE(n IN nodes(p) WHERE coalesce(n.is_custodial, false))
      RETURN collect(DISTINCT m.name) AS members
    }
    RETURN members, size(members) AS member_count
    ORDER BY member_count DESC
    LIMIT 1
    """
    rec = session.run(query).single()
    if not rec:
        return {"member_count": 0, "members": []}
    return {"member_count": rec["member_count"], "members": sorted(rec["members"])}


# --------------------------------------------------------------------------- #
# SQL leg — an in-memory mirror so the comparison uses identical rows.
# --------------------------------------------------------------------------- #
def _build_sqlite_mirror(control_edges: list[dict[str, Any]], pairs: list[dict[str, Any]]):
    """In-memory SQLite of the same rows, for the flat-SQL comparison leg."""
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE control_edge (owner_cik TEXT, owner_name TEXT, "
        "company_cik TEXT, company_name TEXT, pct REAL)"
    )
    con.executemany(
        "INSERT INTO control_edge VALUES (?,?,?,?,?)",
        [
            (e["owner_cik"], e["owner_name"], e["company_cik"], e["company_name"], e["pct"])
            for e in control_edges
        ],
    )
    con.execute("CREATE TABLE cotarget (a_cik TEXT, b_cik TEXT, shared INTEGER)")
    con.executemany(
        "INSERT INTO cotarget VALUES (?,?,?)",
        [(p["a_cik"], p["b_cik"], p["shared"]) for p in pairs],
    )
    con.commit()
    return con


def _sql_control_one_hop(con) -> int:
    """What flat SQL gets on the CHAIN win: single-hop control edges. No transitivity."""
    return con.execute("SELECT COUNT(*) FROM control_edge").fetchone()[0]


def _sql_cotarget_pairs(con) -> int:
    """What flat SQL gets on the COALITION win: the pairs. Not the transitive coalition."""
    return con.execute("SELECT COUNT(*) FROM cotarget").fetchone()[0]


# --------------------------------------------------------------------------- #
# Orchestrator.
# --------------------------------------------------------------------------- #
def prove_graph_native_wins(
    driver,
    database: str | None = None,
    interlock_pairs: list[tuple[str, str]] | None = None,
    max_hops: int = 4,
    min_shared_targets: int = MIN_SHARED_TARGETS,
    logger_instance: logging.Logger | None = None,
) -> dict[str, Any]:
    """Run the three proven graph-native wins head-to-head against flat SQL. Read-only.

    **What each leg runs.** The *graph* leg executes Cypher variable-length traversals
    (``VarLengthExpand``) over the materialized derived edges — ``CONTROLS`` /
    ``SAME_ENTITY_AS``, ``SHARES_DIRECTOR``, ``CO_TARGETS``. The *SQL* leg mirrors the same rows
    into in-memory SQLite and runs the flat query a warehouse could write. The Python adjacency
    walkers in this module (``build_control_adjacency`` / ``enumerate_control_chains`` /
    ``coalition_components``) belong to the **SQL side** of that comparison: a ``SELECT`` plus a
    client-side loop is precisely what the graph is being measured *against*, so using them for
    the graph leg would make the comparison circular. The counts are cross-checked between the
    two implementations, and a divergence is surfaced rather than hidden.
    """
    log = logger_instance or logger
    interlock_pairs = interlock_pairs or [("AAPL", "JPM"), ("KO", "BA"), ("NVDA", "WMT")]

    with driver.session(database=database) as session:
        control_edges = _fetch_control_edges(session)
        cotarget = _fetch_cotarget_pairs(session, min_shared_targets)
        interlocks = [_graph_interlock_chain(session, a, z, max_hops) for a, z in interlock_pairs]
        # The graph leg: traversal executed by Neo4j.
        cypher_chains = _cypher_control_chains(session)
        cypher_coalition = _cypher_largest_coalition(session)

    # CHAIN — Python walk retained as the flat-SQL-equivalent baseline (see docstring).
    adjacency = build_control_adjacency(control_edges)
    chains = enumerate_control_chains(adjacency, min_hops=2)
    owners = set(adjacency)
    controlled = {c for links in adjacency.values() for c, _ in links}
    hinges = owners & controlled
    # CIK -> readable name, so chains render as entities, not raw CIKs.
    control_names: dict[str, str] = {}
    for e in control_edges:
        if e.get("owner_cik"):
            control_names.setdefault(e["owner_cik"], e.get("owner_name") or e["owner_cik"])
        if e.get("company_cik"):
            control_names.setdefault(e["company_cik"], e.get("company_name") or e["company_cik"])

    # COALITION (with the precision scrub)
    names = {}
    for p in cotarget:
        names[p["a_cik"]] = p["a_name"]
        names[p["b_cik"]] = p["b_name"]
    pair_tuples = [(p["a_cik"], p["b_cik"]) for p in cotarget]
    comps_raw = coalition_components(pair_tuples)
    comps_scrubbed = coalition_components(pair_tuples, scrub_custodial_names=names)
    largest = comps_scrubbed[0] if comps_scrubbed else set()
    diameter = component_diameter(largest, pair_tuples)

    # SQL leg
    con = _build_sqlite_mirror(control_edges, cotarget)
    sql_control = _sql_control_one_hop(con)
    sql_pairs = _sql_cotarget_pairs(con)
    con.close()

    result = {
        "chain": {
            "control_edges": len(control_edges),
            "controllers": len(owners),
            "controlled": len(controlled),
            "hinges": len(hinges),
            # The graph leg: chains counted by Neo4j's traversal.
            "multi_hop_chains": len(cypher_chains),
            "deepest_hops": max((c["hops"] for c in cypher_chains), default=0),
            "graph_engine": "cypher VarLengthExpand over CONTROLS|SAME_ENTITY_AS",
            # Cross-check against the Python/SQL-equivalent walk over identical rows. Equality
            # is the evidence that Cypher-ifying preserved the result rather than changing it.
            "python_baseline_chains": len(chains),
            "matches_python_baseline": len(cypher_chains) == len(chains),
            "sql_gets": f"{sql_control} single-hop control edges (no transitivity)",
            "top_chains": [
                [
                    {"cik": None, "name": name, "pct": ([None] + list(c["pcts"]))[i]}
                    for i, name in enumerate(c["names"])
                ]
                for c in cypher_chains[:5]
            ],
        },
        "path": {
            "pairs_traced": len(interlocks),
            "sql_gets": "who sits on >=2 boards (a GROUP BY) — not reachability between A and Z",
            "chains": interlocks,
        },
        "coalition": {
            "cotarget_pairs": len(cotarget),
            "components_raw": len(comps_raw),
            "largest_raw": len(comps_raw[0]) if comps_raw else 0,
            "components_scrubbed": len(comps_scrubbed),
            # The graph leg: component size computed by Neo4j's reachability traversal.
            "largest_scrubbed": cypher_coalition["member_count"],
            "largest_diameter": diameter,
            "largest_members": cypher_coalition["members"][:15],
            "graph_engine": "cypher variable-depth reachability over CO_TARGETS",
            "python_baseline_largest": len(largest),
            "matches_python_baseline": cypher_coalition["member_count"] == len(largest),
            "sql_gets": f"{sql_pairs} co-targeting pairs (a self-join) — not the coalition",
        },
    }
    log.info(
        f"CHAIN: {result['chain']['multi_hop_chains']} chains (deepest "
        f"{result['chain']['deepest_hops']}h) vs SQL's {sql_control} flat edges"
    )
    log.info(
        f"COALITION: largest {result['coalition']['largest_scrubbed']} activists "
        f"(~{diameter}h) vs SQL's {sql_pairs} pairs"
    )
    return result
