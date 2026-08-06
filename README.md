# SEC Ownership Graph

**Every SEC ownership filing as one graph, keyed on CIK — so you can ask who moved first on a
target, who operates as a coalition, and who really controls an issuer.** Built on Neo4j. Every
answer cites an SEC accession number, and abstains when the data doesn't support one.

Data: Schedule 13D/13G (beneficial ownership), Form 3/4/5 (insiders and directors), Form 13F
(institutional holdings). Straight from EDGAR — no paid data vendor. Depth per layer, and what
that does and doesn't support, is in [Honest limits](#honest-limits).

**New to SEC filings?** [`docs/data_sources_and_forms.md`](docs/data_sources_and_forms.md) explains
what each form is, what triggers it, and exactly which node or edge it became — including which
four edges are *derived* rather than filed. Start there if the form names above aren't familiar.

---

## The demo in one question

> *"Who can actually move this company — and can you prove it from two independent filings?"*

```
$ make demo

Stake + board seat — issuers where a holder clears a Fed control-presumption tier
AND currently sits on the board. Two independent filing types agreeing:

  TICKER        SIZE   STAKE  13D    SEAT     HOLDER
  BRK-B      $479.9B   37.0%  2024   2026-05  BUFFETT WARREN E
  TMUS        $92.6B   74.3%  2013   2025-10  DEUTSCHE TELEKOM AG
  CHTR        $22.4B   26.1%  2014   2026-06  Liberty Broadband Corp
  RDDT        $20.5B   61.5%  2024   2026-06  Huffman Steve Ladd
```

The stake comes from a Schedule 13D; the board seat comes from Form 3/4/5. A screener sells you
either list — the *pairing* is the finding, and it is the join a single-table query cannot do for
you. Note the two date columns: Liberty Broadband declared 26.1% of Charter in **2014**, and its
director was on file in **2026**. A 13D has no exit obligation below 5%, so an old stake proves
nothing alone; the current seat is what corroborates it.

And when there is nothing to say, it says so — ten of ten mega-caps abstain on control:

```
$ control_chain("AAPL")
No graph-grounded answer for 'Apple Inc.' (no_verified_control_chain).
Issuer has no >=50% verified 13D control edge on a chain.
```

**→ Full walkthrough: [`docs/demo_script_governance_desk.md`](docs/demo_script_governance_desk.md)**
(five questions, ~10 minutes, including what this *cannot* tell you.)

---

## What is actually graph-native here

Three questions need a relationship followed to a depth the data decides — one declarative Cypher
pattern, executed next to the data:

| Question | Cypher | Why a traversal |
| --- | --- | --- |
| Who ultimately controls this issuer? | `(root)-[:CONTROLS\|SAME_ENTITY_AS*1..N]->(target)` | chain depth is unknown up front |
| Who operates as a coalition with X? | `(seed)-[:CO_TARGETS*0..N]-(m)` | the component is emergent, not a fixed join |
| Who bridges these two boards? | `shortestPath((a)-[:SHARES_DIRECTOR*..N]-(z))` | reachability, not "who sits on ≥2 boards" |

Stated precisely, because overclaiming here loses technical audiences: **SQL cannot express a
single query whose traversal depth is decided by the data.** A warehouse can still reach the same
answers with a recursive CTE. The advantage is one indexed declarative pattern next to the data —
not that the answer is unobtainable elsewhere.

`make prove` runs both legs for real — Cypher inside Neo4j, and genuine `WITH RECURSIVE` CTEs over
identical rows — and **publishes the agreement, with timings for both**. At this scale SQL is
*faster* on the chain (~0.7 ms vs ~13 ms) and slower on the path (~20 ms vs ~1 ms). Anyone
evaluating this should read those numbers before the prose.

---

## Quickstart

```bash
# 1. Install (project-local venv — required for the MCP launcher)
uv venv && source .venv/bin/activate
uv pip install -e ".[dev,llm]"   # or: make install
cp .env.sample .env              # fill in NEO4J_PASSWORD and SEC_USER_AGENT

# 2. Check preconditions in seconds, before committing to a multi-hour build
make preflight

# 3. Point at a graph. Either build it (hours — downloads from EDGAR):
make build                  # dry-run: prints the full phased plan + preflight, writes nothing
make build-exec             # for real

# 4. Ask it things
make demo                   # the activist convergence screen
make prove                  # graph vs SQL, head to head
make smoke-mcp              # curated tool catalog + demo queries against the live DB
make serve                  # curated MCP tools over stdio (or use Cursor — see below)
```

