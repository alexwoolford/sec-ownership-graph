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
pip install -e ".[dev,llm]"   # NOT just [dev] — that omits openai, which the build needs
cp .env.sample .env           # fill NEO4J_PASSWORD *and* SEC_USER_AGENT

make test                    # unit suite (fully mocked, no DB)
make check                   # lint + tests
make preflight               # verify build preconditions in seconds (writes nothing)
make demo                    # activist convergence screen + MNRO timeline
make prove                   # graph vs flat-SQL head-to-head
make serve                   # curated MCP tools over stdio
make build                   # dry-run the phased build; build-exec to run it
```

**Building requires Neo4j Enterprise (or Docker `neo4j:enterprise`) + the GDS plugin**, because
phase 0 runs `CREATE DATABASE` and the density gate runs `gds.wcc.write`. Community and Aura
expose a single database named `neo4j`; target it with `make build-exec DB=neo4j`.

**`SEC_USER_AGENT` is required.** The built-in default is a placeholder on the RFC-2606 reserved
domain `example.com`, which SEC rejects with HTTP 403. `make preflight` refuses to start until it
is set to a real contact string. Run `make preflight` before any long build — every precondition
it checks used to surface only after minutes-to-hours of EDGAR crawling.

Tests: `pytest tests/ -m "not integration and not contract"`. One file, one test, one case:

```bash
pytest tests/unit/test_campaign_timeline.py                       # one file
pytest tests/unit/test_campaign_timeline.py::test_first_mover     # one test
pytest tests/ -k "coalition and not custodial"                    # by expression
```

**There is no CI.** No `.github/` exists; several docstrings say "runs in CI" aspirationally.
`make check` is the stand-in, and pre-commit (ruff + gitleaks + file hygiene) is the only
enforcement that actually runs on its own — `pre-commit install` after cloning.

`make check` is **not** a read-only verification: `make lint` runs `ruff check --fix` and
`ruff format`, so it rewrites source. Use `ruff check` without `--fix` if you need to inspect
without mutating. `mypy` is configured in `pyproject.toml` but wired into nothing — no make
target, no hook.

**The `integration` and `contract` markers are registered but unused.** `tests/` contains only
`unit/`; no test carries either marker today. `conftest.py`'s `--run-contract` gate and the
`NEO4J_PASSWORD` skip are live scaffolding for lanes that are currently empty — don't go looking
for integration tests that aren't there.

## The reproducibility contract

The demo is meant to be rebuildable from EDGAR, so **every acquisition step must be pinnable**.
`--as-of YYYY-MM-DD` bounds all four (`345`/`FTD`/`13F` staging plus the 13D/G crawl). Without it,
"most recent N quarters" resolves against the run date and two builds a week apart cannot agree.
An unpinned `--execute` run warns about exactly this at the end.

Rules that follow, and the traps behind each:

1. **Never widen a window implicitly.** `staged_zip_paths` globs the *whole* local cache, so a
   machine that once staged 16 quarters loads all 16 while a fresh clone loads 12 — same command,
   different graph. Pass `limit=`/`as_of=`.
2. **Filter by as-of *before* any cap.** `filings_as_of` runs ahead of the 40-filing per-subject
   truncation; reversing them gives a pinned build *less* history than an unpinned one.
3. **Order before you truncate.** `collect()` order is unspecified. `via_ciks[0]` is rendered as
   *the* bridging director, so an unordered collect changed that name between runs on an identical
   graph. Same for `CO_TARGETS` evidence CIKs.
4. **No unseeded randomness in a decision path.** The density gate sampled with `ORDER BY rand()`,
   so the same graph could PASS then FAIL.
5. **Record provenance, not just results.** `collect_provenance` writes the as-of, the *resolved*
   staged periods, and whether control figures came from the committed CSV or a live LLM run. A
   cloner who can't see the inputs can't tell drift from breakage.

Control figures are the LLM-free path: `reference/control_figures.csv` (produced by
`export_control_figures.py`, applied by `load_control_figures.py`) makes `CONTROLS` deterministic.
The pipeline prefers it and falls back to LLM extraction; `--extract-control` runs both so the CSV
seeds and the model fills only newer filings.

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

The phase order in `_steps()` encodes hard data dependencies, not preference. Three that will
silently produce an empty result if reordered: `extract_control_edges` must precede
`materialize_control_edges` (the latter reads `control_class='control'`, which the former writes);
both 13D-derived edges (`CONTROLS`, `CO_TARGETS`) must follow `load_beneficial_owners`; and
`build_cusip_crosswalk` needs the FTD staging step before `load_institutional_holdings` can key 13F
by CUSIP-9. On `--refresh`, DB creation and the gate are dropped (`build_only`) and the
materializers gain `--replace`.

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

`docs/graph_schema.md` is **generated** — never hand-edit it; regenerate from the YAML.

### The density gate (what blocks a build)

`density.py` projects an undirected company↔company graph through shared `DIRECTOR_OF`/`OFFICER_OF`
insiders (`TEN_PCT_OWNER_OF` is deliberately excluded — a stake is not a governance tie and dominant
holders would create spurious hubs), runs `gds.wcc.write`, and checks three thresholds:

| Constant | Value | Meaning |
| --- | --- | --- |
| `GATE_MIN_IN_COMPONENT_PCT` | 60.0 | % of companies in a size-≥2 component |
| `GATE_MIN_GIANT_PCT` | 25.0 | giant component as % of universe |
| `GATE_MIN_MULTIHOP_PAIRS` | 1 | ≥1 concrete ≥3-hop chain |

Any miss → exit 2 → the build aborts at step 5 of 14, before the downstream layers load. The
remedy is more Form 3/4/5 history: `make build-exec QUARTERS_345=16`, or
`--quarters-345 N` on `build_secgraph.py`. Staged zips are cached, so re-running only fetches the
new quarters. Writing `ownership_component` is the gate's only mutation; requires GDS.

**Why history matters more than it looks:** a Form 4 is filed per *transaction*, so one quarter
surfaces only the directors who happened to trade in it (~3-5 per issuer) — well short of the
~11.8 distinct directors per company that the 60%-in-component threshold needs. Coverage
saturates with depth rather than scaling linearly, because `insiders.py` MERGEs on the
(insider, company) pair. The default is `_QUARTERS_345 = 12` for this reason; it was 4, which
NO-GOs a cold start.

The gate's path sampling uses **unseeded `ORDER BY rand()`** (`density.py:125`), so a marginal
graph can PASS one run and FAIL the next. Not yet fixed — treat a borderline result as
inconclusive and stage more quarters.

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
- **A system `NEO4J_PASSWORD` silently overrides `.env`.** pydantic-settings ranks real env vars
  above the `.env` file, so an exported password wins and `settings.py` warns rather than fails.
  `unset NEO4J_PASSWORD`, or call `get_settings_from_env_file()` to force `.env`.
- **The committed `.venv/` is empty** — three files, no packages. `python` resolves to Anaconda,
  which is where the dev install actually lives. Don't trust `.venv/bin/python`; it has no pytest.
- **`fastmcp` is a core dependency but the MCP test `importorskip`s it.** So
  `tests/unit/test_ownership_mcp_server.py` skips silently in an environment missing it (currently
  the case: 223 passed, **1 skipped**). A green suite does not mean the MCP surface was exercised —
  check for the skip before believing the serving layer is covered.
- **Underscores are illegal in Neo4j database names.** `secgraph_repro` is rejected; use
  `secgraph-repro`. And a dashed name must be **backtick-quoted** in `CREATE DATABASE`, or Cypher
  reads the dash as an operator. Both cost real time; `ownership_create_database.py` now validates
  the name up front instead of surfacing a driver stack trace.
- **`verify_connection` needs a database-appropriate probe.** `RETURN 1` is rejected on the
  `system` database ("can only be executed in a user database"), so `system` is probed with
  `SHOW DATABASES`. Phase 0 depends on this: it must verify against `system` because the target
  database does not exist yet.
- **`gds.version()` is unavailable on `system`.** Probe it on a user database — the preflight
  falls back to `neo4j` when the target database doesn't exist yet.
- **A 404 is data; a 403 is not.** `edgar_client` caches an empty result *only* on 404. Caching
  any other failure is how a placeholder User-Agent once produced a green build over an empty
  graph: every issuer cached `[]`, and the loader exited 0. `beneficial.py` now aborts if ≥20% of
  subjects fail or if zero 13D/G edges resolve universe-wide.
- **`IncompleteRead` is an `HTTPException`, not an `OSError`.** A retry list of
  `(TimeoutError, URLError, OSError)` silently misses a truncated response — the commonest symptom
  of a network drop — and it killed a 2.5-hour crawl. Use `_TRANSIENT_FETCH_ERRORS`. Related: a
  narrow `except` around `future.result()` on a thread pool *looks* fault-tolerant and isn't; any
  uncaught worker exception ends the whole run.
- **One `BENEFICIAL_OWNER_OF` edge per (owner, company, filing_type).** The MERGE key collapses
  every amendment, so `accession_number` and `filing_date` hold **whichever filing the crawl wrote
  last**, not the first or the latest. Consequences: an activist's earlier 13D on the same issuer
  is invisible, and a `--since` cutoff can therefore exclude a filing the published memo cited.
  This is why a rebuild showed 7 convergence issuers instead of 8 — HRI's Icahn edge carried a
  2022-12-15 accession rather than the published 2023-01-27 one, so `--since 2023-01-01` dropped
  it; `--since 2022-01-01` returns all 8. Not a bug in the rebuild, but treat per-edge accessions
  as *an* citation, not *the* full filing history.

## Packaging: use automatic discovery, never a manual list

`pyproject.toml` uses `[tool.setuptools.packages.find] include = ["secgraph*"]`. It previously
declared `packages = ["secgraph"]`, which shipped **only** `secgraph/__init__.py` — every
subpackage (`cli/`, `neo4j/`, `ingestion/`, `mcp/`, `schema/`, `core/`, `gds/`, `utils/`) was
omitted from an install. Verified: 1 file before, 43 after. Don't go back to a manual list; a new
subpackage would silently reintroduce it.

Note this bit harder than "non-editable installs only": `scripts/*.py` put `scripts/` on
`sys.path[0]`, **not** the repo root, so `python scripts/build_secgraph.py` needs a real install.
The test suite passed regardless because pytest inserts the rootdir. If you see
`ModuleNotFoundError: No module named 'secgraph'`, the package isn't installed —
`pip install -e ".[dev,llm]"`.

## Changing a published figure

Several numbers appear in `README.md`, `docs/`, and `results/*.md` (e.g. the 16-member coalition,
11 control chains, MNRO's 96-day gap). If a fix changes one, **update every occurrence and state
the reason**. Silently moving a headline number is the fastest way to lose a technical audience;
the precision fix that took the coalition from 22 to 16 is documented for exactly that reason.

Grep before editing — the current call sites are:

- **16-member coalition** — `docs/reference_architecture_secgraph.md` (×2, incl. the 22→16 note),
  `docs/demo_script_activist_desk.md`, `results/graph_native_proof.md`
- **11 control chains** — `docs/reference_architecture_secgraph.md`
- **96 days (MNRO)** — `README.md` (×2), both `docs/*.md`, `results/activist_convergence.md` (×2)

`results/activist_convergence.md` and `results/graph_native_proof.md` are regenerable
(`make demo` / `make prove` with `--markdown`), so prefer regenerating over hand-editing.
`results/secgraph_freshness.json` is the build's own output and is the only committed JSON under
`results/` — it currently reads `as_of: 2026-07-24`, which is what every served answer stamps.
