"""
SEC ownership-relationship graph ingestion.

Builds a standalone, reproducible graph of CIK-keyed structured SEC filings:

- Company universe (``company_tickers.json``)
- Insiders (Form 3/4/5 reporting owners → DIRECTOR_OF/OFFICER_OF/TEN_PCT_OWNER_OF)
- Beneficial owners (Schedule 13D/13G → BENEFICIAL_OWNER_OF)
- Institutional managers (Form 13F → HOLDS)

Every artifact is (re)built from SEC source by :mod:`~secgraph.ingestion.ownership.pipeline`;
nothing depends on files left on disk by an earlier run. See
``docs/reference_architecture_secgraph.md`` for the design and the honest limits, and
``docs/demo_script_governance_desk.md`` for what the graph is actually for.
"""
