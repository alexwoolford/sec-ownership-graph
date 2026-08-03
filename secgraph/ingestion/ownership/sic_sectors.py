"""
Static SIC-range → sector mapping (no network access).

The SEC assigns each filer a 4-digit Standard Industrial Classification (SIC)
code. This module buckets those codes into the coarse divisions defined by the
SIC standard so that ``Company.sector`` is populated deterministically from a
hard-keyed source, without a Yahoo Finance round-trip.

Reference: the SIC division structure published by the US Department of Labor /
SEC (``https://www.sec.gov/corpfin/division-of-corporation-finance-standard-industrial-classification-sic-code-list``).
"""

from __future__ import annotations

# (inclusive_low, inclusive_high, sector) ordered by range.
# Ranges follow the standard SIC "divisions" (A–J plus public administration).
_SIC_RANGES: list[tuple[int, int, str]] = [
    (100, 999, "Agriculture, Forestry & Fishing"),
    (1000, 1499, "Mining"),
    (1500, 1799, "Construction"),
    (2000, 3999, "Manufacturing"),
    (4000, 4999, "Transportation & Public Utilities"),
    (5000, 5199, "Wholesale Trade"),
    (5200, 5999, "Retail Trade"),
    (6000, 6799, "Finance, Insurance & Real Estate"),
    (7000, 8999, "Services"),
    (9100, 9729, "Public Administration"),
    (9900, 9999, "Nonclassifiable"),
]


def sector_for_sic(sic_code: str | int | None) -> str | None:
    """Return the coarse SIC division for a SIC code, or None if unmappable.

    Accepts a string or int; non-numeric / empty / out-of-range inputs return
    None (the caller omits the property rather than storing a placeholder).

    Examples:
        >>> sector_for_sic("3571")
        'Manufacturing'
        >>> sector_for_sic(6021)
        'Finance, Insurance & Real Estate'
        >>> sector_for_sic("") is None
        True
    """
    if sic_code is None:
        return None
    try:
        code = int(str(sic_code).strip())
    except (ValueError, TypeError):
        return None
    for low, high, sector in _SIC_RANGES:
        if low <= code <= high:
            return sector
    return None
