"""
Materialize the size proxy ``Company.institutional_value_usd`` from 13F holdings.

**Why this exists.** Every structural result the graph produces was unrankable. A verified ≥50%
control relationship over T-Mobile US and one over a $30 shell rendered as peer rows, so the
demo's own docs conceded "no materiality data — results cannot be ranked by 'does this matter'"
and called it the largest remaining gap.

The gap turned out to be presentational, not a data gap. Measured on the built graph, **39 of 825
controlled issuers are ≥$10B** by ``size_usd`` and 150 are ≥$1B — Deutsche Telekom at 74.3% of
T-Mobile, Ergen at 51.8% of EchoStar, GE at 62.6% of Baker Hughes, Woodbridge at 70% of Thomson
Reuters. (Those counts were 20 and 97 when size was 13F float alone; see ``materialize_size``
below and :mod:`financials` for why float understates this population specifically.) The
activist coalition already co-targets Deere, Freeport, Ecolab and Occidental. **Those findings were
always there; there was simply no column to sort by, so the output surfaced closed-end funds and
nano-caps first and the recognizable names sank.** One ``sum()`` over the 13F edges already loaded
fixes that — no new dataset, no new download step, nothing further to go stale.

**What the number is, and is not.** It is the total 13F-reported institutional dollars in one
issuer for a single quarter. It is **not** a market cap, and is deliberately not named as one.
Three limits, each stated rather than smoothed over:

- **~25% of the universe has no value at all** (no 13F coverage in any quarter). A null means
  "not institutionally held" — it must never be coerced to 0, which would sort those issuers as
  *smallest* rather than *unknown*. Those are different claims, and only one of them is true.
- **It measures free float.** An issuer with a 74% strategic holder shows only its ~26% float, so
  the proxy systematically *understates* exactly the concentrated-ownership companies control
  chains are about. The bias direction is knowable and conservative: if the proxy says $10B, the
  real figure is higher. It never manufactures a false positive.
- **ETFs are in the Company universe and 13F filers report them**, so SPY and QQQ rank near the
  top on other people's money. Roughly 10 of the top 500 — small, but real.

Per-quarter by construction: 13F ``HOLDS`` is keyed on ``report_period``, and summing across
quarters would multiply-count one position. The period travels with the figure in
``institutional_value_period`` so a served answer can be audited against its source quarter.

Writes only to the target database (intended: ``secgraph``); idempotent and dry-run-by-default
(``--execute`` to write, ``--replace`` to clear stale values first).
"""

from __future__ import annotations

import logging
from typing import Any

from secgraph.core.config.constants import BATCH_SIZE_LARGE

logger = logging.getLogger(__name__)

# The newest 13F quarter present. Chosen at materialize time rather than hard-coded so a refresh
# picks up a new quarter automatically; reported in the summary so the figure is auditable.
_LATEST_PERIOD_QUERY = """
    MATCH ()-[h:HOLDS]->()
    RETURN max(h.report_period) AS period
"""

# Read-only aggregation. Restricted to ONE report_period: HOLDS is a per-quarter position time
# series, so summing across quarters would count the same holding several times over.
_COMPUTE_QUERY = """
    MATCH (:InstitutionalManager)-[h:HOLDS]->(c:Company)
    WHERE h.report_period = $period AND h.value_usd IS NOT NULL
    WITH c.cik AS cik, sum(h.value_usd) AS value_usd, count(DISTINCT h) AS positions
    RETURN cik, value_usd, positions
    ORDER BY value_usd DESC
"""

_WRITE_QUERY = """
    UNWIND $batch AS row
    MATCH (c:Company {cik: row.cik})
    SET c.institutional_value_usd = row.value_usd,
        c.institutional_value_period = date($period)
    RETURN count(c) AS n
"""

# Clear before a --replace rebuild so an issuer that loses 13F coverage does not keep a stale
# figure. Absence is meaningful here ("not institutionally held"), so a stale value is worse than
# no value: it would rank a now-uncovered issuer as though it were still held.
_CLEAR_QUERY = """
    MATCH (c:Company)
    WHERE c.institutional_value_usd IS NOT NULL
    REMOVE c.institutional_value_usd, c.institutional_value_period
    RETURN count(c) AS n
"""

