# Public Company Graph — Schema Reference

> **Auto-generated** from `schema/graph_schema.yaml`.
> Last generated: 2026-08-05 21:26 UTC
>
> Do **not** edit this file by hand. Run:
> ```bash
> python scripts/generate_schema_docs.py --execute
> ```

## Node Types

### Company

A public issuer that files with the SEC. The universe is SEC filers with a ticker (see scripts/load_company_universe.py); every ownership edge attaches to one of these. NOTE: no revenue and no true market cap. Two size measures exist, and they are different claims: `total_assets_usd` is a real balance-sheet figure from the Financial Statement Data Sets (~63% of the universe), while `institutional_value_usd` is a 13F free-float PROXY (~75%). `size_usd` coalesces them and `size_source` says which one applied. See each property, and docs/reference_architecture_secgraph.md "Honest limits".


- **Unique key:** `cik`

#### Required Properties

| Property | Type | Description |
|----------|------|-------------|
| `cik` | String | SEC Central Index Key — the hard key everything joins on |
| `name` | String | Company legal name |
| `loaded_at` | DateTime | When node was loaded |

#### Optional Properties

| Property | Type | Description |
|----------|------|-------------|
| `ticker` | String | Stock ticker symbol |
| `sector` | String | Business sector derived from the SIC code |
| `sic_code` | String | SIC industry code (from EDGAR) |
| `state_of_incorp` | String | State/country of incorporation (from EDGAR) |
| `ownership_component` | Long | WCC component id over the ownership-interlock graph (GDS) |
| `institutional_value_usd` | Float | Size PROXY, not a market cap: total 13F-reported institutional dollars in this issuer for one quarter (sum of HOLDS.value_usd). Exists so structural results can be ranked by materiality — a $95B control relationship and a $30 one are not the same finding. Three limits, all load-bearing: (1) ABSENT for ~25% of the universe (no 13F coverage), and a null means "not institutionally held", never zero; (2) measures FREE FLOAT, so it understates exactly the concentrated-ownership issuers control chains are about — conservative, never a false positive; (3) ETFs are in the universe and 13F filers report them, so SPY/QQQ rank high on other people's money. Written by materialize_materiality.py.
 |
| `institutional_value_period` | Date | The 13F report_period institutional_value_usd was summed over. Required for auditability: the figure is one quarter's snapshot, not a running total, and 13F has a 2024 coverage step-up — so the period must travel with the number.
 |
| `total_assets_usd` | Float | Total consolidated assets as reported on the balance sheet (XBRL tag "Assets", uom USD, qtrs=0, no segment/coreg breakdown), from the SEC Financial Statement Data Sets and keyed on the CIK in sub.txt. Exists because institutional_value_usd measures FREE FLOAT, which by construction understates the concentrated-ownership issuers this graph is about: EchoStar carries $60.9B of assets and is 51.8% controlled, yet has no 13F coverage at all. Limits: (1) ABSENT for ~42% of the universe — no 10-K/10-Q in the staged window covers ETFs, funds and many foreign filers, and a null means "not reported here", never zero; (2) NOT comparable across sectors — a bank's assets are its balance sheet, so JPMorgan's $4.0T is not "bigger than" a $200B industrial in any economically meaningful sense; (3) it is a point-in-time balance, not market value, and says nothing about equity value or leverage. Written by load_company_financials.py.
 |
| `total_assets_period` | Date | The XBRL ddate total_assets_usd was read from — the balance-sheet date, not the filing date. Required for auditability for the same reason as institutional_value_period: the figure is one instant, and the staged window moves with --quarters-fsds.
 |
| `total_assets_accession` | String | The SEC accession number of the 10-K/10-Q the assets figure came from, so the number is citable per the evidence-or-abstain rule. This is strictly better than institutional_value_usd, which is an aggregate over thousands of 13F edges and carries no single citation. Also the tiebreak that makes the figure reproducible: measured on 2026q1, one (cik, ddate) pair reports two different values from two accessions (an original and an amendment), so without a total order two builds over identical inputs could publish different figures. Highest accession — the later filing — wins.
 |
| `size_usd` | Float | The combined size measure used for filtering and ranking: coalesce(total_assets_usd, institutional_value_usd). Prefers the real balance sheet and falls back to 13F float, which lifts coverage from 75% (13F alone) to ~85%. Neither input is modified, so published float-based figures stay comparable. ALWAYS read size_source alongside it — a $60.9B assets figure and a $60.9B float figure are different claims. Null means neither source could size the issuer; such rows are excluded from size-filtered results and the exclusion count is reported rather than silently dropped. Written by materialize_materiality.py.
 |
