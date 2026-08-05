"""
Ownership graph-native query core — the read-only intelligence engine for ``secgraph``.

A deterministic, LLM-free, **read-only** engine that answers the three graph-native questions as
parameterized, evidence-returning, **abstain-on-no-support** calls — so they can be *served* (via
the curated MCP tools in :mod:`secgraph.mcp`) rather than only emitted as static markdown by a
proof script.

The three wins, each sharing the one signature SQL cannot express and vectors cannot
represent — *variable-depth reachability over a hard-keyed (CIK) graph*:

- :meth:`control_chain` — transitive control up/down through verified ``>=50%`` 13D edges.
- :meth:`board_path` — ``shortestPath`` between two boards over the persisted
  ``SHARES_DIRECTOR`` interlock edge.
- :meth:`coalition` — the custodial-scrubbed activist wolf-pack connected component around a
  named activist, with its diameter.

Two disciplines carried over from the GraphRAG citation contract:

- **Evidence or abstain.** Every non-abstaining answer carries an ``evidence`` block (13D
  accession numbers, filing dates, shared-director counts / shared-target CIKs). When the
  anchor has no *verified* support (e.g. a company with no ``control_class='control'`` edge,
  or two boards with no interlock path), the result is an explicit ``abstained=True`` — never
  a fabricated chain from ``stake``/``unknown`` edges.
- **Truth-in-inclusion.** Noise is handled by the established scrubs (human-directors-only,
  operating-companies-only, custodial-hub removal), never by dropping true facts.

**The traversal runs in the database, not here.** All three wins execute as Cypher
variable-length patterns (``VarLengthExpand``) over *materialized* derived edges — ``CONTROLS``
(+ the ``SAME_ENTITY_AS`` CIK-identity bridge), ``SHARES_DIRECTOR``, and ``CO_TARGETS``. That is
deliberate and load-bearing: an earlier version pulled every edge out with a flat ``MATCH`` and
walked it in Python, which any warehouse reproduces with a ``SELECT`` plus a loop — so the
"SQL cannot express this" claim was not actually earned by the graph. The Python adjacency
walkers in :mod:`.graph_native_proof` are retained deliberately, but only as the *flat-SQL
side* of the head-to-head comparison. This module contributes anchor resolution, evidence
assembly, and rendering — the query core around the traversal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, cast

from secgraph.ingestion.ownership.campaign_timeline import ACTIVIST_FRANCHISES
from secgraph.ingestion.ownership.graph_native_proof import is_custodial_hub

logger = logging.getLogger(__name__)


def _current_year() -> int:
    """Today's year — the reference point for judging evidence age."""
    from datetime import date

    return date.today().year


# Default traversal depth for chain/path queries. Deliberately bounded: control pyramids
# and board-interlock paths deeper than this are vanishingly rare in the data and unbounded
# depth invites hub blow-up. Callers can raise it explicitly.
DEFAULT_MAX_HOPS = 4

# Minimum shared 13D targets to link two activists in the coalition graph (the same
# threshold the proof uses; a single shared target is too weak to imply coordination).
DEFAULT_MIN_SHARED_TARGETS = 2


@dataclass
class OwnershipIntelligenceResult:
    """Structured, serializable result for one ownership graph-native query.

    ``abstained`` is the load-bearing field: True means the graph carried no verified
    support for the question (no answer is invented). ``result`` holds the task-specific
    payload (chains / path / coalition), ``evidence`` the citations that ground it.
    """

    anchor: str
    task_type: str
    abstained: bool
    result: dict[str, Any]
    evidence: list[dict[str, Any]]
    metadata: dict[str, Any]

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
def chain_contains(chain: list[dict[str, Any]], cik: str) -> bool:
    """True if a rendered control chain passes through ``cik`` at any step."""
    return any(step.get("cik") == cik for step in chain)


def chains_through_anchor(
    chains: list[list[tuple[str, float]]], anchor_cik: str
) -> list[list[tuple[str, float]]]:
    """Filter enumerated control chains to those that touch ``anchor_cik``.

    A control-chain query is anchored on one issuer; we surface only the chains that
    actually pass through it, ordered longest-first (deepest control structure first).
    """
    hits = [c for c in chains if any(cik == anchor_cik for cik, _ in c)]
    return sorted(hits, key=len, reverse=True)


def render_control_chain(
    chain: list[tuple[str, float]], names: dict[str, str]
) -> list[dict[str, Any]]:
    """Turn a ``[(cik, pct), ...]`` chain into named, cited steps for output."""
    return [{"cik": cik, "name": names.get(cik, cik), "pct": pct} for cik, pct in chain]


# A supporting filing older than this makes an answer last-known rather than current. Chosen
# because 13D carries no exit obligation once a holder drops below 5%, so an old percent has no
# corroboration at all — and half the CONTROLS population predates 2020.
STALE_EVIDENCE_YEARS = 5


