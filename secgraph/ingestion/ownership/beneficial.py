"""
Beneficial-owner loader — SEC Schedule 13D/13G (>5% stakes; Phase 2a).

No bulk dataset exists for 13D/13G, so this crawls EDGAR per in-universe subject
company (:mod:`.edgar_client`) and parses only the SGML submission *header*. The
header cleanly separates the ``SUBJECT COMPANY`` (the issuer, always CIK-keyed)
from the ``FILED BY`` party (the beneficial owner). The filer usually carries a
header CIK too; when it does not, we fall back to a name-slug ``owner_key`` and
mark ``resolved=false`` so resolution rate is auditable.

Produces ``BeneficialOwner {owner_key}`` nodes + ``BENEFICIAL_OWNER_OF`` edges,
keyed by (owner, company). Only the subject issuer is required to be in-universe.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from secgraph.core.config.constants import BATCH_SIZE_LARGE
from secgraph.ingestion.ownership.bulk_datasets import (
    get_ownership_data_dir,
    normalize_cik,
)
from secgraph.ingestion.ownership.edgar_client import (
    SubmissionsFetchError,
    fetch_13dg_accessions,
    fetch_submission_header,
)
from secgraph.neo4j.utils import clean_properties_batch

logger = logging.getLogger(__name__)

# Fraction of subjects that may fail to fetch before the crawl is treated as systemically
# blocked rather than unlucky. Isolated blips over ~65k requests are normal; a fifth of the
# universe failing means something is wrong (usually SEC 403-ing a placeholder User-Agent),
# and continuing would write a plausible-looking but silently incomplete graph.
_MAX_SUBJECT_FAILURE_PCT = 20.0

# The header is organized in blocks: "SUBJECT COMPANY:" then "FILED BY:".
# Within each block, CENTRAL INDEX KEY / COMPANY CONFORMED NAME are indented.
_CIK_RE = re.compile(r"CENTRAL INDEX KEY:\s*(\d+)")
_NAME_RE = re.compile(r"COMPANY CONFORMED NAME:\s*(.+)")
_SLUG_RE = re.compile(r"[^a-z0-9]+")

_MERGE_QUERY = """
    UNWIND $batch AS row
    MATCH (c:Company {cik: row.company_cik})
    MERGE (b:BeneficialOwner {owner_key: row.owner_key})
      ON CREATE SET b.name = row.owner_name, b.loaded_at = datetime()
    SET b.name = coalesce(b.name, row.owner_name),
        b.cik = row.owner_cik,
        b.resolved = row.resolved
    MERGE (b)-[r:BENEFICIAL_OWNER_OF {filing_type: row.filing_type}]->(c)
    SET r.source = 'sec_13dg',
        r.filing_date = date(row.filing_date),
        r.accession_number = row.accession_number,
        r.loaded_at = datetime()
    RETURN count(r) AS n
