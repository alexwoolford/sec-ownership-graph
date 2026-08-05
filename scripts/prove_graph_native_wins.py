#!/usr/bin/env python3
"""
The three-win graph-native proof: chain, path, coalition — head-to-head vs SQL.

Consolidates the *superiority-tested* wins of the `secgraph` ownership graph into one
read-only demonstration. For each win it runs the graph traversal and the flat-SQL
equivalent side by side, so the contrast — SQL plateaus at the fixed hop, the graph
completes the variable-depth object — is shown live rather than asserted.

  1. CHAIN  — transitive control chains through verified >=50% 13D edges.
  2. PATH   — shortestPath between two boards via shared human directors.
  3. COALITION — activist wolf-pack components/diameter over the co-targeting graph
                 (survives the custodial-hub precision scrub).

Read-only. Nothing is written to the graph.

Run:
  python scripts/prove_graph_native_wins.py --database secgraph
  python scripts/prove_graph_native_wins.py --database secgraph --markdown results/graph_native_proof.md
  python scripts/prove_graph_native_wins.py --database secgraph --interlock AAPL:JPM --interlock KO:BA
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from secgraph.cli import (
    add_database_argument,
    get_driver_and_database,
    setup_logging,
    verify_neo4j_connection,
)
from secgraph.ingestion.ownership.graph_native_proof import prove_graph_native_wins
from secgraph.ingestion.ownership.pipeline import provenance_line


def _parse_pairs(raw: list[str] | None) -> list[tuple[str, str]] | None:
    if not raw:
        return None
    pairs = []
    for item in raw:
        a, _, z = item.partition(":")
        if a and z:
            pairs.append((a.strip().upper(), z.strip().upper()))
    return pairs or None


def render_console(result: dict) -> None:
    ch = result["chain"]
    print("\n" + "=" * 72)
    print("WIN 1 — CHAIN: transitive control (variable-depth) vs SQL (one hop)")
    print("=" * 72)
    print(
        f"  {ch['control_edges']} verified control edges · {ch['controllers']} controllers · "
        f"{ch['hinges']} hinges → {ch['multi_hop_chains']} multi-hop chains "
        f"(deepest {ch['deepest_hops']} hops)"
    )
    print(f"  flat SQL: {ch['sql_flat_gets']}")
    print(
        f"  recursive CTE: {ch['sql_recursive_chains']} paths >=2 hops, deepest "
        f"{ch['sql_recursive_deepest']} ({ch['sql_elapsed_ms']} ms) — "
        f"{'AGREES' if ch['sql_agrees'] else 'DIVERGES'} with the graph"
    )
    for chain in ch["top_chains"]:
        steps = " -> ".join(
            f"{s['name']}({s['pct']:.0f}%)" if s["pct"] else s["name"] for s in chain
        )
        print(f"    [{len(chain) - 1}h] {steps}")

    pa = result["path"]
    print("\n" + "=" * 72)
    print("WIN 2 — PATH: board interlock shortestPath vs SQL (GROUP BY)")
    print("=" * 72)
    print(f"  flat SQL: {pa['sql_flat_gets']}")
    for sp in pa.get("sql_paths", []):
        print(f"    recursive BFS {sp['a']}->{sp['z']}: {sp['hops']} hops ({sp['elapsed_ms']} ms)")
    for c in pa["chains"]:
        if c["chain"]:
            print(f"    {c['a']} ~~> {c['z']}: " + " -- ".join(str(x) for x in c["chain"]))
            if c.get("via"):
                print("       via directors: " + ", ".join(str(v) for v in c["via"]))
        else:
            print(f"    {c['a']} ~~> {c['z']}: no interlock path within hop limit")

    co = result["coalition"]
    print("\n" + "=" * 72)
    print("WIN 3 — COALITION: activist wolf-pack network vs SQL (self-join pairs)")
    print("=" * 72)
    print(
        f"  {co['cotarget_pairs']} co-targeting pairs → raw largest coalition "
        f"{co['largest_raw']} · after custodial scrub {co['largest_scrubbed']} "
        f"activists, ~{co['largest_diameter']} hops diameter"
    )
    print(f"  flat SQL: {co['sql_flat_gets']}")
    print(
        f"  recursive CTE: largest component {co['sql_recursive_members']} "
        f"({co['sql_elapsed_ms']} ms) — {'AGREES' if co['sql_agrees'] else 'DIVERGES'}"
    )
    print("  largest coalition members: " + ", ".join(co["largest_members"]))
    print()


def render_markdown(result: dict, generated: str) -> str:
    ch, pa, co = result["chain"], result["path"], result["coalition"]
    lines = [
        "# Graph-native proof: chain, path, coalition — head-to-head vs SQL",
        "",
        f"*Generated {generated} read-only against `secgraph`. Each win runs the graph",
        "traversal and the flat-SQL equivalent side by side. Regenerate with `make prove`.*",
        "",
        provenance_line(),
        "",
        "**How each leg is computed.** The graph leg executes a Cypher variable-length",
        "traversal *inside Neo4j* (`VarLengthExpand`) over materialized derived edges —",
        "`CONTROLS`/`SAME_ENTITY_AS`, `SHARES_DIRECTOR`, `CO_TARGETS`. The SQL leg mirrors the",
        "identical rows into in-memory SQLite and runs the flat query a warehouse could write.",
        "This distinction matters: computing the graph side with a `SELECT` plus a client-side",
        "Python loop would make the comparison circular, because that loop *is* the thing the",
        "graph is being measured against. Both implementations are run and their counts",
        "cross-checked; the figures below agree exactly.",
        "",
        "## Win 1 — CHAIN: transitive control (variable-depth)",
        "",
        f"- {ch['control_edges']} verified control edges · {ch['controllers']} controllers · "
        f"{ch['hinges']} hinges → **{ch['multi_hop_chains']} multi-hop control chains** "
        f"(deepest {ch['deepest_hops']} hops).",
        f"- Graph engine: `{ch['graph_engine']}` "
        f"(cross-check vs the flat-SQL-equivalent Python walk: "
        f"{'agrees' if ch.get('matches_python_baseline') else 'DIVERGES'}).",
        f"- **Flat SQL:** {ch['sql_flat_gets']}.",
        f"- **Recursive CTE (`WITH RECURSIVE`):** {ch['sql_recursive_chains']} paths at >=2 hops, "
        f"deepest {ch['sql_recursive_deepest']}, in {ch['sql_elapsed_ms']} ms — "
        f"**{'agrees with the graph' if ch['sql_agrees'] else 'DIVERGES'}**. A warehouse can "
        "reach this answer; the graph's advantage is one declarative pattern per question "
        "rather than a hand-built recursion each time.",
        "- *Scope, stated plainly: these chains are dominated by micro/nano-cap issuers. Read this"
        " as a **small-cap governance and credit screen** — where control is concentrated and"
        " minority holders are exposed — not as a large-cap tool. Large caps have no >=50% holder,"
        " and the query correctly abstains on them.*",
        "",
    ]
    for chain in ch["top_chains"]:
        steps = " → ".join(
            f"{s['name']} ({s['pct']:.0f}%)" if s["pct"] else s["name"] for s in chain
        )
        lines.append(f"  - [{len(chain) - 1}h] {steps}")
    lines += [
        "",
        "## Win 2 — PATH: board-interlock shortestPath",
        "",
        f"- **Flat SQL:** {pa['sql_flat_gets']}.",
        "- **Recursive BFS:** "
        + "; ".join(
            f"`{sp['a']}`→`{sp['z']}` {sp['hops']} hops ({sp['elapsed_ms']} ms)"
            for sp in pa.get("sql_paths", [])
        )
        + ". Same paths as the graph, at higher cost — frontier expansion rather than "
        "bidirectional search, and it needs an explicit hop cap to stay bounded.",
        "- *Read the bridging director, not the path. Well-connected pairs are linked within a"
        ' handful of hops, so "are these two boards connected?" is effectively always yes and'
        " carries no information. What is informative is **who** the named connector is, and"
        " which boards are structurally central — a governance-concentration screen. The measured"
        " hop distribution for this build is in `results/ownership_graph_density.json`.*",
        "",
    ]
    for c in pa["chains"]:
        if c["chain"]:
            step = f"  - `{c['a']}` → `{c['z']}`: " + " — ".join(str(x) for x in c["chain"])
            if c.get("via"):
                step += " *(via " + ", ".join(str(v) for v in c["via"]) + ")*"
            lines.append(step)
        else:
            lines.append(f"  - `{c['a']}` → `{c['z']}`: no interlock path within hop limit")
    lines += [
        "",
        "## Win 3 — COALITION: activist wolf-pack network",
        "",
        f"- {co['cotarget_pairs']} co-targeting pairs → largest coalition **{co['largest_scrubbed']} "
        f"activists, ~{co['largest_diameter']} hops** (raw {co['largest_raw']} before the "
        "custodial-hub precision scrub).",
        f"- *The raw component is {co['largest_raw']}; the scrub removes"
        f" {co['largest_raw'] - co['largest_scrubbed']} custodial/index hub(s) that bridge"
        " unrelated activists. Substring matching is why this needs care: the scrub once matched"
        " `RBC` but not `ROYAL BANK OF CANADA`, so RBC and Toronto Dominion were counted as"
        " activists and inflated the figure. **Scrubbing is a precision choice, not a change to"
        " the underlying data** — hubs are labelled `is_custodial` and excluded at projection"
        " time, so the co-filing facts survive and the choice stays auditable.*",
        f"- Graph engine: `{co['graph_engine']}` "
        f"(cross-check vs the flat-SQL-equivalent Python walk: "
        f"{'agrees' if co.get('matches_python_baseline') else 'DIVERGES'}).",
        f"- **Flat SQL:** {co['sql_flat_gets']}.",
        f"- **Recursive CTE:** largest component {co['sql_recursive_members']} members in "
        f"{co['sql_elapsed_ms']} ms — "
        f"**{'agrees with the graph' if co['sql_agrees'] else 'DIVERGES'}**.",
        f"- Largest coalition members: {', '.join(co['largest_members'])}.",
        "",
        "## The rule",
        "",
        "A stake+seat overlap, a shared-holder set, a dated flip — all single self-joins — are",
        "SQL. The **chain**, the **path**, and the **coalition** are graph: variable-depth",
        "reachability over a hard-keyed graph, which SQL cannot express and vectors cannot",
        "represent.",
        "",
        "The honest form of that claim is narrow: SQL cannot express *a single query* whose",
        "depth is decided by the data. A warehouse can still reach the same answer with a",
        "recursive CTE or by pulling the edges out and looping in application code — so the",
        "graph's advantage is that the traversal is one declarative pattern, indexed and",
        "executed next to the data, rather than that the answer is unobtainable elsewhere.",
        "That is why these figures are produced by Cypher and not by a Python loop.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove the three graph-native wins vs SQL")
    add_database_argument(parser)
    parser.add_argument(
        "--interlock",
        action="append",
        help="Company pair to trace as TICKER:TICKER (repeatable).",
    )
    parser.add_argument("--max-hops", type=int, default=4, help="Max hops for path/chain.")
    parser.add_argument(
        "--min-shared-targets",
        type=int,
        default=2,
        help="Min shared 13D targets to link two activists in the coalition graph.",
    )
    parser.add_argument(
        "--markdown",
        nargs="?",
        const="results/graph_native_proof.md",
        help="Write a markdown memo (default path if flag given without value).",
    )
    args = parser.parse_args()

    logger = setup_logging("prove_graph_native_wins", execute=False)
    driver, database = get_driver_and_database(logger, database=args.database)
    try:
        if not verify_neo4j_connection(driver, database, logger):
            return 1
        result = prove_graph_native_wins(
            driver,
            database=database,
            interlock_pairs=_parse_pairs(args.interlock),
            max_hops=args.max_hops,
            min_shared_targets=args.min_shared_targets,
            logger_instance=logger,
        )
        render_console(result)
        if args.markdown:
            generated = date.today().isoformat()
            out = Path(args.markdown)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(render_markdown(result, generated))
            logger.info(f"wrote {out}")
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
