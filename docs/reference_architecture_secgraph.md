# Reference architecture — `secgraph`: a graph-native SEC ownership intelligence layer

*The one-page story for a finance professional: what the graph does that a warehouse and a
vector store cannot, how it stays honest, and how to build, refresh, and query it.*

---

## The thesis in one sentence

`secgraph` answers questions that need **variable-depth reachability over a hard-keyed
(CIK) graph across multiple relationship types** — the one shape flat SQL cannot express and
vector embeddings cannot represent — and it refuses to answer when the graph carries no
verified support.

Most "graph in finance" pitches fail a simple test: a fixed-hop self-join (a stake+seat
overlap, a shared-holder set, a dated 13G→13D flip) reproduces them exactly in one `GROUP BY`
on a warehouse. Those are honestly labelled **SQL-class** here and are never the pitch. What
survives the three-competitor bar (must beat vector RAG, beat SQL, and be valuable) is the
set of objects whose depth is *not known in advance*:

| Win | Object | Cypher that computes it | Persona |
| --- | --- | --- | --- |
| **CHAIN** | transitive ≥50% control chains through 13D edges | `(root)-[:CONTROLS\|SAME_ENTITY_AS*1..N]->(target)` | PE / credit / risk |
| **PATH** | `shortestPath` between two boards over shared directors | `shortestPath((a)-[:SHARES_DIRECTOR*..N]-(z))` | governance |
| **COALITION** | activist wolf-pack component + diameter | `(seed)-[:CO_TARGETS*0..N]-(m)`, custodial hubs excluded | event-driven / risk |

All three execute **inside Neo4j** as variable-length traversals (`VarLengthExpand`) over
materialized derived edges — not as a flat `SELECT` plus a client-side Python loop. That
distinction is load-bearing: a client-side walk is exactly what a warehouse reproduces, so it
would have made the claim unearned. The Python adjacency walkers retained in
`graph_native_proof.py` are deliberately the **flat-SQL side** of the head-to-head, and the two
implementations are cross-checked (they agree: 11 chains / 16-member coalition).

**The claim, stated precisely.** SQL cannot express *a single query* whose traversal depth is
decided by the data. A warehouse can still reach the same answers via a recursive CTE or
application-side looping — so the advantage is that the traversal is one declarative,
indexed pattern executed next to the data, not that the answer is unobtainable elsewhere.
Overclaiming here is the fastest way to lose a technical audience.

Evidence base: [`results/pillar_superiority_verdict.md`](../results/pillar_superiority_verdict.md),
[`results/insider_interlock_proof.md`](../results/insider_interlock_proof.md),
[`results/control_chain_analysis.md`](../results/control_chain_analysis.md), and the live
head-to-head [`results/graph_native_proof.md`](../results/graph_native_proof.md).

---

## Architecture — three layers

```
Layer 3  MCP server (FastMCP)   ── curated read-only tools ─► Claude Desktop / any agent
Layer 2  Query core library     ── control_chain / board_path / coalition / snapshot (+evidence, abstain)
Layer 1  Reproducible secgraph  ── build orchestrator + refresh/freshness + schema contract
```

Each layer is independently testable and each higher layer is thin. The query core is the asset:
read-only, deterministic, evidence-returning, abstain-capable — and transport-agnostic, so the MCP
layer is a ~10-line adapter that could be swapped for REST without touching a traversal.

### Layer 1 — Reproducible `secgraph`

`secgraph` is a standalone Neo4j Enterprise database, separate from the main company graph.

- **Build:** `python scripts/build_secgraph.py --database secgraph [--execute]`
  (`make secgraph-build` / `secgraph-build-exec`). Dry-run by default: prints the full phased
  plan and writes nothing. `--execute` runs, aborting on the first failure. Phases:
  1. create DB + ownership constraints
  2. load the Company universe (SEC filers with a ticker)
  3. stage + load Form 3/4/5 insiders → **density GO/NO-GO gate (fail-closed)** —
     the build aborts if the insider layer is too sparse to support the wins
  4. materialize the derived `SHARES_DIRECTOR` board-interlock edge
  5. load 13D/13G beneficial owners → extract control-vs-stake figures →
     materialize `CONTROLS` + `SAME_ENTITY_AS` and `CO_TARGETS` (the traversable
     derived graph the CHAIN and COALITION wins run over)
  6. build the CUSIP-9→CIK crosswalk → load 13F institutional holdings
