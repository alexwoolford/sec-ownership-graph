# Demo runbook — walking the graph in Bloom

*A six-act visual walkthrough of `secgraph` using the committed perspective. Roughly 18 minutes;
Acts 1, 3 and 6 alone make a strong 8-minute version. Every figure below was re-derived from the
live graph, and each act states the query that reproduces it.*

**Who this is for:** anyone who needs to *see* the ownership network before trusting a tool that
queries it — governance and proxy teams, credit and counterparty risk, or an engineering audience
evaluating whether a graph database earns its place. The
[MCP walkthrough](demo_script_governance_desk.md) covers the same graph in question-and-answer form;
this one is for exploration.

**Setup:** import [`bloom/SEC Ownership Graph - governance desk.json`](../bloom/SEC%20Ownership%20Graph%20-%20governance%20desk.json)
via Bloom's perspective drawer (**Import perspective**), pointed at database `secgraph`. Build
details are in [`bloom_perspective_spec.md`](bloom_perspective_spec.md).

---

## The editorial rule for this document

> **Every claim here is readable off the graph.** Where a connection is notable, show the connection
> and stop — name the edge, the filing type and the date, and let the audience draw its own
> conclusion. No characterization of any person or company, and no facts sourced from outside the
> SEC filings. If you extend this runbook, hold that line: the demo's credibility rests on the
> audience being able to verify every sentence against a filing.

---

## Before you start — four things

**1. `HOLDS` is visible, and that is a standing hazard.** The perspective hides `OFFICER_OF` and
`TEN_PCT_OWNER_OF` but **not** `HOLDS`, which has 6.7M edges and a maximum of 5,643 distinct issuers
on a single manager. See [`bloom_perspective_spec.md` §3](bloom_perspective_spec.md) for why this is
a known, documented deviation.

> **The one rule for the whole demo: never right-click → Expand a green `InstitutionalManager`
> node.** All four `Company` Scene Actions are degree-guarded (`m.holds_count < 500`) and the T6
> search is too. Manual expand is not guarded, and it will hang the browser mid-demo.

**2. Clear the scene between acts.** Click the canvas, `Cmd+A`, `Cmd+H`. Bloom persists scene state
across reloads, so a leftover 500-node scene will be waiting next time.

**3. Say the caveat before someone else finds it.** There is no prediction in this graph. The alpha
question was tested and came back null — 13Ds are public the moment they land. Leading with that
buys credibility for everything after it.

**4. Ownership percentages are last-known, not current.** Schedule 13D carries no exit obligation
below 5%, so a stake shown at 57.6% means "57.6% as of that filing date". Read the year off the edge
every time.

**If you see a warning triangle on a Scene Action, ignore it.** `Expand board interlocks` and
`Show directors` carry `hasCypherErrors: true` in the exported file — that is Bloom's stored lint
state from export time, not a runtime failure. Both were executed against the live graph and return
rows.

---

## Act 1 — one company, three layers

**Type:** `TRUMP MEDIA` → choose **Full-text search**

One blue node: `DJT`. The inspector shows `ticker DJT`, `sector Services`, `cik 0001849635`,
`size_source dera_assets`, `held_by_count 499`.

### 1a. Who owns it

Right-click DJT → **Scene actions → Show 13D/G filers** — 14 filers fan out.

| Filer | Form | Stake | Filed |
| --- | --- | --- | --- |
| TRUMP DONALD J | 13D | **57.6%** | 2024-04-01 |
| ARC Global Investments II LLC | 13D | 17.8% | 2021-09-24 |
| K2 Principal Fund · Radcliffe Capital | 13G | — | 2021-09-03 |
| Boothbay · ATW SPAC · Saba · D. E. Shaw · Highbridge | 13G | — | 2021-09-08 → 09-24 |
| Lighthouse Investment Partners | 13G | — | 2021-10-08 |
| United Atlantic Ventures · Jane Street · Vanguard | 13G | — | 2024-09 → 2025-10 |

**What to point out.** Two different populations sit inside one relationship type, separated by form
and by date. One 13D at 57.6% is a control position. Eight 13Gs filed within five weeks of September
2021 are a different phenomenon: that is the window around Digital World Acquisition Corp, the SPAC
that later became this registrant. A filings search returns 14 documents. The graph lets you
partition them on `filing_type` and `filing_date` without reading any of them.

