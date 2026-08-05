"""Unit tests for Phase 0 — database creation preconditions (no live Neo4j).

These pin the cold-start path: the one where the target database does not exist yet. Every
failure covered here used to surface either as a misleading "Could not connect to Neo4j" or as
a raw driver stack trace from inside phase 0.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from neo4j.exceptions import ClientError

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ownership_create_database.py"


def _load_script_module():
    """Import the thin script by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("ownership_create_database", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load_script_module()


class TestValidateDatabaseName:
    """Neo4j allows ascii letters/numbers/dots/dashes, must start with a letter, 2-63 chars."""

    @pytest.mark.parametrize("name", ["secgraph", "neo4j", "secgraph-repro", "db2", "a.b"])
    def test_accepts_legal_names(self, name):
        assert mod._validate_database_name(name) is None

    def test_rejects_underscore_and_suggests_dash(self):
        # The natural choice for a scratch database — and it is illegal in Neo4j.
        error = mod._validate_database_name("secgraph_repro")
        assert error is not None
        assert "underscores are not allowed" in error
        assert "secgraph-repro" in error  # actionable suggestion

    @pytest.mark.parametrize("name", ["1db", "-db", ".db"])
    def test_rejects_non_letter_start(self, name):
        assert mod._validate_database_name(name) is not None

    @pytest.mark.parametrize("name", ["a", "x" * 64])
    def test_rejects_out_of_range_length(self, name):
        assert mod._validate_database_name(name) is not None

    def test_rejects_backtick_injection(self):
        # The name is interpolated into `CREATE DATABASE \`{name}\``, so a backtick must never
        # survive validation.
        assert mod._validate_database_name("a`b") is not None


class _FakeClientError(ClientError):
    """ClientError with a settable ``code``.

    The real driver builds these via private hydration helpers and exposes ``code`` as a
    read-only property, so a stub is both simpler and less likely to break on a driver bump.
    """

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self._message = message
        self._code = code

    @property
    def code(self) -> str:
        return self._code

    def __str__(self) -> str:
        return self._message


class TestUnsupportedAdminCommandDetection:
    """Community/Aura reject CREATE DATABASE; that must read as an edition problem."""

    def test_detects_by_code(self):
        exc = _FakeClientError("nope", "Neo.ClientError.Statement.UnsupportedAdministrationCommand")
        assert mod._is_unsupported_admin_command(exc) is True

    def test_detects_by_message(self):
        exc = _FakeClientError(
            "Unsupported administration command: CREATE DATABASE foo",
            "Neo.ClientError.Statement.SomethingElse",
        )
        assert mod._is_unsupported_admin_command(exc) is True

    def test_unrelated_client_error_is_not_swallowed(self):
        # A genuine syntax/argument error must propagate rather than be misreported as
        # "wrong Neo4j edition" — this is the dashed-name bug that the backtick fix addressed.
        exc = _FakeClientError(
            "Invalid input '-': expected a database name",
            "Neo.ClientError.Statement.SyntaxError",
        )
        assert mod._is_unsupported_admin_command(exc) is False
