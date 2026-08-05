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
implementations are cross-checked (they agree: 22 chains / 25-member coalition).

**The claim, stated precisely.** SQL cannot express *a single query* whose traversal depth is
decided by the data. A warehouse can still reach the same answers via a recursive CTE or
application-side looping — so the advantage is that the traversal is one declarative,
indexed pattern executed next to the data, not that the answer is unobtainable elsewhere.
Overclaiming here is the fastest way to lose a technical audience.

**How these three were chosen.** Each candidate was tested against a three-competitor bar: it had
to beat vector/semantic retrieval, beat flat SQL on a warehouse, *and* be useful to a desk.
Several plausible-looking candidates failed the SQL leg and were dropped from the pitch rather
than dressed up — a stake-plus-board-seat overlap, a shared-top-holder set, and a 13G→13D flip
screen are each reproducible with one `GROUP BY` and a self-join, so they are honestly SQL-class,
not graph wins. Only the three above need a traversal whose depth the data decides.

The live head-to-head is reproducible here: [`results/graph_native_proof.md`](../results/graph_native_proof.md)
(regenerate with `make prove`), which runs the graph and flat-SQL legs side by side and reports
whether they agree.

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
the rest is supporting context. See [`docs/demo_script_governance_desk.md`](demo_script_governance_desk.md)
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

- `activist_convergence(since='2023-01-01')` → 5 issuers, incl. **MNRO** (GAMCO → Icahn, 96 days),
  **SION** (OrbiMed → RA Capital, 5 days), **GDV** (Saba raids a Gabelli fund; GAMCO defends).
- `campaign_timeline("MNRO")` → GAMCO **5.01%** on 2025-08-01, then **Icahn 14.79%** exactly
  **96 days later**; BlackRock/Dimensional correctly labelled index money, Nomura a custodian.
- `activist_coalition("ICAHN CARL C")` → the scrubbed cluster: **25 CIKs / 21 distinct actors**,
  ~5 hops (Icahn, GAMCO/Gabelli, Bulldog, Karpus, Saba, Cannell, Royce, Malone, Glenview,
  Cascade, the Dolans, B. Riley, …). Both counts are reported: affiliated vehicles of one
  manager are collapsed for `distinct_actors`, since 3 Bulldog CIKs are 1 firm.
- `control_chain("Income Opportunity Realty", "up")` →
  Basic Capital → American Realty (62%) → Transcontinental (83%) → Income Opportunity (85%).
- `board_interlock_path("AAPL", "JPM")` → AAPL — JPM *via BELL JAMES A*.
- `control_chain("AAPL")` → **abstains** (no verified ≥50% control edge) — the honesty machinery
  working as designed.

> **Note on the coalition figure.** It has moved three times, for three different reasons, and
> conflating them is how a headline number loses credibility.
>
> **22 → 16 was a precision fix.** The custodial scrub matched the substring `RBC`, which does
> **not** match `ROYAL BANK OF CANADA` — so RBC, Toronto Dominion, Lazard, City of London and Ohio
> PERS were counted as activists. Correcting the name list removed six non-activists. No
> underlying data changed.
>
> **16 → 13 was data-window drift.** Under `--as-of 2026-06-30` the Dolan/Maffei Liberty Media
> sub-cluster stopped reaching Malone: the ≥2-shared-target edge bridging them fell outside the
> window.
>
> **13 → 25 is coverage.** The crawl now prioritises **original** 13D filings over amendments
> (EDGAR returns newest-first, so a newest-40 cap was dropping originals entirely). Filers whose
> originals predate that window became visible for the first time — including the Dolans again.
> Nothing was scrubbed and nothing broke.
>
> Two counts are now reported: **25 CIKs / 21 distinct actors**, the latter collapsing affiliated
> vehicles (three Bulldog CIKs are one firm; Goldstein is its principal; Digirad renamed to Star
> Equity). A component's membership is emergent — the property that makes this graph-native also
> means the count tracks the data window, so both memos under `results/` carry a provenance line.


---

## A second reading: the same traversal as a UBO / counterparty chain

The demo above is written for an activist desk, because that is the walkthrough that works. But
`CHAIN` is worth reading twice, because **it is literally an ultimate-beneficial-ownership
traversal** — parent → subsidiary → sub-subsidiary through verified ≥50% stakes, hard-keyed on CIK,
every hop citing an SEC accession number. Nothing has to be built for that reading; it is the same
Cypher:

```
control_chain("TMUS")  →  DEUTSCHE TELEKOM AG —74.3%→ T-Mobile US, Inc.  ($92.6B institutional)
                          cited: 0001193125-13-214474 (2013-05-10)
```

