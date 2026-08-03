"""
Configuration management for secgraph.

Uses pydantic-settings for type-safe configuration with automatic
environment variable loading and validation.

Environment Variable Precedence:
By default, pydantic-settings uses this precedence (highest to lowest):
1. Arguments passed to Settings()
2. System environment variables (e.g., export NEO4J_PASSWORD=...)
3. Variables from .env file
4. Default field values

This means system environment variables will override .env file values.

To use .env file values instead of system environment variables:
- Option 1 (Recommended): Unset the system variable before running:
  ```bash
  unset NEO4J_PASSWORD
  python scripts/chat_graphrag.py
  ```
- Option 2: Use a different variable name in your system environment
- Option 3: Use get_settings_from_env_file() to explicitly load .env values
"""

import logging
import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Store original environment at module import time (before load_dotenv() runs)
# This allows us to detect if a variable was set by the system vs loaded from .env
_ORIGINAL_ENV: dict[str, str | None] = {}
for key in ["NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_DATABASE"]:
    _ORIGINAL_ENV[key] = os.environ.get(key)


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    All settings are validated at startup. Required settings will raise
    an error if not provided, ensuring fail-fast behavior.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # Ignore extra env vars
        # Note: pydantic-settings precedence is: init args > env vars > .env file > defaults
        # System environment variables will override .env file values.
        # To use .env file values, unset system environment variables or use a different variable name.
    )

    # Neo4j Configuration
    neo4j_uri: str = Field(
        default="bolt://localhost:7687",
        description="Neo4j connection URI",
    )
    neo4j_user: str = Field(
        default="neo4j",
        description="Neo4j username",
    )
    neo4j_password: str = Field(
        default="",
        description="Neo4j password (required)",
    )
    neo4j_database: str = Field(
        default="neo4j",
        description="Neo4j database name",
    )

    # OpenAI Configuration
    openai_api_key: str = Field(
        default="",
        description="OpenAI API key for embeddings",
    )
    openai_max_concurrent: int = Field(
        default=30,
        description="Max concurrent API requests for embedding creation (default: 30)",
    )

    # Datamule Configuration
    datamule_api_key: str | None = Field(
        default=None,
        description="Datamule API key (optional)",
    )

    @field_validator("neo4j_password", "openai_api_key", mode="before")
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        """Strip whitespace from string values."""
        if isinstance(v, str):
            return v.strip()
        return v

    @field_validator("datamule_api_key", mode="before")
    @classmethod
    def empty_string_to_none(cls, v: str | None) -> str | None:
        """Convert empty strings to None for optional fields."""
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v


@lru_cache
def get_settings() -> Settings:
    """
    Get cached settings instance.

    Uses lru_cache to ensure settings are only loaded once.

    Note: Environment variable precedence (pydantic-settings default):
    1. Arguments passed to Settings()
    2. System environment variables (highest priority)
    3. Variables from .env file
    4. Default field values

    If you have system environment variables set (e.g., NEO4J_PASSWORD in your shell),
    they will override values in .env. To use .env values, either:
    - Unset the system environment variable: `unset NEO4J_PASSWORD`
    - Or use a different variable name in your system environment
    - Or use get_settings_from_env_file() to explicitly load .env values
    """
    settings = Settings()

    # Check if NEO4J_PASSWORD was set in system environment BEFORE load_dotenv() ran
    # _ORIGINAL_ENV stores the state at module import time
    had_system_neo4j_password = _ORIGINAL_ENV.get("NEO4J_PASSWORD") is not None
    env_file_path = Path(".env")
    has_env_file = env_file_path.exists()

    # Only warn if the variable was set in system env AND .env file exists
    # This means system env is overriding .env
    if had_system_neo4j_password and has_env_file:
        logger.warning(
            "⚠️  System NEO4J_PASSWORD environment variable is set and will override .env file. "
            "To use .env file values, run: unset NEO4J_PASSWORD"
        )
        # Also print to stderr so it's visible in interactive scripts
        import sys

        print(
            "⚠️  WARNING: System NEO4J_PASSWORD is overriding .env file. "
            "To use .env values, run: unset NEO4J_PASSWORD",
            file=sys.stderr,
        )
    elif had_system_neo4j_password:
        logger.debug("Settings loaded: Using NEO4J_PASSWORD from system environment")
    elif has_env_file:
        logger.debug("Settings loaded: Using NEO4J_PASSWORD from .env file")
    else:
        logger.debug("Settings loaded: Using default/empty NEO4J_PASSWORD")

    return settings


def get_settings_from_env_file(env_file: str | Path = ".env") -> Settings:
    """
    Get settings instance that prioritizes .env file over system environment variables.

    This function temporarily removes system environment variables, loads settings from .env,
    then restores the original environment. This allows .env file values to take precedence.

    Args:
        env_file: Path to .env file (default: ".env" in current directory)

    Returns:
        Settings instance with .env file values taking precedence

    Example:
        # Use .env file values even if system NEO4J_PASSWORD is set
        settings = get_settings_from_env_file()
        driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password)
        )
    """
    env_file_path = Path(env_file)
    if not env_file_path.exists():
        logger.warning(f"Env file not found: {env_file_path}, falling back to standard settings")
        return Settings()

    # Store original env vars that might conflict
    neo4j_vars = ["NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_DATABASE"]
    original_values = {var: os.environ.get(var) for var in neo4j_vars}

    try:
        # Temporarily unset system env vars so .env file takes precedence
        for var in neo4j_vars:
            if var in os.environ:
                del os.environ[var]

        # Clear the cache so Settings() reloads
        get_settings.cache_clear()

        # Load settings (will now use .env file values)
        settings = Settings()

        return settings
    finally:
        # Restore original environment variables
        for var, value in original_values.items():
            if value is not None:
                os.environ[var] = value
            elif var in os.environ:
                del os.environ[var]

        # Clear cache again so future calls use restored env
        get_settings.cache_clear()


