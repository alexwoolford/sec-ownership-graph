# Public Company Graph — Schema Reference

> **Auto-generated** from `schema/graph_schema.yaml`.
> Last generated: 2026-08-04 11:26 UTC
>
> Do **not** edit this file by hand. Run:
> ```bash
> python scripts/generate_schema_docs.py --execute
> ```

## Node Types

### Company

A public issuer that files with the SEC. The universe is SEC filers with a ticker (see scripts/load_company_universe.py); every ownership edge attaches to one of these. NOTE: this graph carries no market-cap, revenue or financial data — results cannot be ranked by materiality. See docs/reference_architecture_secgraph.md "Honest limits".


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
| `is_custodial` | Boolean | True for broker/custodian/index hubs that bridge unrelated activists. Labelled (never deleted) so the fact survives; excluded at coalition projection time for precision. |
| `coalition_id` | Long | Activist coalition component id (GDS WCC over CO_TARGETS, custodial hubs excluded) |

## Relationship Types

| Relationship | Pattern | Description |
|-------------|---------|-------------|
| `DIRECTOR_OF` | `(Insider)-[:DIRECTOR_OF]->(Company)` | Reporting owner is a director of the issuer (SEC Form 3/4/5) |
| `OFFICER_OF` | `(Insider)-[:OFFICER_OF]->(Company)` | Reporting owner is an officer of the issuer (SEC Form 3/4/5) |
| `TEN_PCT_OWNER_OF` | `(Insider)-[:TEN_PCT_OWNER_OF]->(Company)` | Reporting owner holds >10% of the issuer (SEC Form 3/4/5) |
| `SHARES_DIRECTOR` | `(Company)-[:SHARES_DIRECTOR]->(Company)` | Two operating boards share >=1 human director (derived from DIRECTOR_OF; board-interlock edge, stored undirected once with id(a)<id(b)) |
| `CONTROLS` | `(BeneficialOwner)-[:CONTROLS]->(Company)` | Verified >=50% control of the issuer (derived from BENEFICIAL_OWNER_OF 13D edges where control_class='control'; self-filings excluded). Materialized so the transitive control chain is a real variable-depth Cypher traversal rather than a client-side walk; chains continue where a controlled Company's CIK also exists as a BeneficialOwner. |
| `SAME_ENTITY_AS` | `(Company)-[:SAME_ENTITY_AS]->(BeneficialOwner)` | The Company and the BeneficialOwner are the same legal entity, matched on identical CIK (a hard key, never a name match). Purely structural: it lets a control chain continue past an intermediate holding company that both IS controlled and DOES control, so (root)-[:CONTROLS|SAME_ENTITY_AS*]->(target) traverses the full pyramid in Cypher. Carries no ownership semantics of its own. |
| `CO_TARGETS` | `(BeneficialOwner)-[:CO_TARGETS]->(BeneficialOwner)` | Two 13D filers co-target >=2 of the same issuers (derived activist co-targeting edge, stored undirected once with a.cik<b.cik). Substrate for the wolf-pack coalition component; custodial/broker hubs are labelled is_custodial on the node and excluded at projection time rather than deleted. |
| `BENEFICIAL_OWNER_OF` | `(BeneficialOwner)-[:BENEFICIAL_OWNER_OF]->(Company)` | >5% beneficial owner of the issuer (SEC Schedule 13D/13G). ONE edge per (owner, company, filing_type), so a filer's whole 13D history on an issuer collapses here. filing_date/accession_number therefore report the EARLIEST ORIGINAL (non-/A) filing — when the position was actually disclosed — while first_seen/last_seen keep the full observed span and amendment_count shows how much history the edge stands in for. Reporting the last-written filing instead manufactured false convergences (amendments to years-old stakes looked like fresh arrivals).
 |
| `HOLDS` | `(InstitutionalManager)-[:HOLDS]->(Company)` | 13F reported holding of the issuer by an institutional manager, keyed by report_period so each quarter is a distinct edge (a position time series, not a latest-only snapshot). Slice/compare quarter-over-quarter on report_period to see accumulation/trimming.
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
- **Optional:** `value_usd`, `shares`, `cusip`, `accession_number`

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

## Validation

Run the schema health-check before chat/evaluation runs:

```bash
python scripts/validate_graph_schema.py --execute
```

This fails hard if required labels, relationship types, properties,
constraints, or indexes are missing from the live database.
