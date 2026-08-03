"""
CUSIP → CIK crosswalk (key-based, via SEC fails-to-deliver SYMBOL bridge).

13F ``INFOTABLE`` rows are keyed by CUSIP, but the ownership graph is keyed by
CIK. There is no single authoritative public CUSIP→CIK map, but there *is* an
authoritative keyed chain, all published by the SEC:

    13F CUSIP-9  →  fails-to-deliver file (CUSIP|SYMBOL)  →  ticker  →  CIK

The fails-to-deliver dataset lists ``CUSIP | SYMBOL`` for essentially every
traded US equity, twice a month. The ticker→CIK half is already trusted — it is
how the Company universe was defined (``company_tickers.json``). So the crosswalk
is a pure key join with **no fuzzy name matching**: we map each CUSIP-9 to its
majority SYMBOL, then that symbol to the in-universe ticker's CIK.

This replaced an earlier name-normalization matcher that was not only incomplete
(~50% of holding-rows) but *wrong* — it silently misattributed ``RIO TINTO PLC``
to Rio Tinto Ltd, ``GRAHAM CORP`` to Graham Holdings, etc. The keyed approach
attributes ~74% of holding-rows and corrects those errors; the residual is
dominated by ETFs/bonds/foreign ADRs that are legitimately out of the universe.

CUSIPs whose symbol is not an in-universe ticker are logged (never guessed). The
output is written to ``data/sec_ownership/cusip_crosswalk/`` as a JSON map plus an
``unmatched.json`` for auditing; it is a pure artifact, rebuildable from source.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from pathlib import Path

from secgraph.ingestion.ownership.bulk_datasets import (
    get_ownership_data_dir,
    iter_ftd_rows,
)

logger = logging.getLogger(__name__)

_TICKER_NOISE_RE = re.compile(r"[^A-Z0-9]")


def cusip6(cusip: str) -> str | None:
    """Return the 6-char issuer prefix of a CUSIP, or None if too short.

    Retained as a utility; the crosswalk itself keys on the full 9-char CUSIP
    (the security-level key) to avoid the prefix collisions that recycled and
    misattributed issuers.

    Examples:
        >>> cusip6("037833100")
        '037833'
        >>> cusip6("123") is None
        True
    """
    cusip = (cusip or "").strip()
    return cusip[:6] if len(cusip) >= 6 else None


def cusip9(cusip: str) -> str | None:
    """Return the full 9-char CUSIP (the security-level key), or None if malformed.

    Examples:
        >>> cusip9("037833100")
        '037833100'
        >>> cusip9(" 037833100 ")
        '037833100'
        >>> cusip9("03783") is None
        True
    """
    cusip = (cusip or "").strip()
    return cusip if len(cusip) == 9 else None


def normalize_ticker(ticker: str) -> str:
    """Uppercase and strip punctuation so class shares align across sources.

    FTD writes ``BRKB`` where ``company_tickers.json`` stores ``BRK.B``; this
    dot/dash-insensitive form lets the two meet without any fuzzy matching.

    Examples:
        >>> normalize_ticker("BRK.B")
        'BRKB'
        >>> normalize_ticker("brk-b")
        'BRKB'
    """
    return _TICKER_NOISE_RE.sub("", ticker.upper())


def collect_ftd_symbols(
    ftd_zip_paths: list[Path], log: logging.Logger | None = None
) -> dict[str, str]:
    """CUSIP-9 → most-common SYMBOL across all fails-to-deliver rows.

    A CUSIP is stable to one security, but a filing may carry an occasional typo,
    so we take the majority symbol seen at each CUSIP-9.
    """
    log = log or logger
    counters: dict[str, Counter] = defaultdict(Counter)
    for zip_path in ftd_zip_paths:
        for row in iter_ftd_rows(zip_path):
            cu = cusip9(row.get("CUSIP", ""))
            sym = (row.get("SYMBOL") or "").strip().upper()
            if cu and sym:
                counters[cu][sym] += 1
    log.info(f"  {len(counters):,} distinct CUSIP-9 with a symbol (from fails-to-deliver)")
    return {cu: c.most_common(1)[0][0] for cu, c in counters.items()}


def build_ticker_index(company_rows: list[dict[str, str]]) -> dict[str, str]:
    """Map ticker (exact + punctuation-normalized) → CIK for in-universe companies.

    Both the exact ticker and its normalized form are indexed so an FTD ``BRKB``
    resolves to the universe's ``BRK.B``. Exact keys win over normalized ones on
    collision (the normalized index is only a fallback).
    """
    exact: dict[str, str] = {}
    normalized: dict[str, str] = {}
    for row in company_rows:
        ticker = (row.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        exact.setdefault(ticker, row["cik"])
        normalized.setdefault(normalize_ticker(ticker), row["cik"])
    # Merge with exact taking precedence.
    return {**normalized, **exact}


def _resolve_symbol(symbol: str, ticker_index: dict[str, str]) -> str | None:
    """Resolve an FTD symbol to a universe CIK (exact, then punctuation-insensitive)."""
    return ticker_index.get(symbol) or ticker_index.get(normalize_ticker(symbol))


def match_cusips(
    cusip_symbols: dict[str, str],
    ticker_index: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Match CUSIP-9 → CIK via its FTD symbol → in-universe ticker.

    Returns (matched: cusip9→cik, unmatched: cusip9→symbol). Unmatched symbols are
    securities whose ticker is not in the universe (ETFs, bonds, foreign ADRs).
    """
    matched: dict[str, str] = {}
    unmatched: dict[str, str] = {}
    for cu, symbol in cusip_symbols.items():
        cik = _resolve_symbol(symbol, ticker_index)
        if cik is not None:
            matched[cu] = cik
        else:
            unmatched[cu] = symbol
    return matched, unmatched


