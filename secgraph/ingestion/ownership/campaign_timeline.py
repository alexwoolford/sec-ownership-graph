"""
Activist campaign timing — the *sequenced* ownership question, not the static one.

**Why this pillar exists.** The coalition win answers "who operates together"; it does not answer
the question an event-driven desk actually asks: **who moved first, and who followed?** Schedule
13D carries a real filing date (1994→present, ~1,000 filings/yr recently) and it is the one
trustworthy time series in ``secgraph`` — so the ordering is available and was simply unused.
Across the graph, **1,441 issuers** have two non-custodial activists filing 13D within 180 days
of each other, which is a dense enough signal to screen on.

Two entry points, both read-only and both honouring the evidence-or-abstain contract used by
:class:`~.intelligence.OwnershipIntelligenceEngine`:

- :meth:`CampaignTimelineEngine.campaign_timeline` — every 13D/13G filing on one issuer in date
  order, each filer classified so the reader can tell a campaign from index money.
- :meth:`CampaignTimelineEngine.convergence_scan` — issuers where several *known activist
  franchises* filed 13D inside a rolling window: the "what is heating up" screen.

**The franchise gate is load-bearing, not a convenience.** Ungated "pile-on" detection is
dominated by things that are not activism at all: micro-cap founders and insiders crossing 5%,
and same-day filing-group artifacts where one manager files through seven affiliated entities
(e.g. seven Richmond Hill vehicles on RSVR, or a company's own executives on STEP). Those would
be embarrassing in front of a finance professional. Restricting to recognised activist franchises
is what makes the output demo-grade — at the cost of recall, which is the right trade here and is
reported explicitly in the result metadata rather than hidden.

Only ``BENEFICIAL_OWNER_OF.filing_date`` is treated as a time series. ``DIRECTOR_OF`` /
``OFFICER_OF`` are a keep-latest snapshot over the staged Form 3/4/5 window and 13F
``HOLDS`` has a 2024 coverage
step-up, so neither is used for sequencing here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, cast

from secgraph.ingestion.ownership.graph_native_proof import custodial_category

logger = logging.getLogger(__name__)

# Rolling window (days) within which two 13D filings count as a clustered arrival. One quarter
# either side of a filing: long enough to catch a deliberate follow-on, short enough that
# unrelated campaigns years apart are not called a convergence.
DEFAULT_WINDOW_DAYS = 180

# Minimum distinct activist franchises on one issuer to count as a convergence.
DEFAULT_MIN_ACTIVISTS = 2

# Recognised activist franchises. Substring-matched case-insensitively on the filer name, so
# short tokens must be distinctive enough not to collide with unrelated filers. This list is the
# precision gate described in the module docstring; extending it trades precision for recall.
ACTIVIST_FRANCHISES = (
    "ICAHN",
    "GAMCO",
    "GABELLI",
    "BULLDOG INVESTORS",
    "GOLDSTEIN PHILLIP",
    "KARPUS",
    "SABA CAPITAL",
    "CANNELL CAPITAL",
    "ELLIOTT ASSOCIATES",
    "ELLIOTT INVESTMENT",
    "STARBOARD VALUE",
    "THIRD POINT",
    "TRIAN",
    "VALUEACT",
    "JANA PARTNERS",
    "ENGINE CAPITAL",
    "ANCORA",
    "LEGION PARTNERS",
    "BARINGTON",
    "LAND & BUILDINGS",
    "COVE STREET",
    "RA CAPITAL",
    "ORBIMED",
    "MARCATO",
    "SACHEM HEAD",
    "PENTWATER",
    "HG VORA",
    "IRENIC",
    "POLITAN",
    "BLUE ORCA",
    "GLAZER CAPITAL",
    "SPRINGOWL",
    "VOSS CAPITAL",
)


@dataclass
class CampaignTimelineResult:
    """Structured, serializable result for one campaign-timing query.

    Mirrors :class:`~.intelligence.OwnershipIntelligenceResult` so both pillars serialize
    identically on the MCP surface. ``abstained`` True means the graph held no dated support.
    """

    anchor: str
    task_type: str
    abstained: bool
    result: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor": self.anchor,
            "task_type": self.task_type,
            "abstained": self.abstained,
            "result": self.result,
            "evidence": self.evidence,
            "metadata": self.metadata,
        }


# --------------------------------------------------------------------------- #
# Pure helpers — the unit-test surface (no DB, no network).
# --------------------------------------------------------------------------- #
def is_activist_franchise(name: str | None) -> bool:
    """True if the filer name matches a recognised activist franchise."""
    upper = (name or "").upper()
    return any(f in upper for f in ACTIVIST_FRANCHISES)


def classify_filer(name: str | None, filing_type: str | None) -> str:
    """Label a filer so a reader can tell a campaign from index money.

    Order matters: a recognised activist franchise wins even if its name happens to contain a
    bank-like token, because the franchise list is the more specific signal. Otherwise a
    custodian/passive classification applies, then 13D-without-a-known-name is treated as
    ``insider_or_other`` — most such filers are founders/affiliates crossing 5%, which is a real
    fact but not activism.
    """
    if is_activist_franchise(name):
        return "activist"
    category = custodial_category(name)
    if category == "broker":
        return "custodian"
    if category == "passive":
        return "passive_index"
    if (filing_type or "").upper() == "13D":
        return "insider_or_other"
    return "other_holder"


def order_filings(filings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort filings oldest-first by date; undated entries sink to the end.

    The sequence *is* the product here, so undated rows must not silently interleave and imply
    an ordering the data does not support.
    """
    dated = [f for f in filings if f.get("filing_date")]
    undated = [f for f in filings if not f.get("filing_date")]
    return sorted(dated, key=lambda f: str(f["filing_date"])) + undated


