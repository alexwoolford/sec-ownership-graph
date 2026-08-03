"""Pytest configuration for the SEC ownership graph tests.

The default (unit) lane is fully mocked and needs no database — the ownership tests build their
own in-test fakes rather than sharing mock fixtures, so this file stays deliberately small.
Integration tests read ``NEO4J_*`` from the environment and skip themselves when
``NEO4J_PASSWORD`` is unset.
"""

import os

import pytest

# Defaults so modules that read settings at import time work in the unit lane. NEO4J_PASSWORD is
# deliberately NOT defaulted — its absence is what makes the integration lane skip.
os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("NEO4J_USER", "neo4j")
os.environ.setdefault("NEO4J_DATABASE", "secgraph")


def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption(
        "--run-contract",
        action="store_true",
        default=False,
        help="Run contract tests (requires a production-like database)",
    )


def pytest_collection_modifyitems(config, items):
    """Skip contract tests unless ``--run-contract`` is given.

    Contract tests assert live graph state against baselines, so they need a populated database
    and are opt-in rather than part of the default lane.
    """
    if config.getoption("--run-contract"):
        return
    skip_contract = pytest.mark.skip(reason="Contract tests require --run-contract flag")
    for item in items:
        if "contract" in item.keywords:
            item.add_marker(skip_contract)
