"""
Validate the live database against ``schema/graph_schema.yaml``.

**Why this exists.** The generated documentation has been telling readers to run
``scripts/validate_graph_schema.py --execute`` — *"This fails hard if required labels, relationship
types, properties, constraints, or indexes are missing from the live database"* — and that script
did not exist. The contract was published and unenforced.

Everything the repo had was one of two weaker things:

- ``tests/unit/test_schema_consistency.py`` — static regex over ``.py`` source. Catches a query
  referencing an undeclared label; never opens a connection, never looks at a property.
- ``pipeline.py``'s constraint check — reads live ``SHOW CONSTRAINTS`` but compares against a
  hardcoded Python tuple that "mirrors" the YAML by hand, so the two can drift silently. The
  ``indexes:`` block was never checked against ``SHOW INDEXES`` at all.

So nothing asked the database whether the schema it claims is the schema it has.

**What "trustworthy" means here, precisely.** Two different questions, and they are not treated the
same way:

1. **Structure** — does every declared label, relationship type, constraint and index exist? A
   missing one is a **hard failure**: the contract is wrong, or the build is incomplete, and either
   way a served answer cannot be trusted.
2. **Coverage** — what fraction of nodes actually carry each declared optional property? This is
   **reported, never asserted.** Coverage legitimately varies with the staged EDGAR window — a
   4-quarter build and a 16-quarter build have genuinely different ``total_assets_usd`` coverage —
   so a threshold here would fail honest builds. Reporting it is what replaced
   ``contract.py``'s hand-maintained ``_PROPERTY_STATS``, which had drifted so far it reported the
   6.7M-edge ``HOLDS`` relationship as ``count=0, coverage=SPARSE``.

**Provenance is checked too**, which is the part that makes "we know where every property came
from" a test rather than a claim: every ``written_by`` must name a file that exists on disk, and
every relationship's declared ``source_value`` must actually appear on that relationship in the
graph. A renamed module or a changed source string fails here instead of quietly making the schema
a work of fiction.

Read-only. Runs no writes, creates nothing, and is safe against a production database.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from secgraph.schema.contract import _load_yaml_schema

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]

# A declared property present on <this> fraction of nodes is reported as a notable gap. Not a
# failure — see the module docstring on why coverage cannot be asserted — but worth surfacing,
# because a required property at 0% means a loader silently wrote nothing.
_LOW_COVERAGE_PCT = 1.0


def _label_counts(session, labels: list[str]) -> dict[str, int]:
    """Node count per declared label. A zero is a structural failure, not a coverage gap."""
    counts = {}
    for label in labels:
        # Label interpolated from the YAML, never from user input — same rule as pipeline._run_step.
        counts[label] = session.run(f"MATCH (n:`{label}`) RETURN count(n) AS n").single()["n"]
    return counts


def _relationship_counts(session, rel_types: list[str]) -> dict[str, int]:
    """Edge count per declared relationship type."""
    counts = {}
    for rel in rel_types:
        counts[rel] = session.run(f"MATCH ()-[r:`{rel}`]->() RETURN count(r) AS n").single()["n"]
    return counts


def _property_coverage(session, label: str, props: list[str], total: int) -> dict[str, float]:
    """Fraction of ``label`` nodes carrying each property, in ONE pass over the label.

    One aggregation rather than a query per property: a per-property scan would be N full label
    scans, which on 78,870 Insider nodes is gratuitous.
    """
    if not props or total == 0:
        return dict.fromkeys(props, 0.0)
    counts = ", ".join(
        f"sum(CASE WHEN n.`{p}` IS NOT NULL THEN 1 ELSE 0 END) AS `{p}`" for p in props
    )
    row = session.run(f"MATCH (n:`{label}`) RETURN {counts}").single()
    return {p: round(100.0 * (row[p] or 0) / total, 1) for p in props}


def _declared_properties(node_def: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(required, optional) property names for one node label."""
    return (
        list((node_def.get("required_properties") or {}).keys()),
        list((node_def.get("optional_properties") or {}).keys()),
    )


def check_structure(session, schema: dict[str, Any]) -> list[str]:
    """Hard-failure checks: every declared label and relationship type must exist and be non-empty.

    An empty declared label means the contract describes a graph this database is not. That is
    exactly the failure mode a green test suite over an empty graph hides, so it fails loudly.
    """
    failures: list[str] = []

    labels = list(schema.get("nodes", {}))
    for label, count in _label_counts(session, labels).items():
        if count == 0:
            failures.append(f"label :{label} is declared but has ZERO nodes in the database")

    rel_types = list(schema.get("relationships", {}))
    for rel, count in _relationship_counts(session, rel_types).items():
        if count == 0:
            failures.append(f"relationship :{rel} is declared but has ZERO edges in the database")

    return failures