| `size_source` | String | Which input produced size_usd: "dera_assets" (balance-sheet Assets) or "institutional_13f" (13F free-float proxy). Absent when size_usd is null. This is what keeps a size-ranked answer auditable — without it the two measures are indistinguishable in output despite meaning different things.
 |
| `interlock_betweenness` | Float | Betweenness centrality over the UNDIRECTED company-to-company board-interlock graph (SHARES_DIRECTOR, which already carries the human-director and fund-family scrubs) — how often this board sits on the shortest path between two others. Answers "which boards broker between otherwise-unconnected clusters", a whole-graph question no per-row screener can answer. ABSENT (never 0) for a company outside the scrubbed projection: fund vehicles are excluded by name, and a company with no human-director interlock has no score. A null means "not in the interlock graph", which is a different claim from "in it, with no brokering role" — 1,129 companies carry a measured 0.0, which is a real finding. The projection MUST be undirected: a directed one makes this a proxy for node-insertion order (node id tracks market-cap rank), which fabricated a large-cap ranking. Top brokers are genuinely mixed-cap — XOS $0.1B with 18 real interlock neighbours sits alongside IFF $25.5B — because boards that bridge separate clusters are often small companies whose directors also sit on larger ones. Band by size_usd if you need recognizable names; do not expect centrality to track size. Deterministic — every GDS call is pinned to concurrency=1, since the parallel default is not reproducible. Written by materialize_interlock_features.py.
 |
| `interlock_community` | Long | Raw Louvain community id over the same scrubbed interlock projection. NOT PORTABLE across builds: the integer is arbitrary and carries no meaning beyond "these nodes are in the same cluster in THIS build". Stable within a build only because concurrency=1 is pinned; the unpinned default reassigned 52.4% of nodes between two identical runs. For anything that must survive a rebuild — a saved query, a visualization colour, a comparison between two graphs — use interlock_community_anchor instead. Kept for reference and debugging. Written by materialize_interlock_features.py.
 |
| `interlock_community_anchor` | String | The lowest CIK in this node's Louvain cluster — a STABLE handle for the community, derived from membership rather than from GDS iteration order. This is the property to group, colour or compare by: verified 100% of members map to the same anchor across independent runs, where the raw id does not. Also names the cluster with something a human can look up, and reuses CIK (the repo's hard key) so it adds no new join axis. Absent wherever interlock_community is absent. Written by materialize_interlock_features.py.
 |
| `interlock_community_size` | Long | Number of companies in this node's interlock cluster. Travels with the anchor because a 2-company cluster and an 809-company cluster are not the same finding, and a consumer filtering for meaningful clusters should not have to re-aggregate to find out. Written by materialize_interlock_features.py.
 |
| `interlock_degree` | Long | Count of distinct companies sharing at least one human director with this one — the degree over the materialized SHARES_DIRECTOR edge, so the number matches the edges actually traversable from the node. Absent for a company with no interlock. Written by materialize_interlock_features.py.
 |
| `held_by_count` | Long | Number of distinct 13F managers reporting a position in this issuer. A traversal-cost guard, not a materiality measure: it lets a client threshold BEFORE expanding the HOLDS layer rather than discovering the fan-out live. Absent for the ~25% of issuers with no 13F coverage. Written by materialize_materiality.py.
 |

### Insider

SEC Form 3/4/5 reporting owner (director/officer/10% holder), CIK-keyed at source

- **Unique key:** `cik`

#### Required Properties

| Property | Type | Description |
|----------|------|-------------|
| `cik` | String | SEC Central Index Key of the reporting owner (unique key) |
| `name` | String | Reporting owner name as filed |
| `loaded_at` | DateTime | When node was loaded |

### InstitutionalManager

SEC Form 13F institutional investment manager, CIK-keyed

- **Unique key:** `cik`

#### Required Properties

| Property | Type | Description |
|----------|------|-------------|
| `cik` | String | SEC Central Index Key of the 13F filer (unique key) |
| `name` | String | Manager name as filed |
| `loaded_at` | DateTime | When node was loaded |

#### Optional Properties

| Property | Type | Description |
|----------|------|-------------|
| `holds_count` | Long | Number of distinct issuers this manager reports a position in. A traversal-cost guard, not an analytic measure. HOLDS is 6.7M edges with out-degree p50 220 and a worst case of 45,694 on a single manager — expanding that node in an interactive client hangs it, and the client cannot know the cost before paying it. Reading this first makes the fan-out knowable in advance. Written by materialize_materiality.py.
 |

### BeneficialOwner

SEC Schedule 13D/13G >5% beneficial owner (filer); CIK if resolved, else name-slug

