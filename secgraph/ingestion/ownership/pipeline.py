"""Build + refresh orchestrator for the standalone ``secgraph`` ownership graph.

The ownership analog of :mod:`scripts.run_all_pipelines` for the main company graph: the
missing ``run_all`` that sequences the per-script phases (each already documented in its own
docstring) into one reproducible, dry-run-by-default, abort-on-failure build. Two modes:

- **build** (default) — the full cold-start sequence: create DB → load universe → stage +
  load Form 3/4/5 insiders → **density GO/NO-GO gate (fail-closed)** → materialize the
  derived board-interlock edge → load 13D/13G beneficial owners → extract control edges →
  stage + load 13F institutional holdings via the CUSIP crosswalk.
- **refresh** (``--refresh``) — the incremental re-pull: re-stage recent quarters, reload the
  changed slices, then **re-materialize** ``SHARES_DIRECTOR`` and **re-extract** control on
  new 13D edges (both already idempotent / resumable). Skips DB creation and the one-time gate.

Each phase shells out to its existing thin script (subprocess, ``shell=False``, name from a
fixed table — never user input), mirroring ``run_all_pipelines.run_script``. The ownership
children have non-uniform CLIs (``download_ownership_data`` takes ``--form``/``--quarters`` and
has no ``--execute``; ``measure_ownership_density`` has no ``--execute`` and signals NO-GO via
exit code 2; several take ``--replace``), so each step declares its own args explicitly rather
than assuming the uniform ``--execute`` contract.

Read-only-except-``secgraph`` and dry-run-by-default are preserved: without ``execute=True``
the orchestrator prints the phased plan and writes nothing.
"""

from __future__ import annotations

import importlib.util
import logging
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from secgraph.core.config.settings import ModelConfig
from secgraph.ingestion.ownership.bulk_datasets import staged_zip_paths

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_DIR = _REPO_ROOT / "scripts"

# Committed reference inputs — these are what make a rebuild reproducible rather than
# "whatever EDGAR served today". Absent on a first build; see scripts/export_control_figures.py.
_CONTROL_FIGURES_CSV = _REPO_ROOT / "reference" / "control_figures.csv"

# Exit code the density gate uses to signal NO-GO (see measure_ownership_density.py).
_DENSITY_NO_GO_EXIT = 2

# Default staging depth per form. FTD needs a deeper window to cover the CUSIP crosswalk.
#
# _QUARTERS_345 = 16, not 4: the density gate needs enough Form 3/4/5 history for board coverage
# to saturate. A Form 4 is filed per transaction, so one quarter surfaces only the directors who
# happened to trade in it (~3-5 per issuer), well short of what the 60%-in-component threshold
# needs. Four quarters NO-GOs the gate and aborts the build at step 5 of 14.
#
# Measured on 2026-08-03 (as-of 2026-06-30, 8,000-company universe): 12 quarters passes at
# 61.3% in-component against a 60.0% threshold — only 1.3 points of margin, on the binding
# criterion. 12 is the floor, not a safe default, so ship 16 and leave headroom for a universe
# whose interlock density differs. Extra quarters cost ~10 MB and a few seconds each.
_QUARTERS_345 = 16
_QUARTERS_13F = 4
_QUARTERS_FTD = 14


@dataclass
class Step:
    """One orchestrated phase: which script to run and with what arguments.

    ``adds_execute`` mirrors the repo's ``--execute`` contract; steps whose child does not use
    it (staging, the density gate) set it False. ``gate`` marks the density GO/NO-GO step so a
    non-zero (specifically ``_DENSITY_NO_GO_EXIT``) exit aborts the whole build fail-closed.
    ``refresh`` / ``build_only`` restrict a step to one mode.
    """

    script: str
    description: str
    adds_execute: bool = True
    extra_args: list[str] = field(default_factory=list)
    gate: bool = False
    build_only: bool = False
    refresh_only: bool = False


def _relative_csv_path() -> str:
    """Control-figures path relative to the repo root, falling back to absolute.

    Keeps the printed plan machine-independent. The fallback matters for tests, which point
    ``_CONTROL_FIGURES_CSV`` at a tmp dir outside the repo.
    """
    try:
        return str(_CONTROL_FIGURES_CSV.relative_to(_REPO_ROOT))
    except ValueError:
        return str(_CONTROL_FIGURES_CSV)


