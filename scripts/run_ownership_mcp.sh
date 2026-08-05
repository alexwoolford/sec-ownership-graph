#!/usr/bin/env bash
# Launch the curated ownership MCP server using the repo-local .venv.
# Resolves the repo root from this script's location so MCP clients need no
# machine-absolute Python path — only a path to this launcher (or ${workspaceFolder}).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${ROOT}/.venv/bin/python"
SERVE="${ROOT}/scripts/serve_ownership_mcp.py"

if [[ ! -x "$PY" ]]; then
  echo "error: ${PY} not found or not executable." >&2
  echo "Create and install the project venv from the repo root:" >&2
  echo "  uv venv && source .venv/bin/activate && uv pip install -e \".[dev,llm]\"" >&2
  exit 1
fi

if [[ ! -f "$SERVE" ]]; then
  echo "error: missing ${SERVE}" >&2
  exit 1
fi

exec "$PY" "$SERVE" "$@"