_UNIVERSE_QUERY = "MATCH (c:Company) RETURN count(c) AS n"

# The combined measure every size filter and ranking reads. Written here rather than in a third
# materializer because it is a function of the two inputs and must never drift from them.
#
# Precedence is total_assets_usd first, deliberately: it is a real balance sheet, where
# institutional_value_usd is free float and therefore smallest exactly where ownership is
# concentrated. EchoStar carries $43B of assets, is 51.8% controlled, and has NO 13F coverage at
# all — so under the float-only measure it was invisible to every size-filtered query.
#
# size_source is not decoration. A $43B assets figure and a $43B float figure are different
# claims about different quantities, and without the label they are indistinguishable in output.
# Any consumer that renders size_usd must be able to say which one it got.
_SIZE_QUERY = """
    MATCH (c:Company)
    WHERE c.total_assets_usd IS NOT NULL OR c.institutional_value_usd IS NOT NULL
    SET c.size_usd = coalesce(c.total_assets_usd, c.institutional_value_usd),
        c.size_source = CASE
            WHEN c.total_assets_usd IS NOT NULL THEN 'dera_assets'
            ELSE 'institutional_13f' END
    RETURN count(c) AS n
"""

# Clear first, always — not just on --replace. An issuer that loses BOTH inputs must lose
# size_usd too, and a company that gains assets must flip size_source away from the 13F label.
# Recomputing in place without clearing would leave a stale figure that outranks live ones.
_SIZE_CLEAR_QUERY = """
    MATCH (c:Company)
    WHERE c.size_usd IS NOT NULL OR c.size_source IS NOT NULL
    REMOVE c.size_usd, c.size_source
    RETURN count(c) AS n
"""

_SIZE_BREAKDOWN_QUERY = """
    MATCH (c:Company) WHERE c.size_source IS NOT NULL
    RETURN c.size_source AS source, count(*) AS n
    ORDER BY n DESC
"""


def materialize_size(
    driver,
    database: str | None = None,
    execute: bool = False,
    logger_instance: logging.Logger | None = None,
) -> dict[str, Any]:
    """Recompute ``size_usd`` / ``size_source`` from the two size inputs. Dry-run unless execute.

    Idempotent and safe to run whenever either input changes. Must run **after** both
    ``institutional_value_usd`` and ``total_assets_usd`` are loaded, since it reads both; running
    it earlier is not an error but yields partial coverage.
    """
    log = logger_instance or logger

    with driver.session(database=database) as session:
        universe = session.run(_UNIVERSE_QUERY).single()["n"]
        sizable = session.run(
            """
            MATCH (c:Company)
            WHERE c.total_assets_usd IS NOT NULL OR c.institutional_value_usd IS NOT NULL
            RETURN count(c) AS n
            """
        ).single()["n"]

    log.info(
        f"combined size measure: {sizable:,} of {universe:,} companies "
        f"({100 * sizable / universe:.1f}%) have at least one size input"
    )

    if not execute:
        log.info(f"DRY RUN — would set size_usd/size_source on {sizable:,} Company nodes")
        return {"sizable": sizable, "universe": universe, "dry_run": True}

    with driver.session(database=database) as session:
        cleared = session.run(_SIZE_CLEAR_QUERY).single()["n"]
        written = session.run(_SIZE_QUERY).single()["n"]
        breakdown = {r["source"]: r["n"] for r in session.run(_SIZE_BREAKDOWN_QUERY)}

    log.info(
        f"✓ size_usd set on {written:,} companies "
        f"(assets {breakdown.get('dera_assets', 0):,} · 13F float "
        f"{breakdown.get('institutional_13f', 0):,}) · {universe - written:,} remain unsized"
    )
    return {
        "sizable": sizable,
        "universe": universe,
        "cleared": cleared,
        "written": written,
        "by_source": breakdown,
    }