def _llm_path_unavailable_reason() -> str | None:
    """Why control extraction could not run, or None if it can.

    Split out so preflight can treat the same condition two ways: fatal with no committed CSV
    (nothing would classify *any* edge), advisory with one (the gap may be zero).
    """
    if importlib.util.find_spec("openai") is None:
        return (
            "the 'openai' package is not installed, so control extraction cannot run.\n"
            '        Install it with:  pip install -e ".[dev,llm]"\n'
            "        (the documented '[dev]' extra deliberately omits openai)"
        )

    from secgraph.core.config.settings import get_settings

    if not get_settings().openai_api_key:
        return (
            "OPENAI_API_KEY is not set, so control extraction cannot run.\n"
            "        Set OPENAI_API_KEY in .env."
        )
    return None


def _steps(
    database: str,
    *,
    refresh: bool,
    quarters_345: int = _QUARTERS_345,
    quarters_13f: int = _QUARTERS_13F,
    extract_control: bool = False,
    as_of: str | None = None,
) -> list[Step]:
    """The full phased plan. ``--database`` is threaded onto every DB-touching child.

    ``quarters_345`` is caller-overridable because it is the one knob that decides whether the
    density gate passes, and a NO-GO aborts the build at step 5 of 14.

    ``extract_control`` re-extracts **every** 13D edge (``--all``) rather than only the ones the
    committed CSV missed. The gap fill itself is always in the plan, so this flag is only for
    deliberately rebuilding the whole control layer from filings — e.g. after changing the
    extraction prompt or the control threshold.

    ``as_of`` pins every acquisition step to a date, so the staged windows and the crawled
    filings match a reference build instead of drifting forward with EDGAR.
    """
    db = ["--database", database]
    as_of_args = ["--as-of", as_of] if as_of else []
    plan: list[Step] = [
        # Phase 0 — standalone database + constraints (cold-start only).
        Step(
            "ownership_create_database.py",
            "Phase 0 — create the secgraph database + ownership constraints",
            extra_args=db,
            build_only=True,
        ),
        # Universe — every edge attaches to an in-universe Company; prereq for all loaders.
        Step(
            "load_company_universe.py",
            "Universe — load the Company universe (SEC filers with a ticker)",
            extra_args=[*db, "--enrich-sic"] + (["--refresh"] if refresh else []),
        ),
        # Phase 1 — stage + load Form 3/4/5 insiders (the density-gate layer).
        Step(
            "download_ownership_data.py",
            "Phase 1 stage — download Form 3/4/5 bulk datasets",
            adds_execute=False,
            extra_args=["--form", "345", "--quarters", str(quarters_345)]
            + as_of_args
            + (["--refresh"] if refresh else []),
        ),
        Step(
            "load_insiders.py",
            "Phase 1 — load Insider nodes + DIRECTOR_OF/OFFICER_OF role edges",
            extra_args=db,
        ),
        # Phase 1 gate — density GO/NO-GO (fail-closed; no --execute; NO-GO => exit 2).
        Step(
            "measure_ownership_density.py",
            "Phase 1 GATE — ownership-graph density GO/NO-GO (fail-closed)",
            adds_execute=False,
            extra_args=db,
            gate=True,
            build_only=True,
        ),
        # Derived board-interlock edge (needs insiders); idempotent, --replace on refresh.
        Step(
            "materialize_interlock_edges.py",
            "Derived — materialize the SHARES_DIRECTOR board-interlock edge",
            extra_args=[*db] + (["--replace"] if refresh else []),
        ),
        # Phase 2a — 13D/13G beneficial owners (crawls EDGAR per subject; needs universe).
        Step(
            "load_beneficial_owners.py",
            "Phase 2a — load BeneficialOwner nodes + 13D/13G BENEFICIAL_OWNER_OF edges",
            extra_args=[*db] + as_of_args + (["--refresh"] if refresh else []),
        ),
        # Control figures on 13D edges (needs Phase 2a). Two cooperating steps, always in this
        # order — CSV first, then a gap fill:
        #
        #   1. load_control_figures.py — applies reference/control_figures.csv when committed.
        #      Deterministic, no API key, identical on every rebuild.
        #   2. extract_control_edges.py — classifies whatever the CSV did not cover. Runs
        #      **unconditionally**, because the CSV covers only the EDGAR window it was exported
        #      from. A cloner building a year later crawls newer 13Ds that no committed row
        #      matches, and an unclassified edge has no percent_of_class at all — so it silently
        #      vanishes from CONTROLS and INFLUENCES rather than erroring. This step is what
        #      collapses "CSV present but stale" (a knowingly wrong middle state) into either a
        #      complete layer or a hard failure.
        #
        # It is cheap and self-limiting: only_missing=True is the default, so with full coverage
        # the edge list is empty, no LLM client is constructed, and no key is needed. Cost scales
        # with the gap — regex resolves ~93% of edges for free and only the remainder reaches
        # gpt-4o-mini (a full 10.6k-edge re-extract is ~$0.21; a year of drift is cents).
        #
        # Both write the same properties (control_class / percent_of_class / pct_verified), so
        # materialize_control_edges downstream cannot tell them apart.
        *(
            [
                Step(
                    "load_control_figures.py",
                    "Derived — apply committed control figures (deterministic, no LLM)",
                    # Repo-relative, not absolute: the printed plan is copy-pasteable and does
                    # not leak a machine-specific path into logs or docs.
                    extra_args=[*db, "--csv", _relative_csv_path()],
                )
            ]
            if _CONTROL_FIGURES_CSV.exists()
            else []
        ),
        Step(
            "extract_control_edges.py",
            "Derived — classify any 13D edges the committed figures did not cover (LLM gap fill)"
            if _CONTROL_FIGURES_CSV.exists()
            else "Derived — extract control-vs-stake figures for Schedule 13D edges (LLM)",
            extra_args=[*db] + (["--all"] if extract_control else []),
        ),
        # Re-export the merged figures so the *cloner's* next rebuild is deterministic too.
        # Without this the loop never closes: the gap fill labels the new edges in Neo4j, but
        # reference/control_figures.csv still ends at the original export window, so every
        # subsequent rebuild re-extracts the same edges and pays for them again.
        Step(
            "export_control_figures.py",
            "Derived — re-export control figures to reference/ (keeps future rebuilds LLM-free)",
            extra_args=["--database", database, "--out", _relative_csv_path()],
        ),
        # Promote verified control to a traversable edge (+ CIK-identity bridge). Must follow
        # extract_control_edges: it reads control_class='control'. This is what makes the
        # transitive control chain a real Cypher traversal rather than a client-side walk.
        Step(
            "materialize_control_edges.py",
            "Derived — materialize CONTROLS + SAME_ENTITY_AS (traversable control graph)",
            extra_args=[*db] + (["--replace"] if refresh else []),
        ),
        # Activist co-targeting edge (needs Phase 2a 13D edges); enables the coalition component.
        Step(
            "materialize_cotarget_edges.py",
            "Derived — materialize CO_TARGETS activist co-targeting edges",
            extra_args=[*db] + (["--replace"] if refresh else []),
        ),
        # Influence tiers (12 CFR 225.2(e)). Needs the 13D edges from Phase 2a AND the insider
        # role edges from Phase 1, because each edge carries a board_seat flag joined on CIK.
        Step(
            "materialize_influence_edges.py",
            "Derived — materialize INFLUENCES tiers + board-seat flag (12 CFR 225.2(e))",
            extra_args=[*db] + (["--replace"] if refresh else []),
        ),
        # Phase 2b — CUSIP crosswalk (needs FTD) then 13F institutional holdings.
        Step(
            "download_ownership_data.py",
            "Phase 2b stage — download FTD datasets (for the CUSIP crosswalk)",
            adds_execute=False,
            extra_args=["--form", "ftd", "--quarters", str(_QUARTERS_FTD)]
            + as_of_args
            + (["--refresh"] if refresh else []),
        ),
        Step(
            "build_cusip_crosswalk.py",
            "Phase 2b prereq — build the CUSIP-9 → CIK crosswalk",
            extra_args=db,
        ),
        Step(
            "download_ownership_data.py",
            "Phase 2b stage — download Form 13F datasets",
            adds_execute=False,
            extra_args=["--form", "13f", "--quarters", str(quarters_13f)]
            + as_of_args
            + (["--refresh"] if refresh else []),
        ),
        Step(
            "load_institutional_holdings.py",
            "Phase 2b — load InstitutionalManager nodes + HOLDS edges (13F)",
            # --replace on refresh: a changed crosswalk requires re-loading HOLDS cleanly.
            extra_args=[*db] + (["--replace"] if refresh else []),
        ),
        # Derived size proxy. Must follow the 13F load — it sums HOLDS.value_usd. This is what
        # makes every structural result rankable by materiality; without it a $95B control
        # relationship and a $30 one render as peer rows.
        Step(
            "materialize_materiality.py",
            "Derived — materialize the institutional-value size proxy on Company",
            extra_args=[*db] + (["--replace"] if refresh else []),
        ),
    ]
    if refresh:
        return [s for s in plan if not s.build_only]
    return [s for s in plan if not s.refresh_only]


