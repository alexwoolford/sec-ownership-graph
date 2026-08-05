# Bloom perspective specification — `secgraph`

*A build brief. Everything here has been verified against the live `secgraph` database; every Cypher
query in this document was executed and its row count and latency recorded. Hand this to an agent
with Bloom access (Claude Desktop + browser plugin) and it should be buildable without coming back
with questions.*

**Target:** Neo4j Bloom 2.21.x against database `secgraph` (Neo4j 2026.05 Enterprise + GDS).

---

## 0. Read this first — three things that will otherwise waste your time

**1. Scenes cannot be scripted, and should not be treated as deliverables.** There is no
`storeScene` procedure — verified, `SHOW PROCEDURES` matching `scene` returns nothing. A
`_Bloom_Scene_` node stores `nodesv2` / `relationshipsv2` (pinned element lists) plus `zoomLevel`
and `panCoordinates`. That is instance state tied to specific node ids and pixel positions, so a
"committed scene" would break on any rebuild. **The reproducible artifacts are the perspective, its
categories/style rules, and its saved Cypher (`templates`).** Where this document says "scene", build
it as a saved search that *reconstructs* the view.

**2. One relationship type will hang the browser.** `HOLDS` has **6,742,154** edges, out-degree p50
**78** and max **5,643** distinct issuers on one manager. Expanding a large `InstitutionalManager`
node interactively is not slow — it is fatal. `HOLDS` must be in `hiddenRelationshipTypes`, and
every query touching it must filter on `holds_count` first. This is why that property exists.

**3. One Cypher idiom silently returns nothing.** Verified on the live graph:

```
WHERE NONE(n IN nodes(p) WHERE n.is_custodial)                    ->     0 paths
WHERE NONE(n IN nodes(p) WHERE coalesce(n.is_custodial, false))   -> 1,928 paths
```

`is_custodial` is absent on all but 13 of 17,235 filers, and `NONE(... WHERE null)` evaluates to
**null, not true**, so the naive form filters out everything and reports success. Never write a
predicate over a nullable property without `coalesce`.

---

## 1. What is in the graph

Four node labels, ten relationship types. Live counts:

| Label | Count | | Relationship | Count |
| --- | --- | --- | --- | --- |
| `Company` | 8,000 | | `HOLDS` | 6,742,154 |
| `Insider` | 78,870 | | `BENEFICIAL_OWNER_OF` | 62,705 |
| `BeneficialOwner` | 17,235 | | `DIRECTOR_OF` | 55,507 |
| `InstitutionalManager` | 9,137 | | `OFFICER_OF` | 43,091 |
| | | | `SHARES_DIRECTOR` | 14,266 |
| | | | `TEN_PCT_OWNER_OF` | 14,769 |
| | | | `INFLUENCES` | 5,396 |
| | | | `CONTROLS` | 1,045 |
| | | | `CO_TARGETS` | 203 |
| | | | `SAME_ENTITY_AS` | 94 |

Full field reference: [`graph_schema.md`](graph_schema.md) (generated). What each SEC form means and
where every property came from: [`data_sources_and_forms.md`](data_sources_and_forms.md).

Run `python scripts/validate_graph_schema.py --database secgraph` before building. It exits non-zero
if any declared label, relationship, constraint or index is missing, and prints live coverage per
property — so you know what will actually render before you style it.

---

## 2. Search: the entry point

Bloom's search bar resolves typed text through a **full-text index**. It is called
`entity_name_fulltext` and covers `name` on all four labels — one index, because a user typing
"Icahn" does not know whether that is a person, a filer, or a company. Verified: "icahn" returns
`ICAHN BRETT` (Insider), `ICAHN CARL C` (BeneficialOwner), `ICAHN CARL C` (Insider).

```cypher
CALL db.index.fulltext.queryNodes('entity_name_fulltext', $searchTerm)
YIELD node, score
RETURN node ORDER BY score DESC LIMIT 25
```

If search feels broken, check the index is `ONLINE` — a `POPULATING` full-text index returns partial
results without erroring.

---

## 3. Categories, icons and captions

One category per label. Icons are suggestions from Bloom's built-in set; substitute freely, but keep
**people visually distinct from institutions** — the demo's strongest moment is a *person* holding a
stake while sitting on a board, and that has to read at a glance.

| Category | Colour | Icon | Caption | Secondary caption |
| --- | --- | --- | --- | --- |
| `Company` | `#57C7E3` (blue) | `building` / `office` | `ticker`, falling back to `name` | `sector` |
| `BeneficialOwner` | `#F16667` (red) | `briefcase` | `name` | `resolved` |
| `Insider` | `#FFE081` (amber) | `user` | `name` | — |
| `InstitutionalManager` | `#8DCC93` (green) | `bank` | `name` | `holds_count` |

