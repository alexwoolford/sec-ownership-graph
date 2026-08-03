PYTHON := python
DB := secgraph

.PHONY: help install lint format test check demo prove build build-exec refresh refresh-exec serve clean

help:
	@echo "SEC ownership graph — common commands"
	@echo ""
	@echo "  make install        Install the package with dev extras"
	@echo "  make test           Run the unit suite (fully mocked, no database)"
	@echo "  make lint           Ruff check (--fix) + format"
	@echo "  make check          Lint + tests, as CI runs them"
	@echo ""
	@echo "  make demo           Activist convergence screen + the MNRO timeline"
	@echo "  make prove          Graph-vs-SQL head-to-head for all three wins"
	@echo "  make serve          Serve the curated MCP tools over stdio"
	@echo ""
	@echo "  make build          Dry-run the full graph build (prints the plan)"
	@echo "  make build-exec     Build the graph for real (hours; downloads from EDGAR)"
	@echo "  make refresh        Dry-run an incremental refresh"
	@echo "  make refresh-exec   Run the incremental refresh"

install:
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	ruff check --fix secgraph/ scripts/ tests/
	ruff format secgraph/ scripts/ tests/

format:
	ruff format secgraph/ scripts/ tests/

test:
	$(PYTHON) -m pytest tests/ -m "not integration and not contract"

check: lint test

# --- Demo surfaces (read-only) ---------------------------------------------- #
demo:
	$(PYTHON) scripts/activist_convergence.py --database $(DB) --since 2023-01-01 --timeline MNRO

prove:
	$(PYTHON) scripts/prove_graph_native_wins.py --database $(DB)

serve:
	$(PYTHON) scripts/serve_ownership_mcp.py --database $(DB)

# --- Build / refresh (dry-run by default) ----------------------------------- #
build:
	$(PYTHON) scripts/build_secgraph.py --database $(DB)

build-exec:
	$(PYTHON) scripts/build_secgraph.py --database $(DB) --execute

refresh:
	$(PYTHON) scripts/build_secgraph.py --database $(DB) --refresh

refresh-exec:
	$(PYTHON) scripts/build_secgraph.py --database $(DB) --refresh --execute

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .mypy_cache