def _run_step(step: Step, *, execute: bool, logger_instance: logging.Logger) -> int:
    """Run one step's script as a subprocess. Returns the child's exit code (0 = ok)."""
    script_path = _SCRIPT_DIR / step.script
    if not script_path.exists():
        logger_instance.error("Script not found: %s", script_path)
        return 1

    cmd = [sys.executable, str(script_path)]
    if step.adds_execute and execute:
        cmd.append("--execute")
    cmd.extend(step.extra_args)

    logger_instance.info("")
    logger_instance.info("=" * 70)
    logger_instance.info(step.description)
    logger_instance.info("=" * 70)
    logger_instance.info("Running: %s", " ".join(cmd[1:]))

    start = time.time()
    try:
        result = subprocess.run(cmd, check=False, capture_output=False)  # noqa: S603
    except KeyboardInterrupt:
        logger_instance.warning("Interrupted by user")
        return 130
    elapsed = time.time() - start
    if result.returncode == 0:
        logger_instance.info("✓ %s completed (%.1fs)", step.script, elapsed)
    else:
        logger_instance.error("✗ %s exited %d after %.1fs", step.script, result.returncode, elapsed)
    return result.returncode


def _print_plan(steps: list[Step], *, database: str, refresh: bool, logger_instance) -> None:
    mode = "REFRESH" if refresh else "BUILD"
    logger_instance.info("=" * 70)
    logger_instance.info("SECGRAPH %s PLAN (dry run) — database '%s'", mode, database)
    logger_instance.info("=" * 70)
    for i, step in enumerate(steps, 1):
        exec_note = "" if step.adds_execute else "  (no --execute)"
        gate_note = "  [FAIL-CLOSED GATE]" if step.gate else ""
        logger_instance.info("  %2d. %s%s%s", i, step.description, exec_note, gate_note)
        logger_instance.info("      %s %s", step.script, " ".join(step.extra_args))
    logger_instance.info("=" * 70)
    verb = "--refresh --execute" if refresh else "--execute"
    logger_instance.info(
        "To run: python scripts/build_secgraph.py --database %s %s", database, verb
    )
    logger_instance.info("=" * 70)