# --------------------------------------------------------------------------- #
# Pure helpers — the unit-test surface.
# --------------------------------------------------------------------------- #
def batches(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    """Split rows into write batches of at most ``size`` (last batch may be short)."""
    if size <= 0:
        raise ValueError("batch size must be positive")
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def summarize_materiality(rows: list[dict[str, Any]], universe_count: int) -> dict[str, Any]:
    """Coverage + distribution stats over the computed values.

    The coverage figure is the honest headline: a proxy that silently omits a quarter of the
    universe would otherwise look complete. Buckets are reported because the *shape* is the
    argument — that large controlled issuers exist at all is what the property is for.
    """
    values = sorted((r["value_usd"] for r in rows if r.get("value_usd")), reverse=True)
    covered = len(values)
    buckets = {
        "ge_100b": sum(1 for v in values if v >= 100e9),
        "ge_10b": sum(1 for v in values if v >= 10e9),
        "ge_1b": sum(1 for v in values if v >= 1e9),
        "ge_100m": sum(1 for v in values if v >= 100e6),
        "lt_100m": sum(1 for v in values if v < 100e6),
    }
    return {
        "companies_with_value": covered,
        "companies_without_value": max(0, universe_count - covered),
        "coverage_pct": round(covered / universe_count * 100, 1) if universe_count else 0.0,
        "total_usd": sum(values),
        "max_usd": values[0] if values else None,
        "median_usd": values[covered // 2] if covered else None,
        "buckets": buckets,
    }


# --------------------------------------------------------------------------- #
# DB-bound compute + write.
# --------------------------------------------------------------------------- #
def materialize_materiality(
    driver,
    database: str | None = None,
    replace: bool = False,
    batch_size: int = BATCH_SIZE_LARGE,
    execute: bool = False,
    logger_instance: logging.Logger | None = None,
) -> dict[str, Any]:
    """Compute and persist ``institutional_value_usd``. Dry-run unless ``execute``.

    Returns a summary dict (coverage, buckets, the period used), plus ``written`` / ``cleared``
    when executed. Abstains cleanly — returning a summary with ``period: None`` rather than
    raising — when no 13F layer is present, since the graph is legitimately usable without it.
    """
    log = logger_instance or logger

    with driver.session(database=database) as session:
        period = session.run(_LATEST_PERIOD_QUERY).single()["period"]
        if period is None:
            log.warning(
                "no 13F HOLDS edges found — skipping the materiality proxy. "
                "Structural results will be unrankable by size until 13F is loaded."
            )
            return {"period": None, "companies_with_value": 0, "skipped": True}
        universe_count = session.run(_UNIVERSE_QUERY).single()["n"]
        rows = session.run(_COMPUTE_QUERY, period=period).data()

    summary = summarize_materiality(rows, universe_count)
    summary["period"] = str(period)
    b = summary["buckets"]
    log.info(
        f"13F institutional value for {period}: {summary['companies_with_value']:,} of "
        f"{universe_count:,} companies ({summary['coverage_pct']}%) — "
        f"{b['ge_100b']:,} >=$100B · {b['ge_10b']:,} >=$10B · {b['ge_1b']:,} >=$1B · "
        f"{b['lt_100m']:,} <$100M"
    )
    log.info(
        f"  {summary['companies_without_value']:,} companies have NO 13F coverage — they get no "
        f"value at all (a null means 'not institutionally held', never zero)"
    )

    if not execute:
        log.info("")
        log.info(
            f"DRY RUN — would set institutional_value_usd on "
            f"{summary['companies_with_value']:,} Company nodes (period {period})"
        )
        if replace:
            log.info("  (after clearing existing values)")
        log.info("Run with --execute to write.")
        summary["dry_run"] = True
        return summary

    with driver.session(database=database) as session:
        if replace:
            summary["cleared"] = session.run(_CLEAR_QUERY).single()["n"]
            log.info(f"cleared {summary['cleared']:,} existing values")
        written = 0
        for batch in batches(rows, batch_size):
            written += session.run(_WRITE_QUERY, batch=batch, period=str(period)).single()["n"]
        summary["written"] = written

    log.info(f"✓ Set institutional_value_usd on {written:,} companies (period {period})")
    return summary
