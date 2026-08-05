"""
SEC bulk financial-statement dataset acquisition (shared).

The SEC publishes quarterly bulk ZIP archives of structured filing data. This
module discovers the current download URLs by scraping the dataset landing page
(the SEC recently moved the insider path from ``structureddata/`` to
``datastandardsinnovation/``, so URLs must never be hard-coded), downloads each
quarter into a pipeline-managed cache, and parses the TSV tables inside.

Two dataset families are handled here:

- **Form 3/4/5 (insiders)** — ``form-345`` landing page, tables ``SUBMISSION``
  (accession → issuer CIK) and ``REPORTINGOWNER`` (owner CIK/name/relationship).
- **Form 13F (institutional holdings)** — ``form-13f`` landing page, tables
  ``COVERPAGE`` (filer CIK) and ``INFOTABLE`` (CUSIP-keyed holdings).

Cache layout (under ``data/sec_ownership/<subdir>/``)::

    <quarter>_<slug>.zip        # raw archive, keyed by quarter
    <quarter>/<TABLE>.tsv       # not extracted; tables are read from the zip

Idempotent: an existing cached zip is reused unless ``refresh=True``.
"""

from __future__ import annotations

import calendar
import io
import logging
import re
import urllib.request
import zipfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path

from secgraph.core.config.constants import SEC_EDGAR_RATE_LIMIT
from secgraph.core.config.settings import get_data_dir
from secgraph.core.rate_limiting import get_rate_limiter

logger = logging.getLogger(__name__)

# SEC fair-access requires a descriptive User-Agent (name + contact).
_SEC_USER_AGENT = "public-company-graph research contact@example.com"

# Dataset landing pages (scraped for the current .zip URLs — never hard-code).
FORM_345_LANDING = "https://www.sec.gov/dera/data/form-345"
FORM_13F_LANDING = "https://www.sec.gov/dera/data/form-13f"
# Fails-to-deliver: the keyed CUSIP↔SYMBOL bridge (see cusip_crosswalk). SEC has
# shuffled this between /files/data/ and /files/data/other/, so scrape, never hardcode.
FTD_LANDING = "https://www.sec.gov/data-research/sec-markets-data/fails-deliver-data"

_SEC_BASE = "https://www.sec.gov"
# Three naming schemes seen in the wild:
#   Form 345:  2025q1_form345.zip                     → key "2025q1"
#   Form 13F:  01sep2025-30nov2025_form13f.zip        → key "01sep2025-30nov2025"
#   FTD:       cnsfails202606b.zip                    → key "202606b" (b = 2nd half)
_QUARTER_RE = re.compile(r"(\d{4}q[1-4])", re.IGNORECASE)
_DATERANGE_RE = re.compile(r"(\d{2}[a-z]{3}\d{4}-\d{2}[a-z]{3}\d{4})", re.IGNORECASE)
_FTD_RE = re.compile(r"(\d{6}[ab])", re.IGNORECASE)


def _period_key(href: str) -> str | None:
    """Extract the period key from a bulk-dataset zip URL (quarter, range, or FTD half)."""
    match = _QUARTER_RE.search(href) or _DATERANGE_RE.search(href) or _FTD_RE.search(href)
    return match.group(1).lower() if match else None


def _user_agent() -> str:
    import os

    return os.environ.get("SEC_USER_AGENT", _SEC_USER_AGENT)


def _http_get(url: str) -> bytes:
    """Fetch a URL as bytes, throttled to the SEC fair-access limit."""
    limiter = get_rate_limiter("sec_edgar", requests_per_second=SEC_EDGAR_RATE_LIMIT)
    if limiter is not None:
        limiter()
    req = urllib.request.Request(url, headers={"User-Agent": _user_agent()})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 - fixed https host
        return resp.read()


def get_ownership_data_dir(subdir: str) -> Path:
    """Return (creating) the cache directory for a dataset family."""
    path = get_data_dir() / "sec_ownership" / subdir
    path.mkdir(parents=True, exist_ok=True)
    return path


def discover_quarter_zip_urls(landing_url: str) -> dict[str, str]:
    """Scrape a dataset landing page → {``"2025q1"``: absolute_zip_url}.

    Keeps only the first URL seen per quarter (the landing page lists each
    quarter once; duplicates would indicate a page-format change).
    """
    html = _http_get(landing_url).decode("utf-8", "ignore")
    hrefs = re.findall(r'href="([^"]+\.zip)"', html)
    quarters: dict[str, str] = {}
    for href in hrefs:
        key = _period_key(href)
        if key is None or key in quarters:
            continue
        quarters[key] = href if href.startswith("http") else f"{_SEC_BASE}{href}"
    return quarters