# --------------------------------------------------------------------------- #
# Preflight.
# --------------------------------------------------------------------------- #
# Free space needed for staged EDGAR data: 13F zips run ~87 MB each, FTD ~21 MB, plus up to
# 600 KB per cached 13D body across ~14k accessions. 20 GB is comfortable headroom.
_MIN_FREE_DISK_GB = 20.0

# (label, property) pairs every loader MERGEs on. Mirrors schema/graph_schema.yaml's
# `constraints:` block; the preflight checks these exist before a multi-hour load.
_REQUIRED_CONSTRAINTS = (
    ("Company", "cik"),
    ("Insider", "cik"),
    ("InstitutionalManager", "cik"),
    ("BeneficialOwner", "owner_key"),
)


def preflight_checks(
    *,
    database: str,
    driver=None,
    refresh: bool = False,
    log: logging.Logger | None = None,
) -> list[str]:
    """Check every precondition a full build needs. Returns a list of failure messages.

    Every one of these used to surface only after minutes-to-hours of EDGAR crawling, as a
    generic non-zero exit from a child script. Checking them up front is the difference between
    a 5-second actionable error and a wasted overnight run. Read-only: no writes, no network.

    ``driver`` is optional so the dry run can report everything checkable without credentials.
    """
    log = log or logger
    failures: list[str] = []

    # --- SEC fair access. A placeholder UA earns HTTP 403 on every request, which used to
    # produce a green build over an empty graph (see edgar_client.SubmissionsFetchError).
    from secgraph.ingestion.ownership.edgar_client import (
        _user_agent,
        is_placeholder_user_agent,
    )

    if is_placeholder_user_agent():
        failures.append(
            "SEC_USER_AGENT is unset or still the placeholder "
            f"({_user_agent()!r}). SEC fair-access rejects generic agents with HTTP 403. "
            "Set a real contact string, e.g.\n"
            "        export SEC_USER_AGENT='Your Name your.email@domain.com'"
        )
    else:
        log.info(f"  ✓ SEC_USER_AGENT: {_user_agent()}")

    # --- Disk. The 13F layer alone writes millions of edges from ~350 MB of zips.
    try:
        import shutil

        free_gb = shutil.disk_usage(Path.cwd()).free / 1024**3
        if free_gb < _MIN_FREE_DISK_GB:
            failures.append(
                f"only {free_gb:.1f} GB free on the volume holding data/; "
                f"a full build stages ~{_MIN_FREE_DISK_GB:.0f} GB of EDGAR data"
            )
        else:
            log.info(f"  ✓ disk: {free_gb:.1f} GB free")
    except OSError as exc:  # pragma: no cover - platform-dependent
        log.warning(f"  ? could not check free disk space: {exc}")

    # --- Control figures. Checked here because the control steps land ~8 of 16 — hours in —
    # and an unusable LLM path dies on a bare import.
    #
    # The committed CSV does NOT remove the need for a working extraction path: it covers only
    # the EDGAR window it was exported from, and the build always runs a gap fill behind it. A
    # cloner building later crawls newer 13Ds the CSV cannot match, and an unclassified edge is
    # absent from CONTROLS/INFLUENCES rather than erroring. So the LLM path is checked either
    # way — as a hard failure with no CSV, and as a warning with one (the gap may well be zero,
    # which is the only case that legitimately needs no key).
    if _CONTROL_FIGURES_CSV.exists():
        log.info(f"  ✓ control figures: {_CONTROL_FIGURES_CSV}")

    llm_gap = _llm_path_unavailable_reason()
    if llm_gap is None:
        log.info("  ✓ control extraction: openai + OPENAI_API_KEY available (gap fill ready)")
    elif _CONTROL_FIGURES_CSV.exists():
        log.warning(
            f"  ⚠ control gap fill unavailable: {llm_gap}\n"
            "        The committed CSV covers only its own export window. If your EDGAR crawl\n"
            "        picks up newer 13D filings, they cannot be classified and will be MISSING\n"
            "        from control and influence answers. The build will stop at that step\n"
            "        unless you pass --skip-uncovered to accept an incomplete layer."
        )
    else:
        failures.append(f"no committed control figures at {_CONTROL_FIGURES_CSV} and {llm_gap}")

    # --- Neo4j. Only checkable with a driver; the dry run says so rather than guessing.
    if driver is None:
        log.info("  ? Neo4j checks skipped (no driver in dry-run mode)")
        return failures

    try:
        with driver.session(database="system") as session:
            session.run("SHOW DATABASES YIELD name RETURN count(*) AS n").consume()
        log.info("  ✓ Neo4j reachable (system database)")
    except Exception as exc:
        failures.append(f"cannot reach Neo4j's system database: {exc}")
        return failures  # everything below needs a working connection

    # Named databases need Enterprise. Only relevant on a cold start, which creates one.
    if not refresh:
        try:
            with driver.session(database="system") as session:
                rows = session.run(
                    "SHOW DATABASES YIELD name RETURN collect(name) AS names"
                ).single()
                existing = set(rows["names"] or [])
            if database not in existing:
                edition = _server_edition(driver)
                if edition and edition != "enterprise":
                    failures.append(
                        f"database '{database}' does not exist and this server is "
                        f"{edition} edition, which cannot CREATE DATABASE.\n"
                        "        Use Neo4j Enterprise (or the neo4j:enterprise Docker image), "
                        "or target the existing database:\n"
                        "          make build-exec DB=neo4j   (with NEO4J_DATABASE=neo4j)"
                    )
                else:
                    log.info(f"  ✓ database '{database}' will be created (enterprise edition)")
            else:
                log.info(f"  ✓ database '{database}' already exists")
        except Exception as exc:  # pragma: no cover - defensive
            log.warning(f"  ? could not inspect databases: {exc}")

    # Uniqueness constraints on an *existing* target database. Phase 0 creates them, but it is
    # `CREATE DATABASE ... IF NOT EXISTS` — so pointing at a database that already exists but was
    # never initialised skips them silently. Without them every `MERGE (i:Insider {cik})` in a
    # 5,000-row batch does a full label scan: measured at ~500 edges/min instead of ~50,000, i.e.
    # hours of apparent progress on a load that should take minutes.
    if database in existing:
        try:
            with driver.session(database=database) as session:
                found = {
                    tuple(r["labelsOrTypes"] or []) + tuple(r["properties"] or [])
                    for r in session.run(
                        "SHOW CONSTRAINTS YIELD labelsOrTypes, properties "
                        "RETURN labelsOrTypes, properties"
                    )
                }
            missing = [
                f"{label}.{prop}"
                for label, prop in _REQUIRED_CONSTRAINTS
                if (label, prop) not in found
            ]
            if missing:
                failures.append(
                    f"database '{database}' exists but is missing uniqueness constraint(s): "
                    f"{', '.join(missing)}.\n"
                    "        Loaders MERGE on these keys, so without them every batch does a "
                    "full label scan (hours instead of minutes).\n"
                    f"        Fix: python scripts/ownership_create_database.py "
                    f"--database {database} --execute"
                )
            else:
                log.info(f"  ✓ constraints present ({len(_REQUIRED_CONSTRAINTS)} uniqueness)")
        except Exception as exc:  # pragma: no cover - defensive
            log.warning(f"  ? could not inspect constraints: {exc}")

    # GDS is needed on BOTH sides: the `graphdatascience` Python client and the server plugin.
    # Checking only the server let the density gate die on a bare ImportError at step 5 of 14.
    if importlib.util.find_spec("graphdatascience") is None:
        failures.append(
            "the 'graphdatascience' Python client is not installed, but the density gate "
            "needs it.\n"
            '        Fix: pip install -e ".[dev,llm]"'
        )
    else:
        log.info("  ✓ graphdatascience client installed")

    # gds.version() must be called on a *user* database — it is unavailable on `system`. On a
    # cold start the target database does not exist yet, so probe any existing user database.
    existing = _existing_databases(driver)
    gds_db = database if database in existing else _any_user_database(existing)
    if gds_db is None:
        log.warning("  ? GDS check skipped (no user database available to probe)")
        return failures
    try:
        with driver.session(database=gds_db) as session:
            version = session.run("RETURN gds.version() AS v").single()["v"]
        log.info(f"  ✓ GDS {version} (probed on '{gds_db}')")
    except Exception:
        failures.append(
            "the GDS plugin is not available, but the density gate needs gds.wcc.write.\n"
            "        Install Graph Data Science on the server "
            "(Docker: NEO4J_PLUGINS='[\"graph-data-science\"]')."
        )

    return failures


