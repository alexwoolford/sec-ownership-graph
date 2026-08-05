"""
Control-vs-stake extraction for Schedule 13D edges.

A ``BENEFICIAL_OWNER_OF {filing_type:'13D'}`` edge as loaded by :mod:`.beneficial`
says only *"this filer took a >5% active-intent stake in this issuer"* — it carries
no ownership magnitude, so a 5.1% toehold and a 53% control block look identical in
the graph. That flattening is fatal for control-chain reasoning: a transitive chain
built on undifferentiated 13D edges conflates genuine control with mere large
positions.

This module recovers the missing scalar. For each 13D edge it fetches the filing
body (the SGML *header* the loader keeps has only the two CIKs), reads the Schedule
13D cover page — CUSIP table rows 11 (aggregate amount beneficially owned) and 13
(percent of class) — with a light LLM extractor, and writes ``percent_of_class`` /
``sole_voting`` / ``shared_voting`` back onto the edge, plus a ``control_class``
label (``control`` / ``stake`` / ``unknown``).

Two disciplines, both load-bearing:

- **Full coverage, honest coverage.** Every 13D edge is attempted; nothing is
  hand-picked. What cannot be extracted is labelled ``unknown`` and *counted*, never
  silently dropped — the coverage rate is a reported number.
- **Verify against source, fail-closed.** An extracted percent is written only if it
  actually appears in the filing text (:func:`verify_percent`). A number the model
  produces but that is not in the document is discarded to ``unknown`` — we never
  write a figure we cannot cite. This is the same fail-closed rule that governs the
  GraphRAG citation contract.

Deterministic regex alone recovers only ~10% of filings (the percent sits in a
cover-page *table* cell, far from its "PERCENT OF CLASS" label, and HTML→text
flattening scatters the two), so the extractor is regex-first with an LLM fallback
for the messy majority. The LLM does *attribute* extraction (one number from a known
document), never *entity* resolution — the entities are already CIK-keyed by the
loader.

READ side-effects: writes only new edge properties via MERGE-idempotent ``SET``;
intended for the ``secgraph`` database. Read-only everywhere else.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from secgraph.core.config.constants import BATCH_SIZE_LARGE
from secgraph.ingestion.ownership.bulk_datasets import get_ownership_data_dir
from secgraph.ingestion.ownership.edgar_client import fetch_submission_body
from secgraph.neo4j.utils import clean_properties_batch

logger = logging.getLogger(__name__)

# A reporting person at or above this percent of the class is treated as exercising
# control (majority / near-majority of the voting class). Below it, the 13D is a
# large active-intent stake but not, on ownership alone, control. This is the
# control-chain gate; it is deliberately a bright line on the *reported* percent, not
# an inference about super-voting share classes (which we do not have per-class data
# for and so do not attempt to model).
CONTROL_THRESHOLD_PCT = 50.0

# The cover page sits at the top of the filing; only this leading slice is sent to the
# extractor. Keeps LLM cost bounded and excludes inlined exhibits (the pathological
# multi-MB bodies) that would otherwise dominate the token count.
_COVER_WINDOW_CHARS = 6000

# A percent-of-class above this is impossible (a rounding/parse artifact, e.g. reading
# a share count as a percent). Reject rather than write a nonsense figure.
_MAX_PLAUSIBLE_PCT = 100.0

_TAG_RE = re.compile(r"<[^>]+>")
_ENTITY_RE = re.compile(r"&[a-z#0-9]+;", re.I)
_WS_RE = re.compile(r"\s+")
_PCT_TOKEN_RE = re.compile(r"([0-9]{1,3}(?:\.[0-9]+)?)\s*%")

# Deterministic first pass: "PERCENT OF CLASS ... NN.N%" when the label and value
# survive flattening adjacent (the ~10% easy case). Kept because it is free.
_PCT_OF_CLASS_RE = re.compile(r"PERCENT\s+OF\s+CLASS[^%]{0,80}?([0-9]{1,3}(?:\.[0-9]+)?)\s*%", re.I)
_PROSE_PCT_RE = re.compile(
    r"representing\s+(?:approximately\s+)?([0-9]{1,3}(?:\.[0-9]+)?)\s*%", re.I
)

# The GROUP AGGREGATE sentence — the authoritative figure when several affiliated vehicles
# file together. A filing group's cover pages carry one row-13 percent *per vehicle*, none of
# which is the group's total; the aggregate appears only in prose like:
#
#   "the aggregate number of Securities to which this Schedule 13D relates is 1,502,130
#    shares, representing 5.01% of the 29,978,942 shares outstanding"
#
# Reading a per-vehicle row instead produced 4.0% on a Schedule 13D — impossible, since 13D
# requires >5% — on the demo's headline output. Preferred over any single row-13 value.
_AGGREGATE_PCT_RE = re.compile(
    # "aggregate number|amount of <any words>, representing [approximately] N%".
    # Permissive between the two anchors — issuers write "Securities", "Common Stock",
    # "shares of Common Stock, par value $0.01" — but it must not reach across a sentence
    # boundary and pair the aggregate clause with an unrelated later percent.
    #
    # The boundary is "period followed by whitespace", NOT any period: filings are full of
    # decimals ("$0.01", "1,502,130.5"), and `[^.]` broke on every one of them.
    r"aggregate\s+(?:number|amount)\s+of(?:(?!\.\s).){0,220}?"
    r"representing\s+(?:approximately\s+)?([0-9]{1,3}(?:\.[0-9]+)?)\s*%",
    re.I,
)

# The GROUP TOTALS ROW — the other way filings state an aggregate. Instead of prose, some
# filings print a per-vehicle table ending in a "Totals" line:
#
#   Name Owned Beneficially Approximate % Class
#   ARL* 6,721,999 82.85 %   EQK* 5,521,999 68.06 %   TCI AcqSub 1,200,000 14.79 %
#   Totals 6,721,999 82.85 %
#
# The extractor read 14.79% here (the last subsidiary row) and silently dropped a verified
# 82.85% control link, deleting the deepest control chain in the graph. Caught by re-checking
# the chain count after the sub-5% fix, not by the sub-5% rule — 14.79% is above 5%, so the
# threshold test cannot see this class of error at all.
# `\btotals?\b` alone was too loose: cover-page row 12 reads "Check if the Aggregate Amount in
# Row (11) Excludes Certain Shares", and "Totals" also appears in unrelated tables, so the
# pattern skated forward to whatever percent came next. Require the number within ~40 chars and
# forbid an intervening digit-comma run (a share count), which keeps it on a real totals line.
_TOTALS_PCT_RE = re.compile(r"\btotals?\b[^%\n]{0,40}?([0-9]{1,3}(?:\.[0-9]+)?)\s*%", re.I)

# Minimum percent-of-class that can appear on an *original* Schedule 13D. Section 13(d)
# reporting is triggered by crossing 5%, so a lower figure on an original filing is a parse
# error, not a fact. Amendments are exempt: a 13D/A legitimately reports an exit below 5%.
_MIN_ORIGINAL_13D_PCT = 5.0


# --------------------------------------------------------------------------- #
# Pure helpers (no DB, no network) — the unit-test surface.
# --------------------------------------------------------------------------- #
def strip_markup(raw: str) -> str:
    """Flatten an SEC filing body (SGML/HTML) to whitespace-normalized text."""
    text = _TAG_RE.sub(" ", raw or "")
    text = _ENTITY_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def cover_window(text: str, chars: int = _COVER_WINDOW_CHARS) -> str:
    """The cover-page slice to hand the extractor.

    Anchors on the first ``PERCENT OF CLASS`` / ``AGGREGATE AMOUNT`` signal (the
    cover-page table) and returns a bounded window around it; falls back to the
    document head when neither signal is present. Bounding the slice keeps LLM cost
    flat regardless of how large inlined exhibits make the full body.
    """
    up = text.upper()
    idx = up.find("PERCENT OF CLASS")
    if idx < 0:
        idx = up.find("AGGREGATE AMOUNT")
    if idx < 0:
        return text[:chars]
    start = max(0, idx - chars // 2)
    return text[start : idx + chars // 2]


def parse_aggregate_percent(text: str) -> float | None:
    """The group-aggregate percent, when the filing states one.

    Authoritative for a filing group: several affiliated vehicles each carry their own row-13
    percent, and none of them is the group total. Two forms are recognised — the prose
    "aggregate ... representing N%" sentence, and a per-vehicle table's "Totals" row. Returns
    None when neither is present (the common single-filer case, where row 13 *is* the answer).
    """
    for pattern in (_AGGREGATE_PCT_RE, _TOTALS_PCT_RE):
        m = pattern.search(text)
        if m:
            pct = _coerce_pct(m.group(1))
            if pct is not None:
                return pct
    return None


def parse_percent_deterministic(text: str) -> tuple[float | None, str | None]:
    """Recover percent-of-class by regex. Returns ``(percent, source)``.

    Tries the group aggregate first — a per-vehicle row-13 value understates a filing group
    and produced sub-5% figures on Schedule 13Ds. ``source`` records which rule matched, so a
    served figure is auditable rather than just a number.

    Returns ``(None, None)`` when flattening scattered the label from the value (the LLM
    fallback's job). Never returns an implausible (>100%) value.
    """
    for pattern, source in (
        (_AGGREGATE_PCT_RE, "aggregate_prose"),
        (_PCT_OF_CLASS_RE, "row13"),
        (_PROSE_PCT_RE, "prose"),
    ):
        m = pattern.search(text)
        if m:
            val = _coerce_pct(m.group(1))
            if val is not None:
                return val, source
    return None, None


def _coerce_pct(value: Any) -> float | None:
    """Parse a percent to a plausible float in (0, 100], else None."""
    try:
        pct = float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None
    if pct <= 0.0 or pct > _MAX_PLAUSIBLE_PCT:
        return None
    return pct


def verify_percent(pct: float | None, text: str) -> bool:
    """True if ``pct`` actually appears as a percent token in the filing text.

    Fail-closed: a figure the model asserts but that is not present in the document
    is not trustworthy, so it is rejected. Matches on the number appearing adjacent
    to a ``%`` sign, tolerant of the model normalizing ``30`` vs ``30.0`` vs
    ``30.00`` (SEC cover pages write percents inconsistently).

    **This is a presence check, not an identity check** — it cannot tell the group aggregate
    from one affiliated vehicle's row, because both are real numbers in the document. That is
    how 899 sub-5% figures passed on Schedule 13Ds. :func:`resolve_percent` adds the identity
    rule; this stays as the necessary-but-insufficient first gate.
    """
    if pct is None:
        return False
    present = {_coerce_pct(tok) for tok in _PCT_TOKEN_RE.findall(text)}
    present.discard(None)
    return any(p is not None and abs(p - pct) < 0.05 for p in present)


def resolve_percent(
    pct: float | None,
    text: str,
    *,
    filing_type: str = "13D",
    is_amendment: bool = False,
    source: str | None = None,
) -> tuple[float | None, str | None]:
    """Decide the percent to trust, and record why. Returns ``(percent, source)``.

    Layers an *identity* rule on top of :func:`verify_percent`'s presence check:

    1. The figure must appear in the document (presence), else reject.
    2. On an **original** Schedule 13D, a percent below 5% is impossible — Section 13(d)
       reporting is triggered by crossing 5%. Such a figure is a per-vehicle row mistaken for
       the group total, so prefer the stated group aggregate when the filing has one.
    3. If no aggregate is available, reject rather than publish an impossible number. A missing
       percent is honest; ``4.0%`` on a 13D is not, and it is the first thing a filings-literate
       reader challenges.

    Amendments are exempt from rule 2: a 13D/A legitimately reports falling below 5% on exit.
    13G is exempt too — its thresholds differ by filer class (5% generally, but institutional
    and exempt filers report on other triggers).
    """
    if pct is None or not verify_percent(pct, text):
        return None, None

    # A stated group aggregate outranks any per-vehicle row, regardless of magnitude. The 5%
    # threshold test alone was not enough: one filing's subsidiary row read 14.79% against an
    # 82.85% group total — above 5%, so invisible to the threshold, yet it silently deleted a
    # verified control link. Only *raise* to the aggregate; a candidate already at or above it
    # is left alone, so a correctly-read total is never revised downward.
    aggregate = parse_aggregate_percent(text)
    final, final_source = (
        (aggregate, "aggregate_total") if aggregate is not None and aggregate > pct else (pct, None)
    )

    # The threshold check runs LAST, on whatever value won — including a substituted aggregate.
    # Checking it before the substitution let three impossible figures through: the aggregate
    # regex matched a cover-page row label rather than a real totals line, raised the value, and
    # then bypassed the gate entirely. An aggregate that is still impossible is still wrong.
    original_13d = filing_type.upper().startswith("13D") and not is_amendment
    if original_13d and final < _MIN_ORIGINAL_13D_PCT:
        return None, "rejected_below_13d_threshold"

    return final, final_source or source or "llm"


def classify_control(pct: float | None, threshold: float = CONTROL_THRESHOLD_PCT) -> str:
    """Label an edge ``control`` / ``stake`` / ``unknown`` from its percent of class.

    ``unknown`` when no percent was verified — an honest third state, never coerced
    to ``stake``, so coverage gaps stay visible in the graph rather than masquerading
    as small positions.
    """
    if pct is None:
        return "unknown"
    return "control" if pct >= threshold else "stake"


def build_edge_result(
    accession: str,
    extracted: dict[str, Any] | None,
    text: str,
    threshold: float = CONTROL_THRESHOLD_PCT,
    *,
    filing_type: str = "13D",
    is_amendment: bool = False,
) -> dict[str, Any]:
    """Assemble the verified edge-property row from a raw extraction + source text.

    Applies :func:`resolve_percent` — presence *and* identity — coerces voting counts, and
    derives the control label. This is the pure core of the pipeline: given what the model
    returned and the document it read, decide what (if anything) is safe to write. A percent
    that fails either gate collapses the row to ``unknown``.

    ``pct_source`` records which rule produced the figure, so a served number is auditable:
    ``aggregate_prose`` (the group total), ``row13``, ``prose``, ``llm``, or
    ``rejected_below_13d_threshold`` when an impossible sub-5% original was thrown out.
    """
    extracted = extracted or {}
    pct = _coerce_pct(extracted.get("percent_of_class"))
    final_pct, source = resolve_percent(
        pct, text, filing_type=filing_type, is_amendment=is_amendment
    )
    return {
        "accession_number": accession,
        "percent_of_class": final_pct,
        "sole_voting": _coerce_int(extracted.get("sole_voting")),
        "shared_voting": _coerce_int(extracted.get("shared_voting")),
        "control_class": classify_control(final_pct, threshold),
        "pct_verified": final_pct is not None,
        "pct_source": source,
    }


def _coerce_int(value: Any) -> int | None:
    """Parse a share count to a non-negative int, tolerating commas; else None."""
    if value is None:
        return None
    try:
        n = int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def coverage_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Honest coverage stats over a batch of edge results.

    Reports how many edges got a verified percent, and the control/stake/unknown
    split — the numbers we state plainly rather than a hand-picked highlight.
    """
    total = len(results)
    verified = sum(1 for r in results if r.get("pct_verified"))
    by_class: dict[str, int] = {}
    for r in results:
        by_class[r["control_class"]] = by_class.get(r["control_class"], 0) + 1
    return {
        "total": total,
        "verified": verified,
        "coverage_pct": round(verified / total * 100, 1) if total else 0.0,
        "control": by_class.get("control", 0),
        "stake": by_class.get("stake", 0),
        "unknown": by_class.get("unknown", 0),
    }


# --------------------------------------------------------------------------- #
# LLM extraction (attribute extraction, not entity resolution).
# --------------------------------------------------------------------------- #
_SYSTEM_PROMPT = (
    "You read the cover page of an SEC Schedule 13D/13G filing and extract the "
    "reporting person's beneficial-ownership figures. The cover page has a numbered "
    "table; row 11 is AGGREGATE AMOUNT BENEFICIALLY OWNED and row 13 is PERCENT OF "
    "CLASS REPRESENTED BY AMOUNT IN ROW 11. Return ONLY compact JSON with keys: "
    '"percent_of_class" (number, the row-13 percent, e.g. 53.7; null if absent), '
    '"sole_voting" (integer shares, row 7; null if absent), '
    '"shared_voting" (integer shares, row 9; null if absent). '
    "CRITICAL — filing groups: several affiliated vehicles often file together, each with its "
    "OWN row-13 percent, none of which is the group total. If the text gives both a per-entity "
    "breakdown and an aggregate for the group, return the AGGREGATE. Prefer a sentence of the "
    'form "the aggregate number of Securities ... representing N% of the M shares outstanding" '
    "over any single vehicle's row-13 value. Returning one vehicle's percent understates the "
    "filing and, on a Schedule 13D, produces a figure below the 5% reporting threshold — which "
    "is impossible and is treated as an error. "
    "Do not guess — use null when a value is not present in the text."
)


def extract_with_llm(text_window: str, llm_client: Any, model: str) -> dict[str, Any] | None:
    """Ask the mini model for the cover-page figures. Returns parsed JSON or None.

    Robust to the model wrapping JSON in prose or code fences. Any client/parse
    failure returns None (the caller records the edge as ``unknown``), so one bad
    filing never aborts the run.

    A **missing** client is different from a failing one, and must not be absorbed by that
    catch-all: with ``llm_client=None`` every attribute access raises, so an entire run would
    label itself ``unknown`` and exit 0 — a green build over an unclassified control layer.
    That is the same failure shape as the placeholder-User-Agent bug. Fail fast instead.
    """
    if llm_client is None:
        raise ValueError(
            "extract_with_llm called without an LLM client. The caller must resolve a client "
            "before extraction, or skip extraction entirely when there is nothing to extract."
        )
    try:
        resp = llm_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text_window},
            ],
            temperature=0,
            max_tokens=120,
        )
        content = resp.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001 - any client error → unknown, never abort
        logger.debug(f"LLM extraction failed: {exc}")
        return None
    return _parse_json_object(content)