def check_constraints_and_indexes(session, schema: dict[str, Any]) -> list[str]:
    """Every declared constraint and index must exist in the live database.

    Reads ``SHOW CONSTRAINTS`` / ``SHOW INDEXES`` and compares against the YAML — not against a
    hardcoded tuple. The YAML `indexes:` block had never been verified against anything before.
    """
    failures: list[str] = []

    live_constraints = {
        (tuple(r["labelsOrTypes"] or [])[0] if r["labelsOrTypes"] else None, r["name"])
        for r in session.run(
            "SHOW CONSTRAINTS YIELD name, labelsOrTypes RETURN name, labelsOrTypes"
        )
    }
    live_constraint_names = {name for _, name in live_constraints}
    for con in schema.get("constraints", []) or []:
        if con.get("name") not in live_constraint_names:
            failures.append(
                f"constraint '{con.get('name')}' on :{con.get('label')}({con.get('property')}) "
                f"is declared but MISSING from the database"
            )

    live_indexes = {
        r["name"]: r
        for r in session.run(
            "SHOW INDEXES YIELD name, type, labelsOrTypes, properties, state "
            "RETURN name, type, labelsOrTypes, properties, state"
        )
    }
    # Range indexes are declared without a name, so match on (label, property) instead.
    live_range = {
        (tuple(r["labelsOrTypes"] or [None])[0], tuple(r["properties"] or [None])[0])
        for r in live_indexes.values()
        if r["type"] == "RANGE"
    }
    indexes = schema.get("indexes", {}) or {}
    for idx in indexes.get("range", []) or []:
        if (idx.get("label"), idx.get("property")) not in live_range:
            failures.append(
                f"range index on :{idx.get('label')}({idx.get('property')}) is declared but MISSING"
            )

    for idx in indexes.get("fulltext", []) or []:
        name = idx.get("name")
        live = live_indexes.get(name)
        if live is None:
            failures.append(f"fulltext index '{name}' is declared but MISSING from the database")
        elif live["state"] != "ONLINE":
            # A POPULATING index silently returns incomplete results rather than erroring, which
            # would make a search-driven client look broken for no visible reason.
            failures.append(f"fulltext index '{name}' exists but is {live['state']}, not ONLINE")

    return failures


def check_provenance(session, schema: dict[str, Any]) -> list[str]:
    """Every declared writer must exist on disk; every declared source_value must be in the graph.

    This is what turns "we know where each property came from" into something enforced. A renamed
    module or a changed ``source`` literal fails here rather than leaving the schema quietly wrong.
    """
    failures: list[str] = []

    for label, node_def in (schema.get("nodes") or {}).items():
        writers = [node_def.get("written_by")] + list(
            (node_def.get("property_writers") or {}).values()
        )
        if not node_def.get("written_by"):
            failures.append(f"label :{label} declares no `written_by` — provenance is unknown")
        for w in writers:
            if w and not (_REPO_ROOT / w).exists():
                failures.append(f"label :{label} names writer '{w}', which does not exist on disk")

    for rel, rel_def in (schema.get("relationships") or {}).items():
        writer = rel_def.get("written_by")
        if not writer:
            failures.append(f"relationship :{rel} declares no `written_by`")
        elif not (_REPO_ROOT / writer).exists():
            failures.append(f":{rel} names writer '{writer}', which does not exist on disk")

        declared = rel_def.get("source_value")
        if not declared:
            failures.append(f"relationship :{rel} declares no `source_value`")
            continue
        found = session.run(
            f"MATCH ()-[r:`{rel}`]->() WHERE r.source = $v RETURN count(r) AS n LIMIT 1", v=declared
        ).single()["n"]
        if found == 0:
            actual = [
                x["src"]
                for x in session.run(
                    f"MATCH ()-[r:`{rel}`]->() RETURN DISTINCT r.source AS src LIMIT 3"
                )
            ]
            failures.append(
                f":{rel} declares source_value '{declared}' but NO edge carries it "
                f"(found instead: {actual})"
            )

    return failures


def validate_live_schema(
    driver, database: str | None = None, logger_instance: logging.Logger | None = None
) -> dict[str, Any]:
    """Compare the live database against the YAML contract. Read-only.

    Returns:
        A report dict with ``failures`` (structural — the caller should exit non-zero if non-empty),
        ``coverage`` (per label/property, reported not asserted), and live counts.
    """
    log = logger_instance or logger
    schema = _load_yaml_schema()

    report: dict[str, Any] = {"failures": [], "coverage": {}, "nodes": {}, "relationships": {}}

    with driver.session(database=database) as session:
        report["failures"] += check_structure(session, schema)
        report["failures"] += check_constraints_and_indexes(session, schema)
        report["failures"] += check_provenance(session, schema)

        labels = list(schema.get("nodes", {}))
        report["nodes"] = _label_counts(session, labels)
        report["relationships"] = _relationship_counts(
            session, list(schema.get("relationships", {}))
        )

        log.info("Node labels (declared vs live):")
        for label in labels:
            node_def = schema["nodes"][label]
            required, optional = _declared_properties(node_def)
            total = report["nodes"][label]
            cov = _property_coverage(session, label, required + optional, total)
            report["coverage"][label] = cov
            log.info(
                f"  :{label} — {total:,} nodes, {len(required + optional)} declared properties"
            )
            spec = node_def.get("optional_properties") or {}
            for prop in required + optional:
                pct = cov[prop]
                # A REQUIRED property below 100% is always a problem. For an optional one, low
                # coverage is ambiguous — it can mean "that layer was not loaded" OR "this is a
                # deliberately sparse flag". `is_custodial` is set on 13 of 17,235 filers by
                # design, and calling that "absent" would train a reader to ignore the warning.
                # So the schema declares which properties are expected to be sparse.
                prop_spec = spec.get(prop) if isinstance(spec.get(prop), dict) else {}
                flag = ""
                if prop in required and pct < 100.0:
                    flag = "  <- REQUIRED but not on every node"
                elif pct < _LOW_COVERAGE_PCT and not prop_spec.get("expected_sparse"):
                    flag = "  <- absent (layer not loaded?)"
                elif prop_spec.get("expected_sparse"):
                    flag = "  (sparse by design)"
                log.info(f"      {prop:30s} {pct:>5.1f}%{flag}")

        log.info("Relationship types (declared vs live):")
        for rel, count in report["relationships"].items():
            src = (schema["relationships"][rel] or {}).get("source_value", "?")
            log.info(f"  :{rel:22s} {count:>10,}  source='{src}'")

    return report