def _any_user_database(existing: set[str]) -> str | None:
    """Pick a user database to probe for GDS. Prefers ``neo4j``; never ``system``."""
    if "neo4j" in existing:
        return "neo4j"
    candidates = sorted(existing - {"system"})
    return candidates[0] if candidates else None


def _existing_databases(driver) -> set[str]:
    """Names of databases on this server; empty set if it cannot be determined."""
    try:
        with driver.session(database="system") as session:
            row = session.run("SHOW DATABASES YIELD name RETURN collect(name) AS names").single()
        return set(row["names"] or [])
    except Exception:  # pragma: no cover - defensive
        return set()


def _server_edition(driver) -> str | None:
    """Server edition ('enterprise' / 'community'), or None if undeterminable."""
    try:
        with driver.session(database="system") as session:
            row = session.run(
                "CALL dbms.components() YIELD edition RETURN edition LIMIT 1"
            ).single()
        return (row["edition"] or "").lower() or None
    except Exception:  # pragma: no cover - defensive
        return None


def _report_preflight(failures: list[str], *, log: logging.Logger) -> bool:
    """Log preflight results. Returns True when the build may proceed."""
    if not failures:
        log.info("✓ preflight passed")
        return True
    log.error("")
    log.error("=" * 70)
    log.error(f"✗ PREFLIGHT FAILED — {len(failures)} issue(s). Nothing has been written.")
    log.error("=" * 70)
    for i, msg in enumerate(failures, 1):
        log.error(f"  {i}. {msg}")
    log.error("=" * 70)
    return False