- **Refresh:** `--refresh [--execute]` (`make secgraph-refresh` / `-exec`). Incremental
  re-pull of recent quarters, reload of changed slices, then idempotent re-materialization of
  `SHARES_DIRECTOR` and resumable re-extraction of control on new 13D edges. Skips DB creation
  and the one-time gate.
- **Freshness manifest:** a successful run writes
  [`results/secgraph_freshness.json`](../results/secgraph_freshness.json) — per-layer row
  counts + max `filing_date`. Every served answer carries an **"as of"** stamp from it (the
  newest 13D/13G filing date — the one trustworthy dated layer).

### Layer 2 — Query core (`ingestion/ownership/intelligence.py`)

`OwnershipIntelligenceEngine` — read-only, LLM-free, deterministic Cypher with anchor
resolution, evidence assembly, and a `format_answer()` renderer. Reuses the already-unit-tested
traversal math from `graph_native_proof.py` (`build_control_adjacency`,
`enumerate_control_chains`, `coalition_components`, `component_diameter`, `is_custodial_hub`).
Every method returns an `OwnershipIntelligenceResult` that **either carries graph-grounded
evidence or abstains** (`abstained=True`) — it never fabricates a chain from sub-50% or
unclassified stakes, and never invents a path that does not exist.

### Layer 3 — Curated MCP server (`mcp/ownership_server.py`)

`create_ownership_mcp_server(driver, database="secgraph", read_only=True)` returns a FastMCP
server. **Curated tools, not raw text2cypher** — each wraps exactly one Layer-2 method, so the
graph cannot be mutated through this surface (there is no Cypher passthrough; `read_only=False`
is refused). Serve it over stdio:

```bash
python scripts/serve_ownership_mcp.py --database secgraph
```

Drop into Claude Desktop / this project via [`.mcp.json`](../.mcp.json).

---

## Tool catalog (what a finance pro asks)

Ordered by demo value — the timing and coalition questions are what an event-driven desk asks;
the rest is supporting context. See [`docs/demo_script_activist_desk.md`](demo_script_activist_desk.md)
for the scripted walkthrough.

| Tool | Ask it | Returns |
| --- | --- | --- |
| **`activist_convergence(since)`** | "What's heating up?" | issuers where ≥N recognised activist franchises filed 13D within a rolling window, with the sequence |
| **`campaign_timeline(company)`** | "Who moved first on X, and who followed?" | every dated 13D/13G in order, each filer classed activist / insider / passive-index / custodian, + the day-gap per follower |
| **`activist_coalition(activist)`** | "Who operates as a de-facto coalition with X?" | the scrubbed connected component + diameter + shared-target evidence |
| `ownership_snapshot(company)` | "Who owns X, and is anyone in control?" | top holders, board size, whether a verified ≥50% control edge exists |
| `control_chain(company, direction)` | "Trace control up/down from X." | the transitive ≥50% chain with `percent_of_class` + accession number. **Small-cap screen** — see limits |
| `board_interlock_path(from, to)` | "*Who* bridges A's and B's boards?" | the path + the **named bridging director**. Read the director, not the path's existence |
| `get_secgraph_schema()` | (grounding) | the curated ownership schema + honest limits |

**Reproducible headline answers** (live against `secgraph`):

- `activist_convergence(since='2023-01-01')` → 8 issuers, incl. **MNRO** (GAMCO → Icahn, 96 days),
  **SION** (OrbiMed → RA Capital, 5 days), **GDV** (Saba raids a Gabelli fund; GAMCO defends).
- `campaign_timeline("MNRO")` → GAMCO **4.0%** on 2025-08-01, then **Icahn 14.79%** exactly
  **96 days later**; BlackRock/Dimensional correctly labelled index money, Nomura a custodian.
- `activist_coalition("ICAHN CARL C")` → the **16-member** scrubbed cluster, ~5 hops
  (Icahn, GAMCO/Gabelli, Bulldog/Goldstein, Karpus, Saba, Cannell, Royce, Dolan/Malone/Maffei).
