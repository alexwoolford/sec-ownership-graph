"""
SEC ownership-relationship graph ingestion.

Builds a standalone, reproducible graph of CIK-keyed structured SEC filings:

- Company universe (``company_tickers.json``)
- Insiders (Form 3/4/5 reporting owners → DIRECTOR_OF/OFFICER_OF/TEN_PCT_OWNER_OF)
- Beneficial owners (Schedule 13D/13G → BENEFICIAL_OWNER_OF)
- Institutional managers (Form 13F → HOLDS)

Every artifact is (re)built from SEC source by the pipeline; nothing depends on
files a prior project left on disk. See
``.claude/plans/i-created-this-demo-nested-scott.md`` for the full design and
``results/insider_interlock_proof.md`` for the empirical motivation.
"""
