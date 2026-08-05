# What the forms mean, and where every node and edge came from

*Written for someone who just cloned this repo and knows Neo4j but not SEC filings. If you know
the filings and want property-level detail, go to [`graph_schema.md`](graph_schema.md) — it is
generated from `schema/graph_schema.yaml` and is the authoritative field reference. This file
explains **what the data is**; that one explains **what the fields are**.*

---

## The one-paragraph version

US securities law forces certain people and institutions to *publicly disclose* their stakes in
public companies. Four disclosure regimes matter here: **Schedule 13D/13G** (anyone crossing 5%
of a company), **Forms 3/4/5** (insiders — directors, officers, 10% owners — reporting their own
trades), and **Form 13F** (investment managers over $100M listing quarterly holdings). Each is a
different obligation, with a different trigger, a different deadline, and a different reliability.
This graph loads all four, keyed on **CIK**, so a question can cross between them.

**Why crossing between them is the point:** each form alone is a list you can buy from a data
vendor. A 13D tells you who owns 30% of a company. Form 4 tells you who sits on its board. The
*conjunction* — a 30% holder who also sits on the board — is a materially different fact, and it
lives in neither dataset alone.

---

## The four sources

### CIK — the key that makes this work

A **Central Index Key** is the permanent numeric ID the SEC assigns to every filer — company,
fund, or individual. It never changes, even when a company renames or re-tickers.

Everything in this graph joins on CIK (or, for securities, on **CUSIP-9**). Never on names. This
matters more than it sounds: `BLACKROCK INC.`, `BlackRock, Inc.` and `Blackrock Inc` are the same
filer, and no amount of string normalization reliably decides that in general. The repo's rule is
**hard keys only** — prefer understating a relationship to inventing one. A consequence you will
see in results: family and affiliate structures are *understated*, because separate CIKs are kept
separate unless a filing links them.

### Schedule 13D and 13G — "I crossed 5%"

Filed when a person or entity acquires beneficial ownership of **more than 5%** of a registered
voting class. Two flavors, and the difference is the single most useful signal in this graph:

| | **13D** | **13G** |
| --- | --- | --- |
| Filed by | Anyone who may seek to influence control | Passive investors, and qualified institutions |
| Says, in effect | "I may push for change" | "I'm just holding this" |
| Filed promptly? | Yes — days | No — quarterly-ish |
| Typical filer | Activist fund, strategic acquirer, founder | Index fund, pension, bank |