- **Unique key:** `owner_key`

#### Required Properties

| Property | Type | Description |
|----------|------|-------------|
| `owner_key` | String | CIK if resolvable, else a name-slug (unique key) |
| `name` | String | Filer name as it appears in the submission header |
| `loaded_at` | DateTime | When node was loaded |

#### Optional Properties

| Property | Type | Description |
|----------|------|-------------|
| `cik` | String | Filer CIK when resolvable from the submission header |
| `resolved` | Boolean | True if owner_key is a CIK, False if a name-slug |
| `is_custodial` | Boolean | True for broker/custodian/index hubs that bridge unrelated activists. Labelled (never deleted) so the co-filing fact survives; excluded at coalition projection time for precision. Fixing a leak in this scrub once moved a published coalition figure from 22 to 16, which is why the exclusion is auditable rather than baked into the data.
 |

## Relationship Types

| Relationship | Pattern | Description |
|-------------|---------|-------------|
| `DIRECTOR_OF` | `(Insider)-[:DIRECTOR_OF]->(Company)` | Reporting owner is a director of the issuer (SEC Form 3/4/5) |
| `OFFICER_OF` | `(Insider)-[:OFFICER_OF]->(Company)` | Reporting owner is an officer of the issuer (SEC Form 3/4/5) |
| `TEN_PCT_OWNER_OF` | `(Insider)-[:TEN_PCT_OWNER_OF]->(Company)` | Reporting owner holds >10% of the issuer (SEC Form 3/4/5) |
| `SHARES_DIRECTOR` | `(Company)-[:SHARES_DIRECTOR]->(Company)` | Two operating boards share >=1 human director (derived from DIRECTOR_OF; board-interlock edge, stored undirected once with id(a)<id(b)) |
| `CONTROLS` | `(BeneficialOwner)-[:CONTROLS]->(Company)` | Verified >=50% control of the issuer (derived from BENEFICIAL_OWNER_OF 13D edges where control_class='control'; self-filings excluded). Materialized so the transitive control chain is a real variable-depth Cypher traversal rather than a client-side walk; chains continue where a controlled Company's CIK also exists as a BeneficialOwner. |
| `SAME_ENTITY_AS` | `(Company)-[:SAME_ENTITY_AS]->(BeneficialOwner)` | The Company and the BeneficialOwner are the same legal entity, matched on identical CIK (a hard key, never a name match). Purely structural: it lets a control chain continue past an intermediate holding company that both IS controlled and DOES control, so (root)-[:CONTROLS|SAME_ENTITY_AS*]->(target) traverses the full pyramid in Cypher. Carries no ownership semantics of its own. |
| `INFLUENCES` | `(BeneficialOwner)-[:INFLUENCES]->(Company)` | A 13D stake at or above a Federal Reserve control-presumption tier (12 CFR 225.2(e)): 10 / 15 / 25 / 50 percent. DELIBERATELY SEPARATE FROM `CONTROLS`, which stays at >=50% so that "control" keeps meaning control — a 25% holder has influence, blocking rights and often a board designee, but not control, and relabelling it invites a correct objection. Exists because a >=50% single stake is structurally anti-selected for large caps: an issuer with a majority holder has little float, so the >=50% set was finding illiquidity as much as control. Median issuer size rises monotonically as the tier falls, and at 25% the set includes Berkshire, Walmart, Charter and Ferrari. CAVEAT that must travel with it: `percent_of_class` is percent of the class covered by the filing, NOT voting power. Several of the largest names here are dual-class (Berkshire, the Liberty complex, Carvana, Sea), where economic and voting stakes diverge — so the 25%-of- voting test cannot be evaluated cleanly from this data. `board_seat` is the second, independent limb of 225.2(e): true when the same CIK also currently sits on the board via Form 3/4/5. Self-filings excluded, as in `CONTROLS`.
 |
| `CO_TARGETS` | `(BeneficialOwner)-[:CO_TARGETS]->(BeneficialOwner)` | Two 13D filers co-target >=2 of the same issuers (derived activist co-targeting edge, stored undirected once with a.cik<b.cik). Substrate for the wolf-pack coalition component; custodial/broker hubs are labelled is_custodial on the node and excluded at projection time rather than deleted. |
| `BENEFICIAL_OWNER_OF` | `(BeneficialOwner)-[:BENEFICIAL_OWNER_OF]->(Company)` | >5% beneficial owner of the issuer (SEC Schedule 13D/13G). ONE edge per (owner, company, filing_type), so a filer's whole 13D history on an issuer collapses here. filing_date/accession_number therefore report the EARLIEST ORIGINAL (non-/A) filing — when the position was actually disclosed — while first_seen/last_seen keep the full observed span and amendment_count shows how much history the edge stands in for. Reporting the last-written filing instead manufactured false convergences (amendments to years-old stakes looked like fresh arrivals).
 |