def evidence_age(
    evidence: list[dict[str, Any]], as_of_year: int, stale_after: int = STALE_EVIDENCE_YEARS
) -> dict[str, Any]:
    """Filing years behind an answer, and whether the freshest is stale.

    Presenting a percent without its date tells a reader a 2006 fact in the present tense —
    Goldcorp's 75% of Wheaton is on file from 2006, and Goldcorp ceased to exist in 2019. The age
    belongs in the answer so the reader can supply their own scepticism.
    """
    years = sorted(
        {
            int(str(e["filing_date"])[:4])
            for e in evidence
            if str(e.get("filing_date") or "")[:4].isdigit()
        }
    )
    if not years:
        return {"evidence_years": [], "stale_evidence": False, "stale_after_years": stale_after}
    return {
        "evidence_years": years,
        # Judged on the NEWEST filing: one fresh confirmation redeems a chain with old links.
        "stale_evidence": (as_of_year - max(years)) > stale_after,
        "stale_after_years": stale_after,
    }


def coalition_of(components: list[set[str]], anchor_cik: str) -> set[str]:
    """The connected component (activist coalition) containing ``anchor_cik``, else empty."""
    for comp in components:
        if anchor_cik in comp:
            return comp
    return set()


# Identities a franchise-token match cannot see, because the two names share no substring.
# Each is an evidenced one-actor relationship, not a name-similarity guess — the same hard-key
# discipline the rest of the graph follows:
#
#   GOLDSTEIN PHILLIP  -> Bulldog's principal, who also files personally.
#   DIGIRAD CORP       -> renamed Star Equity Holdings; the two file on shared targets
#                         (GYRO, SVVC), so they are one filer under two names.
#
# Keyed on the UPPERCASED filer name, checked before the franchise tokens so a mapped name wins.
_AFFILIATE_IDENTITIES = {
    "GOLDSTEIN PHILLIP": "BULLDOG INVESTORS",
    "DIGIRAD CORP": "STAR EQUITY",
    "STAR EQUITY FUND, LP": "STAR EQUITY",
}


def _actor_key(name: str) -> str | None:
    """The distinct-actor key for a filer name, or None when no franchise/identity matches.

    Franchise tokens are substrings, so they collapse "Bulldog Investors" /
    "Bulldog Investors, LLP" / "Bulldog Investors General Partnership" — three CIKs of one firm
    — onto one key automatically. ``_AFFILIATE_IDENTITIES`` covers the rest.
    """
    upper = str(name or "").upper().strip()
    if upper in _AFFILIATE_IDENTITIES:
        return _AFFILIATE_IDENTITIES[upper]
    matched = next((f for f in ACTIVIST_FRANCHISES if f in upper), None)
    if matched is None:
        return None
    return _AFFILIATE_IDENTITIES.get(matched, matched)


def collapse_affiliates(names: list[str]) -> list[str]:
    """Collapse affiliated filers to one entry per distinct actor.

    A coalition roster counts CIKs, but one manager frequently files through several: "Bulldog
    Investors" and "Bulldog Investors, LLP" are two CIKs, and Phillip Goldstein is Bulldog's
    principal — three rows for one actor. Presenting 13 CIKs as 13 *actors* overstates the
    coalition, and the first person who knows the names will say so.

    Collapses on the franchise token (as ``distinct_franchises`` does for the convergence
    screen), plus the ``_PRINCIPAL_TO_FIRM`` identities that a token match cannot see. Names
    matching no known franchise pass through — they may be genuine one-off filers, and dropping
    them would understate the coalition instead.

    Note what is deliberately *not* collapsed: GAMCO and Marc Gabelli are separate filers with
    separate 13D histories, so they stay distinct despite the family relationship.
    """
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        key = _actor_key(name)
        if key is None:
            out.append(name)
            continue
        if key not in seen:
            seen.add(key)
            out.append(name)
    return out


def chains_from_paths(
    rows: list[dict[str, Any]],
) -> tuple[list[list[dict[str, Any]]], list[dict[str, Any]]]:
    """Turn Cypher control-path rows into rendered chains + a deduped evidence block.

    Each row carries the bridge-collapsed node ``steps`` plus per-control-hop ``pcts`` /
    ``accessions`` / ``filing_dates``. The percentage on a step is the stake by which the
    *previous* step controls it, so the root carries no percentage. Evidence is one entry per
    distinct control filing across all surfaced chains, keyed on accession + owner + target so
    a filing shared by several chains is cited once.
    """
    chains: list[list[dict[str, Any]]] = []
    evidence: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for row in rows:
        steps = row.get("steps") or []
        pcts = row.get("pcts") or []
        accessions = row.get("accessions") or []
        filing_dates = row.get("filing_dates") or []
        if len(steps) < 2:
            continue

        # `institutional_value_usd` rides along on each step so a consumer can see the scale of
        # the controlled issuer. Without it, Deutsche Telekom's 74.3% of T-Mobile ($95B) and a
        # 74% stake in a $30 shell render identically.
        rendered = [
            {
                "cik": steps[0].get("cik"),
                "name": steps[0].get("name"),
                "ticker": steps[0].get("ticker"),
                "pct": 0.0,
                "institutional_value_usd": steps[0].get("institutional_value_usd"),
            }
        ]
        for idx, step in enumerate(steps[1:]):
            rendered.append(
                {
                    "cik": step.get("cik"),
                    "name": step.get("name"),
                    "ticker": step.get("ticker"),
                    "pct": pcts[idx] if idx < len(pcts) else None,
                    "institutional_value_usd": step.get("institutional_value_usd"),
                }
            )
        chains.append(rendered)

        for idx in range(min(len(pcts), len(steps) - 1)):
            owner, target = rendered[idx], rendered[idx + 1]
            accession = accessions[idx] if idx < len(accessions) else None
            key = (str(accession), str(owner["name"]), str(target["name"]))
            if key in seen:
                continue
            seen.add(key)
            evidence.append(
                {
                    "owner": owner["name"],
                    "company": target["name"],
                    "percent_of_class": pcts[idx],
                    "accession_number": accession,
                    "filing_date": filing_dates[idx] if idx < len(filing_dates) else None,
                }
            )
    return chains, evidence


