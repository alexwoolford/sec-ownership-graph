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

import logging
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCRIPT_DIR = Path(__file__).resolve().parents[3] / "scripts"

# Exit code the density gate uses to signal NO-GO (see measure_ownership_density.py).
_DENSITY_NO_GO_EXIT = 2

# Default staging depth per form. FTD needs a deeper window to cover the CUSIP crosswalk.
_QUARTERS_345 = 4
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


def _steps(database: str, *, refresh: bool) -> list[Step]:
    """The full phased plan. ``--database`` is threaded onto every DB-touching child."""
    db = ["--database", database]
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
            extra_args=["--form", "345", "--quarters", str(_QUARTERS_345)]
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
            extra_args=[*db] + (["--refresh"] if refresh else []),
        ),
        # Control extraction on 13D edges (needs Phase 2a); only_missing keeps it resumable.
        Step(
            "extract_control_edges.py",
            "Derived — extract control-vs-stake figures for Schedule 13D edges",
            extra_args=db,
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
        # Phase 2b — CUSIP crosswalk (needs FTD) then 13F institutional holdings.
        Step(
            "download_ownership_data.py",
            "Phase 2b stage — download FTD datasets (for the CUSIP crosswalk)",
            adds_execute=False,
            extra_args=["--form", "ftd", "--quarters", str(_QUARTERS_FTD)]
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
            extra_args=["--form", "13f", "--quarters", str(_QUARTERS_13F)]
            + (["--refresh"] if refresh else []),
        ),
        Step(
            "load_institutional_holdings.py",
            "Phase 2b — load InstitutionalManager nodes + HOLDS edges (13F)",
            # --replace on refresh: a changed crosswalk requires re-loading HOLDS cleanly.
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
}
RETURN k, c, max_date
"""


def collect_freshness(driver, database: str) -> dict[str, Any]:
    """Read per-layer row counts + max filing dates for the freshness manifest (read-only).

    The served answer's "as of" is the newest 13D/13G ``filing_date`` — the one trustworthy
    dated layer (1994→present). Board/insider layers are a keep-latest snapshot, so no date is
    claimed for them.
    """
    with driver.session(database=database) as session:
        rows = session.run(_FRESHNESS_QUERY).data()
    layers = {r["k"]: {"count": r["c"], "max_filing_date": r["max_date"]} for r in rows}
    as_of = layers.get("BENEFICIAL_OWNER_OF", {}).get("max_filing_date")
    return {"database": database, "as_of": as_of, "layers": layers}


def write_freshness_manifest(driver, database: str, out_path: Path) -> dict[str, Any]:
    """Compute and persist the freshness manifest; returns the manifest dict."""
    import json

    manifest = collect_freshness(driver, database)
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
    logger_instance: logging.Logger | None = None,
) -> bool:
    """Run the phased secgraph build (or refresh). Returns True on success.

    Dry-run by default: without ``execute`` it prints the plan and returns True. On
    ``execute``, runs each phase in order, aborting on the first non-zero exit; a density-gate
    NO-GO (exit ``_DENSITY_NO_GO_EXIT``) aborts fail-closed. When a ``driver`` is supplied and
    the run succeeds, writes the freshness manifest.
    """
    log = logger_instance or logger
    steps = _steps(database, refresh=refresh)

    if not execute:
        _print_plan(steps, database=database, refresh=refresh, logger_instance=log)
        return True

    mode = "REFRESH" if refresh else "BUILD"
    log.info("=" * 70)
    log.info("RUNNING SECGRAPH %s — database '%s'", mode, database)
    log.info("=" * 70)

    started = time.time()
    for step in steps:
        code = _run_step(step, execute=execute, logger_instance=log)
        if code != 0:
            if step.gate and code == _DENSITY_NO_GO_EXIT:
                log.error("")
                log.error("=" * 70)
                log.error("✗ DENSITY GATE NO-GO — aborting build fail-closed.")
                log.error("  The insider layer is too sparse to support the graph-native")
                log.error("  wins. Stage more quarters (download_ownership_data --form 345")
                log.error("  --quarters N) and re-run before loading downstream layers.")
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
            write_freshness_manifest(driver, database, out)
        except Exception as exc:  # pragma: no cover - manifest is best-effort
            log.warning("could not write freshness manifest: %s", exc)
    return True