def _period_sort_key(period: str) -> tuple:
    """Sort key ordering 'YYYYqN', 'DDmonYYYY-DDmonYYYY', and FTD 'YYYYMMh' periods.

    Range keys sort by their END date; quarter keys by (year, quarter); FTD keys
    by (year, month, half) so the 2nd-half file of a month sorts after the 1st.
    """
    range_match = _DATERANGE_RE.fullmatch(period)
    if range_match:
        end = period.split("-")[1]  # e.g. "30nov2025"
        year = int(end[-4:])
        month = _MONTHS.get(end[2:5].upper(), "00")
        return (year, int(month), 0)
    ftd_match = _FTD_RE.fullmatch(period)
    if ftd_match:
        return (int(period[:4]), int(period[4:6]), 1 if period[6] == "b" else 0)
    year, quarter = period.split("q")
    return (int(year), int(quarter) * 3, 0)


def period_end_date(period: str) -> date:
    """Last calendar day covered by a period key.

    Lets ``--as-of`` bound selection: "the 12 quarters ending on or before D" is reproducible,
    whereas "the most recent 12" silently means something different every week.
    """
    year, month, half = _period_sort_key(period)
    if _FTD_RE.fullmatch(period):
        # FTD files are half-months: 'a' covers the 1st-15th, 'b' the rest of the month.
        if half == 0:
            return date(year, month, 15)
        return date(year, month, calendar.monthrange(year, month)[1])
    # Quarter and date-range keys both resolve to a month end via _period_sort_key.
    return date(year, month, calendar.monthrange(year, month)[1])


def select_quarters(
    available: dict[str, str], num_quarters: int, as_of: date | str | None = None
) -> list[str]:
    """Return the most-recent ``num_quarters`` period keys, newest first.

    ``as_of`` (a date or ``YYYY-MM-DD`` string) excludes periods ending after that date, so a
    rebuild selects the same windows the reference build did instead of drifting forward as SEC
    publishes new quarters.
    """
    keys = list(available)
    if as_of is not None:
        cutoff = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
        keys = [k for k in keys if period_end_date(k) <= cutoff]
    ordered = sorted(keys, key=_period_sort_key, reverse=True)
    return ordered[:num_quarters]


def download_quarter_zip(
    quarter: str,
    url: str,
    subdir: str,
    refresh: bool = False,
    log: logging.Logger | None = None,
) -> Path:
    """Download (and cache) a single quarter's zip; reuse cache unless refresh."""
    log = log or logger
    cache_dir = get_ownership_data_dir(subdir)
    dest = cache_dir / f"{quarter}_{subdir}.zip"
    if dest.exists() and not refresh:
        log.info(f"  cache hit: {dest.name} ({dest.stat().st_size:,} bytes)")
        return dest
    log.info(f"  downloading {url}")
    data = _http_get(url)
    dest.write_bytes(data)
    log.info(f"  wrote {dest.name} ({len(data):,} bytes)")
    return dest


def download_dataset(
    landing_url: str,
    subdir: str,
    num_quarters: int,
    refresh: bool = False,
    as_of: date | str | None = None,
    log: logging.Logger | None = None,
) -> list[Path]:
    """Discover + download the most recent ``num_quarters`` zips for a dataset.

    ``as_of`` bounds selection to periods ending on or before that date, so the staged window is
    reproducible instead of sliding forward as SEC publishes.
    """
    log = log or logger
    available = discover_quarter_zip_urls(landing_url)
    if not available:
        raise RuntimeError(f"No quarterly zip links found at {landing_url}")
    selected = select_quarters(available, num_quarters, as_of=as_of)
    if not selected:
        raise RuntimeError(
            f"No periods at {landing_url} end on or before as-of {as_of}; "
            f"available: {sorted(available)[:5]}..."
        )
    suffix = f" (as of {as_of})" if as_of else ""
    log.info(f"  {len(available)} periods available; selecting {selected}{suffix}")
    return [
        download_quarter_zip(q, available[q], subdir, refresh=refresh, log=log) for q in selected
    ]