# --------------------------------------------------------------------------- #
# Freshness manifest.
# --------------------------------------------------------------------------- #
_FRESHNESS_QUERY = """
CALL {
  MATCH (n:Company) RETURN 'Company' AS k, count(n) AS c, null AS max_date
  UNION ALL MATCH (n:Insider) RETURN 'Insider' AS k, count(n) AS c, null AS max_date
  UNION ALL MATCH (n:BeneficialOwner) RETURN 'BeneficialOwner' AS k, count(n) AS c, null AS max_date
  UNION ALL MATCH (n:InstitutionalManager) RETURN 'InstitutionalManager' AS k, count(n) AS c, null AS max_date
  UNION ALL MATCH ()-[r:DIRECTOR_OF]->() RETURN 'DIRECTOR_OF' AS k, count(r) AS c, null AS max_date
  UNION ALL MATCH ()-[r:OFFICER_OF]->() RETURN 'OFFICER_OF' AS k, count(r) AS c, null AS max_date
  UNION ALL MATCH ()-[r:BENEFICIAL_OWNER_OF]->()
            RETURN 'BENEFICIAL_OWNER_OF' AS k, count(r) AS c, toString(max(r.filing_date)) AS max_date
  UNION ALL MATCH ()-[r:HOLDS]->()
            RETURN 'HOLDS' AS k, count(r) AS c, toString(max(r.filing_date)) AS max_date
  UNION ALL MATCH ()-[r:SHARES_DIRECTOR]->() RETURN 'SHARES_DIRECTOR' AS k, count(r) AS c, null AS max_date
  UNION ALL MATCH ()-[r:CONTROLS]->() RETURN 'CONTROLS' AS k, count(r) AS c, null AS max_date
  UNION ALL MATCH ()-[r:SAME_ENTITY_AS]->() RETURN 'SAME_ENTITY_AS' AS k, count(r) AS c, null AS max_date
  UNION ALL MATCH ()-[r:CO_TARGETS]->() RETURN 'CO_TARGETS' AS k, count(r) AS c, null AS max_date
  UNION ALL MATCH ()-[r:INFLUENCES]->()
            RETURN 'INFLUENCES' AS k, count(r) AS c, toString(max(r.filing_date)) AS max_date
}
RETURN k, c, max_date
"""