### What a build needs

| | |
| --- | --- |
| **Neo4j** | **Enterprise** (or Docker `neo4j:enterprise`) **+ the GDS plugin**. Phase 0 runs `CREATE DATABASE`; the density gate runs `gds.wcc.write`. Community/Aura expose one database — use `make build-exec DB=neo4j`. |
| **`SEC_USER_AGENT`** | **Required.** SEC fair access rejects generic agents with HTTP 403. The built-in default is a placeholder on the reserved domain `example.com`; `make preflight` refuses to start until you set a real contact string. |
| **OpenAI key** | **Effectively required for a fresh build.** `reference/control_figures.csv` covers only the EDGAR window it was exported from, so any 13D filed since needs classifying — and the build runs that gap fill unconditionally rather than leaving those edges unclassified (an unclassified edge silently vanishes from control and influence answers). It is cheap and scales with the gap: a regex resolves ~93% of edges for free, so only the remainder reaches `gpt-4o-mini` — **~$0.21 to classify all 10.6k edges**, cents for a year of drift, and **$0 when the CSV is already current**. Pass `--skip-uncovered` to build without a key and accept a knowingly incomplete layer. |
| **Time & disk** | Several hours, ~20 GB. Dominated by the ~8,000-issuer 13D/G crawl and the 13F load, both bounded by SEC's 10 req/s ceiling. |

`make preflight` checks every one of these. Each used to surface only after minutes-to-hours of
crawling, as a generic non-zero exit from a child script.

### Curated MCP (Cursor / Claude Desktop)

Seven read-only tools — `activist_convergence`, `campaign_timeline`, `activist_coalition`,
`ownership_snapshot`, `control_chain`, `board_interlock_path`, `get_secgraph_schema`. Curated,
not raw text2cypher: no Cypher passthrough and no write path, so the scrubs and thresholds that
make the answers correct cannot be bypassed.

**Cursor:** open this repo; [`.cursor/mcp.json`](.cursor/mcp.json) uses `${workspaceFolder}` and
[`scripts/run_ownership_mcp.sh`](scripts/run_ownership_mcp.sh) so a clone needs no path edits
after `.venv` exists. Enable **secgraph-ownership**, reload MCP, then ask the demo questions in
natural language. If a user-level `~/.cursor/mcp.json` also defines `secgraph-ownership`, point
it at the same launcher (bare `python scripts/serve_ownership_mcp.py` fails outside the venv).

**Claude Desktop:** root [`.mcp.json`](.mcp.json) is the same shape; Claude does not expand
`${workspaceFolder}` — set `command` once to the absolute path of
`scripts/run_ownership_mcp.sh` (the launcher still finds `.venv` relative to the repo).

`make smoke-mcp` proves the tool catalog and the demo queries against a live `secgraph` DB
without an MCP client.


---

## How it is built

```
Layer 3  MCP server        curated read-only tools ─► Claude Desktop / any agent
Layer 2  Query core        traversals + evidence + abstain (transport-agnostic)
Layer 1  Reproducible DB   phased build, density gate, freshness manifest
```

`make build` runs the phases in order, aborting on the first failure: create the database →
load the filer universe → stage and load Form 3/4/5 insiders → **density GO/NO-GO gate**
(fail-closed: the build stops if the insider layer is too sparse to support the wins) →
materialize the derived edges (`SHARES_DIRECTOR`, `CONTROLS`, `SAME_ENTITY_AS`, `CO_TARGETS`) →
load 13D/13G and 13F. A successful run writes `results/secgraph_freshness.json`, which every
served answer reports as its "as of" date.

`schema/graph_schema.yaml` is the single source of truth — 4 node labels, 10 relationship types.
`tests/unit/test_schema_consistency.py` scans every `.py` file and fails the build if any Cypher
references something undeclared.

**Architecture detail: [`docs/reference_architecture_secgraph.md`](docs/reference_architecture_secgraph.md)**
**What the forms mean and where each element came from: [`docs/data_sources_and_forms.md`](docs/data_sources_and_forms.md)**
**Bloom demo runbook (six acts, ~18 min): [`docs/demo_runbook_bloom.md`](docs/demo_runbook_bloom.md)**
**Bloom visualization build brief: [`docs/bloom_perspective_spec.md`](docs/bloom_perspective_spec.md)**
**Field-level schema (generated): [`docs/graph_schema.md`](docs/graph_schema.md)**

