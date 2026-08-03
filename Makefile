PYTHON := python
# ?= so NEO4J_DATABASE / `make DB=neo4j ...` can retarget. Community and Aura expose a single
# database named `neo4j` and cannot CREATE DATABASE, so a hard-coded name locked them out.
DB ?= secgraph
# Quarters of Form 3/4/5 to stage. This is what decides whether the density gate passes;
# override on a NO-GO (`make build-exec QUARTERS_345=16`).
QUARTERS_345 ?= 16

.PHONY: help install lint format test check preflight demo prove build build-exec refresh refresh-exec serve clean

help:
	@echo "SEC ownership graph — common commands"
	@echo ""
	@echo "  make install        Install the package with dev + llm extras"
	@echo "  make test           Run the unit suite (fully mocked, no database)"
	@echo "  make lint           Ruff check (--fix) + format"
	@echo "  make check          Lint + tests"
	@echo "  make preflight      Check build preconditions in seconds (writes nothing)"
	@echo ""
	@echo "  make demo           Activist convergence screen + the MNRO timeline"
	@echo "  make prove          Graph-vs-SQL head-to-head for all three wins"
	@echo "  make serve          Serve the curated MCP tools over stdio"
	@echo ""
	@echo "  make build          Dry-run the full graph build (plan + preflight)"
	@echo "  make build-exec     Build the graph for real (hours; downloads from EDGAR)"
	@echo ""
	@echo "  Overrides: DB=$(DB)  QUARTERS_345=$(QUARTERS_345)"
	@echo "  Requires Neo4j Enterprise + GDS, and SEC_USER_AGENT set to a real contact."
	@echo "  make refresh        Dry-run an incremental refresh"
	@echo "  make refresh-exec   Run the incremental refresh"

install:
	# [dev,llm]: the build's control-extraction phase imports openai, so a plain [dev]
	# install cannot complete `make build-exec` on a graph without committed control figures.
	$(PYTHON) -m pip install -e ".[dev,llm]"

lint:
	ruff check --fix secgraph/ scripts/ tests/
	ruff format secgraph/ scripts/ tests/

format:
	ruff format secgraph/ scripts/ tests/

test:
	$(PYTHON) -m pytest tests/ -m "not integration and not contract"

check: lint test

# Verify every build precondition (Neo4j edition, GDS, SEC_USER_AGENT, control figures, disk)
# in seconds, instead of discovering them hours into a build. Writes nothing.
preflight:
	$(PYTHON) scripts/build_secgraph.py --database $(DB) --preflight-only

# --- Demo surfaces (read-only) ---------------------------------------------- #
demo:
	$(PYTHON) scripts/activist_convergence.py --database $(DB) --since 2023-01-01 --timeline MNRO

prove:
	$(PYTHON) scripts/prove_graph_native_wins.py --database $(DB)

serve:
	$(PYTHON) scripts/serve_ownership_mcp.py --database $(DB)

# --- Build / refresh (dry-run by default) ----------------------------------- #
build:
	$(PYTHON) scripts/build_secgraph.py --database $(DB) --quarters-345 $(QUARTERS_345)

build-exec:
	$(PYTHON) scripts/build_secgraph.py --database $(DB) --quarters-345 $(QUARTERS_345) --execute

refresh:
	$(PYTHON) scripts/build_secgraph.py --database $(DB) --refresh

refresh-exec:
	$(PYTHON) scripts/build_secgraph.py --database $(DB) --refresh --execute

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .mypy_cache