**Why red for `BeneficialOwner`:** these are the 13D/13G filers — the actors in every activist story.
They should draw the eye. **Why amber for `Insider`:** they are natural persons, and the contrast
with institutional green/blue is the point.

Set `hideUncategorisedData: true`.

### Hidden relationship types

`hiddenRelationshipTypes: ["HOLDS"]`

Non-negotiable — see §0.2. Also consider hiding `OFFICER_OF` (43,091) and `TEN_PCT_OWNER_OF` by
default: they are true and useful but they crowd every board view. `DIRECTOR_OF` stays visible; it is
half of the two-limb story.

---

## 4. Style rules — this is where the graph work pays off

Style rules key off properties that exist because of this demo's analytics. All thresholds below are
**measured percentiles**, not guesses.

### 4a. Node size → structural importance (`interlock_betweenness`)

How often a board sits on the shortest path between two others. Live distribution:

| p50 | p90 | p99 | max |
| --- | --- | --- | --- |
| 4,161 | 22,510 | 55,368 | 156,509 (XOS) |

| Rule | Condition | Size |
| --- | --- | --- |
| Broker | `interlock_betweenness >= 55000` | largest |
| Connector | `>= 22500` | large |
| Linked | `> 0` | medium |
| Isolated / unmeasured | property absent | small |

> **Set expectations before the demo: centrality does NOT track size.** Top brokers are mixed-cap —
> **XOS $0.1B** (18 genuine interlock neighbours) sits alongside **IFF $25.5B** and **PBI $3.2B**.
> Boards that bridge otherwise-separate clusters are often *small* companies whose directors also sit
> on larger ones. That is a more interesting finding than "big companies are central", and it is what
> the data says. If someone needs recognizable names, band by `size_usd` — do not imply centrality
> implies scale.
>
> Absence is meaningful: **4,532 of 8,000** companies have a betweenness value at all. The other
> 3,468 have no board interlock, so they were never measured — distinct from the **1,129** that carry
> a genuine measured `0.0` ("in the graph, brokers nothing").

### 4b. Node colour → interlock cluster (`interlock_community_anchor`)

Louvain community, identified by the **lowest CIK in the cluster** rather than the raw
`interlock_community` integer. Use the anchor: raw ids are arbitrary and reshuffle between builds,
while the anchor is stable (verified 100% of members map to the same anchor across runs) and names
the cluster with something lookup-able.

The five clusters worth distinct colours:

| Anchor | Ticker | Members |
| --- | --- | --- |
| `0000001750` | AIR | 747 |
| `0000010795` | BDX | 559 |
| `0000002230` | ADX | 429 |
| `0000003545` | ALCO | 238 |
| `0000004904` | AEP | 190 |

Everything else: one neutral colour. **Do not colour by `interlock_community`** — same cluster,
unstable label.

### 4c. Border / badge → size measure (`size_usd` + `size_source`)

`size_usd` is `coalesce(total_assets_usd, institutional_value_usd)`. Distribution: p50 **$0.34B**,
p90 **$14.2B**, max **$4,425B** (JPMorgan).

**`size_source` must be visible** — as a border style, a badge, or at minimum a caption. The two
inputs are different quantities:

- `dera_assets` — a filed balance-sheet total (63% of the universe)
- `institutional_13f` — 13F free float (75%)

A $43B balance sheet and $43B of float are not the same claim, and a viewer who cannot tell them
apart will draw a wrong conclusion. **17%** of companies have neither and should render unstyled.

### 4d. `INFLUENCES` relationship thickness → presumption tier

Tier `10 / 15 / 25 / 50`, from 12 CFR 225.2(e). Thicker = higher tier. Dashed where
`board_seat = false`, solid where true — the solid edges are the two-limb finding.

---

## 5. Saved Cypher (`templates`)

**Every query below was executed against the live graph.** Row counts and latencies are real. Keep
the parameter names — Bloom prompts on them.

### T1 — "Who can actually move this company?" *(the headline)*

**Purpose.** Issuers where one holder has a Fed-presumption-tier stake **and** currently sits on the
board. Its force is the **conjunction of two independent filing types**: the stake from a Schedule
13D, the board seat from Form 3/4/5. A screener sells you either list; the pairing is the finding,
and it is the join a single-table query cannot do.

**Verified: 25 rows, 104 ms.**

```cypher
MATCH (b:BeneficialOwner)-[r:INFLUENCES]->(c:Company)
WHERE r.board_seat AND r.tier >= $minTier AND c.size_usd >= $minSize
RETURN b, r, c
ORDER BY c.size_usd DESC
LIMIT 25
```