def summarize_sequence(ordered: list[dict[str, Any]]) -> dict[str, Any]:
    """Who moved first among activists, who followed, and the gap in days.

    ``days_after_first`` is the number a desk reads first: a follow-on three months behind the
    initial 13D is a very different situation from two filings on the same day (which usually
    means one filing group, not two independent actors).
    """
    activists = [f for f in ordered if f.get("filer_class") == "activist" and f.get("filing_date")]
    if not activists:
        return {"activist_count": 0, "first_mover": None, "followers": []}

    first = activists[0]
    followers = []
    for f in activists[1:]:
        followers.append(
            {
                "filer": f.get("filer"),
                "filing_date": f.get("filing_date"),
                "percent_of_class": f.get("percent_of_class"),
                "days_after_first": days_between(first.get("filing_date"), f.get("filing_date")),
            }
        )
    return {
        "activist_count": len(activists),
        "first_mover": {
            "filer": first.get("filer"),
            "filing_date": first.get("filing_date"),
            "percent_of_class": first.get("percent_of_class"),
        },
        "followers": followers,
    }


def days_between(start: str | None, end: str | None) -> int | None:
    """Whole days between two ISO date strings, or None if either is missing/unparseable."""
    if not start or not end:
        return None
    from datetime import date

    try:
        a = date.fromisoformat(str(start)[:10])
        b = date.fromisoformat(str(end)[:10])
    except ValueError:
        return None
    return (b - a).days


def distinct_franchises(filings: list[dict[str, Any]]) -> list[str]:
    """Distinct activist *franchises* among filings, collapsing affiliated entities.

    Deliberately collapses on the matched franchise token rather than the filer name, so
    "Bulldog Investors" and "Bulldog Investors, LLP" — or seven affiliated vehicles of one
    manager — count once. Without this, a single manager filing through multiple entities looks
    like a multi-activist convergence, which is the artifact this pillar must not surface.
    """
    found: list[str] = []
    for f in filings:
        upper = str(f.get("filer") or "").upper()
        for franchise in ACTIVIST_FRANCHISES:
            if franchise in upper and franchise not in found:
                found.append(franchise)
                break
    return found


