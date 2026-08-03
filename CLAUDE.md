# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

## What this is

A reproducible Neo4j graph of SEC ownership relationships — Schedule 13D/13G, Form 3/4/5, Form 13F
— hard-keyed on CIK, with a curated read-only MCP serving layer. Python package `secgraph` plus
thin `scripts/` wrappers. Target database: `secgraph`.

The point of the project is the handful of questions that need a relationship followed to a
data-determined depth: activist campaign timing, activist coalitions, transitive control chains,
board interlocks. See `docs/demo_script_activist_desk.md` for what it's *for*, and
`docs/reference_architecture_secgraph.md` for how it's assembled.

## Environment & common commands

```bash
pip install -e ".[dev]"
cp .env.sample .env          # fill NEO4J_PASSWORD

make test                    # unit suite (fully mocked, no DB)
make check                   # lint + tests, as CI runs them
make demo                    # activist convergence screen + MNRO timeline
make prove                   # graph vs flat-SQL head-to-head
make serve                   # curated MCP tools over stdio
make build                   # dry-run the phased build; build-exec to run it
```

Tests: `pytest tests/ -m "not integration and not contract"`. Contract tests are **skipped unless
`--run-contract`**. Integration tests skip themselves when `NEO4J_PASSWORD` is unset.

## Core conventions (enforced)

1. **Dry-run by default; `--execute` to write.** A script without `--execute` prints its plan and
   writes nothing. Standardized via `cli/args.py::add_execute_argument`.
2. **MERGE, never CREATE; idempotent and re-runnable.** Loaders batch via `UNWIND $batch`.
   Constraints use `IF NOT EXISTS`. Re-running a materializer must not change counts.
3. **Fail fast, no silent fallbacks** on critical preconditions (connectivity, constraints, the
   density gate).
4. **Sessions are always scoped to a named database:** `driver.session(database=...)`. Never the
   server default. Prefer `neo4j_driver_context()` for one-offs.
5. **Scripts are thin.** argparse + logging + `get_driver_and_database()`, then delegate to the
   library. Real logic lives in `secgraph/`.
6. **Hard keys only.** Join on CIK or CUSIP-9, never on fuzzy name matching. Prefer understating a
   relationship to inventing one.
7. **Truth-in-inclusion.** Never delete a true fact to make output cleaner. Noise is handled by
   labelling and excluding at query time — e.g. custodians carry `is_custodial` on the node and are
   filtered at projection, so the co-filing fact survives and the choice stays auditable.
8. **Evidence or abstain.** Every served answer carries its SEC accession numbers, or sets
   `abstained=True` with a reason. Never answer from inference when the graph has no support.

## Architecture

### Three layers

```
Layer 3  secgraph/mcp/                curated read-only MCP tools (the serving surface)
Layer 2  ingestion/ownership/         query core: traversals + evidence + abstain
Layer 1  ingestion/ownership/pipeline.py  phased reproducible build
```

Layer 2 is the asset and is transport-agnostic; Layer 3 is a ~10-line adapter per tool. A REST
adapter could replace it without touching a traversal.

- **Query cores:** `intelligence.py` (`OwnershipIntelligenceEngine`: control_chain, board_path,
  coalition, ownership_snapshot) and `campaign_timeline.py` (`CampaignTimelineEngine`:
  campaign_timeline, convergence_scan). Both return a structured result + `evidence` + `abstained`
  and expose a static `format_answer()`.
- **MCP server:** `mcp/ownership_server.py::create_ownership_mcp_server` → FastMCP with 7 tools, all
  `readOnlyHint=True`. **Curated tools, not text2cypher** — no Cypher passthrough, no write path;
  `read_only=False` is refused. Tool *order* matters: it steers which tool an agent reaches for, so
  the timing/coalition tools are registered first.
- **Build orchestrator:** `pipeline.py::build_secgraph` shells out to the phase scripts in order,
  aborting on the first non-zero exit. The density gate signals NO-GO via exit code 2 and aborts
  fail-closed. Writes `results/secgraph_freshness.json`.

### The traversal must run in the database

All three wins execute as Cypher variable-length patterns (`VarLengthExpand`) over **materialized**
derived edges. This is load-bearing: an earlier version pulled every edge out with a flat `MATCH`
and walked it in Python, which any warehouse reproduces with a `SELECT` plus a loop — so the
"SQL can't express this" claim was not earned. If you find yourself fetching edges to loop over
them, that is a regression.

The Python adjacency walkers retained in `graph_native_proof.py` are deliberately the **flat-SQL
side** of the head-to-head comparison. Using them for the graph side would make the proof circular.

### Derived edges (materialized, not filed)

| Edge | From | Script |
| --- | --- | --- |
| `SHARES_DIRECTOR` | two boards share ≥1 human director | `materialize_interlock_edges.py` |
| `CONTROLS` | verified ≥50% 13D stake, self-filings excluded | `materialize_control_edges.py` |
| `SAME_ENTITY_AS` | CIK identity bridge — makes chains transitive | same script |
| `CO_TARGETS` | two 13D filers co-target ≥2 issuers | `materialize_cotarget_edges.py` |

`SAME_ENTITY_AS` must be a real edge: a variable-length pattern cannot jump between nodes on a
property equality, so without it `CONTROLS*` stops at one hop.

### Schema contract — the single source of truth

**`schema/graph_schema.yaml`** (4 node labels, 9 relationship types) is loaded at import time by
`schema/contract.py`. **Do not reference a label or relationship type in Cypher that isn't declared
there** — `tests/unit/test_schema_consistency.py` scans every `.py` file and fails the build.
Declare it in the YAML *first*, then write the query, then
`python scripts/generate_schema_docs.py --execute`.

## Gotchas that have cost real time

- **`coalesce()` on nullable properties.** `NONE(n IN nodes(p) WHERE n.is_custodial)` evaluates to
  *null* — not true — when the property is absent, which silently filters out everything. Always
  `coalesce(n.is_custodial, false)`. `schema/validation.py` lints for this.
- **Substring blocklists need expanded legal names.** The custodial scrub matched `"RBC"`, which
  does not match `"ROYAL BANK OF CANADA"` — so RBC and Toronto Dominion were being counted as
  activists and inflated a headline figure. Every abbreviation needs its full legal name listed.
- **Activist detection must be franchise-gated.** Ungated "pile-on" detection surfaces micro-cap
  founders crossing 5% and filing-group artifacts (one manager filing through seven affiliated
  vehicles). `distinct_franchises()` collapses on the matched franchise token, not the filer name.
- **Temporal trust is layer-specific.** Only 13D/13G `filing_date` is a real time series
  (1994→present). `DIRECTOR_OF`/`OFFICER_OF` are a 2023–2026 keep-latest snapshot; 13F `HOLDS` has a
  2024 coverage step-up. Never imply a trend from those.
- **MCP stdio and stdout.** `serve_ownership_mcp.py` configures logging to **stderr** directly,
  because the shared `setup_logging` dry-run path writes to stdout and would corrupt the protocol.

## Changing a published figure

Several numbers appear in `README.md`, `docs/`, and `results/*.md` (e.g. the 16-member coalition,
11 control chains, MNRO's 96-day gap). If a fix changes one, **update every occurrence and state
the reason**. Silently moving a headline number is the fastest way to lose a technical audience;
the precision fix that took the coalition from 22 to 16 is documented for exactly that reason.