def collect_provenance(
    *,
    as_of: str | None,
    quarters_345: int,
    quarters_13f: int,
) -> dict[str, Any]:
    """Record what this build was built *from*, not just what came out of it.

    Without this a cloner who gets different numbers cannot tell drift from breakage. The
    freshness manifest already reported output counts; the missing half is the inputs — the
    pin date, the staged windows, and whether the control layer came from a committed file or
    a live LLM run.
    """
    staged: dict[str, list[str]] = {}
    for subdir in ("form345", "form13f", "ftd"):
        try:
            paths = staged_zip_paths(subdir, as_of=as_of)
        except OSError:  # pragma: no cover - cache dir may not exist on a partial build
            paths = []
        staged[subdir] = [p.name.rsplit(f"_{subdir}.zip", 1)[0] for p in paths]

    return {
        "as_of_requested": as_of,
        "as_of_pinned": as_of is not None,
        "quarters_345_requested": quarters_345,
        "quarters_13f_requested": quarters_13f,
        "staged_periods": staged,
        # "reference_csv_plus_gap_fill", not "reference_csv": the CSV covers only the window it
        # was exported from, and the build always runs the LLM gap fill behind it. Claiming a
        # pure-CSV provenance would understate what produced the layer — the extractor may have
        # classified newer edges, and a reader comparing two builds needs to know that.
        "control_figures": (
            {
                "source": "reference_csv_plus_gap_fill",
                "path": str(_CONTROL_FIGURES_CSV.relative_to(_REPO_ROOT)),
                "gap_fill_model": ModelConfig.LLM_MINI_MODEL,
            }
            if _CONTROL_FIGURES_CSV.exists()
            else {"source": "llm_extraction", "model": ModelConfig.LLM_MINI_MODEL}
        ),
    }