- `control_chain("Income Opportunity Realty", "up")` →
  Basic Capital → American Realty (62%) → Transcontinental (83%) → Income Opportunity (85%).
- `board_interlock_path("AAPL", "JPM")` → AAPL — JPM *via BELL JAMES A*.
- `control_chain("AAPL")` → **abstains** (no verified ≥50% control edge) — the honesty machinery
  working as designed.

> **Note on the coalition figure.** This was previously reported as 22 members. The custodial
> scrub matched the substring `RBC`, which does **not** match `ROYAL BANK OF CANADA` — so RBC,
> Toronto Dominion, Lazard, City of London and Ohio PERS were being counted as activists.
> Correcting the name list removed six non-activists. **A precision fix, not a change in the
> data** — and 16 is the stronger claim, because every remaining name is a real activist or
> control person.

---

## Honest limits (part of what makes it defensible)

- **CIK-keyed only.** Understates family/affiliate structure; deliberately conservative — no
  fuzzy name-matching that would inflate precision claims.
- **No prediction / alpha claims.** Settled negative (`results/alpha_lens_backtest.md`).
- **Temporal trust is layer-specific.** 13D `filing_date` is real 1994→present history (the
  "as of" source). `DIRECTOR_OF`/`OFFICER_OF` are a 2023–2026 keep-latest **snapshot**, not a
  time series. 13F `HOLDS` is quarterly with the 2024 coverage step-up excluded below the
  trend threshold — feature the cross-section, not a trend.
- **Truth-in-inclusion.** Noise is handled by re-ranking / labelling / the custodial-hub scrub,
  never by dropping true facts. Custodians and index funds are flagged `is_custodial` on the node
  and excluded *at projection time*, so the co-filing fact survives in the graph.
- **No raw Cypher over the served surface.** Curated tools only — safer and more reliable in a
  POC than a text2cypher passthrough.
- **No materiality data.** `Company` nodes carry `cik / ticker / name / sector / sic_code /
  state_of_incorp` — and **no market cap, size or financials** on any of the 8,046 issuers. Nothing
  can be ranked by "does this matter"; the user must bring their own universe filter. *This is the
  largest remaining gap for a finance audience.*
- **Control chains are a small-cap instrument.** Every verified ≥50% chain in this dataset is a
  micro/nano-cap issuer (only Embraer is a widely-recognised name). Genuine as a governance and
  minority-holder-risk screen; not a large-cap feature.
- **Board-interlock path *existence* is uninformative.** Measured over a 60-pair sample of
  well-connected companies, **every** pair links within 4 hops (5 at 1 hop, 18 at 2, 27 at 3,
  10 at 4). Lead with the named bridging director and with board centrality
  (PG 101 interlocks, HPQ 90, AIG 90, GE 88), never with "these two are connected."
- **Activist screens trade recall for precision.** `activist_convergence` matches a curated
  franchise list. Ungated detection is dominated by micro-cap founders crossing 5% and by
  filing-group artifacts (one manager filing through seven affiliated vehicles), so a first-time
  or unlisted activist is missed by design. The trade is reported in the result metadata.

---

## Verify

```bash
# Query-core + MCP + orchestrator unit tests (no live DB)
pytest tests/unit/test_ownership_intelligence.py \
       tests/unit/test_ownership_mcp_server.py \
       tests/unit/test_secgraph_pipeline.py -v

# Schema contract stays consistent after the BENEFICIAL_OWNER_OF property additions
pytest tests/unit/test_schema_consistency.py

# Live: serve the tools and reproduce the headline answers from Claude Desktop
python scripts/serve_ownership_mcp.py --database secgraph

# Dry-run the reproducible build (prints the phased plan, writes nothing)
python scripts/build_secgraph.py --database secgraph
```

See also [`docs/demo_script_activist_desk.md`](demo_script_activist_desk.md) for the scripted
five-question walkthrough, and the schema contract in
[`schema/graph_schema.yaml`](../schema/graph_schema.yaml) (`BENEFICIAL_OWNER_OF`,
`SHARES_DIRECTOR`).