---

## Honest limits

Read these before demoing — they are part of what makes the rest credible.

- **No prediction.** The alpha question was tested and came back null. Efficient markets; 13Ds are
  public. This is a structural and temporal map, not a signal.
- **Size is a threshold, not a market cap — and there are two measures.** `size_usd` is what
  filters and ranks: it prefers `total_assets_usd`, a **filed balance-sheet total** from the SEC
  Financial Statement Data Sets (**63%** of the universe), and falls back to
  `institutional_value_usd`, one quarter of 13F holdings (**75%**). Combined coverage is **83%**;
  `size_source` records which applied, and **17% have neither** and are excluded from
  size-filtered results rather than ranked. Each has a distinct limit: 13F measures **free float**,
  so it understates concentrated-ownership issuers and counts ETFs; total assets are **not
  comparable across sectors** — a bank's assets *are* its balance sheet, so JPMorgan's $4.4T is not
  "bigger than" a $200B industrial in any meaningful sense. Still **no revenue and no true market
  cap**, so this does not support leverage or coverage ratios.
- **Activist screens trade recall for precision.** Gated to a curated franchise list; ungated
  detection is dominated by micro-cap founders crossing 5% and by filing-group artifacts (one
  manager filing through seven affiliated vehicles). First-time activists are missed by design.
- **Only 13D/13G dates are a time series.** Board and officer edges are a keep-latest
  snapshot; 13F has a 2024 coverage step-up. Don't read trends into them.
- **13D/13G history is capped, so "1994→present" holds only for light filers.** The crawl reads
  each issuer's `filings.recent` (~1,000 most recent filings) and takes at most 40 Schedule
  13D/G per subject. For a company that files hundreds of Form 4s a year, `recent` may reach back
  only a year or two, and its older 13D/Gs are invisible. Small caps — where the control chains
  are — get the full history; mega caps do not.
- **At this scale, a warehouse is a real alternative.** The three wins run on ~1,100 derived
  edges, measured depth is overwhelmingly 1-2 hops with a maximum of 3, and a recursive CTE
  answers all three in single-digit milliseconds. The honest case for a graph here is authoring
  cost per *new* question, GDS algorithms with no SQL equivalent (Louvain, betweenness), and the
  curated serving layer — **not** tractability. Tractability would only become the argument at a
  far larger universe than 8,000 tickered issuers.
- **Rebuilds drift, by construction.** The staging window resolves against the run date, and
  EDGAR keeps accruing filings, so a rebuild today will not reproduce the figures below exactly.
  New 13Ds can also *remove* a convergence hit, because the screen measures a total span rather
  than a rolling window. Compare against `results/secgraph_freshness.json` (`as_of`) before
  concluding something broke.
- **Chain *depth* is a small-cap signal; single-hop control is not.** **39 of 825** controlled
  issuers are ≥$10B by `size_usd` and **150** are ≥$1B — Deutsche Telekom holds 74.3% of T-Mobile
  US ($219.2B in assets), Ergen holds 51.8% of EchoStar ($43.0B). Those counts were **20 and 97**
  when size was 13F float alone: a controlled issuer has little float by definition, so the float
  proxy was hiding exactly this population — EchoStar has *no* 13F coverage at all. But the
  **multi-hop** pyramids top out near $1.5B, so read a deep chain as a small-cap governance screen
  and a single-hop one as general-purpose. Most large caps have no ≥50% holder and correctly
  abstain.
- **Board-interlock path *existence* is uninformative.** Measured: every well-connected pair links
  within 4 hops. The named bridging director is the signal, not the connection.
- **CIK-keyed only.** Deliberately conservative — understates family/affiliate structure rather
  than inventing links through fuzzy name matching.
- **Custodians and index funds are labelled, not deleted.** They're excluded at query time so the
  underlying co-filing facts stay in the graph and the precision choice stays auditable.

## Development

```bash
make test        # unit suite: fully mocked, no database needed
make check       # lint + tests (note: `make lint` rewrites source via ruff --fix)
make preflight   # verify build preconditions against your Neo4j
```

There is no CI. `make check` is the stand-in; `pre-commit install` gets ruff + gitleaks on
commit.

## License

MIT. SEC filing data is public domain.