*Reproduce:* `MATCH (b:BeneficialOwner)-[e:BENEFICIAL_OWNER_OF]->(c:Company {ticker:'DJT'}) RETURN b.name, e.filing_type, e.percent_of_class, e.filing_date ORDER BY e.filing_date`

### 1b. Who runs it

Right-click DJT → **Scene actions → Show directors** — 11 amber person-nodes.

Patel Kashyap · Lighthizer Robert · McMahon Linda E. · Bernhardt David Longly · Nunes Devin G. ·
EPSHTEYN BORIS · Trump Donald J. JR · Swider Eric · Green W. Kyle · Holding George Edward Bell ·
O'Rourke Meredith Michelle

**What to point out.** These names come from Form 3/4/5 insider filings keyed on CIK — nothing about
them is annotated in the data, and the graph makes no claim about who they are. The structural point
for a finance audience is that **governance sits in the director layer, and the director layer is a
graph** — which 1c demonstrates.

### 1c. Where those directors also serve

Right-click DJT → **Scene actions → Expand board interlocks** — 7 blue companies via
`SHARES_DIRECTOR`:

**PSQH** (PSQ Holdings) · **PEW** (GrabAGun Digital Holdings) · **CLBR** (Colombier Acquisition
Corp. II) · **RTAC** (Renatus Tactical Acquisition) · **BLUW** (Blue Water Acquisition) ·
**TVA** (Texas Ventures Acquisition III) · **MCGA** (Yorkville Acquisition)

Two directors account for all seven:

| Director | Also a director of |
| --- | --- |
| **Nunes Devin G.** | RTAC, BLUW, TVA, MCGA |
| **Trump Donald J. JR** | CLBR, PEW, PSQH |
| Swider Eric | RTAC |

Right-click **Trump Donald J. JR** → **Scene actions → Other boards this person sits on** to show it
from the person's side.

**What to point out.** Neither bridge appears in any single filing — each is the intersection of Form
4 filings across eight registrants. **This is the shape to read: the named bridging director, never
the mere existence of a connection.** Every well-connected pair in this graph links within four
hops, so "are these two companies connected?" is always yes and always uninformative. *Who* connects
them is the finding.

*Reproduce:* `MATCH (i:Insider)-[:DIRECTOR_OF]->(:Company {ticker:'DJT'}) MATCH (i)-[:DIRECTOR_OF]->(o:Company) WHERE o.ticker <> 'DJT' RETURN i.name, collect(DISTINCT o.ticker)`

---

## Act 2 — the shape of a portfolio

**Type:** `CoreWeave` → choose the structured suggestion **Company name (equals): CoreWeave, Inc.**

Right-click `CRWV` → **Scene actions → Show top holders (degree-guarded)**

**9 managers across 20 `HOLDS` edges.** The action caps at `LIMIT 20`, and NVIDIA occupies three of
those rows because 13F is quarterly — the same manager reports once per period. Without the limit,
the degree-guarded pattern matches 524 managers over 1,015 edges, so the count only means something
when you state the cap.

Captions carry `holds_count`:

| Manager | `holds_count` | Largest reported position |
| --- | --- | --- |
| **NVIDIA CORP** | **11** | $3.96B |
| COATUE MANAGEMENT LLC | 94 | $2.90B |
| Proficio Capital Partners LLC | 337 | $2.44B |
| Situational Awareness LP | 39 | $0.56B |

Then show NVIDIA's entire disclosed book — 11 distinct issuers, newest quarter per name:

**INTC $9.48B · CRWV $3.96B · SNPS $2.26B · COHR $1.86B · NOK $1.34B · ARM $0.18B · APLD $0.18B ·
NBIS $0.13B · RXRX $0.04B · WRD $0.02B · GENB $0.01B**

**What to point out.** `holds_count = 11` is the observation. A diversified manager holds hundreds of
names; this book holds eleven, and the composition overlaps NVIDIA's own commercial relationships —
CoreWeave rents out its GPUs, Applied Digital and Nebius operate datacenters, Synopsys and Coherent
sit in its supply chain. Contrast that with Coatue at 94 and Proficio at 337 in the same scene. **The
degree property tells you what kind of holder you are looking at before you read a single position.**

**Honest limits.** `value_usd` is *share* holdings only — options live in `put_notional_usd` and
`call_notional_usd`, because a market-making firm once surfaced as a $41B utility's largest holder
while owning 67 shares. And 13F is quarterly, which is why 20 edges resolve to 9 managers.