| `HOLDS` | `(InstitutionalManager)-[:HOLDS]->(Company)` | 13F reported holding of the issuer by an institutional manager, keyed by report_period so each quarter is a distinct edge (a position time series, not a latest-only snapshot). Slice/compare quarter-over-quarter on report_period to see accumulation/trimming. IMPORTANT: value_usd/shares are SHARE OWNERSHIP only. 13F also reports option positions, and their notional is carried separately in call_notional_usd / put_notional_usd — an option is a position, not a stake. Conflating them (reading VALUE without PUTCALL) made a market maker holding 67 shares of Eversource look like its largest owner at $18.68B. Roughly 7% of all reported 13F dollars are options, concentrated in market-maker books, so the distortion lands hard on specific issuers.
 |

### Relationship Properties

#### DIRECTOR_OF

`(Insider)-[:DIRECTOR_OF]->(Company)`

- **Required:** `filing_date`, `source`, `loaded_at`
- **Optional:** `accession_number`, `first_seen`, `last_seen`

#### OFFICER_OF

`(Insider)-[:OFFICER_OF]->(Company)`

- **Required:** `filing_date`, `source`, `loaded_at`
- **Optional:** `accession_number`, `officer_title`, `first_seen`, `last_seen`

#### TEN_PCT_OWNER_OF

`(Insider)-[:TEN_PCT_OWNER_OF]->(Company)`

- **Required:** `filing_date`, `source`, `loaded_at`
- **Optional:** `accession_number`, `first_seen`, `last_seen`

#### SHARES_DIRECTOR

`(Company)-[:SHARES_DIRECTOR]->(Company)`

- **Required:** `director_count`, `source`, `computed_at`
- **Optional:** `via_ciks`

#### CONTROLS

`(BeneficialOwner)-[:CONTROLS]->(Company)`

- **Required:** `percent_of_class`, `source`, `computed_at`
- **Optional:** `accession_number`, `filing_date`

#### SAME_ENTITY_AS

`(Company)-[:SAME_ENTITY_AS]->(BeneficialOwner)`

- **Required:** `source`, `computed_at`
- **Optional:** `cik`

#### INFLUENCES

`(BeneficialOwner)-[:INFLUENCES]->(Company)`

- **Required:** `percent_of_class`, `tier`, `source`, `computed_at`
- **Optional:** `accession_number`, `filing_date`, `board_seat`, `board_seat_last_seen`

#### CO_TARGETS

`(BeneficialOwner)-[:CO_TARGETS]->(BeneficialOwner)`

- **Required:** `shared_target_count`, `source`, `computed_at`
- **Optional:** `shared_target_ciks`

#### BENEFICIAL_OWNER_OF

`(BeneficialOwner)-[:BENEFICIAL_OWNER_OF]->(Company)`

- **Required:** `filing_type`, `filing_date`, `source`, `loaded_at`
- **Optional:** `percent_of_class`, `accession_number`, `control_class`, `sole_voting`, `shared_voting`, `pct_verified`, `pct_source`, `control_extracted_at`, `first_seen`, `last_seen`, `amendment_count`, `filing_is_original`

#### HOLDS

`(InstitutionalManager)-[:HOLDS]->(Company)`

- **Required:** `report_period`, `filing_date`, `source`, `loaded_at`
- **Optional:** `value_usd`, `shares`, `call_notional_usd`, `put_notional_usd`, `cusip`, `accession_number`

## Constraints

| Label | Property | Type |
|-------|----------|------|
| Company | `cik` | UNIQUENESS |
| Insider | `cik` | UNIQUENESS |
| InstitutionalManager | `cik` | UNIQUENESS |
| BeneficialOwner | `owner_key` | UNIQUENESS |

## Indexes

### Range Indexes

| Label | Property |
|-------|----------|
| Company | `ticker` |
| Company | `sector` |
| Company | `sic_code` |
| Insider | `name` |
| InstitutionalManager | `name` |
| BeneficialOwner | `name` |

### Fulltext Indexes

| Name | Labels | Properties |
|------|--------|------------|
| `entity_name_fulltext` | Company, Insider, InstitutionalManager, BeneficialOwner | `name` |

## Validation

Run the schema health-check before chat/evaluation runs:

```bash
python scripts/validate_graph_schema.py --execute
```

This fails hard if required labels, relationship types, properties,
constraints, or indexes are missing from the live database.
