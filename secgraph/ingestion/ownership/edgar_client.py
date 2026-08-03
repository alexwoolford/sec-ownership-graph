"""
Throttled EDGAR client for the 13D/13G header crawl (shared).

There is no bulk dataset for Schedule 13D/13G (>5% beneficial-ownership) filings,
so this is the one path that crawls EDGAR per subject company. We only ever read
the tiny SGML submission *header* (``*.hdr.sgml`` / the top of the ``.txt``), never
the HTML body — the header carries the two CIKs (subject + filer), the form type,
and the filing date, which is all the graph needs.

All requests go through the shared SEC rate limiter (10 req/s fair-access
ceiling). Per-CIK submission indexes and per-accession headers are cached on disk
so a re-run is cheap and resumable.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

from secgraph.core.config.constants import SEC_EDGAR_RATE_LIMIT
from secgraph.core.rate_limiting import get_rate_limiter

logger = logging.getLogger(__name__)

_SEC_USER_AGENT = "public-company-graph research contact@example.com"


def is_schedule_13dg(form: str) -> bool:
    """True for a Schedule 13D/13G filing under either EDGAR form-code regime.

    The SEC's late-2024 filing modernization renamed the codes from ``SC 13D`` /
    ``SC 13G`` to ``SCHEDULE 13D`` / ``SCHEDULE 13G`` (amendments keep the ``/A``
    suffix). Matching only the legacy prefix silently drops every post-migration
    filing — which zeroed out our 2025+ beneficial-ownership coverage until this
    was caught. Accept both.
    """
    f = (form or "").upper().replace("SCHEDULE ", "SC ")
    return f.startswith("SC 13D") or f.startswith("SC 13G")


_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_HEADER_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{acc}.txt"


class SubmissionsFetchError(RuntimeError):
    """A subject's submissions index could not be fetched, and the result is unknown.

    Distinct from "this subject has no 13D/G filings", which is a legitimate empty result and
    *is* cached. This exception means we do not know, so nothing may be cached — see
    :func:`fetch_13dg_accessions`.
    """


def is_placeholder_user_agent(ua: str | None = None) -> bool:
    """True when the SEC User-Agent is still the built-in placeholder.

    ``example.com`` is RFC 2606 reserved, so it is not a declaration of identity. SEC
    fair-access rejects generic agents with HTTP 403, which used to surface as a silent
    empty graph. The preflight refuses to start a build in this state.
    """
    return "example.com" in (ua if ua is not None else _user_agent()).lower()


def _user_agent() -> str:
    import os

    return os.environ.get("SEC_USER_AGENT", _SEC_USER_AGENT)


def _throttled_get(url: str, timeout: int = 60, retries: int = 3) -> bytes:
    """Fetch a URL as bytes, throttled to the SEC limit, retrying transients.

    Read timeouts and connection resets are common over a ~200k-request crawl.
    They are retried with linear backoff (re-acquiring a rate-limit slot each
    time) so a single blip does not surface to the caller; the final attempt's
    exception propagates only if every retry fails.
    """
    limiter = get_rate_limiter("sec_edgar", requests_per_second=SEC_EDGAR_RATE_LIMIT)
    req = urllib.request.Request(url, headers={"User-Agent": _user_agent()})
    last_exc: Exception | None = None
    for attempt in range(retries):
        if limiter is not None:
            limiter()
        try:
            with urllib.request.urlopen(  # noqa: S310 - fixed https host
                req, timeout=timeout
            ) as resp:
                return resp.read()
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            # HTTP status errors (e.g. 404) are deterministic — don't retry them.
            if isinstance(exc, urllib.error.HTTPError):
                raise
            last_exc = exc
            logger.debug(
                f"transient fetch error (attempt {attempt + 1}/{retries}) for {url}: {exc}"
            )
            time.sleep(1.0 * (attempt + 1))
    raise last_exc if last_exc is not None else RuntimeError(f"fetch failed: {url}")


def fetch_13dg_accessions(
    subject_cik: str,
    cache_dir: Path,
    refresh: bool = False,
) -> list[dict[str, str]]:
    """Return this subject's Schedule 13D/13G filings: [{accession, form, date}].

    Reads the cached submissions index; filters to Schedule 13D/13G under either
    form-code regime (:func:`is_schedule_13dg`), including amendments. Cached
    per-CIK.
    """
    cache_file = cache_dir / f"{subject_cik}_index.json"
    if cache_file.exists() and not refresh:
        try:
            return json.loads(cache_file.read_text())
        except (ValueError, OSError):
            pass
    try:
        sub = json.loads(_throttled_get(_SUBMISSIONS_URL.format(cik=subject_cik)))
    except urllib.error.HTTPError as exc:
        # A 404 is a real answer: this CIK has no submissions file. Cache the empty result so a
        # resume skips it. Anything else (403 fair-access rejection, 5xx) is NOT an answer and
        # must never be cached — caching a 403 permanently zeroes the subject, and because the
        # default User-Agent is a placeholder that SEC rejects, that used to zero every issuer
        # while the build still exited 0.
        if exc.code == 404:
            logger.debug(f"no submissions file for {subject_cik} (404)")
            cache_file.write_text("[]")
            return []
        raise SubmissionsFetchError(
            f"EDGAR returned HTTP {exc.code} for CIK {subject_cik}. "
            f"Not cached. If this is 403, set a real SEC_USER_AGENT "
            f"(SEC rejects the placeholder contact address)."
        ) from exc
    except (OSError, urllib.error.URLError, ValueError) as exc:
        # Transient (timeout / connection reset / malformed body) and already retried by
        # _throttled_get. Do not cache — a network blip must not become permanent data loss.
        raise SubmissionsFetchError(
            f"submissions fetch failed for CIK {subject_cik} after retries: {exc}"
        ) from exc
    recent = sub.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    out = [
        {"accession": accessions[i], "form": forms[i], "date": dates[i]}
        for i, form in enumerate(forms)
        if is_schedule_13dg(form)
    ]
    cache_file.write_text(json.dumps(out))
    return out


def fetch_submission_header(
    subject_cik: str,
    accession: str,
    cache_dir: Path,
    refresh: bool = False,
) -> str | None:
    """Fetch (cached) the SGML submission header text for one accession.

    Only the header region (up to ``</SEC-HEADER>``) is retained on disk; the
    HTML body is discarded.
    """
    cache_file = cache_dir / f"{accession.replace('-', '')}.hdr"
    if cache_file.exists() and not refresh:
        return cache_file.read_text()
    url = _HEADER_URL.format(
        cik_int=int(subject_cik), acc_nodash=accession.replace("-", ""), acc=accession
    )
    try:
        raw = _throttled_get(url).decode("utf-8", "ignore")
    except (OSError, urllib.error.URLError) as exc:
        # OSError catches post-retry TimeoutError/reset; skip this one header
        # rather than crashing the crawl (build_edge_rows re-raises via .result).
        logger.debug(f"header fetch failed for {accession}: {exc}")
        return None
    end = raw.find("</SEC-HEADER>")
    header = raw[: end + len("</SEC-HEADER>")] if end != -1 else raw[:8000]
    cache_file.write_text(header)
    return header


# The full submission body can be enormous when exhibits are inlined (observed:
# an 8 MB filing whose text held 24k percent-tokens). We only need the cover-page
# region, so cap the download and the on-disk cache well below that.
_MAX_BODY_BYTES = 600_000


def fetch_submission_body(
    subject_cik: str,
    accession: str,
    cache_dir: Path,
    refresh: bool = False,
    max_bytes: int = _MAX_BODY_BYTES,
) -> str | None:
    """Fetch (cached) the full submission text for one accession, size-capped.

    Unlike :func:`fetch_submission_header`, this keeps the body — the Schedule
    13D cover page (CUSIP table, rows 11/13: aggregate amount and percent of
    class) lives here, not in the SGML header. The download is truncated to
    ``max_bytes`` because inlined exhibits can balloon a filing to multiple MB
    while every field we need sits near the top; the cover page is always well
    within the cap. Cached per accession (``.body``).
    """
    cache_file = cache_dir / f"{accession.replace('-', '')}.body"
    if cache_file.exists() and not refresh:
        return cache_file.read_text()
    url = _HEADER_URL.format(
        cik_int=int(subject_cik), acc_nodash=accession.replace("-", ""), acc=accession
    )
    try:
        raw = _throttled_get(url)[:max_bytes].decode("utf-8", "ignore")
    except (OSError, urllib.error.URLError) as exc:
        logger.debug(f"body fetch failed for {accession}: {exc}")
        return None
    cache_file.write_text(raw)
    return raw