Defaults: `minTier = 25`, `minSize = 1000000000`.

**Why the filters exist.** `minTier >= 25` is the Federal Reserve's own control presumption — the
threshold is the regulator's, not ours. `minSize` keeps results recognizable rather than nano-cap
noise; issuers with **no** size figure are excluded rather than ranked, because an unsized row cannot
honestly claim materiality. Say out loud that `percent_of_class` is **not voting power** — several top
names are dual-class (Berkshire, the Liberty complex, Sea).

### T2 — Transitive control chain

**Purpose.** Control that runs through an intermediate holding company. This is the query that
cannot be written as one SQL statement — it is a variable-depth traversal to a data-determined depth.

**Verified: 25 rows, 44 ms.**

```cypher
MATCH p = (root:BeneficialOwner)-[:CONTROLS]->(:Company)
          -[:SAME_ENTITY_AS]->(:BeneficialOwner)-[:CONTROLS]->(:Company)
RETURN p
LIMIT 25
```

**Why `SAME_ENTITY_AS` is in the pattern.** A holding company that is both controlled and
controlling exists as *two* nodes — a `Company` and a `BeneficialOwner` sharing one CIK. A
variable-length pattern **cannot jump between nodes on a property equality**, so without that
materialized bridge `CONTROLS*` stops at one hop. Its presence is what makes the chain traversable.

**State the limit:** `CONTROLS` means a **verified ≥50%** 13D stake, and 13D carries no exit
obligation below 5% — so roughly half these edges predate 2020 and are *last-known*, not current.
Goldcorp still appears owning 75% of Wheaton from a **2006** filing; Goldcorp was absorbed by Newmont
in 2019. Show `filing_date` on the edge.

### T3 — Activist coalition around a filer

**Purpose.** The connected component of filers who co-target the same issuers. Size and diameter are
*emergent* — a warehouse can tell you who co-filed on one name, but cannot hand you the cluster.

**Verified: 43 rows, 60 ms.**

```cypher
MATCH p = (seed:BeneficialOwner {name: $filerName})-[:CO_TARGETS*1..4]-(m:BeneficialOwner)
WHERE ALL(n IN nodes(p) WHERE NOT coalesce(n.is_custodial, false))
RETURN p
LIMIT 50
```

Default: `filerName = 'ICAHN CARL C'`.

**The `coalesce` is load-bearing and not optional.** Without it this query returns **zero** paths
instead of 1,928 (§0.3). Custodians and index hubs (State Street, FMR, BlackRock, JPMorgan) co-file
on everything and would bridge unrelated activists into one fake coalition; they are **labelled, not
deleted**, so the co-filing fact survives and the exclusion stays auditable. Fixing a leak in this
scrub once moved a published coalition figure from 22 to 16.

### T4 — Broker boards *(the GDS view)*

**Purpose.** The boards with the highest betweenness and their immediate interlock neighbourhood —
who bridges otherwise-unconnected clusters. No screener answers this; it needs the whole graph.

**Verified: 60 rows, 32 ms.**

```cypher
MATCH (c:Company)-[r:SHARES_DIRECTOR]-(o:Company)
WHERE c.interlock_betweenness >= $minBetweenness
RETURN c, r, o
LIMIT 60
```

Default: `minBetweenness = 55000` (the p99).

**Point out that the answer is mixed-cap**, per §4a. And that `SHARES_DIRECTOR` already excludes fund
vehicles and non-human "directors" — without those scrubs a single BlackRock closed-end-fund complex
manufactures a "director on 36 boards".

### T5 — One interlock cluster, whole

**Purpose.** Show a Louvain community as a community — the visual payoff of the GDS work.

**Verified: 100 rows, 42 ms.**

```cypher
MATCH (c:Company {interlock_community_anchor: $anchor})-[r:SHARES_DIRECTOR]-(o:Company)
WHERE o.interlock_community_anchor = $anchor
RETURN c, r, o
LIMIT 100
```

Default: `anchor = '0000004904'` (AEP, 190 members — big enough to look like a community, small
enough to render). For a larger view use `0000001750` (AIR, 747) but raise the limit deliberately.

**Worth saying:** these clusters are **structural, not sectoral** — measured sector purity is
typically 34–52%, so they are not a sector restatement in disguise.

### T6 — Institutional holders, degree-guarded

**Purpose.** Who holds an issuer, safely.

**Verified: 20 rows, 46 ms.**

```cypher
MATCH (m:InstitutionalManager)-[h:HOLDS]->(c:Company {ticker: $ticker})
WHERE m.holds_count < $maxHoldings
RETURN m, h, c
ORDER BY h.value_usd DESC
LIMIT 20
```

