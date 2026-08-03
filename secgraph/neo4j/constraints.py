"""
Neo4j constraint and index creation for the SEC ownership graph.

Constraints define MERGE identity, so they must exist **before** any loader runs — see
``scripts/ownership_create_database.py`` (Phase 0), which creates the database and then calls both
functions here. Unique keys: ``Company.cik``, ``Insider.cik``, ``InstitutionalManager.cik``,
``BeneficialOwner.owner_key`` (a CIK when resolvable, else a name slug).

Every statement uses ``IF NOT EXISTS`` and is safe to re-run. What is declared here must match
``schema/graph_schema.yaml`` — ``tests/unit/test_schema_consistency.py`` enforces that.
"""

import logging

logger = logging.getLogger(__name__)


def _run_constraints(
    driver,
    constraints: list[str],
    database: str | None = None,
    log: logging.Logger | None = None,
) -> None:
    """
    Run a list of constraint/index creation statements.

    Args:
        driver: Neo4j driver instance
        constraints: List of Cypher constraint statements
        database: Neo4j database name
        log: Logger instance (defaults to module logger)
    """
    if log is None:
        log = logger

    with driver.session(database=database) as session:
        for constraint in constraints:
            try:
                session.run(constraint)
                log.info(f"✓ Created: {constraint[:50]}...")
            except Exception as e:
                error_str = str(e).lower()
                # Constraint already exists - this is fine
                if "already exists" in error_str or "equivalent" in error_str:
                    log.debug(f"Constraint already exists: {constraint[:50]}")
                else:
                    log.warning(f"⚠ Warning creating constraint: {e}")


def create_company_constraints(
    driver, database: str | None = None, logger: logging.Logger | None = None
) -> None:
    """
    Create constraints and indexes for Company nodes.

    ``cik`` is the unique key — the hard identifier every ownership edge joins on. The sector and
    SIC indexes support universe filtering; there is deliberately no market-cap or financial index
    because this graph carries no such data.

    Args:
        driver: Neo4j driver instance
        database: Neo4j database name
        logger: Optional logger instance
    """
    constraints = [
        "CREATE CONSTRAINT company_cik IF NOT EXISTS FOR (c:Company) REQUIRE c.cik IS UNIQUE",
        "CREATE INDEX company_ticker IF NOT EXISTS FOR (c:Company) ON (c.ticker)",
        "CREATE INDEX company_sector IF NOT EXISTS FOR (c:Company) ON (c.sector)",
        "CREATE INDEX company_sic_code IF NOT EXISTS FOR (c:Company) ON (c.sic_code)",
    ]
    _run_constraints(driver, constraints, database=database, log=logger)


def create_ownership_constraints(
    driver, database: str | None = None, logger: logging.Logger | None = None
) -> None:
    """
    Create constraints and indexes for the SEC ownership filer nodes.

    CIK-keyed structured filings (Form 3/4/5, 13F, 13D/G) produce distinct Insider,
    InstitutionalManager and BeneficialOwner nodes. ``BeneficialOwner`` is keyed on ``owner_key``
    rather than ``cik`` because a minority of 13D/G filers cannot be resolved to a CIK from the
    submission header; those fall back to a name slug and carry ``resolved=false``.

    Also creates relationship-property indexes on the two hot ``BENEFICIAL_OWNER_OF`` filters
    (``filing_type``, ``control_class``) that every control-chain and coalition query uses.

    Args:
        driver: Neo4j driver instance
        database: Neo4j database name
        logger: Optional logger instance
    """
    constraints = [
        (
            "CREATE CONSTRAINT unique_insider_cik IF NOT EXISTS "
            "FOR (i:Insider) REQUIRE i.cik IS UNIQUE"
        ),
        (
            "CREATE CONSTRAINT unique_institutional_manager_cik IF NOT EXISTS "
            "FOR (m:InstitutionalManager) REQUIRE m.cik IS UNIQUE"
        ),
        (
            "CREATE CONSTRAINT unique_beneficial_owner_key IF NOT EXISTS "
            "FOR (b:BeneficialOwner) REQUIRE b.owner_key IS UNIQUE"
        ),
        "CREATE INDEX insider_name IF NOT EXISTS FOR (i:Insider) ON (i.name)",
        (
            "CREATE INDEX institutional_manager_name IF NOT EXISTS "
            "FOR (m:InstitutionalManager) ON (m.name)"
        ),
        "CREATE INDEX beneficial_owner_name IF NOT EXISTS FOR (b:BeneficialOwner) ON (b.name)",
        # Relationship-property indexes for the hot traversal filters.
        (
            "CREATE INDEX boo_filing_type IF NOT EXISTS "
            "FOR ()-[r:BENEFICIAL_OWNER_OF]-() ON (r.filing_type)"
        ),
        (
            "CREATE INDEX boo_control_class IF NOT EXISTS "
            "FOR ()-[r:BENEFICIAL_OWNER_OF]-() ON (r.control_class)"
        ),
    ]
    _run_constraints(driver, constraints, database=database, log=logger)


def create_all_constraints(
    driver, database: str | None = None, logger: logging.Logger | None = None
) -> None:
    """Create every constraint the ownership graph needs (Company + filer nodes)."""
    create_company_constraints(driver, database=database, logger=logger)
    create_ownership_constraints(driver, database=database, logger=logger)