*Reproduce:* `MATCH (m:InstitutionalManager {name:'NVIDIA CORP'})-[h:HOLDS]->(c:Company) RETURN c.ticker, h.value_usd, h.report_period ORDER BY h.value_usd DESC`

---

## Act 3 — a stake and a seat are two different facts

**Type:** `Who can move this company at tier 25 and size 1000000000` → Enter

**79 rows match; the search renders the top 25.** The conjunction is the point: a Federal Reserve
presumption-tier stake (Schedule 13D) **and** a current board seat (Form 3/4/5). Two filing types,
one finding.

| Holder | Company | Stake | Size |
| --- | --- | --- | --- |
| BUFFETT WARREN E | BRK-B | 37.0% | $1,222B |
| HOLDING FRANK B JR | FCNCA | 29.4% | $230B |
| **DEUTSCHE TELEKOM AG** | **TMUS** | **74.3%** | $219B |
| Liberty Broadband Corp | CHTR | 26.1% | $154B |
| WARREN KELCY L | ET | 50.0% | $141B |
| WILLIAMS RANDA DUNCAN | EPD | 36.9% | $78B |
| BERKLEY WILLIAM R | WRB | 25.5% | $44B |
| ERGEN CHARLES W | ECHO | 51.8% | $43B |

**What to point out.** Lead with **T-Mobile US**: the third-largest US wireless carrier has a single
disclosed holder at 74.3%, and that holder also sits on the board. Both halves are separately
sourced and separately verifiable. Then **Liberty Broadband → Charter**, which sets up Act 4.

### The counter-example that teaches the query

**Donald Trump does not appear in this list**, despite the 57.6% stake in DJT from Act 1 — a tier-50
holding, above the Fed's own control presumption. The reason is a single property:
**`board_seat = false`**. The stake is there; the seat is not.

> Ask the room what a screen built as "holders above 50%" would have returned. It would have put that
> row and the Deutsche Telekom row in the same bucket. The graph keeps the stake and the seat as
> **separate, independently sourced facts**, so you can ask for either or for the conjunction. That
> is the difference between a filter and a model.

**Say this out loud:** `percent_of_class` is **not** voting power. Several names here are dual-class
— Berkshire, the Liberty complex, Sea Limited. Economic ownership and voting control are different
questions and this field answers only the first.

*Reproduce:* `MATCH (b:BeneficialOwner)-[r:INFLUENCES]->(c:Company) WHERE r.board_seat AND r.tier >= 25 AND c.size_usd >= 1e9 RETURN count(*)`

---

## Act 4 — control that runs through a holding company

**Type:** `Transitive control chain` → Enter

25 paths, each `BeneficialOwner → CONTROLS → Company → SAME_ENTITY_AS → BeneficialOwner → CONTROLS →
Company`. Two worth narrating:

| Chain | Filed |
| --- | --- |
| **Embraer Aircraft Holding =88%⇒ EMBJ =88%⇒ EVEX** | 2024-07-02 / 2024-09-09 |
| **CONTRAN CORP =94%⇒ VHI =68%⇒ NL** | 2011-06-29 / 1994-11-14 |

**What to point out — the technical heart of the demo.** A holding company that is both *controlled*
and *controlling* exists here as **two nodes sharing one CIK**: a `Company` and a `BeneficialOwner`.
A variable-length Cypher pattern cannot hop between nodes on a property equality, so without the
materialized `SAME_ENTITY_AS` bridge, `CONTROLS*` stops dead at one hop. That edge is what makes the
chain traversable at all.

This is also the honest version of the "SQL can't do this" claim. **A warehouse can reach the same
answer with a recursive CTE or an application-side loop — don't overclaim, you'll lose the engineers
in the room.** The advantage is one declarative indexed pattern executed next to the data, with the
traversal depth decided by the data rather than written into the query.

**State the limits immediately.** The largest leaf in any verified chain is **TNK at $1.44B** — every
one of these is a small- or micro-cap structure, so this is a governance and minority-holder-risk
screen, not a large-cap feature. And roughly half of all `CONTROLS` edges predate 2020: Goldcorp
still shows as owning 75% of Wheaton from a **2006** filing, and Goldcorp was absorbed by Newmont in
2019. Show `filing_date` on the edge, every time.