"""


def name_slug(name: str) -> str:
    """Slugify a filer name for use as a fallback owner_key.

    Examples:
        >>> name_slug("RC Ventures LLC")
        'rc-ventures-llc'
    """
    return _SLUG_RE.sub("-", name.strip().lower()).strip("-")


def parse_header(header: str) -> dict[str, str] | None:
    """Extract (subject_cik, filer_cik, filer_name, form_type, filing_date).

    Splits the header at ``FILED BY`` so the subject CIK is read from the block
    above and the filer CIK/name from the block below. Returns None if either
    the subject CIK or filer name is missing.
    """
    if "FILED BY" not in header:
        return None
    subject_part, _, filer_part = header.partition("FILED BY")

    subject_cik_match = _CIK_RE.search(subject_part)
    filer_cik_match = _CIK_RE.search(filer_part)
    filer_name_match = _NAME_RE.search(filer_part)

    form_match = re.search(r"CONFORMED SUBMISSION TYPE:[ \t]*(.+)", header)
    date_match = re.search(r"FILED AS OF DATE:\s*(\d{8})", header)

    if subject_cik_match is None or filer_name_match is None:
        return None

    filing_date = None
    if date_match:
        d = date_match.group(1)
        filing_date = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"

    return {
        "subject_cik": normalize_cik(subject_cik_match.group(1)),
        "filer_cik": normalize_cik(filer_cik_match.group(1)) if filer_cik_match else None,
        "filer_name": filer_name_match.group(1).strip(),
        "form_type": form_match.group(1).strip() if form_match else "SC 13D/G",
        "filing_date": filing_date,
    }


def _classify_filing(form_type: str) -> str:
    """Normalize a submission type to '13D' or '13G' (amendments collapse)."""
    return "13D" if "13D" in form_type.upper() else "13G"


def filings_as_of(filings: list[dict], as_of: str | None) -> list[dict]:
    """Drop filings dated after ``as_of`` (a ``YYYY-MM-DD`` string).

    Applied *before* the per-subject cap, not after: filtering a list that has already been
    truncated to the 40 most recent would silently return fewer than 40 filings for any active
    subject, so a pinned rebuild would see less history than an unpinned one.

    A filing with a missing or malformed date is kept — dropping it would lose a real edge over
    a formatting quirk, and ``parse_header`` supplies the authoritative date downstream.
    """
    if not as_of:
        return filings
    kept = []
    for filing in filings:
        raw = (filing.get("date") or "").strip()
        try:
            # Parse rather than compare strings: "not-a-date" is also 10 characters, so a
            # length check would treat it as a real date and silently drop the filing.
            parsed = date.fromisoformat(raw)
        except ValueError:
            kept.append(filing)
            continue
        if parsed.isoformat() <= as_of:
            kept.append(filing)
    return kept


def _crawl_subject(
    subject_cik: str,
    cache_dir: Path,
    refresh_index: bool,
    refresh_headers: bool,
    max_filings_per_subject: int,
    as_of: str | None = None,
) -> list[dict]:
    """Crawl one subject's 13D/G headers → its BENEFICIAL_OWNER_OF edge rows.

    Pure per-subject unit of work (no shared mutable state) so it is safe to run
    across a thread pool. All network calls go through the shared SEC rate
    limiter, which enforces the 10 req/s fair-access ceiling globally.

    ``refresh_index`` re-fetches the per-subject submissions index (needed after
    a filter change, e.g. picking up the new ``SCHEDULE 13D/G`` form codes);
    ``refresh_headers`` re-fetches the SGML headers, which are immutable by
    accession and so almost never need refreshing — newly-discovered accessions
    are fetched regardless. ``as_of`` pins the crawl to filings on or before a date.
    """
    filings = fetch_13dg_accessions(subject_cik, cache_dir, refresh=refresh_index)
    filings = filings_as_of(filings, as_of)
    rows: list[dict] = []
    for filing in filings[:max_filings_per_subject]:
        header = fetch_submission_header(
            subject_cik, filing["accession"], cache_dir, refresh=refresh_headers
        )
        if header is None:
            continue
        parsed = parse_header(header)
        if parsed is None or parsed["subject_cik"] is None:
            continue
        resolved = parsed["filer_cik"] is not None
        owner_key = parsed["filer_cik"] if resolved else name_slug(parsed["filer_name"])
        if not owner_key:
            continue
        rows.append(
            {
                "owner_key": owner_key,
                "owner_cik": parsed["filer_cik"],
                "owner_name": parsed["filer_name"],
                "resolved": resolved,
                "company_cik": parsed["subject_cik"],
                "filing_type": _classify_filing(parsed["form_type"]),
                "filing_date": parsed["filing_date"] or filing["date"],
                "accession_number": filing["accession"],
            }
        )
    return rows


def _crawl_workers() -> int:
    """Concurrent crawl workers (env override, default 10).

    The crawl is latency-bound: each EDGAR round-trip is ~380 ms, so a serial
    loop only reaches ~2.6 req/s. Running subjects concurrently keeps the pipe
    full; the shared rate limiter still caps actual throughput at 10 req/s, so
    more workers cannot breach SEC fair-access.
    """
    import os

    try:
        return max(1, int(os.environ.get("OWNERSHIP_CRAWL_WORKERS", "10")))
    except ValueError:
        return 10


def build_edge_rows(
    subject_ciks: list[str],
    refresh: bool = False,
    refresh_headers: bool = False,
    max_filings_per_subject: int = 40,
    as_of: str | None = None,
    on_batch: Callable[[list[dict]], None] | None = None,
    checkpoint_every: int = 250,
    log: logging.Logger | None = None,
) -> list[dict]:
    """Crawl 13D/G headers for each subject → BENEFICIAL_OWNER_OF edge rows.

    Subjects are crawled concurrently across a thread pool. The shared SEC rate
    limiter serializes request *release* to 10 req/s while the ~380 ms HTTP
    round-trips overlap, so throughput saturates the fair-access ceiling without
    exceeding it. Cached headers/indexes make the crawl resumable — a re-run
    reads them from disk instead of re-fetching.

    ``refresh`` re-fetches the per-subject submissions *index* (cheap, ~8k
    requests; needed after a form-code/filter change). ``refresh_headers`` also
    re-fetches every cached SGML header — rarely needed, since headers are
    immutable by accession; newly-discovered accessions are always fetched.

    ``on_batch`` is called with accumulated rows every ``checkpoint_every`` subjects. Without
    it every row is held until the crawl finishes, so a crash at 95% loses the DB write for
    the whole run even though the fetch cache survives — which is exactly what happened on a
    home-internet drop at subject 3,500 of 8,000. Rows handed to ``on_batch`` are not
    returned again; the MERGE is idempotent, so a partial run is safe to resume.
    """
    log = log or logger
    cache_dir = get_ownership_data_dir("form13dg")
    workers = _crawl_workers()
    total = len(subject_ciks)
    log.info(f"  crawling {total:,} subjects with {workers} concurrent workers...")
    if on_batch is not None:
        log.info(f"  checkpointing to the database every {checkpoint_every:,} subjects")

    rows: list[dict] = []
    pending: list[dict] = []
    flushed = 0
    done = 0
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _crawl_subject,
                cik,
                cache_dir,
                refresh,
                refresh_headers,
                max_filings_per_subject,
                as_of,
            ): cik
            for cik in subject_ciks
        }
        for future in as_completed(futures):
            try:
                got = future.result()
                pending.extend(got) if on_batch is not None else rows.extend(got)
            except SubmissionsFetchError as exc:
                # Tolerate isolated failures (one unreachable subject must not lose a
                # multi-hour crawl) but never silently: they are counted, and a systemic
                # rate — e.g. SEC 403-ing every request over a placeholder User-Agent — is
                # fatal below. Nothing was cached, so a re-run retries these subjects.
                failed.append(futures[future])
                log.debug(f"  subject fetch failed: {exc}")
            except Exception as exc:  # noqa: BLE001 - see below
                # Catch-all on purpose. Anything escaping a worker propagates through
                # .result() and kills the entire crawl; an http.client.IncompleteRead from a
                # dropped connection did exactly that. Losing one subject is recoverable —
                # losing hours of throttled fetching is not.
                failed.append(futures[future])
                log.debug(f"  subject crawl error ({type(exc).__name__}): {exc}")
            done += 1
            if on_batch is not None and done % checkpoint_every == 0 and pending:
                on_batch(pending)
                flushed += len(pending)
                pending = []
            if done % 250 == 0:
                seen = flushed + len(pending) if on_batch is not None else len(rows)
                log.info(f"  crawled {done:,}/{total:,} subjects, {seen:,} edges so far")

    if on_batch is not None and pending:
        on_batch(pending)
        flushed += len(pending)

    if failed:
        pct = 100.0 * len(failed) / total if total else 0.0
        log.warning(f"  ⚠ {len(failed):,}/{total:,} subjects ({pct:.1f}%) could not be fetched")
        # A handful of blips is normal over ~65k requests; a large fraction means the crawl is
        # systematically blocked, and continuing would write a plausible-looking partial graph.
        if pct >= _MAX_SUBJECT_FAILURE_PCT:
            raise SubmissionsFetchError(
                f"{len(failed):,}/{total:,} subjects ({pct:.1f}%) failed to fetch — aborting "
                f"rather than building a silently-incomplete graph. Most likely cause: SEC is "
                f"rejecting the requests. Set a real SEC_USER_AGENT "
                f"('Name email@domain') and re-run; nothing was cached, so the retry is clean."
            )
    return rows


def load_beneficial_owners(
    driver,
    subject_ciks: list[str] | None = None,
    database: str | None = None,
    refresh: bool = False,
    refresh_headers: bool = False,
    max_filings_per_subject: int = 40,
    batch_size: int = BATCH_SIZE_LARGE,
    as_of: str | None = None,
    execute: bool = False,
    logger_instance: logging.Logger | None = None,
) -> dict:
    """Load BeneficialOwner nodes + BENEFICIAL_OWNER_OF edges from 13D/G headers.

    Args:
        driver: Neo4j driver
        subject_ciks: In-scope issuer CIKs to crawl; if None, read from Company nodes
        database: Target Neo4j database
        refresh: If True, re-fetch the per-subject submissions index (cheap;
            needed after a form-code/filter change)
        refresh_headers: If True, also re-fetch cached SGML headers (rarely
            needed — headers are immutable by accession)
        max_filings_per_subject: Cap on 13D/G filings crawled per subject
        batch_size: Edge MERGE batch size
        execute: If False, only report the plan
        logger_instance: Optional logger

    Returns:
        Dict of statistics including filer resolution rate.
    """
    log = logger_instance or logger

    if subject_ciks is None:
        with driver.session(database=database) as session:
            subject_ciks = [r["cik"] for r in session.run("MATCH (c:Company) RETURN c.cik AS cik")]
    log.info(f"Crawling 13D/13G headers for {len(subject_ciks):,} subject companies...")

    # Under --execute, write to Neo4j as the crawl proceeds instead of holding every row until
    # the end. A multi-hour crawl on a home connection *will* be interrupted; the fetch cache
    # made a re-run cheap, but the DB write was all-or-nothing, so a drop at 95% wrote nothing.
    stats = {"owners": 0, "edges": 0, "checkpoints": 0}
    seen_owners: set[str] = set()
    resolved_count = 0
    total_rows = 0

    def flush(batch_rows: list[dict]) -> None:
        nonlocal resolved_count, total_rows
        seen_owners.update(r["owner_key"] for r in batch_rows)
        resolved_count += sum(1 for r in batch_rows if r["resolved"])
        total_rows += len(batch_rows)
        with driver.session(database=database) as session:
            for start in range(0, len(batch_rows), batch_size):
                chunk = clean_properties_batch(batch_rows[start : start + batch_size])
                stats["edges"] += session.run(_MERGE_QUERY, batch=chunk).single()["n"]
        stats["checkpoints"] += 1
        log.info(f"    ✓ checkpoint {stats['checkpoints']}: {stats['edges']:,} edges written")

    rows = build_edge_rows(
        subject_ciks,
        refresh=refresh,
        refresh_headers=refresh_headers,
        max_filings_per_subject=max_filings_per_subject,
        as_of=as_of,
        on_batch=flush if execute else None,
        log=log,
    )

    if execute:
        if subject_ciks and total_rows == 0:
            raise SubmissionsFetchError(
                f"crawled {len(subject_ciks):,} subjects and found 0 Schedule 13D/13G edges. "
                f"This is never a valid outcome for a real universe. Check that SEC_USER_AGENT "
                f"is a real contact string, and that data/sec_ownership/form13dg/ does not hold "
                f"empty cached indexes from an earlier blocked run (delete it to re-crawl)."
            )
        resolution_rate = round(resolved_count / total_rows * 100, 1) if total_rows else 0.0
        log.info(
            f"✓ Loaded {len(seen_owners):,} beneficial owners, {stats['edges']:,} edges "
            f"across {stats['checkpoints']} checkpoint(s), filer resolution {resolution_rate}%"
        )
        return {
            "owners": len(seen_owners),
            "edges": stats["edges"],
            "resolution_rate_pct": resolution_rate,
        }

    if subject_ciks and not rows:
        # Every 13D/G-driven demo surface (convergence, coalition, control chains) depends on
        # this layer. Zero edges across the whole universe is never a legitimate result, but it
        # used to exit 0 and let the build finish "successfully" over an empty graph.
        raise SubmissionsFetchError(
            f"crawled {len(subject_ciks):,} subjects and found 0 Schedule 13D/13G edges. "
            f"This is never a valid outcome for a real universe. Check that SEC_USER_AGENT is "
            f"a real contact string, and that data/sec_ownership/form13dg/ does not hold "
            f"empty cached indexes from an earlier blocked run (delete it to force a re-crawl)."
        )

    distinct_owners = len({r["owner_key"] for r in rows})
    resolved = sum(1 for r in rows if r["resolved"])
    resolution_rate = round(resolved / len(rows) * 100, 1) if rows else 0.0
    log.info(
        f"  {len(rows):,} edges, {distinct_owners:,} owners, filer resolution {resolution_rate}%"
    )
    log.info("")
    log.info("DRY RUN — would MERGE BeneficialOwner nodes + BENEFICIAL_OWNER_OF edges:")
    log.info(f"  owners: {distinct_owners:,}, edges: {len(rows):,}")
    log.info("Run with --execute to load.")
    return {
        "dry_run": True,
        "owners": distinct_owners,
        "edges": len(rows),
        "resolution_rate_pct": resolution_rate,
    }