# --------------------------------------------------------------------------- #
# The engine.
# --------------------------------------------------------------------------- #
class OwnershipIntelligenceEngine:
    """Read-only ownership graph-native query engine over ``secgraph``.

    Never writes. Every method returns an :class:`OwnershipIntelligenceResult` that either
    carries graph-grounded evidence or abstains.
    """

    def __init__(self, driver, database: str | None = None):
        self.driver = driver
        self.database = database

    # -- anchor resolution -------------------------------------------------- #
    def resolve_company(self, hint: str) -> dict[str, Any] | None:
        """Resolve a ticker / company-name / CIK hint to ``{cik, name, ticker}`` or None."""
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

    def resolve_activist(self, hint: str) -> dict[str, Any] | None:
        """Resolve an activist-name / CIK hint to a 13D-filing ``BeneficialOwner``.

        Restricted to owners that actually file 13D (the activist layer); a name that
        matches only a passive 13G filer is not an activist for coalition purposes.
        """
        hint = str(hint or "").strip()
        if not hint:
            return None
        query = """
        MATCH (b:BeneficialOwner)-[:BENEFICIAL_OWNER_OF {filing_type:'13D'}]->(:Company)
        WHERE b.cik = $hint
           OR toUpper(b.name) = toUpper($hint)
           OR toUpper(b.name) STARTS WITH toUpper($hint)
        WITH b, count(*) AS filings
        RETURN b.cik AS cik, b.name AS name, filings
        ORDER BY CASE WHEN b.cik = $hint THEN 0
                      WHEN toUpper(b.name) = toUpper($hint) THEN 1 ELSE 2 END,
                 filings DESC
        LIMIT 1
        """
        with self.driver.session(database=self.database) as session:
            rec = session.run(query, hint=hint).single()
        return dict(rec) if rec else None

    # -- DB fetches (read-only) --------------------------------------------- #
    def _fetch_control_chains(self, session, cik: str, max_hops: int) -> list[dict[str, Any]]:
        """Transitive control chains into ``cik`` via **Cypher variable-depth traversal**.

        The traversal runs in the database (``VarLengthExpand``) over the materialized
        ``CONTROLS`` edge, hopping intermediate holding companies through the
        ``SAME_ENTITY_AS`` CIK-identity bridge. Bridge nodes are dropped from the rendered
        chain so it reads as one step per control hop.

        ``max_hops`` counts *control* hops; the pattern budget is doubled because each control
        hop past the first may consume a bridge hop as well.
        """
        rows = session.run(
            f"""
            MATCH (target:Company {{cik: $cik}})
            MATCH p = (root:BeneficialOwner)-[:CONTROLS|SAME_ENTITY_AS*1..{int(max_hops) * 2}]->(target)
            // Only maximal chains: the root must not itself be controlled by another filer.
            WHERE NOT EXISTS {{ MATCH ()-[:CONTROLS]->(:Company)-[:SAME_ENTITY_AS]->(root) }}
            WITH p, [r IN relationships(p) WHERE type(r) = 'CONTROLS'] AS ctrl
            WHERE size(ctrl) >= 1
            RETURN [n IN nodes(p) WHERE NOT (n:BeneficialOwner AND n <> head(nodes(p)))
                    | {{cik: n.cik, name: coalesce(n.name, n.cik), ticker: n.ticker,
                         institutional_value_usd: n.institutional_value_usd}}] AS steps,
                   [r IN ctrl | r.percent_of_class] AS pcts,
                   [r IN ctrl | r.accession_number] AS accessions,
                   [r IN ctrl | r.filing_date] AS filing_dates,
                   size(ctrl) AS hops
            ORDER BY hops DESC
            """,
            cik=cik,
        ).data()
        return cast("list[dict[str, Any]]", rows)

    def _fetch_coalition(self, session, cik: str, min_shared: int) -> list[dict[str, Any]]:
        """The coalition reachable from ``cik`` via **Cypher variable-depth traversal**.

        Walks the materialized ``CO_TARGETS`` edge (direction-agnostic — it is stored once per
        unordered pair). Custodial hubs are excluded *at projection time* via the
        ``is_custodial`` node flag, so the underlying co-targeting facts stay in the graph while
        the component stays precise. ``coalesce(..., false)`` is required: a missing property
        would make the ``NONE(...)`` predicate null and silently filter out every member.

        ``min_shared`` re-asserts the edge threshold at read time so a caller asking for a
        stricter bar than the materializer used still gets a correct answer.
        """
        rows = session.run(
            """
            MATCH (seed:BeneficialOwner {cik: $cik})
            MATCH p = (seed)-[rels:CO_TARGETS*0..12]-(m:BeneficialOwner)
            WHERE NONE(n IN nodes(p) WHERE coalesce(n.is_custodial, false))
              AND ALL(r IN rels WHERE r.shared_target_count >= $min_shared)
            RETURN DISTINCT m.cik AS cik, m.name AS name
            """,
            cik=cik,
            min_shared=min_shared,
        ).data()
        return cast("list[dict[str, Any]]", rows)

    def _coalition_diameter(self, session, member_ciks: list[str]) -> int:
        """Longest shortest-path (in hops) between any two coalition members, via Cypher.

        The diameter is what makes the coalition a *graph* object rather than a set: it is the
        length of the longest geodesic inside the component, which no self-join can produce.
        """
        rec = session.run(
            """
            MATCH (a:BeneficialOwner), (b:BeneficialOwner)
            WHERE a.cik IN $ciks AND b.cik IN $ciks AND a.cik < b.cik
            MATCH p = shortestPath((a)-[:CO_TARGETS*..12]-(b))
            WHERE NONE(n IN nodes(p) WHERE coalesce(n.is_custodial, false))
            RETURN max(length(p)) AS diameter
            """,
            ciks=member_ciks,
        ).single()
        return int(rec["diameter"]) if rec and rec["diameter"] is not None else 0

    def _coalition_evidence(self, session, member_ciks: list[str]) -> list[dict[str, Any]]:
        """The co-targeting links (with shared target CIKs) internal to the coalition."""
        rows = session.run(
            """
            MATCH (a:BeneficialOwner)-[r:CO_TARGETS]-(b:BeneficialOwner)
            WHERE a.cik IN $ciks AND b.cik IN $ciks AND a.cik < b.cik
            RETURN a.name AS activist_a, b.name AS activist_b,
                   r.shared_target_ciks AS shared_target_ciks,
                   r.shared_target_count AS shared_target_count
            ORDER BY r.shared_target_count DESC
            """,
            ciks=member_ciks,
        ).data()
        return cast("list[dict[str, Any]]", rows)

    def _coalition_top_targets(
        self, session, member_ciks: list[str], limit: int = 10
    ) -> list[dict[str, Any]]:
        """The coalition's 13D targets, largest first, named.

        Without this the coalition reported *who* was in it but only CIKs for *what* they were
        targeting, so the output read as opaque and the recognizable names were invisible. Ranked
        by ``institutional_value_usd`` because the unranked view surfaced $15M closed-end funds
        ahead of Deere and Occidental — the same names were always there, just unsorted.

        ``value_usd`` is null for issuers with no 13F coverage; they sort last rather than being
        treated as zero (absence means "not institutionally held", not "smallest").
        """
        rows = session.run(
            """
            MATCH (m:BeneficialOwner)-[:BENEFICIAL_OWNER_OF {filing_type:'13D'}]->(c:Company)
            WHERE m.cik IN $ciks
            WITH c, collect(DISTINCT m.name) AS filers
            RETURN c.cik AS cik, c.ticker AS ticker, coalesce(c.name, c.cik) AS name,
                   c.institutional_value_usd AS value_usd,
                   toString(c.institutional_value_period) AS value_period,
                   size(filers) AS coalition_filers
            // Cypher has no NULLS LAST clause, and DESC sorts nulls FIRST (verified on 2026.05)
            // — which would put every unknown-size issuer at the top. Sort on an explicit
            // has-value flag so nulls land last: no 13F coverage means "unknown size", not
            // "smallest", and it must not outrank a named $114B target.
            ORDER BY value_usd IS NOT NULL DESC, value_usd DESC, name
            LIMIT $limit
            """,
            ciks=member_ciks,
            limit=limit,
        ).data()
        return cast("list[dict[str, Any]]", rows)

    # -- WIN 1: control chain ----------------------------------------------- #
    def control_chain(
        self,
        company: str,
        direction: str = "up",
        max_hops: int = DEFAULT_MAX_HOPS,
    ) -> OwnershipIntelligenceResult:
        """Transitive ``>=50%`` control chain(s) through a named issuer.

        ``direction='up'`` traces who ultimately controls the issuer (issuer is a leaf/
        intermediate); ``'down'`` traces what the issuer controls (issuer is a root/
        intermediate). Abstains when the issuer touches no verified control edge.
        """
        anchor = self.resolve_company(company)
        meta = {
            "strategy_path": "ownership_intelligence",
            "direction": direction,
            "max_hops": max_hops,
            "control_threshold_pct": 50.0,
        }
        if anchor is None:
            return OwnershipIntelligenceResult(
                anchor=str(company),
                task_type="control_chain",
                abstained=True,
                result={"reason": "company_not_found"},
                evidence=[],
                metadata=meta,
            )
        anchor_cik = anchor["cik"]
        meta["resolved"] = anchor
        meta["traversal"] = "cypher_var_length_expand"

        with self.driver.session(database=self.database) as session:
            rows = self._fetch_control_chains(session, anchor_cik, max_hops)

        chains, evidence = chains_from_paths(rows)

        if not chains:
            return OwnershipIntelligenceResult(
                anchor=anchor["name"],
                task_type="control_chain",
                abstained=True,
                result={
                    "reason": "no_verified_control_chain",
                    "note": "Issuer has no >=50% verified 13D control edge on a chain.",
                },
                evidence=[],
                metadata=meta,
            )

        return OwnershipIntelligenceResult(
            anchor=anchor["name"],
            task_type="control_chain",
            abstained=False,
            result={
                "chains": chains[:10],
                "chain_count": len(chains),
                "deepest_hops": max(len(c) - 1 for c in chains),
                **evidence_age(evidence, as_of_year=_current_year()),
            },
            evidence=evidence,
            metadata=meta,
        )

    @staticmethod
    def _names_from_edges(edges: list[dict[str, Any]]) -> dict[str, str]:
        names: dict[str, str] = {}
        for e in edges:
            if e.get("owner_cik"):
                names.setdefault(e["owner_cik"], e.get("owner_name") or e["owner_cik"])
            if e.get("company_cik"):
                names.setdefault(e["company_cik"], e.get("company_name") or e["company_cik"])
        return names

    # -- WIN 2: board-interlock path ---------------------------------------- #
    def board_path(
        self,
        from_company: str,
        to_company: str,
        max_hops: int = DEFAULT_MAX_HOPS,
    ) -> OwnershipIntelligenceResult:
        """``shortestPath`` between two boards over the persisted ``SHARES_DIRECTOR`` edge.

        Direction-agnostic (the edge is stored undirected). Resolves ``via_ciks`` to the
        bridging director's name per hop. Abstains when no interlock path exists within
        ``max_hops``.
        """
        a = self.resolve_company(from_company)
        z = self.resolve_company(to_company)
        meta = {"strategy_path": "ownership_intelligence", "max_hops": max_hops}
        if a is None or z is None:
            return OwnershipIntelligenceResult(
                anchor=f"{from_company} → {to_company}",
                task_type="board_path",
                abstained=True,
                result={"reason": "company_not_found", "from_resolved": a, "to_resolved": z},
                evidence=[],
                metadata=meta,
            )
        meta["from_resolved"], meta["to_resolved"] = a, z

        query = f"""
            MATCH (a:Company {{cik: $a_cik}}), (z:Company {{cik: $z_cik}})
            MATCH p = shortestPath((a)-[:SHARES_DIRECTOR*..{int(max_hops)}]-(z))
            WITH nodes(p) AS ns, relationships(p) AS rs
            UNWIND (CASE WHEN size(rs) = 0 THEN [null] ELSE range(0, size(rs) - 1) END) AS idx
            OPTIONAL MATCH (i:Insider) WHERE idx IS NOT NULL AND i.cik = rs[idx].via_ciks[0]
            WITH ns, idx, rs, coalesce(i.name, rs[idx].via_ciks[0]) AS via_name
            ORDER BY idx
            WITH ns,
                 collect(CASE WHEN idx IS NULL THEN null ELSE via_name END) AS via_names,
                 collect(CASE WHEN idx IS NULL THEN null
                              ELSE rs[idx].director_count END) AS shared_counts
            RETURN [n IN ns | {{cik: n.cik, name: n.name, ticker: n.ticker}}] AS chain,
                   [v IN via_names WHERE v IS NOT NULL] AS via,
                   [s IN shared_counts WHERE s IS NOT NULL] AS shared_director_counts
            LIMIT 1
        """
        with self.driver.session(database=self.database) as session:
            rec = session.run(query, a_cik=a["cik"], z_cik=z["cik"]).single()

        if not rec or not rec["chain"]:
            return OwnershipIntelligenceResult(
                anchor=f"{a['name']} → {z['name']}",
                task_type="board_path",
                abstained=True,
                result={
                    "reason": "no_interlock_path",
                    "note": f"No shared-director path within {max_hops} hops.",
                },
                evidence=[],
                metadata=meta,
            )
        chain = rec["chain"]
        via = rec["via"]
        evidence = [
            {"bridge_director": name, "shared_director_count": cnt}
            for name, cnt in zip(via, rec["shared_director_counts"], strict=False)
        ]
        return OwnershipIntelligenceResult(
            anchor=f"{a['name']} → {z['name']}",
            task_type="board_path",
            abstained=False,
            result={"chain": chain, "via_directors": via, "hops": len(chain) - 1},
            evidence=evidence,
            metadata=meta,
        )

    # -- WIN 3: activist coalition ------------------------------------------ #
    def coalition(
        self,
        activist: str,
        min_shared_targets: int = DEFAULT_MIN_SHARED_TARGETS,
    ) -> OwnershipIntelligenceResult:
        """The custodial-scrubbed activist coalition (connected component) around a filer.

        Two activists are linked when they co-target ``>=min_shared_targets`` of the same
        issuers via 13D. Custodial/broker hubs are scrubbed before components form (the
        precision discipline). Abstains when the activist sits in no multi-member coalition.
        """
        anchor = self.resolve_activist(activist)
        meta = {"strategy_path": "ownership_intelligence", "min_shared_targets": min_shared_targets}
        if anchor is None:
            return OwnershipIntelligenceResult(
                anchor=str(activist),
                task_type="coalition",
                abstained=True,
                result={"reason": "activist_not_found"},
                evidence=[],
                metadata=meta,
            )
        anchor_cik = anchor["cik"]
        meta["resolved"] = anchor
        meta["traversal"] = "cypher_var_length_expand"

        with self.driver.session(database=self.database) as session:
            comp_rows = self._fetch_coalition(session, anchor_cik, min_shared_targets)

        members = [r for r in comp_rows if r.get("cik")]

        if len(members) <= 1:
            # Either scrubbed out as custodial, or not co-targeting anyone at threshold.
            reason = (
                "custodial_hub_excluded" if is_custodial_hub(anchor.get("name")) else "no_coalition"
            )
            return OwnershipIntelligenceResult(
                anchor=anchor["name"],
                task_type="coalition",
                abstained=True,
                result={
                    "reason": reason,
                    "note": "Activist sits in no multi-member co-targeting coalition.",
                },
                evidence=[],
                metadata=meta,
            )

        member_ciks = [m["cik"] for m in members]
        with self.driver.session(database=self.database) as session:
            diameter = self._coalition_diameter(session, member_ciks)
            evidence = self._coalition_evidence(session, member_ciks)
            top_targets = self._coalition_top_targets(session, member_ciks)

        return OwnershipIntelligenceResult(
            anchor=anchor["name"],
            task_type="coalition",
            abstained=False,
            result={
                "members": sorted(m["name"] or m["cik"] for m in members),
                # Both counts are reported. `member_count` is CIKs (what the traversal found);
                # `distinct_actors` collapses affiliated vehicles of one manager, which is the
                # number to quote to someone who knows the names. Reporting only the larger
                # figure overstates the coalition; reporting only the smaller hides the
                # filing-group structure, which is itself informative.
                "member_count": len(members),
                "distinct_actors": len(
                    collapse_affiliates(sorted(m["name"] or m["cik"] for m in members))
                ),
                "distinct_actor_names": collapse_affiliates(
                    sorted(m["name"] or m["cik"] for m in members)
                ),
                "diameter_hops": diameter,
                # What the coalition actually targets, largest first. The members answer "who";
                # this answers "on what" — and ranked, it leads with names a desk trades.
                "top_targets": top_targets,
            },
            evidence=evidence,
            metadata=meta,
        )

    # -- WIN 4: influence map (stake + board seat) -------------------------- #
    def influence_map(
        self,
        min_tier: int = 25,
        min_value_usd: float = 1e9,
        limit: int = 25,
    ) -> OwnershipIntelligenceResult:
        """Issuers where a holder has a presumption-tier stake **and** a current board seat.

        The two-limb test from 12 CFR 225.2(e), and the one finding here that needs no hedging:
        a >=25% holder who also sits on the board is influential by any reading. Its force comes
        from the **conjunction of two independent filing types** — a Schedule 13D on one side,
        Form 3/4/5 board activity on the other — which is exactly the join a filings database or a
        single-table screener cannot do for you.

        It also fixes the freshness problem that makes ``control_chain`` alone unconvincing. Half
        the 13D population predates 2020 (13D carries no exit obligation below 5%, so a percent is
        last-known), but the board limb runs to 2026. Requiring a *current* seat means the answer
        is corroborated by recent activity even when the stake was declared a decade ago —
        Liberty Broadband's 26.1% of Charter was filed in 2014 and its director was seen in 2026.

        ``min_value_usd`` filters on the 13F size proxy so results are recognizable rather than
        nano-cap noise; issuers with no institutional coverage are excluded rather than ranked
        last, because an unranked row cannot honestly claim materiality.
        """
        meta = {
            "strategy_path": "ownership_intelligence",
            "traversal": "cypher_two_limb_join",
            "min_tier": min_tier,
            "min_value_usd": min_value_usd,
            "legal_basis": "12 CFR 225.2(e) control presumptions (stake tier + board control)",
        }
        with self.driver.session(database=self.database) as session:
            rows = session.run(
                """
                MATCH (b:BeneficialOwner)-[r:INFLUENCES]->(c:Company)
                WHERE r.board_seat AND r.tier >= $min_tier
                  AND c.institutional_value_usd >= $min_value
                RETURN c.cik AS company_cik, c.ticker AS ticker,
                       coalesce(c.name, c.cik) AS company,
                       c.institutional_value_usd AS institutional_value_usd,
                       b.cik AS owner_cik, coalesce(b.name, b.cik) AS owner,
                       r.percent_of_class AS percent_of_class, r.tier AS tier,
                       r.accession_number AS accession_number,
                       toString(r.filing_date) AS filing_date,
                       toString(r.board_seat_last_seen) AS board_seat_last_seen
                ORDER BY institutional_value_usd DESC, percent_of_class DESC
                LIMIT $limit
                """,
                min_tier=min_tier,
                min_value=min_value_usd,
                limit=limit,
            ).data()

        if not rows:
            return OwnershipIntelligenceResult(
                anchor=f"influence >= {min_tier}% with a board seat",
                task_type="influence_map",
                abstained=True,
                result={
                    "reason": "no_influence_with_board_seat",
                    "note": (
                        "No issuer meets both limbs at these thresholds. Requires INFLUENCES "
                        "edges (materialize_influence_edges.py) and the 13F size proxy."
                    ),
                },
                evidence=[],
                metadata=meta,
            )

        # Evidence carries BOTH limbs — the 13D accession and the board-seat date. That pairing
        # is the claim, so citing only the filing would understate what supports it.
        evidence = [
            {
                "owner": r["owner"],
                "company": r["company"],
                "percent_of_class": r["percent_of_class"],
                "accession_number": r["accession_number"],
                "filing_date": r["filing_date"],
                "board_seat_last_seen": r["board_seat_last_seen"],
            }
            for r in rows
        ]
        return OwnershipIntelligenceResult(
            anchor=f"influence >= {min_tier}% with a current board seat",
            task_type="influence_map",
            abstained=False,
            result={"cases": rows, "case_count": len(rows)},
            evidence=evidence,
            metadata=meta,
        )

    # -- supporting context: ownership snapshot ---------------------------- #
    def ownership_snapshot(self, company: str, top_n: int = 10) -> OwnershipIntelligenceResult:
        """Supporting context for one issuer: top holders, insiders, and control status.

        Not itself a graph-native "win" — it grounds the headline chains/paths with the
        surrounding ownership facts (largest 13D/13G beneficial owners, board size, whether
        any verified control edge exists). Abstains only when the company can't be resolved.
        """
        anchor = self.resolve_company(company)
        meta = {"strategy_path": "ownership_intelligence", "top_n": top_n}
        if anchor is None:
            return OwnershipIntelligenceResult(
                anchor=str(company),
                task_type="ownership_snapshot",
                abstained=True,
                result={"reason": "company_not_found"},
                evidence=[],
                metadata=meta,
            )
        meta["resolved"] = anchor
        query = """
        MATCH (c:Company {cik: $cik})
        OPTIONAL MATCH (b:BeneficialOwner)-[r:BENEFICIAL_OWNER_OF]->(c)
        WITH c, b, r
        ORDER BY coalesce(r.percent_of_class, 0.0) DESC
        WITH c,
             collect(DISTINCT {
                 owner: b.name, cik: b.cik, filing_type: r.filing_type,
                 percent_of_class: r.percent_of_class,
                 control_class: r.control_class,
                 filing_date: toString(r.filing_date)
             })[0..$top_n] AS holders,
             count(DISTINCT b) AS holder_count
        OPTIONAL MATCH (i:Insider)-[:DIRECTOR_OF]->(c)
        WITH c, holders, holder_count, count(DISTINCT i) AS director_count
        OPTIONAL MATCH (o:Insider)-[:OFFICER_OF]->(c)
        RETURN holders, holder_count, director_count,
               count(DISTINCT o) AS officer_count,
               any(h IN holders WHERE h.control_class = 'control') AS has_control_edge
        """
        with self.driver.session(database=self.database) as session:
            rec = session.run(query, cik=anchor["cik"], top_n=top_n).single()

        holders = [h for h in (rec["holders"] if rec else []) if h.get("owner")]
        return OwnershipIntelligenceResult(
            anchor=anchor["name"],
            task_type="ownership_snapshot",
            abstained=False,
            result={
                "company": anchor,
                "top_holders": holders,
                "beneficial_owner_count": (rec["holder_count"] if rec else 0),
                "director_count": (rec["director_count"] if rec else 0),
                "officer_count": (rec["officer_count"] if rec else 0),
                "has_verified_control_edge": bool(rec["has_control_edge"]) if rec else False,
            },
            evidence=[
                {
                    "owner": h["owner"],
                    "filing_type": h.get("filing_type"),
                    "percent_of_class": h.get("percent_of_class"),
                    "filing_date": h.get("filing_date"),
                }
                for h in holders
                if h.get("percent_of_class") is not None
            ],
            metadata=meta,
        )

    # -- rendering ---------------------------------------------------------- #
    @staticmethod
    def format_answer(result: OwnershipIntelligenceResult) -> str:
        """Render a result as a compact, cited, human-readable answer.

        Mirrors the GraphRAG ``format_answer`` contract: an explicit abstention line when
        support is absent, otherwise the structural object followed by an ``Evidence:``
        block. No prose is invented beyond the graph facts.
        """
        r = result
        if r.abstained:
            reason = r.result.get("reason", "no_support")
            note = r.result.get("note", "")
            return f"No graph-grounded answer for '{r.anchor}' ({reason}). {note}".strip()

        if r.task_type == "influence_map":
            lines = [
                f"Stake + board seat — {r.result['case_count']} issuers "
                f"({r.anchor}). Two independent filing types agreeing:",
                "",
                f"  {'TICKER':8} {'SIZE':>9}  {'STAKE':>6}  {'13D':6} {'SEAT':8} HOLDER",
            ]
            for c in r.result["cases"]:
                size = (
                    f"${c['institutional_value_usd'] / 1e9:.1f}B"
                    if c.get("institutional_value_usd")
                    else "—"
                )
                yr = (c.get("filing_date") or "")[:4]
                seat = (c.get("board_seat_last_seen") or "")[:7]
                lines.append(
                    f"  {c.get('ticker') or c['company_cik']:8} {size:>9}  "
                    f"{c['percent_of_class']:5.1f}%  {yr:6} {seat:8} {c['owner']}"
                )
            lines += [
                "",
                "Each row cites a Schedule 13D accession AND a Form 3/4/5 board date;"
                " see Evidence.",
            ]
            return "\n".join(lines)

        if r.task_type == "control_chain":
            lines = [
                f"Control chains through {r.anchor} "
                f"({r.result['chain_count']}, deepest {r.result['deepest_hops']} hops):"
            ]
            for chain in r.result["chains"]:
                steps = " → ".join(
                    f"{s['name']} ({s['pct']:.0f}%)" if s["pct"] else s["name"] for s in chain
                )
                lines.append(f"  [{len(chain) - 1}h] {steps}")
            # Age is part of the answer, not a footnote. Goldcorp's 75% of Wheaton was filed in
            # 2006 and Goldcorp ceased to exist in 2019 — a reader shown "75%" with no date is
            # being told a 2006 fact in the present tense.
            if r.result.get("evidence_years"):
                yrs = r.result["evidence_years"]
                span = f"{min(yrs)}" if len(set(yrs)) == 1 else f"{min(yrs)}–{max(yrs)}"
                lines.append(f"  Evidence filed: {span}.")
            if r.result.get("stale_evidence"):
                lines.append(
                    "  ⚠ Newest supporting filing is over "
                    f"{r.result['stale_after_years']} years old. 13D carries no exit obligation "
                    "below 5%, so this is a LAST-KNOWN stake, not a confirmed current one."
                )
            lines.append("Evidence (13D filings):")
            for e in r.evidence[:10]:
                lines.append(
                    f"  - {e['owner']} → {e['company']} "
                    f"{e['percent_of_class']}% [{e['accession_number']}, "
                    f"{e['filing_date']}]"
                )
            return "\n".join(lines)

        if r.task_type == "board_path":
            chain = " — ".join(n.get("ticker") or n.get("name") for n in r.result["chain"])
            lines = [f"Board-interlock path ({r.result['hops']} hops): {chain}"]
            if r.result["via_directors"]:
                lines.append("  via directors: " + ", ".join(r.result["via_directors"]))
            return "\n".join(lines)

        if r.task_type == "coalition":
            lines = [
                f"Activist coalition around {r.anchor}: "
                f"{r.result['member_count']} members, "
                f"~{r.result['diameter_hops']} hops diameter."
            ]
            lines.append("  Members: " + ", ".join(r.result["members"]))
            return "\n".join(lines)

        if r.task_type == "ownership_snapshot":
            res = r.result
            control = "yes" if res["has_verified_control_edge"] else "no"
            lines = [
                f"Ownership snapshot — {r.anchor}: "
                f"{res['beneficial_owner_count']} beneficial owners, "
                f"{res['director_count']} directors, {res['officer_count']} officers; "
                f"verified >=50% control edge: {control}.",
                "  Top holders:",
            ]
            for h in res["top_holders"]:
                pct = f"{h['percent_of_class']:.1f}%" if h.get("percent_of_class") else "—"
                lines.append(
                    f"    - {h['owner']} ({h.get('filing_type') or '?'}, {pct}"
                    + (f", {h['control_class']}" if h.get("control_class") else "")
                    + ")"
                )
            return "\n".join(lines)

        return str(r.to_dict())