*Reproduce:* `MATCH (o:BeneficialOwner)-[:CONTROLS]->(m:Company)-[:SAME_ENTITY_AS]->(:BeneficialOwner)-[:CONTROLS]->(t:Company) RETURN t.ticker, t.size_usd ORDER BY t.size_usd DESC LIMIT 3`

---

## Act 5 — why one hop isn't enough

**Type:** `Activist coalition around ICAHN CARL C` → Enter

**24 distinct members** in the connected component of filers who co-target the same issuers:
ICAHN CARL C · GLENVIEW CAPITAL · GAMCO INVESTORS · Bulldog Investors (three filing entities) ·
Saba Capital · Karpus Management · CANNELL CAPITAL · B. Riley Financial · Albion River ·
DEASON DARWIN · DOLAN CHARLES F · DOLAN JAMES LAWRENCE · DIGIRAD CORP · 180 DEGREE CAPITAL ·
CASCADE INVESTMENT · and others.

Now clear the scene, search **BeneficialOwner name (equals): ICAHN CARL C**, right-click →
**Scene actions → Co-targeting peers**. You get **three**: GLENVIEW CAPITAL, GAMCO INVESTORS,
DEASON DARWIN.

**What to point out.** One hop gives you three names. The four-hop traversal gives you twenty-four,
and the component has a diameter — a shape. **A warehouse can tell you who co-filed with a given
filer on one issuer. It cannot hand you the cluster, because the cluster is emergent: nobody decided
it exists, and its size is not known before you traverse.** That gap between 3 and 24 is the argument
for graph, in one click.

**The trap that makes this work.** The query filters custodians with
`NOT coalesce(o.is_custodial, false)`. Without the `coalesce` it returns **zero** paths instead of
1,928 — `is_custodial` is absent on all but 13 of 17,235 filers, and `NONE(... WHERE null)` evaluates
to null rather than true. The query reports success and shows nothing. Worth mentioning to a
technical audience: this is the failure mode that silently ruins ownership analytics.

Custodians and index funds are **labelled, not deleted** — excluded at query time so the co-filing
fact survives in the graph and the precision choice stays auditable. Fixing one leak in that scrub
(the substring `RBC` does not match `ROYAL BANK OF CANADA`) once moved a published coalition figure
from 22 to 16.

*Reproduce:* `MATCH p=(s:BeneficialOwner {name:'ICAHN CARL C'})-[:CO_TARGETS*1..4]-(m:BeneficialOwner) WHERE ALL(n IN nodes(p) WHERE NOT coalesce(n.is_custodial,false)) RETURN count(DISTINCT m)`

---

## Act 6 — Louvain over board interlocks

**Type:** `Interlock cluster 0000004904` → Enter

A dense mesh of `SHARES_DIRECTOR` edges. `LIMIT 100` renders roughly 90 node slots — use
`0000004904` for something that fits one screen, `0000001750` for the largest cluster.

Five communities from one Louvain run, with **cluster membership** (not render count):

| Anchor | Members | Largest members |
| --- | --- | --- |
| `0000001750` (AIR) | **747** | JPM, BAC, C, WFC, GS, MS, AMZN, PRU, MET, USB, COF, MSFT |
| `0000010795` (BDX) | **559** | GILD, REGN, BIIB, VRTX, TEVA |
| `0000002230` (ADX) | **429** | BRK-B, GOOGL, META, TSLA, CHTR |
| `0000003545` (ALCO) | **238** | AVGO, ADI, AMAT, LHX, GFS |
| `0000004904` (AEP) | **190** | AIG, AEP, CNA, FE, SPG, HOOD |

**What to point out.** Nothing told this algorithm what a bank is. There is no sector field in the
input — the only input is *which companies share a human director*. Out falls a money-centre banking
group, then pharma, then large-cap tech, then semiconductors. Directors are recruited from within
industries, and the board network carries that shape.

Then undercut it honestly: **measured sector purity is only 34–52%.** These clusters look sectoral at
the top and are not purely sectoral underneath. They are structural communities that correlate with
sector — a more defensible claim than "we rediscovered GICS", and the one the data supports.

*Reproduce:* `MATCH (c:Company {interlock_community_anchor:'0000001750'}) RETURN count(*)`

### The kicker: centrality does not track size

**Type:** `Broker boards above 55000` → Enter

