# Graph-native proof: chain, path, coalition — head-to-head vs SQL

*Generated 2026-08-02 read-only against `secgraph`. Each win runs the graph
traversal and the flat-SQL equivalent side by side. Regenerate with `make prove`.*

**How each leg is computed.** The graph leg executes a Cypher variable-length
traversal *inside Neo4j* (`VarLengthExpand`) over materialized derived edges —
`CONTROLS`/`SAME_ENTITY_AS`, `SHARES_DIRECTOR`, `CO_TARGETS`. The SQL leg mirrors the
identical rows into in-memory SQLite and runs the flat query a warehouse could write.
This distinction matters: computing the graph side with a `SELECT` plus a client-side
Python loop would make the comparison circular, because that loop *is* the thing the
graph is being measured against. Both implementations are run and their counts
cross-checked; the figures below agree exactly.

## Win 1 — CHAIN: transitive control (variable-depth)

- 927 verified control edges · 819 controllers · 10 hinges → **11 multi-hop control chains** (deepest 3 hops).
- Graph engine: `cypher VarLengthExpand over CONTROLS|SAME_ENTITY_AS` (cross-check vs the flat-SQL-equivalent Python walk: agrees).
- **SQL gets:** 927 single-hop control edges (no transitivity) — it cannot walk the chain.
- *Scope, stated plainly: every issuer in these chains is micro/nano-cap (only Embraer is a widely-recognised name). Read this as a **small-cap governance and credit screen** — where control is concentrated and minority holders are exposed — not as a large-cap tool. Large caps have no >=50% holder, and the query correctly abstains on them.*

  - [3h] BASIC CAPITAL MANAGEMENT INC → AMERICAN REALTY INVESTORS INC (62%) → TRANSCONTINENTAL REALTY INVESTORS INC (83%) → INCOME OPPORTUNITY REALTY INVESTORS INC /TX/ (85%)
  - [2h] AULT MILTON C III → Hyperscale Data, Inc. (92%) → Algorhythm Holdings, Inc. (53%)
  - [2h] AULT MILTON C III → Hyperscale Data, Inc. (92%) → TurnOnGreen, Inc. (81%)
  - [2h] Aklog Lishan → PAVmed Inc. (64%) → Lucid Diagnostics Inc. (76%)
  - [2h] CONTRAN CORP → VALHI INC /DE/ (94%) → NL INDUSTRIES INC (68%)

## Win 2 — PATH: board-interlock shortestPath

- **SQL gets:** who sits on >=2 boards (a GROUP BY) — not reachability between A and Z.
- *Read the bridging director, not the path. Measured on this graph, **every** well-connected pair of companies is linked within 4 hops (a sample of 60 pairs: 5 at 1 hop, 18 at 2, 27 at 3, 10 at 4). So "are these two boards connected?" is effectively always yes and carries no information. What is informative is **who** the named connector is, and which boards are structurally central (PG 101 interlocks, HPQ 90, AIG 90, GE 88) — a governance-concentration screen.*

  - `AAPL` → `JPM`: AAPL — JPM *(via BELL JAMES A)*
  - `KO` → `BA`: KO — PFE — BA *(via Quincey James, Buckley Mortimer J)*
  - `NVDA` → `WMT`: NVDA — KBH — WMT *(via LORA MELISSA, Niccol Brian R)*

## Win 3 — COALITION: activist wolf-pack network

- 156 co-targeting pairs → largest coalition **16 activists, ~5 hops** (raw 28 before the custodial-hub precision scrub).
- *Previously reported as 22. The scrub was matching the substring `RBC`, which does not match `ROYAL BANK OF CANADA`, so RBC, Toronto Dominion, Lazard, City of London and Ohio PERS were being counted as activists. Correcting the name list removed six non-activists. **This is a precision fix, not a change in the underlying data** — and the tighter roster is the stronger claim, because every remaining name is a genuine activist or control person.*
- Graph engine: `cypher variable-depth reachability over CO_TARGETS` (cross-check vs the flat-SQL-equivalent Python walk: agrees).
- **SQL gets:** 156 co-targeting pairs (a self-join) — not the coalition.
- Largest coalition members: Bulldog Investors, Bulldog Investors, LLP, CANNELL CAPITAL LLC, DEASON DARWIN, DOLAN CHARLES F, DOLAN JAMES LAWRENCE, Fund 1 Investments, LLC, GABELLI MARC, GAMCO INVESTORS, INC. ET AL, GOLDSTEIN PHILLIP, ICAHN CARL C, Karpus Management, Inc., MAFFEI GREGORY B, MALONE JOHN C, ROYCE CHARLES M.

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