def build_cusip_crosswalk(
    driver,
    ftd_zip_paths: list[Path],
    database: str | None = None,
    execute: bool = False,
    logger_instance: logging.Logger | None = None,
) -> dict:
    """Build and persist the CUSIP-9 → CIK crosswalk from FTD symbols + universe tickers.

    Args:
        driver: Neo4j driver
        ftd_zip_paths: Cached fails-to-deliver zips (the CUSIP↔SYMBOL bridge)
        database: Target Neo4j database (source of the Company universe)
        execute: If False, only report the plan (crosswalk is still computed)
        logger_instance: Optional logger

    Returns:
        Dict of statistics including match rate; writes crosswalk.json +
        unmatched.json under data/sec_ownership/cusip_crosswalk/.
    """
    log = logger_instance or logger

    if not ftd_zip_paths:
        log.error(
            "No fails-to-deliver zips provided; run download_ownership_data.py --form ftd first"
        )
        return {"error": "no_input"}

    with driver.session(database=database) as session:
        company_rows = [
            {"cik": r["cik"], "ticker": r["ticker"]}
            for r in session.run(
                "MATCH (c:Company) WHERE c.ticker IS NOT NULL "
                "RETURN c.cik AS cik, c.ticker AS ticker"
            )
        ]
    log.info(f"Universe: {len(company_rows):,} companies with a ticker")

    log.info("Collecting CUSIP↔symbol pairs from fails-to-deliver data...")
    cusip_symbols = collect_ftd_symbols(ftd_zip_paths, log=log)
    ticker_index = build_ticker_index(company_rows)
    matched, unmatched = match_cusips(cusip_symbols, ticker_index)

    total = len(cusip_symbols)
    rate = round(len(matched) / total * 100, 1) if total else 0.0
    distinct_companies = len(set(matched.values()))
    log.info(
        f"  matched {len(matched):,}/{total:,} CUSIP-9 ({rate}%) → "
        f"{distinct_companies:,} distinct companies"
    )

    out_dir = get_ownership_data_dir("cusip_crosswalk")
    if execute:
        (out_dir / "crosswalk.json").write_text(json.dumps(matched, indent=0))
        (out_dir / "unmatched.json").write_text(json.dumps(unmatched, indent=0))
        log.info(f"✓ Wrote crosswalk.json ({len(matched):,}) + unmatched.json ({len(unmatched):,})")
    else:
        log.info("DRY RUN — crosswalk computed but not written; run with --execute to persist.")

    return {
        "distinct_cusip9": total,
        "matched": len(matched),
        "unmatched": len(unmatched),
        "distinct_companies": distinct_companies,
        "match_rate_pct": rate,
        "output_dir": str(out_dir),
    }


def load_crosswalk() -> dict[str, str]:
    """Load the persisted CUSIP-9 → CIK crosswalk (empty dict if absent)."""
    path = get_ownership_data_dir("cusip_crosswalk") / "crosswalk.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())