| Company | Betweenness | Interlocks | Size |
| --- | --- | --- | --- |
| **XOS, Inc.** | 156,509 | 18 | **$0.1B** |
| INTERNATIONAL FLAVORS | 136,074 | 32 | $25.5B |
| PITNEY BOWES | 135,914 | 25 | $3.2B |
| Fortinet | 132,392 | 19 | $10.4B |
| ESTÉE LAUDER | 122,073 | 17 | $19.6B |
| **CISCO SYSTEMS** | 91,770 | 31 | **$123.4B** |
| CARDINAL HEALTH | 84,957 | 25 | $58.1B |

**What to point out.** The most structurally central board in this graph belongs to a **$0.1B**
company, ahead of Cisco at **$123B**. Boards that bridge otherwise-separate clusters are often
*small* companies whose directors also sit on large ones. Ranking by market cap would never surface
it. **Set that expectation before you show the screen, or the room will assume the tool is broken.**

Absence is meaningful too: only **4,532 of 8,000** companies have a betweenness value at all. The
other 3,468 have no board interlock and were never measured — different from the **1,129** carrying a
genuine measured `0.0`, meaning "in the network, brokers nothing".

*Reproduce:* `MATCH (c:Company) WHERE c.interlock_betweenness IS NOT NULL RETURN count(*)`

---

## Closing — the limits, said out loud

These are what make the rest credible, and a finance audience will test them.

- **No prediction.** Tested directly, came back null. A structural and temporal map, not a signal.
- **13D percentages are last-known.** No exit obligation below 5%; ~half the control edges predate
  2020.
- **`DIRECTOR_OF` is a transaction log, not a roster.** It lists 24 directors for Charter and 27 for
  Vertiv (~2× reality) while under-counting Berkshire at 9. *"Does this holder sit on the board"* is
  answerable; *"does this holder control the board"* is **not**.
- **Size is a threshold, not a market cap.** `size_usd` is
  `coalesce(total_assets_usd, institutional_value_usd)` and `size_source` tells you which applied.
  Total assets are **not comparable across sectors** — a bank's assets *are* its balance sheet. 17%
  of companies have neither figure.
- **Centrality does not track size**, per Act 6.
- **Activist screens trade recall for precision.** A curated franchise list, so a first-time activist
  is missed by design.
- **CIK-keyed only.** Understates family and affiliate structure rather than inventing links through
  fuzzy name matching. US SEC registrants with a ticker.
- **Scope is SEC ownership filings.** This graph contains no private, social or informal
  relationships — only what was filed with the Commission. If a connection isn't in a filing, it
  isn't here.

---

## Appendix — search phrases and Scene Actions

The perspective stores **no default parameter values**, so type them. Phrases verbatim:

| Search phrase | Parameters |
| --- | --- |
| `Who can move this company at tier $minTier and size $minSize` | 25, 1000000000 |
| `Transitive control chain` | — |
| `Activist coalition around $filerName` | ICAHN CARL C |
| `Broker boards above $minBetweenness` | 55000 |
| `Interlock cluster $anchor` | 0000004904 or 0000001750 |
| `Institutional holders of $ticker under $maxHoldings holdings` | CRWV or MNRO, 500 |

Scene Action names verbatim, with the category each is scoped to:

| Scene Action | On | Verified result |
| --- | --- | --- |
| `Expand board interlocks` | Company | 7 rows (DJT) |
| `Show directors` | Company | 11 rows (DJT) |
| `Show 13D/G filers` | Company | 14 rows (DJT) |
| `Show top holders (degree-guarded)` | Company | 20 rows (CRWV) |
| `This filer's targets` | BeneficialOwner | — |
| `Co-targeting peers` | BeneficialOwner | 3 (ICAHN CARL C) |
| `Other boards this person sits on` | Insider | 3 (Trump Donald J. JR) |

A `Company` node offers exactly four actions, a `BeneficialOwner` two, an `Insider` one.

---

## Provenance

Every figure in this runbook was re-derived from the live `secgraph` database on 2026-08-06, against
the build stamped in `results/secgraph_freshness.json`. Counts move when the graph is rebuilt — run
the *Reproduce* query in each act rather than trusting the number here, and prefer
`python scripts/validate_graph_schema.py --database secgraph` over any figure written down.

Perspective build brief: [`bloom_perspective_spec.md`](bloom_perspective_spec.md) ·
MCP walkthrough: [`demo_script_governance_desk.md`](demo_script_governance_desk.md) ·
Property sources: [`data_sources_and_forms.md`](data_sources_and_forms.md) ·
Field reference: [`graph_schema.md`](graph_schema.md)
