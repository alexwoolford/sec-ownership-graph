# SEC Ownership Graph

**Every SEC ownership filing as one graph, keyed on CIK — so you can ask who moved first on a
target, who operates as a coalition, and who really controls an issuer.** Built on Neo4j. Every
answer cites an SEC accession number, and abstains when the data doesn't support one.

Data: Schedule 13D/13G (beneficial ownership), Form 3/4/5 (insiders and directors), Form 13F
(institutional holdings). Straight from EDGAR — no paid data vendor. Depth per layer, and what
that does and doesn't support, is in [Honest limits](#honest-limits).

---

## The demo in one question

> *"Which companies have had multiple activists show up recently — and who moved first?"*

```
$ make demo

Activist convergence — 5 issuers:

  MNRO — MONRO, INC. (2 franchises within 96 days)
     2025-08-01  GAMCO INVESTORS, INC. ET AL 5.01%
     2025-11-05  ICAHN CARL C 14.79%
  ...

Ownership timeline — MONRO, INC.:
  2025-01-23  13G           DIMENSIONAL FUND ADVISORS LP (passive_index)
  2025-04-29  13G           BlackRock, Inc. (passive_index)
  2025-05-15  13G           NOMURA HOLDINGS INC (custodian)
  2025-08-01  13D    5.01%  GAMCO INVESTORS, INC. ET AL (activist)
  2025-11-05  13D   14.79%  ICAHN CARL C (activist)

First mover: GAMCO INVESTORS, INC. ET AL on 2025-08-01 at 5.01%
  → ICAHN CARL C followed 96 days later at 14.79%
```

Note what the graph does that a filings search cannot: it separates the two *activists* from the
index money and the custodian **inside the same filing type**, and it puts them in order.

**→ Full walkthrough: [`docs/demo_script_activist_desk.md`](docs/demo_script_activist_desk.md)**
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
| **OpenAI key** | Only if `reference/control_figures.csv` is absent. Control extraction is the one LLM step; with the committed figures, a build needs no key at all. |
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

`schema/graph_schema.yaml` is the single source of truth — 4 node labels, 9 relationship types.
`tests/unit/test_schema_consistency.py` scans every `.py` file and fails the build if any Cypher
references something undeclared.

**Architecture detail: [`docs/reference_architecture_secgraph.md`](docs/reference_architecture_secgraph.md)**

---

## Honest limits

Read these before demoing — they are part of what makes the rest credible.

- **No prediction.** The alpha question was tested and came back null. Efficient markets; 13Ds are
  public. This is a structural and temporal map, not a signal.
- **Size is a proxy, not a market cap.** `Company.institutional_value_usd` sums one quarter of
  13F holdings so results *can* be ranked by materiality — but it measures **free float** (so it
  understates concentrated-ownership issuers, conservatively), is **null for ~25% of the universe**
  (no institutional coverage — which is itself a signal, not a zero), and counts ETFs. There is
  still no revenue, assets or true market cap. Bring your own universe filter for anything
  fundamental.
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
- **Chain *depth* is a small-cap signal; single-hop control is not.** 20 of 825 controlled issuers
  carry ≥$10B of institutional ownership — Deutsche Telekom holds 74.3% of T-Mobile US, GE held
  62.6% of Baker Hughes. But the **multi-hop** pyramids top out near $1.5B, so read a deep chain as
  a small-cap governance screen and a single-hop one as general-purpose. Most large caps have no
  ≥50% holder at all and correctly abstain.
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