That matters commercially because the budget behaves differently. An event-driven desk buys
finished signals, not infrastructure — and this asset explicitly has no alpha (see
[Honest limits](#honest-limits-part-of-what-makes-it-defensible)). Risk, compliance and stewardship
functions buy *auditability*, and their spend is triggered by exam findings and reporting deadlines
rather than by conviction. The absence of alpha stops being an apology in that framing.

**What already supports it, with no new code:**

- **Abstain-or-cite is enforced, not aspirational.** `abstained=True` is a typed field with
  machine-readable reasons (`no_verified_control_chain`, `company_not_found`), and an answer never
  comes from a `stake`/`unknown` edge. Ten of ten mega-caps abstain on `control_chain` — the tool
  says "no" far more often than it says "yes".
- **Every hop is independently citable.** The traversal returns parallel `accessions` /
  `filing_dates` / `pcts` arrays, so a four-hop chain yields four filings, not one summary.
- **The serving surface cannot write.** `read_only=False` *raises*; there is no Cypher passthrough;
  all seven tools carry `readOnlyHint=True`. The scrubs and thresholds that make answers correct
  cannot be bypassed by a caller.
- **Provenance travels with the answer.** Every response carries the `as_of` stamp and the build
  records its inputs — the pinned as-of date, the resolved staging windows, and whether control
  figures came from the committed CSV or a live model run.
- **Deterministic.** The served path is LLM-free Cypher. The one model-dependent step (reading
  percent-of-class off 13D cover pages) runs offline at build time and is pinned to a committed
  CSV, so an answer is reproducible without an API key.

**What this reading does *not* support — and these are disqualifying for a sanctions use case:**

- **No sanctions or watchlist data.** There is no OFAC/SDN label, no jurisdiction, no country. A
  screening traversal has no seed set. Calling this "sanctions screening" would be
  aggregate-ownership detection wearing a compliance label.
- **No aggregate ownership roll-up.** `CONTROLS` fires on a *single* ≥50% stake. The OFAC 50% rule
  needs the *sum* across converging paths, and this data cannot support that: 13D co-filers each
  restate the group total (twelve McCann family members each report the same 38.9% of
  1-800-FLOWERS, so a naive sum reads 485%), 546 issuers sum above 100%, there is no filing-group
  identifier to collapse them, and the 13G layer carries **no** percent data at all.
- **US SEC registrants with a ticker only** — ~8,000 issuers. No private companies, no foreign
  subsidiaries, no global corporate hierarchy. Comprehensive UBO needs a data apparatus this
  deliberately does not have.

The honest positioning is a **US-public-company control and governance layer with citation-grade
provenance**, not a UBO product. That is a narrower claim than the traversal could be made to look
like, and it is the one the data actually earns.

---

## Honest limits (part of what makes it defensible)

- **CIK-keyed only.** Understates family/affiliate structure; deliberately conservative — no
  fuzzy name-matching that would inflate precision claims.
- **No prediction / alpha claims.** This was tested directly — a backtest looking for return
  predictability around these ownership events — and came back null. Efficient markets; 13D
  filings are public the moment they land. Treat this as a structural and temporal map, not a
  signal.
- **Temporal trust is layer-specific.** 13D `filing_date` is real 1994→present history (the
  "as of" source). `DIRECTOR_OF`/`OFFICER_OF` are a keep-latest **snapshot** over the staged
  Form 3/4/5 window (currently 2022q3–2026q2; see the freshness manifest), not a
  time series. 13F `HOLDS` is quarterly with the 2024 coverage step-up excluded below the
  trend threshold — feature the cross-section, not a trend.
- **Truth-in-inclusion.** Noise is handled by re-ranking / labelling / the custodial-hub scrub,
  never by dropping true facts. Custodians and index funds are flagged `is_custodial` on the node
  and excluded *at projection time*, so the co-filing fact survives in the graph.
- **No raw Cypher over the served surface.** Curated tools only — safer and more reliable in a
  POC than a text2cypher passthrough.
- **Size is a threshold, not a market cap — two measures, both labelled.** `size_usd`
  (`materialize_materiality.py`) prefers `total_assets_usd`, a **filed balance-sheet total** parsed
  from the SEC Financial Statement Data Sets (`load_company_financials.py`, **63%** of the
  universe, citable via `total_assets_accession`), and falls back to `institutional_value_usd`, one
  quarter of 13F holdings (**75%**). Combined coverage **83%**; `size_source` records which applied.
  This closes what an earlier version of this document named as the remaining gap — *"which is
  where SEC DERA XBRL would earn its keep if the audience shifts to credit risk"* — because the 13F
  proxy measures **free float**, and float is smallest exactly where ownership is concentrated. The
  effect is measurable: controlled issuers at ≥$10B went **20 → 39** and at ≥$1B **97 → 150**, and
  EchoStar ($43.0B assets, 51.8% controlled) has *no* 13F coverage at all, so it could not
  previously appear in any size-filtered result.
  Limits, each specific to its measure: 13F understates concentrated ownership and counts ETFs
  (SPY/QQQ rank high on other people's money); total assets are **not comparable across sectors**
  (a bank's assets *are* its balance sheet), are point-in-time, and are absent for ETFs, funds and
  many foreign filers. **17% of the universe has neither** and is excluded from size-filtered
  results rather than ranked — `influence_map` now reports that count as `excluded_no_size` instead
  of dropping the rows silently. Still **no revenue and no true market cap**, so leverage and
  coverage ratios remain out of scope.
- **Chain *depth* is a small-cap signal; single-hop control is not.** Measured on the built graph,
  **39 of 825** controlled issuers are ≥$10B by `size_usd` and **150** are ≥$1B (20 and 97 under
  the float-only proxy) — Deutsche Telekom 74.3% of T-Mobile US, Ergen 51.8% of EchoStar,
  Brookfield 72.9% of Brookfield Asset Management, GE 62.6% of Baker Hughes, Woodbridge 70% of
  Thomson Reuters. The **multi-hop** pyramids remain small-cap
  (the largest, Teekay Tankers, is ~$1.5B), so depth is the governance/minority-risk screen while
  single-hop control is general-purpose. This corrects an earlier claim that *every* verified chain
  was micro/nano-cap: the large ones were always present, but with no size column the output never
  surfaced them.
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

See also [`docs/demo_script_governance_desk.md`](demo_script_governance_desk.md) for the scripted
five-question walkthrough, and the schema contract in
[`schema/graph_schema.yaml`](../schema/graph_schema.yaml) (`BENEFICIAL_OWNER_OF`,
`SHARES_DIRECTOR`).