# Convenience functions for backwards compatibility
# These will raise clear errors if required values are missing


def get_neo4j_uri() -> str:
    """Get Neo4j URI from settings."""
    return get_settings().neo4j_uri


def get_neo4j_user() -> str:
    """Get Neo4j username from settings."""
    return get_settings().neo4j_user


def get_neo4j_password() -> str:
    """Get Neo4j password from settings."""
    password = get_settings().neo4j_password
    if not password:
        raise ValueError("NEO4J_PASSWORD not set in .env file")
    return password


_non_standard_db_warned = False


def get_neo4j_database() -> str:
    """
    Get Neo4j database name from settings.

    Logs a debug message if using a non-standard database name (not 'neo4j'),
    as this may indicate a configuration mismatch or legacy setup.
    """
    global _non_standard_db_warned
    database = get_settings().neo4j_database
    if database and database != "neo4j" and not _non_standard_db_warned:
        logger.debug(
            f"Using non-standard Neo4j database name: '{database}'. "
            f"Consider using 'neo4j' for consistency with Neo4j Aura and standard setups."
        )
        _non_standard_db_warned = True
    return database


def get_openai_api_key() -> str:
    """Get OpenAI API key from settings."""
    key = get_settings().openai_api_key
    if not key:
        raise ValueError("OPENAI_API_KEY not set in .env file")
    return key


def get_openai_max_concurrent() -> int:
    """Get max concurrent OpenAI requests from settings."""
    return get_settings().openai_max_concurrent


def get_datamule_api_key() -> str | None:
    """Get Datamule API key from settings (optional)."""
    return get_settings().datamule_api_key


# Data paths - not loaded from env, computed from package location


def get_data_dir() -> Path:
    """Get data directory path (project root / data)."""
    # Navigate up from core/config/settings.py to project root
    project_root = Path(__file__).parent.parent.parent.parent
    return project_root / "data"


def get_domain_status_db() -> Path:
    """
    Get path to domain_status.db SQLite database.

    Always returns absolute path relative to package root, ensuring
    the function works regardless of current working directory.
    """
    return get_data_dir() / "domain_status.db"


# =============================================================================
# MODEL CONFIGURATION
# =============================================================================
# Centralized model constants. These can be overridden via environment variables
# to allow easy experimentation with different models.
#
# Model Tiers:
# - FULL: Most capable, used for complex reasoning (RAG answer generation)
# - MINI: Fast and cost-effective, used for extraction and verification
# - EMBEDDING: Text embeddings for similarity search

import os


class ModelConfig:
    """
    Centralized model configuration.

    Only one LLM is used in this project: ``extract_control_edges.py`` reads the
    percent-of-class and control-vs-stake figures out of Schedule 13D cover-page text, which is
    free-form prose that no parser handles reliably. Everything else — every traversal, every
    served answer — is deterministic Cypher with no model in the loop.

    Overridable via environment variables:
    - OPENAI_LLM_MODEL: general-purpose model (default: gpt-4o)
    - OPENAI_LLM_MINI_MODEL: the model actually used for 13D control extraction (default: gpt-4o-mini)
    """

    # Main LLM, retained for any future task needing a larger model.
    LLM_MODEL: str = os.environ.get("OPENAI_LLM_MODEL", "gpt-4o")

    # Mini LLM — used by extract_control_edges.py for 13D cover-page extraction.
    LLM_MINI_MODEL: str = os.environ.get("OPENAI_LLM_MINI_MODEL", "gpt-4o-mini")

    @classmethod
    def to_dict(cls) -> dict[str, str]:
        """Return current model configuration as dict."""
        return {
            "llm_model": cls.LLM_MODEL,
            "llm_mini_model": cls.LLM_MINI_MODEL,
        }

    @classmethod
    def log_config(cls) -> None:
        """Log current model configuration."""
        import logging

        logger = logging.getLogger(__name__)
        logger.info("Model Configuration:")
        logger.info(f"  LLM Model: {cls.LLM_MODEL}")
        logger.info(f"  LLM Mini Model: {cls.LLM_MINI_MODEL}")


# Convenience aliases for direct import
LLM_MODEL = ModelConfig.LLM_MODEL
LLM_MINI_MODEL = ModelConfig.LLM_MINI_MODEL


# =============================================================================
# Query Performance Configuration
# =============================================================================

# Cypher query timeout (seconds) — prevents a runaway traversal from pinning the server.
CYPHER_QUERY_TIMEOUT_SECONDS: int = int(os.environ.get("CYPHER_QUERY_TIMEOUT_SECONDS", "30"))