def _parse_json_object(content: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a model reply, tolerating fences/prose."""
    content = content.strip()
    if not content:
        return None
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(content[start : end + 1])
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def extract_edge(
    row: dict[str, Any],
    cache_dir,
    llm_client: Any,
    model: str,
    refresh: bool = False,
    threshold: float = CONTROL_THRESHOLD_PCT,
) -> dict[str, Any]:
    """Full per-edge extraction: fetch body → regex, else LLM → verify → result row.

    Pure per-edge unit of work (no shared mutable state) so it is safe across a
    thread pool. Fetches the (cached, size-capped) body, tries the free deterministic
    parse first, falls back to the LLM only when needed, and always returns a result
    row — an ``unknown`` row when the body is unreachable or nothing verifies, so
    every input edge is accounted for.
    """
    accession = row["accession_number"]
    # Amendment status gates the sub-5% rule: an original 13D cannot report below 5%, but a
    # 13D/A legitimately can (that is how an exit is disclosed). Read from the edge when the
    # loader recorded it; default to "original" so the stricter rule applies when unknown.
    is_amd = bool(row.get("is_amendment", False))
    kwargs = {"filing_type": row.get("filing_type", "13D"), "is_amendment": is_amd}

    raw = fetch_submission_body(row["subject_cik"], accession, cache_dir, refresh=refresh)
    if not raw:
        return build_edge_result(accession, None, "", threshold, **kwargs)
    text = strip_markup(raw)

    det, det_source = parse_percent_deterministic(text)
    if det is not None:
        result = build_edge_result(accession, {"percent_of_class": det}, text, threshold, **kwargs)
        # Keep the regex provenance when it survived resolution unchanged.
        if result["percent_of_class"] == det and det_source:
            result["pct_source"] = det_source
        return result

    window = cover_window(text)
    extracted = extract_with_llm(window, llm_client, model)
    return build_edge_result(accession, extracted, text, threshold, **kwargs)


# --------------------------------------------------------------------------- #
# DB-bound helpers.
# --------------------------------------------------------------------------- #
def _edges_needing_extraction(session, only_missing: bool) -> list[dict[str, Any]]:
    """13D edges to process: (subject_cik, accession). Skips done ones unless re-run.

    ``only_missing`` skips edges that already carry a ``control_class`` (idempotent
    resume after an interrupted run); False re-extracts all.
    """
    where_done = "" if not only_missing else "AND r.control_class IS NULL"
    return session.run(
        f"""
        MATCH (b:BeneficialOwner)-[r:BENEFICIAL_OWNER_OF {{filing_type: '13D'}}]->(c:Company)
        WHERE c.cik IS NOT NULL AND r.accession_number IS NOT NULL {where_done}
        RETURN DISTINCT c.cik AS subject_cik, r.accession_number AS accession_number,
               // coalesce, not a bare read: edges loaded before is_amendment existed have no
               // such property, and false (treat as original) applies the stricter 5% rule.
               coalesce(r.filing_is_original, true) = false AS is_amendment,
               '13D' AS filing_type
        """
    ).data()


_WRITE_QUERY = """
    UNWIND $batch AS row
    MATCH (:BeneficialOwner)-[r:BENEFICIAL_OWNER_OF {filing_type: '13D'}]->(:Company)
    WHERE r.accession_number = row.accession_number
    SET r.percent_of_class = row.percent_of_class,
        r.sole_voting = row.sole_voting,
        r.shared_voting = row.shared_voting,
        r.control_class = row.control_class,
        r.pct_verified = row.pct_verified,
        r.pct_source = row.pct_source,
        r.control_extracted_at = datetime()
    RETURN count(r) AS n
"""


def _write_results(session, results: list[dict[str, Any]], batch_size: int) -> int:
    """MERGE-idempotent write of extracted figures onto the matching 13D edges."""
    written = 0
    for start in range(0, len(results), batch_size):
        batch = clean_properties_batch(results[start : start + batch_size])
        written += session.run(_WRITE_QUERY, batch=batch).single()["n"]
    return written


# --------------------------------------------------------------------------- #
# Orchestrator.
# --------------------------------------------------------------------------- #
def extract_control_edges(
    driver,
    llm_client: Any,
    model: str,
    database: str | None = None,
    only_missing: bool = True,
    refresh: bool = False,
    workers: int = 8,
    batch_size: int = BATCH_SIZE_LARGE,
    execute: bool = False,
    limit: int | None = None,
    logger_instance: logging.Logger | None = None,
    client_factory: Any = None,
) -> dict[str, Any]:
    """Extract & label control vs. stake for every 13D edge (full coverage).

    Args:
        driver: Neo4j driver.
        llm_client: OpenAI-style client (``.chat.completions.create``). May be None when
            ``client_factory`` is supplied — see below.
        model: Mini model id for attribute extraction (e.g. ``gpt-4o-mini``).
        database: Target database (``secgraph``).
        only_missing: Skip edges already labelled (resumable); False re-does all.
        refresh: Re-fetch filing bodies instead of using the disk cache.
        workers: Concurrent EDGAR-fetch threads (shared 10 req/s limiter caps rate).
        batch_size: Edge-write batch size.
        execute: If False, only report the plan (dry-run default).
        limit: Optional cap on edges (for a bounded trial run).
        logger_instance: Optional logger.
        client_factory: Zero-arg callable returning an LLM client, resolved **only if there
            is work to do**. This is what lets the build run this step unconditionally: with
            full CSV coverage the edge list is empty, the factory is never called, and no API
            key is required. Ignored when ``llm_client`` is already supplied.

    Returns:
        Coverage summary dict (total / verified / coverage_pct / control / stake /
        unknown), plus ``written`` when executed.
    """
    log = logger_instance or logger
    cache_dir = get_ownership_data_dir("form13dg")

    with driver.session(database=database) as session:
        edges = _edges_needing_extraction(session, only_missing)
    if limit is not None:
        edges = edges[:limit]
    total = len(edges)
    log.info(f"13D edges to extract: {total:,} (only_missing={only_missing}, model={model})")

    if not execute:
        log.info("")
        log.info("DRY RUN — would fetch each filing body, extract cover-page figures,")
        log.info(f"  verify against source, and label control/stake for {total:,} edges.")
        log.info("Run with --execute to extract and write.")
        return {"dry_run": True, "total": total}

    # Nothing to do — the committed CSV already covers every 13D edge. Return before touching
    # the LLM client so a fully-covered build needs no API key at all. This is the branch that
    # makes the step safe to run unconditionally.
    if total == 0:
        log.info("✓ Every 13D edge already carries control figures — nothing to extract.")
        return {"total": 0, "written": 0, "skipped_no_work": True}

    # Only now is a client genuinely required, so resolve it late and let the error surface
    # here (with the edge count in hand) rather than at import time.
    if llm_client is None and client_factory is not None:
        log.info(f"resolving LLM client for {total:,} uncovered edge(s)")
        llm_client = client_factory()
    if llm_client is None:
        raise ValueError(
            f"{total:,} 13D edge(s) have no control figures and no LLM client is available. "
            "Supply an OPENAI_API_KEY to classify them, or pass --skip-uncovered to accept an "
            "incomplete control layer."
        )

    results: list[dict[str, Any]] = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(extract_edge, row, cache_dir, llm_client, model, refresh): row
            for row in edges
        }
        for future in as_completed(futures):
            results.append(future.result())
            done += 1
            if done % 250 == 0:
                cov = coverage_summary(results)
                log.info(
                    f"  extracted {done:,}/{total:,} · verified {cov['verified']:,} "
                    f"({cov['coverage_pct']}%) · control {cov['control']:,}"
                )

    summary = coverage_summary(results)
    with driver.session(database=database) as session:
        summary["written"] = _write_results(session, results, batch_size)

    log.info(
        f"✓ Extracted {summary['total']:,} edges · verified {summary['verified']:,} "
        f"({summary['coverage_pct']}%) · control {summary['control']:,} · "
        f"stake {summary['stake']:,} · unknown {summary['unknown']:,}"
    )
    return summary