def staged_zip_paths(
    subdir: str, limit: int | None = None, as_of: date | str | None = None
) -> list[Path]:
    """Return cached zips for a dataset family, newest period first.

    This globs the *whole* local cache, which is a reproducibility hazard: a machine that once
    staged 16 quarters loads all 16, while a fresh clone that staged 12 loads 12 — same command,
    different graph. ``limit`` and ``as_of`` bound the window so a loader reads exactly what was
    intended rather than whatever download history happens to be on disk.

    Args:
        subdir: Dataset family (``form345`` / ``form13f`` / ``ftd``).
        limit: Keep at most this many periods (newest first). None = no cap.
        as_of: Drop periods ending after this date (``YYYY-MM-DD`` or a ``date``).
    """
    cache_dir = get_ownership_data_dir(subdir)
    zips = list(cache_dir.glob(f"*_{subdir}.zip"))

    def period_of(path: Path) -> str:
        return path.name.rsplit(f"_{subdir}.zip", 1)[0]

    def sort_key(path: Path) -> tuple:
        try:
            return _period_sort_key(period_of(path))
        except (ValueError, IndexError):
            return (0, 0, 0)

    if as_of is not None:
        cutoff = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
        kept = []
        for path in zips:
            try:
                if period_end_date(period_of(path)) <= cutoff:
                    kept.append(path)
            except (ValueError, IndexError, KeyError):
                # Unparseable filename: keep it rather than silently dropping data.
                kept.append(path)
        zips = kept

    ordered = sorted(zips, key=sort_key, reverse=True)
    return ordered[:limit] if limit is not None else ordered


def iter_tsv_rows(zip_path: Path, table: str) -> Iterator[dict[str, str]]:
    """Yield each row of a TSV table inside a bulk zip as a dict.

    ``table`` is the base name without extension (e.g. ``"SUBMISSION"``);
    ``.tsv`` is appended. Splits on tab; tolerates ragged rows by zipping
    against the header (missing trailing fields become "").
    """
    filename = f"{table}.tsv"
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        # SEC 13F zips ship two layouts: tables at the archive root
        # ("SUBMISSION.tsv") or nested under a per-period folder
        # ("01jun2025-31aug2025_form13f/SUBMISSION.tsv"). Match either.
        member = next(
            (n for n in names if n == filename or n.rsplit("/", 1)[-1] == filename),
            None,
        )
        if member is None:
            raise KeyError(f"{filename} not in {zip_path.name}: {names}")
        with archive.open(member) as raw:
            stream = io.TextIOWrapper(raw, encoding="utf-8", errors="ignore", newline="")
            header_line = stream.readline().rstrip("\n").rstrip("\r")
            columns = header_line.split("\t")
            for line in stream:
                values = line.rstrip("\n").rstrip("\r").split("\t")
                yield dict(zip(columns, values, strict=False))


def iter_ftd_rows(zip_path: Path) -> Iterator[dict[str, str]]:
    """Yield each row of a fails-to-deliver file as a dict.

    Unlike the bulk TSVs, FTD files are pipe-delimited and latin-1 encoded, with
    a single member per zip. Columns include SETTLEMENT DATE, CUSIP, SYMBOL,
    QUANTITY (FAILS), DESCRIPTION, PRICE. We read them only for the keyed
    CUSIP↔SYMBOL pairing.
    """
    with zipfile.ZipFile(zip_path) as archive:
        member = archive.namelist()[0]
        with archive.open(member) as raw:
            stream = io.TextIOWrapper(raw, encoding="latin-1", newline="")
            header_line = stream.readline().rstrip("\r\n")
            columns = header_line.split("|")
            for line in stream:
                values = line.rstrip("\r\n").split("|")
                yield dict(zip(columns, values, strict=False))


# --- Date parsing: bulk TSVs use DD-MON-YYYY (e.g. "31-MAR-2025") ------------
_MONTHS = {
    "JAN": "01",
    "FEB": "02",
    "MAR": "03",
    "APR": "04",
    "MAY": "05",
    "JUN": "06",
    "JUL": "07",
    "AUG": "08",
    "SEP": "09",
    "OCT": "10",
    "NOV": "11",
    "DEC": "12",
}


def parse_sec_date(value: str | None) -> str | None:
    """Convert a bulk-dataset ``DD-MON-YYYY`` date to ISO ``YYYY-MM-DD``.

    Returns None for empty/malformed values so the caller can omit the property
    (Neo4j has no null) and so ``date()`` is never called on garbage.

    Examples:
        >>> parse_sec_date("31-MAR-2025")
        '2025-03-31'
        >>> parse_sec_date("") is None
        True
    """
    if not value:
        return None
    parts = value.strip().split("-")
    if len(parts) != 3:
        return None
    day, mon, year = parts
    month = _MONTHS.get(mon.upper())
    if month is None or not (day.isdigit() and year.isdigit()):
        return None
    return f"{year}-{month}-{int(day):02d}"


def normalize_cik(raw: str | None) -> str | None:
    """Zero-pad a CIK to 10 digits (the canonical key), or None if not numeric.

    Examples:
        >>> normalize_cik("320193")
        '0000320193'
        >>> normalize_cik("0001840502")
        '0001840502'
        >>> normalize_cik("") is None
        True
    """
    if not raw:
        return None
    digits = raw.strip().lstrip("0") or "0"
    if not digits.isdigit():
        return None
    return digits.zfill(10)
