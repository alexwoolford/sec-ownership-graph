# Graph-native proof: chain, path, coalition — head-to-head vs SQL

*Generated 2026-08-03 read-only against `secgraph`. Each win runs the graph
traversal and the flat-SQL equivalent side by side. Regenerate with `make prove`.*

> **Provenance.** Data as of `2026-06-30` · staging pinned to `2026-06-30` · Form 3/4/5 window `2022q3–2026q2` (16 quarters) · control figures from `reference_csv`. Figures below are specific to this window: a rebuild with a different `--as-of` will legitimately differ.

**How each leg is computed.** The graph leg executes a Cypher variable-length
traversal *inside Neo4j* (`VarLengthExpand`) over materialized derived edges —
`CONTROLS`/`SAME_ENTITY_AS`, `SHARES_DIRECTOR`, `CO_TARGETS`. The SQL leg mirrors the
identical rows into in-memory SQLite and runs the flat query a warehouse could write.
This distinction matters: computing the graph side with a `SELECT` plus a client-side
Python loop would make the comparison circular, because that loop *is* the thing the
graph is being measured against. Both implementations are run and their counts
cross-checked; the figures below agree exactly.

## Win 1 — CHAIN: transitive control (variable-depth)

- 907 verified control edges · 801 controllers · 10 hinges → **11 multi-hop control chains** (deepest 3 hops).
- Graph engine: `cypher VarLengthExpand over CONTROLS|SAME_ENTITY_AS` (cross-check vs the flat-SQL-equivalent Python walk: agrees).
- **SQL gets:** 907 single-hop control edges (no transitivity) — it cannot walk the chain.
- *Scope, stated plainly: these chains are dominated by micro/nano-cap issuers. Read this as a **small-cap governance and credit screen** — where control is concentrated and minority holders are exposed — not as a large-cap tool. Large caps have no >=50% holder, and the query correctly abstains on them.*

  - [3h] BASIC CAPITAL MANAGEMENT INC → AMERICAN REALTY INVESTORS INC (62%) → TRANSCONTINENTAL REALTY INVESTORS INC (83%) → INCOME OPPORTUNITY REALTY INVESTORS INC /TX/ (85%)
  - [2h] AULT MILTON C III → Hyperscale Data, Inc. (92%) → Algorhythm Holdings, Inc. (53%)
  - [2h] AULT MILTON C III → Hyperscale Data, Inc. (92%) → TurnOnGreen, Inc. (81%)
  - [2h] Aklog Lishan → PAVmed Inc. (64%) → Lucid Diagnostics Inc. (76%)
  - [2h] CONTRAN CORP → VALHI INC /DE/ (94%) → NL INDUSTRIES INC (68%)

## Win 2 — PATH: board-interlock shortestPath

- **SQL gets:** who sits on >=2 boards (a GROUP BY) — not reachability between A and Z.
- *Read the bridging director, not the path. Well-connected pairs are linked within a handful of hops, so "are these two boards connected?" is effectively always yes and carries no information. What is informative is **who** the named connector is, and which boards are structurally central — a governance-concentration screen. The measured hop distribution for this build is in `results/ownership_graph_density.json`.*

  - `AAPL` → `JPM`: AAPL — JPM *(via Gorsky Alex)*
  - `KO` → `BA`: KO — PFE — BA *(via Quincey James, Buckley Mortimer J)*
  - `NVDA` → `WMT`: NVDA — KBH — WMT *(via LORA MELISSA, Niccol Brian R)*

## Win 3 — COALITION: activist wolf-pack network

- 131 co-targeting pairs → largest coalition **13 activists, ~5 hops** (raw 22 before the custodial-hub precision scrub).
- *The raw component is 22; the scrub removes 9 custodial/index hub(s) that bridge unrelated activists. Substring matching is why this needs care: the scrub once matched `RBC` but not `ROYAL BANK OF CANADA`, so RBC and Toronto Dominion were counted as activists and inflated the figure. **Scrubbing is a precision choice, not a change to the underlying data** — hubs are labelled `is_custodial` and excluded at projection time, so the co-filing facts survive and the choice stays auditable.*
- Graph engine: `cypher variable-depth reachability over CO_TARGETS` (cross-check vs the flat-SQL-equivalent Python walk: agrees).
- **SQL gets:** 131 co-targeting pairs (a self-join) — not the coalition.
- Largest coalition members: Bulldog Investors, Bulldog Investors, LLP, CANNELL CAPITAL LLC, DEASON DARWIN, Fund 1 Investments, LLC, GABELLI MARC, GAMCO INVESTORS, INC. ET AL, GOLDSTEIN PHILLIP, ICAHN CARL C, Karpus Management, Inc., MALONE JOHN C, ROYCE CHARLES M, Saba Capital Management, L.P..

## The rule

A stake+seat overlap, a shared-holder set, a dated flip — all single self-joins — are
SQL. The **chain**, the **path**, and the **coalition** are graph: variable-depth
reachability over a hard-keyed graph, which SQL cannot express and vectors cannot
represent.

The honest form of that claim is narrow: SQL cannot express *a single query* whose
depth is decided by the data. A warehouse can still reach the same answer with a
recursive CTE or by pulling the edges out and looping in application code — so the
graph's advantage is that the traversal is one declarative pattern, indexed and
executed next to the data, rather than that the answer is unobtainable elsewhere.
That is why these figures are produced by Cypher and not by a Python loop.