# --------------------------------------------------------------------------- #
# The engine.
# --------------------------------------------------------------------------- #
class CampaignTimelineEngine:
    """Read-only activist campaign-timing engine over ``secgraph``. Never writes."""

    def __init__(self, driver, database: str | None = None):
        self.driver = driver
        self.database = database

    def _resolve_company(self, hint: str) -> dict[str, Any] | None:
        """Resolve ticker / CIK / name to ``{cik, name, ticker}``; hard keys preferred."""
        hint = str(hint or "").strip()
        if not hint:
            return None
        query = """
        MATCH (c:Company)
        WHERE toUpper(c.ticker) = toUpper($hint)
           OR c.cik = $hint
           OR toLower(c.name) = toLower($hint)
           OR toLower(c.name) STARTS WITH toLower($hint) + ' '
           OR toLower(c.name) STARTS WITH toLower($hint) + ','
        RETURN c.cik AS cik, c.name AS name, c.ticker AS ticker
        ORDER BY CASE WHEN toUpper(c.ticker) = toUpper($hint) THEN 0
                      WHEN c.cik = $hint THEN 1 ELSE 2 END, size(c.name)
        LIMIT 1
        """
        with self.driver.session(database=self.database) as session:
            rec = session.run(query, hint=hint).single()
        return dict(rec) if rec else None

    def campaign_timeline(self, company: str, since: str | None = None) -> CampaignTimelineResult:
        """Every dated 13D/13G filing on one issuer, in order, with each filer classified.

        Answers "who moved first, who followed, and who is just index money." Abstains when the
        issuer cannot be resolved or carries no dated ownership filing.
        """
        anchor = self._resolve_company(company)
        meta: dict[str, Any] = {
            "strategy_path": "campaign_timeline",
            "dated_layer": "BENEFICIAL_OWNER_OF.filing_date (13D/13G — the one trustworthy series)",
            "since": since,
        }
        if anchor is None:
            return CampaignTimelineResult(
                anchor=str(company),
                task_type="campaign_timeline",
                abstained=True,
                result={"reason": "company_not_found"},
                metadata=meta,
            )
        meta["resolved"] = anchor

        query = """
        MATCH (b:BeneficialOwner)-[e:BENEFICIAL_OWNER_OF]->(c:Company {cik: $cik})
        WHERE e.filing_date IS NOT NULL
          AND ($since IS NULL OR e.filing_date >= date($since))
        RETURN b.name AS filer, b.cik AS filer_cik, e.filing_type AS filing_type,
               toString(e.filing_date) AS filing_date,
               e.percent_of_class AS percent_of_class,
               e.accession_number AS accession_number
        ORDER BY e.filing_date
        """
        with self.driver.session(database=self.database) as session:
            rows = cast(
                "list[dict[str, Any]]",
                session.run(query, cik=anchor["cik"], since=since).data(),
            )

        if not rows:
            return CampaignTimelineResult(
                anchor=anchor["name"],
                task_type="campaign_timeline",
                abstained=True,
                result={
                    "reason": "no_dated_ownership_filings",
                    "note": "Issuer has no dated 13D/13G filing in the graph.",
                },
                metadata=meta,
            )

        for row in rows:
            row["filer_class"] = classify_filer(row.get("filer"), row.get("filing_type"))
        ordered = order_filings(rows)
        sequence = summarize_sequence(ordered)

        return CampaignTimelineResult(
            anchor=anchor["name"],
            task_type="campaign_timeline",
            abstained=False,
            result={
                "company": anchor,
                "timeline": ordered,
                "filing_count": len(ordered),
                "sequence": sequence,
                "franchises": distinct_franchises(ordered),
            },
            # Every dated 13D/13G filing is itself the citation.
            evidence=[
                {
                    "filer": r["filer"],
                    "filing_type": r["filing_type"],
                    "filing_date": r["filing_date"],
                    "percent_of_class": r["percent_of_class"],
                    "accession_number": r["accession_number"],
                }
                for r in ordered
                if r.get("accession_number")
            ],
            metadata=meta,
        )

    def convergence_scan(
        self,
        since: str | None = None,
        min_activists: int = DEFAULT_MIN_ACTIVISTS,
        window_days: int = DEFAULT_WINDOW_DAYS,
        limit: int = 20,
    ) -> CampaignTimelineResult:
        """Issuers where several known activist franchises filed 13D inside a rolling window.

        The "what is heating up" screen. Gated to :data:`ACTIVIST_FRANCHISES` — see the module
        docstring for why ungated detection is dominated by founders and filing-group artifacts.
        Abstains when nothing clears the bar.
        """
        meta: dict[str, Any] = {
            "strategy_path": "campaign_timeline",
            "since": since,
            "min_activists": min_activists,
            "window_days": window_days,
            "franchise_gated": True,
            "recall_note": (
                "Restricted to recognised activist franchises: precision over recall. "
                "Unlisted activists are missed by design."
            ),
        }
        # Franchise filtering happens in Cypher via a name-token ANY(...) so the scan does not
        # pull every 13D in the graph back to the client.
        query = """
        MATCH (b:BeneficialOwner)-[e:BENEFICIAL_OWNER_OF {filing_type:'13D'}]->(c:Company)
        WHERE e.filing_date IS NOT NULL
          AND ($since IS NULL OR e.filing_date >= date($since))
          AND ANY(f IN $franchises WHERE toUpper(b.name) CONTAINS f)
        WITH c, collect({
                filer: b.name, filing_date: toString(e.filing_date),
                percent_of_class: e.percent_of_class,
                accession_number: e.accession_number
             }) AS filings
        WHERE size(filings) >= $min_activists
        RETURN c.cik AS cik, c.name AS name, c.ticker AS ticker, filings
        """
        with self.driver.session(database=self.database) as session:
            rows = cast(
                "list[dict[str, Any]]",
                session.run(
                    query,
                    since=since,
                    min_activists=min_activists,
                    franchises=list(ACTIVIST_FRANCHISES),
                ).data(),
            )

        # Collapse affiliated entities to franchises, then require the arrivals to actually be
        # clustered in time — a 9-year spread is not a convergence.
        hits = []
        for row in rows:
            filings = order_filings(row["filings"])
            franchises = distinct_franchises(filings)
            if len(franchises) < min_activists:
                continue
            span = days_between(filings[0].get("filing_date"), filings[-1].get("filing_date"))
            if span is None or span > window_days:
                continue
            for f in filings:
                f["filer_class"] = classify_filer(f.get("filer"), "13D")
            hits.append(
                {
                    "company": {
                        "cik": row["cik"],
                        "name": row["name"],
                        "ticker": row["ticker"],
                    },
                    "franchises": franchises,
                    "franchise_count": len(franchises),
                    "span_days": span,
                    "filings": filings,
                    "sequence": summarize_sequence(filings),
                }
            )

        hits.sort(key=lambda h: str(h["filings"][-1].get("filing_date") or ""), reverse=True)
        hits = hits[:limit]

        if not hits:
            return CampaignTimelineResult(
                anchor=f"convergence since {since or 'all time'}",
                task_type="convergence_scan",
                abstained=True,
                result={
                    "reason": "no_convergence_found",
                    "note": (
                        f"No issuer had >={min_activists} recognised activist franchises "
                        f"filing 13D within {window_days} days."
                    ),
                },
                metadata=meta,
            )

        return CampaignTimelineResult(
            anchor=f"convergence since {since or 'all time'}",
            task_type="convergence_scan",
            abstained=False,
            result={"targets": hits, "target_count": len(hits)},
            evidence=[
                {
                    "company": h["company"]["ticker"] or h["company"]["name"],
                    "filer": f["filer"],
                    "filing_date": f["filing_date"],
                    "percent_of_class": f.get("percent_of_class"),
                    "accession_number": f.get("accession_number"),
                }
                for h in hits
                for f in h["filings"]
                if f.get("accession_number")
            ],
            metadata=meta,
        )

    # -- rendering ---------------------------------------------------------- #
    @staticmethod
    def format_answer(result: CampaignTimelineResult) -> str:
        """Render a result as a compact, cited answer (abstention stated explicitly)."""
        r = result
        if r.abstained:
            reason = r.result.get("reason", "no_support")
            note = r.result.get("note", "")
            return f"No graph-grounded answer for '{r.anchor}' ({reason}). {note}".strip()

        if r.task_type == "campaign_timeline":
            seq = r.result["sequence"]
            lines = [f"Ownership timeline — {r.anchor} ({r.result['filing_count']} dated filings):"]
            for f in r.result["timeline"]:
                pct = f" {f['percent_of_class']}%" if f.get("percent_of_class") else ""
                acc = f" [{f['accession_number']}]" if f.get("accession_number") else ""
                lines.append(
                    f"  {f['filing_date']}  {f['filing_type']:4}{pct:>8}  "
                    f"{f['filer']} ({f['filer_class']}){acc}"
                )
            if seq["activist_count"] >= 2:
                fm = seq["first_mover"]
                lines.append(
                    f"\nFirst mover: {fm['filer']} on {fm['filing_date']}"
                    + (f" at {fm['percent_of_class']}%" if fm.get("percent_of_class") else "")
                )
                for fo in seq["followers"]:
                    gap = (
                        f"{fo['days_after_first']} days later"
                        if fo["days_after_first"]
                        else "same day"
                    )
                    pct = f" at {fo['percent_of_class']}%" if fo.get("percent_of_class") else ""
                    lines.append(f"  → {fo['filer']} followed {gap}{pct}")
            elif seq["activist_count"] == 1:
                fm = seq["first_mover"]
                lines.append(f"\nSingle activist: {fm['filer']} ({fm['filing_date']}).")
            else:
                lines.append("\nNo recognised activist franchise on this issuer.")
            return "\n".join(lines)

        if r.task_type == "convergence_scan":
            lines = [f"Activist convergence — {r.result['target_count']} issuers:"]
            for h in r.result["targets"]:
                c = h["company"]
                lines.append(
                    f"\n  {c['ticker'] or c['cik']} — {c['name']} "
                    f"({h['franchise_count']} franchises within {h['span_days']} days)"
                )
                for f in h["filings"]:
                    pct = f" {f['percent_of_class']}%" if f.get("percent_of_class") else ""
                    lines.append(f"     {f['filing_date']}  {f['filer']}{pct}")
            return "\n".join(lines)

        return str(r.to_dict())