The exact deadlines were **shortened by the SEC's 2023 amendments** (13D moved from 10 calendar days
to 5 business days; 13G deadlines were compressed and moved to a quarterly cadence). Check the
[SEC's current rules](https://www.sec.gov/rules-regulations) rather than trusting a number in this
file — nothing in the build depends on the deadline, only on the `filing_date` as filed.

**An activist campaign usually begins with a 13D.** That's why `campaign_timeline` and
`activist_convergence` read `filing_type` and treat the two differently — a Vanguard 13G on a
company is noise; an Icahn 13D on the same company is the event.

Amendments (`13D/A`, `13G/A`) report material changes, including exits.

- **In the graph:** `BeneficialOwner -[:BENEFICIAL_OWNER_OF {filing_type}]-> Company`
  — 62,705 edges (**10,593** are 13D, **52,112** are 13G).
- **Source:** the EDGAR submissions API per issuer, then the raw filing text at
  `https://www.sec.gov/Archives/edgar/data/...`. Crawled per subject company, which is why a full
  build takes hours.
- **Loader:** `secgraph/ingestion/ownership/beneficial.py`

> **The trap that matters most.** There is **one edge per (owner, company, filing_type)** — the
> MERGE key collapses every amendment into a single relationship. So `accession_number` and
> `filing_date` are *one* citation, not the filing history. The edge keeps the **earliest
> original** filing (when the position was first disclosed), so an activist's later 13D/A on the
> same issuer is not separately visible. This is why a `--since 2023-01-01` screen can miss a
> filing you know exists.

### Forms 3, 4 and 5 — insider transactions

Corporate insiders — directors, officers, and >10% owners — must report their own transactions in
their company's stock.

| Form | When | Meaning |
| --- | --- | --- |
| **3** | On becoming an insider | Initial statement of holdings |
| **4** | Promptly after a transaction (2 business days) | The workhorse — a buy, sell, grant, or exercise |
| **5** | After fiscal year-end | Annual catch-up for exempt transactions |

Form 4's two-business-day deadline is why insider data is *timely* — but timeliness is not the same
as completeness, which is the trap below.

Each filing carries checkbox flags for the filer's relationship to the issuer, which become three
different edges:

| Flag | Edge | Count |
| --- | --- | --- |
| `Director` | `DIRECTOR_OF` | 55,507 |
| `Officer` | `OFFICER_OF` | 43,091 |
| `TenPercentOwner` | `TEN_PCT_OWNER_OF` | 14,769 |

- **Source:** the SEC's quarterly [Form 345 bulk datasets](https://www.sec.gov/dera/data/form-345)
  — `SUBMISSION.tsv` and `REPORTINGOWNER.tsv`. Downloaded as zips and cached under `data/`.
- **Loader:** `secgraph/ingestion/ownership/insiders.py`

> **This is a transaction log, not a board roster — and that limitation is load-bearing.** A Form 4
> exists only when an insider *trades*. A quarter of data surfaces only the directors who happened
> to transact in it (~3–5 per issuer), not the full board. Two consequences:
>
> 1. **The build needs 12–16 quarters** to clear the density gate. Coverage saturates with depth
>    rather than scaling linearly, because the loader MERGEs on the (insider, company) pair. Four
>    quarters fails the gate and aborts the build at step 5.
> 2. **You cannot compute board majority from this.** It lists 24 directors for Charter and 27 for
>    Vertiv (both roughly 2× reality, since it accumulates departed directors over the window)
>    while *under*-counting Berkshire. "Does this holder sit on the board" is answerable; "does
>    this holder control the board" is not.
>
> `DIRECTOR_OF` and `OFFICER_OF` are a **keep-latest snapshot** over the staged window, which moves
> with `--quarters-345`. Never read a trend from them.

### Form 13F — quarterly institutional holdings

Investment managers exercising discretion over **$100M+** in 13(f) securities must file a holdings
report within **45 days of quarter-end**, listing every position: issuer, CUSIP, value, share
count, and whether the position is stock, a **put**, or a **call**.

- **In the graph:** `InstitutionalManager -[:HOLDS]-> Company` — **6.7M** edges, by far the largest
  layer.
- **Source:** [Form 13F bulk datasets](https://www.sec.gov/dera/data/form-13f) —
  `SUBMISSION.tsv`, `COVERPAGE.tsv`, `INFOTABLE.tsv`.
- **Loader:** `secgraph/ingestion/ownership/institutional.py`

> **13F is keyed on CUSIP, not CIK,** so it cannot join to `Company` directly. The build derives a
> CUSIP-9 → CIK crosswalk from the SEC's
> [fails-to-deliver data](https://www.sec.gov/data-research/sec-markets-data/fails-deliver-data),
> which publishes both identifiers side by side. That is the *only* reason the FTD staging step
> exists — it is not an interesting dataset in itself here.

> **Options are separated from stock, deliberately.** `PUTCALL` is read and option notional goes to
> `call_notional_usd` / `put_notional_usd`, never into `value_usd`. This was a real bug: a
> market-making firm showed up as the **largest holder of a $41B utility** while holding **67
> shares** — the rest was $14.8B of puts and $3.9B of calls. Most aggregators conflate these. A
> put is a position, not a stake.

### The company universe

The 8,000 `Company` nodes come from
[`company_tickers.json`](https://www.sec.gov/files/company_tickers.json) — every SEC filer with a
ticker — enriched with SIC code and state of incorporation from EDGAR.

**There is no configured cap.** The file holds ~10.4k ticker rows, and the loader deduplicates on
CIK (dual-class tickers share one CIK, and the first ticker seen wins), which lands at ~8,000
distinct filers. Every edge in the graph attaches to an in-universe company, so the scope is
**US SEC registrants with a ticker**: no private companies, no foreign subsidiaries, no delisted
shells.

This list is fetched **live** and is not pinned to a committed file. `--as-of` bounds the staged
quarters and the 13D/G crawl, but not the universe — so a rebuild a year from now gets a slightly
different company set as listings change. That is the main reason a rebuild is *equivalent* rather
than *identical*.

---

## Filed facts vs. derived edges

Four of the ten relationship types are **not in any filing**. The build computes and materializes
them. This distinction matters when you are deciding how much to trust a result:

| Edge | Filed or derived | What it means |
| --- | --- | --- |
| `BENEFICIAL_OWNER_OF` | **filed** (13D/G) | Crossed 5% of a voting class |
| `DIRECTOR_OF` / `OFFICER_OF` / `TEN_PCT_OWNER_OF` | **filed** (3/4/5) | Insider relationship, per filing checkbox |
| `HOLDS` | **filed** (13F) | Held this position at quarter-end |
| `CONTROLS` | *derived* | A **verified ≥50%** 13D stake. Self-filings excluded. |
| `INFLUENCES` | *derived* | Tiered at **10 / 15 / 25 / 50%** per 12 CFR 225.2(e), with a `board_seat` flag |
| `SHARES_DIRECTOR` | *derived* | Two companies share ≥1 human director (a board interlock) |
| `SAME_ENTITY_AS` | *derived* | CIK identity bridge — see below |
| `CO_TARGETS` | *derived* | Two 13D filers have co-targeted ≥2 of the same issuers |

### Why `CONTROLS` and `INFLUENCES` are separate

**`CONTROLS` means ≥50%** — an actual majority — so that "control" keeps meaning control.

**`INFLUENCES` uses the Federal Reserve's own presumption tiers** from
[12 CFR 225.2(e)](https://www.ecfr.gov/current/title-12/part-225#p-225.2(e)), which treat 25% of a
voting class *or* board control as presumptive control. Tiering at 10/15/25/50 means the threshold
is the regulator's, not ours. A 25% holder is never labelled as controlling.

> **`percent_of_class` is not voting power.** It is percent of the class *covered by that filing*.
> Several of the largest names here are dual-class — Berkshire, the Liberty complex, Carvana, Sea —
> where economic and voting stakes diverge. So the 25%-of-*voting* test cannot be cleanly evaluated
> from 13D alone.

### Why `SAME_ENTITY_AS` has to exist

A holding company that is both controlled and controlling appears as **two nodes** — a `Company`
and a `BeneficialOwner` — sharing one CIK. A Cypher variable-length pattern **cannot jump between
nodes on a property equality**, so without a real edge between them, `CONTROLS*` stops at one hop.
Materializing the bridge (94 edges) is what turns single edges into multi-hop control chains.

The edge deliberately runs `BeneficialOwner → Company` rather than `Company → Company`: only **67
of 957** controllers also exist as a `Company` node, so a company-to-company edge would silently
discard **93%** of the control graph — a truth-in-inclusion violation.

### Where `CONTROLS` percentages come from

A 13D's percent-of-class is prose on a cover page, not a structured field. The build extracts it in
two passes: a **regex** resolves ~93% of edges for free, and only the remainder reaches a small
LLM (`gpt-4o-mini`). Every extracted figure is **verified against the source text** — anything that
fails verification is labelled `unknown`, never guessed.

`reference/control_figures.csv` commits the extracted results so a rebuild is deterministic and
mostly key-free. It covers only the window it was exported from, so the build always runs a gap
fill behind it for newer filings. See the control-figures section of `CLAUDE.md` for why that is
unconditional.

---

## Two labels that exist to protect precision

The repo's rule is **truth-in-inclusion**: never delete a true fact to make output cleaner. Noise
is handled by *labelling* it and excluding at query time, so the underlying fact survives and the
filtering choice stays auditable.

- **`BeneficialOwner.is_custodial`** — brokers, custodians and index hubs (State Street, Northern
  Trust, RBC…) co-file on everything, which bridges unrelated activists into one fake coalition.
  The 13 flagged filers are labelled, not deleted, and excluded when the coalition graph is
  projected. Fixing a leak here moved a published coalition figure from 22 to 16.
- **`BeneficialOwner.resolved`** — `false` means the CIK could not be resolved from the submission
  header and `owner_key` is a name-slug. Treat those as weaker.

---

## What the graph cannot tell you

Stated up front, because knowing the limits is what makes the rest usable:

- **It does not predict anything.** The alpha question was tested and came back null. 13Ds are
  public; markets are efficient. This is a structural and temporal map, not a signal.
- **Only 13D/13G `filing_date` is a real time series.** Insider edges are a keep-latest snapshot;
  13F is quarterly with a 2024 coverage step-up.
- **13D percentages are last-known, not current.** There is **no exit obligation below 5%**, so
  roughly half the control edges predate 2020. Goldcorp is still on file owning 75% of Wheaton
  from a **2006** filing — Goldcorp was absorbed by Newmont in 2019. Answers report the filing year
  and flag evidence over 5 years old. Treat an old stake as a lead, not a fact.
- **Size is a proxy.** `institutional_value_usd` sums one quarter of 13F *share* holdings. It
  measures free float, is null for ~25% of issuers, includes ETFs, and is not market cap.
- **Activist screens trade recall for precision.** They match a curated franchise list, so a
  first-time activist is missed by design.

---

## Where to go next

| | |
| --- | --- |
| Field-level reference (generated) | [`graph_schema.md`](graph_schema.md) |
| How the layers assemble, and the graph-native argument | [`reference_architecture_secgraph.md`](reference_architecture_secgraph.md) |
| A walkthrough with real output | [`demo_script_governance_desk.md`](demo_script_governance_desk.md) |
| Build order, traps, and conventions | `../CLAUDE.md` |

Counts above are from the reference build (`as_of 2026-07-24`) and will differ in yours —
`results/secgraph_freshness.json` records what your build actually loaded.