def collect_freshness(
    driver, database: str, provenance: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Read per-layer row counts + max filing dates for the freshness manifest (read-only).

    The served answer's "as of" is the newest 13D/13G ``filing_date`` — the one trustworthy
    dated layer. Board/insider layers are a keep-latest snapshot, so no date is claimed for them.
    """
    with driver.session(database=database) as session:
        rows = session.run(_FRESHNESS_QUERY).data()
    layers = {r["k"]: {"count": r["c"], "max_filing_date": r["max_date"]} for r in rows}
    as_of = layers.get("BENEFICIAL_OWNER_OF", {}).get("max_filing_date")
    manifest: dict[str, Any] = {"database": database, "as_of": as_of, "layers": layers}
    if provenance is not None:
        manifest["provenance"] = provenance
    return manifest


def provenance_line(manifest_path: Path | None = None) -> str:
    """One markdown line stating what data a published figure came from.

    Shared by the demo memos. Without it, a cloner whose numbers differ cannot tell expected
    drift (a different ``--as-of``, a wider staging window) from a broken build.
    """
    import json

    path = manifest_path or (Path("results") / "secgraph_freshness.json")
    if not path.exists():
        return (
            f"> **Provenance unavailable** — `{path}` is missing, so the data window behind these "
            "figures is unrecorded. Re-run the build to regenerate it."
        )
    try:
        manifest = json.loads(path.read_text())
    except (OSError, ValueError):
        return f"> **Provenance unreadable** — `{path}` could not be parsed."

    prov = manifest.get("provenance") or {}
    staged = (prov.get("staged_periods") or {}).get("form345") or []
    window = f"{staged[-1]}–{staged[0]}" if staged else "unknown"
    return (
        f"> **Provenance.** Data as of `{manifest.get('as_of', 'unknown')}` · "
        f"staging pinned to `{prov.get('as_of_requested') or 'not pinned'}` · "
        f"Form 3/4/5 window `{window}` ({len(staged)} quarters) · "
        f"control figures from `{(prov.get('control_figures') or {}).get('source', 'unknown')}`. "
        "Figures below are specific to this window: a rebuild with a different `--as-of` will "
        "legitimately differ."
    )


def write_freshness_manifest(
    driver, database: str, out_path: Path, provenance: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Compute and persist the freshness manifest; returns the manifest dict."""
    import json

    manifest = collect_freshness(driver, database, provenance=provenance)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    logger.info("wrote freshness manifest %s (as_of=%s)", out_path, manifest["as_of"])
    return manifest


# --------------------------------------------------------------------------- #
# Entry point.
# --------------------------------------------------------------------------- #
def build_secgraph(
    *,
    database: str = "secgraph",
    refresh: bool = False,
    execute: bool = False,
    driver=None,
    freshness_path: Path | None = None,
    quarters_345: int = _QUARTERS_345,
    quarters_13f: int = _QUARTERS_13F,
    extract_control: bool = False,
    as_of: str | None = None,
    logger_instance: logging.Logger | None = None,
) -> bool:
    """Run the phased secgraph build (or refresh). Returns True on success.

    Dry-run by default: without ``execute`` it prints the plan and returns True. On
    ``execute``, runs the preflight and then each phase in order, aborting on the first non-zero
    exit; a density-gate NO-GO (exit ``_DENSITY_NO_GO_EXIT``) aborts fail-closed. When a
    ``driver`` is supplied and the run succeeds, writes the freshness manifest.
    """
    log = logger_instance or logger
    steps = _steps(
        database,
        refresh=refresh,
        quarters_345=quarters_345,
        quarters_13f=quarters_13f,
        extract_control=extract_control,
        as_of=as_of,
    )

    if not execute:
        _print_plan(steps, database=database, refresh=refresh, logger_instance=log)
        log.info("")
        log.info("PREFLIGHT (what the real run will require):")
        _report_preflight(
            preflight_checks(database=database, driver=driver, refresh=refresh, log=log), log=log
        )
        return True

    mode = "REFRESH" if refresh else "BUILD"
    log.info("=" * 70)
    log.info("RUNNING SECGRAPH %s — database '%s'", mode, database)
    log.info("=" * 70)

    # Fail in seconds rather than after hours of EDGAR crawling.
    log.info("")
    log.info("PREFLIGHT:")
    if not _report_preflight(
        preflight_checks(database=database, driver=driver, refresh=refresh, log=log), log=log
    ):
        return False

    started = time.time()
    for step in steps:
        code = _run_step(step, execute=execute, logger_instance=log)
        if code != 0:
            if step.gate and code == _DENSITY_NO_GO_EXIT:
                deeper = quarters_345 + 4
                log.error("")
                log.error("=" * 70)
                log.error("✗ DENSITY GATE NO-GO — aborting build fail-closed.")
                log.error("  The insider layer is too sparse to support the graph-native wins.")
                log.error("  This build staged %d quarters of Form 3/4/5.", quarters_345)
                log.error("")
                log.error("  Board coverage saturates with history: a Form 4 is filed per")
                log.error("  transaction, so a quarter only surfaces directors who traded in it.")
                log.error("  Stage more and re-run:")
                log.error("")
                log.error("    python scripts/build_secgraph.py --database %s \\", database)
                log.error("        --quarters-345 %d --execute", deeper)
                log.error("")
                log.error("  Staged zips are cached, so re-running only fetches the new quarters.")
                log.error("=" * 70)
            else:
                log.error("Aborting: step failed — %s (exit %d)", step.script, code)
            return False

    elapsed = time.time() - started
    log.info("")
    log.info("=" * 70)
    log.info("SECGRAPH %s COMPLETE (%.0fs)", mode, elapsed)
    log.info("=" * 70)

    if driver is not None:
        out = freshness_path or (Path("results") / "secgraph_freshness.json")
        try:
            write_freshness_manifest(
                driver,
                database,
                out,
                provenance=collect_provenance(
                    as_of=as_of, quarters_345=quarters_345, quarters_13f=quarters_13f
                ),
            )
        except Exception as exc:  # pragma: no cover - manifest is best-effort
            log.warning("could not write freshness manifest: %s", exc)
        if as_of is None:
            log.warning("")
            log.warning(
                "⚠ built without --as-of, so the staged windows tracked today's date. "
                "A later rebuild will not reproduce these counts; pass "
                "--as-of YYYY-MM-DD to pin them."
            )
    return True
