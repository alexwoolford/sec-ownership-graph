#!/usr/bin/env python3
"""
Generate docs/graph_schema.md from schema/graph_schema.yaml.

The YAML is the single source of truth.  This script produces the Markdown
reference so the two can never drift.

Usage:
    python scripts/generate_schema_docs.py              # dry-run: show plan
    python scripts/generate_schema_docs.py --execute    # write docs/graph_schema.md
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "graph_schema.yaml"
DOCS_PATH = Path(__file__).resolve().parent.parent / "docs" / "graph_schema.md"


def load_schema(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def generate_markdown(schema: dict) -> str:  # noqa: C901
    """Generate the full Markdown document from parsed YAML."""
    lines: list[str] = []

    # --- Header ---
    lines.append("# Public Company Graph — Schema Reference")
    lines.append("")
    lines.append("> **Auto-generated** from `schema/graph_schema.yaml`.")
    lines.append(f"> Last generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(">")
    lines.append("> Do **not** edit this file by hand. Run:")
    lines.append("> ```bash")
    lines.append("> python scripts/generate_schema_docs.py --execute")
    lines.append("> ```")
    lines.append("")

    # --- Node Types ---
    lines.append("## Node Types")
    lines.append("")
    for label, node_def in schema.get("nodes", {}).items():
        lines.append(f"### {label}")
        lines.append("")
        lines.append(f"{node_def.get('description', '')}")
        lines.append("")
        lines.append(f"- **Unique key:** `{node_def.get('unique_key', '?')}`")
        lines.append("")

        req = node_def.get("required_properties", {})
        opt = node_def.get("optional_properties", {})

        if req:
            lines.append("#### Required Properties")
            lines.append("")
            lines.append("| Property | Type | Description |")
            lines.append("|----------|------|-------------|")
            for prop_name, prop_def in req.items():
                ptype = prop_def.get("type", "?") if isinstance(prop_def, dict) else "?"
                desc = prop_def.get("description", "") if isinstance(prop_def, dict) else ""
                lines.append(f"| `{prop_name}` | {ptype} | {desc} |")
            lines.append("")

        if opt:
            lines.append("#### Optional Properties")
            lines.append("")
            lines.append("| Property | Type | Description |")
            lines.append("|----------|------|-------------|")
            for prop_name, prop_def in opt.items():
                ptype = prop_def.get("type", "?") if isinstance(prop_def, dict) else "?"
                desc = prop_def.get("description", "") if isinstance(prop_def, dict) else ""
                lines.append(f"| `{prop_name}` | {ptype} | {desc} |")
            lines.append("")

    # --- Relationship Types ---
    lines.append("## Relationship Types")
    lines.append("")
    lines.append("| Relationship | Pattern | Description |")
    lines.append("|-------------|---------|-------------|")
    for rel_name, rel_def in schema.get("relationships", {}).items():
        pattern = rel_def.get("pattern", "")
        desc = rel_def.get("description", "")
        lines.append(f"| `{rel_name}` | `{pattern}` | {desc} |")
    lines.append("")

    # Detailed relationship properties
    lines.append("### Relationship Properties")
    lines.append("")
    for rel_name, rel_def in schema.get("relationships", {}).items():
        req = rel_def.get("required_properties", [])
        opt = rel_def.get("optional_properties", [])
        if not req and not opt:
            continue
        lines.append(f"#### {rel_name}")
        lines.append("")
        lines.append(f"`{rel_def.get('pattern', '')}`")
        lines.append("")
        if req:
            lines.append(f"- **Required:** {', '.join(f'`{p}`' for p in req)}")
        if opt:
            lines.append(f"- **Optional:** {', '.join(f'`{p}`' for p in opt)}")
        lines.append("")

    # --- Constraints ---
    lines.append("## Constraints")
    lines.append("")
    lines.append("| Label | Property | Type |")
    lines.append("|-------|----------|------|")
    for c in schema.get("constraints", []):
        lines.append(f"| {c['label']} | `{c['property']}` | {c.get('type', 'UNIQUENESS')} |")
    lines.append("")

    # --- Indexes ---
    lines.append("## Indexes")
    lines.append("")

    range_idxs = schema.get("indexes", {}).get("range", [])
    if range_idxs:
        lines.append("### Range Indexes")
        lines.append("")
        lines.append("| Label | Property |")
        lines.append("|-------|----------|")
        for idx in range_idxs:
            lines.append(f"| {idx['label']} | `{idx['property']}` |")
        lines.append("")

    vector_idxs = schema.get("indexes", {}).get("vector", [])
    if vector_idxs:
        lines.append("### Vector Indexes")
        lines.append("")
        lines.append("| Name | Label | Property | Dimensions | Similarity |")
        lines.append("|------|-------|----------|-----------|------------|")
        for idx in vector_idxs:
            lines.append(
                f"| `{idx['name']}` | {idx['label']} | `{idx['property']}` "
                f"| {idx.get('dimensions', '?')} | {idx.get('similarity_function', '?')} |"
            )
        lines.append("")

    fulltext_idxs = schema.get("indexes", {}).get("fulltext", [])
    if fulltext_idxs:
        lines.append("### Fulltext Indexes")
        lines.append("")
        lines.append("| Name | Labels | Properties |")
        lines.append("|------|--------|------------|")
        for idx in fulltext_idxs:
            labels = ", ".join(idx.get("labels", []))
            props = ", ".join(f"`{p}`" for p in idx.get("properties", []))
            lines.append(f"| `{idx['name']}` | {labels} | {props} |")
        lines.append("")

    # --- Validation ---
    lines.append("## Validation")
    lines.append("")
    lines.append("Run the schema health-check before chat/evaluation runs:")
    lines.append("")
    lines.append("```bash")
    lines.append("python scripts/validate_graph_schema.py --execute")
    lines.append("```")
    lines.append("")
    lines.append("This fails hard if required labels, relationship types, properties,")
    lines.append("constraints, or indexes are missing from the live database.")
    lines.append("")

    return "\n".join(lines)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(
        description="Generate docs/graph_schema.md from schema/graph_schema.yaml"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually write the docs file (default is dry-run)",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=SCHEMA_PATH,
        help=f"Path to schema YAML (default: {SCHEMA_PATH})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DOCS_PATH,
        help=f"Output path for Markdown (default: {DOCS_PATH})",
    )
    args = parser.parse_args()

    if not args.schema.exists():
        logger.error("Schema file not found: %s", args.schema)
        return 1

    schema = load_schema(args.schema)
    node_count = len(schema.get("nodes", {}))
    rel_count = len(schema.get("relationships", {}))

    logger.info("Loaded schema: %d node types, %d relationship types", node_count, rel_count)

    if not args.execute:
        logger.info("DRY RUN — would generate %s from %s", args.output, args.schema)
        logger.info("Run with --execute to write the file.")
        return 0

    md = generate_markdown(schema)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md)
    logger.info("Wrote %s (%d lines)", args.output, md.count("\n"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