Defaults: `ticker = 'MNRO'`, `maxHoldings = 500`.

**The `holds_count` filter is a safety rail, not a nicety.** It excludes index giants whose expansion
would pull thousands of nodes. Note also that `value_usd` is *share* holdings only — options live in
`put_notional_usd` / `call_notional_usd`, because a market-making firm once appeared as a $41B
utility's largest holder while owning **67 shares** (the rest was puts and calls).

---

## 6. Scene Actions

Bloom Scene Actions run Cypher from a selected node. Each needs a guard, for the reasons above.

| Action | On | Cypher | Guard |
| --- | --- | --- | --- |
| **Expand board interlocks** | `Company` | `MATCH (c)-[r:SHARES_DIRECTOR]-(o:Company) RETURN r, o LIMIT 40` | `LIMIT` — max interlock degree is 45, so this is safe |
| **Show directors** | `Company` | `MATCH (c)<-[r:DIRECTOR_OF]-(i:Insider) RETURN r, i LIMIT 30` | `LIMIT`; a transaction log over-counts (24 for Charter) |
| **Show 13D/G filers** | `Company` | `MATCH (c)<-[r:BENEFICIAL_OWNER_OF]-(b) RETURN r, b LIMIT 25` | `LIMIT` |
| **Show top holders** | `Company` | T6 body, `$ticker` from the node | **`holds_count` filter required** |
| **This filer's targets** | `BeneficialOwner` | `MATCH (b)-[r:BENEFICIAL_OWNER_OF]->(c:Company) RETURN r, c ORDER BY c.size_usd DESC LIMIT 25` | `LIMIT` + size ordering |
| **Co-targeting peers** | `BeneficialOwner` | `MATCH (b)-[r:CO_TARGETS]-(o) WHERE NOT coalesce(o.is_custodial,false) RETURN r, o` | **`coalesce` required** |
| **Other boards this person sits on** | `Insider` | `MATCH (i)-[r:DIRECTOR_OF]->(c:Company) RETURN r, c LIMIT 25` | `LIMIT` |

**Do not add a generic "expand all" action on `InstitutionalManager`.** There is no safe form of it.

---

## 7. Limits that must be visible on screen

Not fine print — this is what makes the rest credible, and a finance audience will test it.

- **No prediction.** The alpha question was tested and came back null. 13Ds are public. This is a
  structural and temporal map, not a signal.
- **13D percentages are last-known, not current.** No exit obligation below 5%; ~half the control
  edges predate 2020. Always show the filing year.
- **`DIRECTOR_OF` is a transaction log, not a roster.** It lists 24 directors for Charter and 27 for
  Vertiv (~2× reality) while under-counting Berkshire at 9. "Does this holder sit on the board" is
  answerable; "does this holder control the board" is **not**.
- **Size is a threshold, not a market cap.** Total assets are **not comparable across sectors** — a
  bank's assets *are* its balance sheet, so JPMorgan's $4.4T is not "bigger than" a $200B industrial.
  13F float understates concentrated ownership and includes ETFs.
- **Centrality does not track size** (§4a).
- **Activist screens trade recall for precision** — a curated franchise list, so a first-time
  activist is missed by design.
- **CIK-keyed only.** Understates family and affiliate structure rather than inventing links through
  fuzzy name matching. US SEC registrants with a ticker.

---

## 8. Acceptance checklist

- [ ] `python scripts/validate_graph_schema.py --database secgraph` exits 0
- [ ] Search finds `MONRO, INC.` for "monro" and three entities for "icahn"
- [ ] All four categories have distinct colour + icon; people read differently from institutions
- [ ] `HOLDS` is hidden by default
- [ ] Node size varies by `interlock_betweenness`; unmeasured companies render small, not zero-sized
- [ ] The five anchor clusters have distinct colours; nothing colours by raw `interlock_community`
- [ ] `size_source` is visible somewhere on a sized node
- [ ] All six templates run from the Bloom search bar and return rows
- [ ] T3 returns a multi-member coalition (**not empty** — if empty, the `coalesce` was dropped)
- [ ] Every Scene Action returns within a second or two
- [ ] No action can expand an `InstitutionalManager` without a `holds_count` filter

---

## 9. Provenance

Every figure in this document was measured on the live `secgraph` database on 2026-08-05, against the
build stamped in `results/secgraph_freshness.json`. Counts move when the graph is rebuilt —
re-derive rather than trusting this file, and prefer `scripts/validate_graph_schema.py` over any
number written here.

Property sources: [`data_sources_and_forms.md`](data_sources_and_forms.md) ·
Field reference: [`graph_schema.md`](graph_schema.md) ·
Walkthrough: [`demo_script_governance_desk.md`](demo_script_governance_desk.md)
