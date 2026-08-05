# Demo script — who actually influences this company?

*A five-question walkthrough of `secgraph`. Every answer is produced live by curated read-only MCP
tools, cited to an SEC accession number, and dated. Roughly 10 minutes.*

**Who this is for:** anyone who needs to know who can move a company and cannot take a vendor's
word for it — governance and proxy teams, credit and counterparty risk, event-driven desks, or a
diligence process that has to survive an audit. Not a quant looking for a signal
([why not](#what-this-cannot-tell-you)).

**Setup (clone → tools):**

1. `uv venv && source .venv/bin/activate && uv pip install -e ".[dev,llm]"`
2. `cp .env.sample .env` — set `NEO4J_PASSWORD` and `SEC_USER_AGENT`
3. Neo4j Enterprise + GDS up with database `secgraph` built
4. **Cursor:** open this repo — [`.cursor/mcp.json`](../.cursor/mcp.json) is portable via
   `${workspaceFolder}`. Enable **secgraph-ownership**, reload MCP.
5. Ask the questions in natural language — the model picks the tool.

`make smoke-mcp` verifies the catalog and demo queries without an MCP client.

---

## The frame (say this first, in 20 seconds)

> "Every SEC ownership filing — 13D, 13G, Form 3/4/5, 13F — as one graph, keyed on CIK rather than
> name-matched. The point isn't search. It's the questions that need two different filing types to
> agree, or a relationship followed to a depth the data decides. Every answer cites its accession
> number and tells you how old the evidence is. When the data doesn't support an answer, it says so."

---

## Q1 — "Who can actually move these companies?"

**Tool:** `influence_map()`

```
  TICKER        SIZE   STAKE  13D    SEAT     HOLDER
  BRK-B      $479.9B   37.0%  2024   2026-05  BUFFETT WARREN E
  TMUS        $92.6B   74.3%  2013   2025-10  DEUTSCHE TELEKOM AG
  LYV         $30.4B   29.7%  2025   2026-03  Liberty Live Holdings, Inc.
  LYV         $30.4B   26.4%  2013   2025-12  Liberty Media Corp
  SE          $27.2B   30.5%  2017   2026-04  Li Xiaodong
  CHTR        $22.4B   26.1%  2014   2026-06  Liberty Broadband Corp
  ET          $20.2B   50.0%  2018   2026-05  WARREN KELCY L
  WRB         $19.6B   25.5%  2026   2026-06  BERKLEY WILLIAM R
```

**Point out three things.**

**The threshold is the regulator's, not ours.** 12 CFR 225.2(e) — the Federal Reserve's control
presumptions — treats 25% of a voting class *or* board control as control. We tier at 10/15/25/50
and never call a 25% stake "control": the edge is `INFLUENCES`, and `CONTROLS` stays at ≥50%.

**Every row is two independent filings agreeing.** The stake comes from a Schedule 13D; the board
seat comes from Form 3/4/5 transaction filings. A screener will sell you either list. The *pairing*
is the finding, and it is the join a single-table query cannot do for you.

**Look at the two date columns.** Liberty Broadband declared 26.1% of Charter in **2014** — and its
director was on file in **June 2026**. A 13D has no exit obligation below 5%, so an old stake proves
nothing on its own; a current board seat is what corroborates it. That pairing is why this answer is
trustworthy where a stake alone isn't.

---

## Q2 — "Charter specifically. Who's around that board?"

**Tool:** `influence_map(min_tier=10)` filtered to CHTR, or ask about Charter directly

```
Liberty Broadband Corp          26.1%  tier 25   13D 2014   seat 2026-06
ADVANCE PUBLICATIONS, INC       12.3%  tier 10   13D 2023   seat 2025-08
ADVANCE/NEWHOUSE PARTNERSHIP    12.3%  tier 10   13D 2021   seat 2025-08
```

**What to point out:** two separate blockholder families, three filing entities, all with current
board representation — the Liberty complex at the 25% presumption tier and the Newhouse vehicles at
10%. Anyone modelling a Charter vote or a change of control needs all three, and they arrive from
three different filings across twelve years. Note also that Advance appears twice: that is the
filing-group structure, not double-counting, and the graph keeps both because both are true.

---

## Q3 — "Show me a company you *can't* answer for."

**Tool:** `control_chain("AAPL")` — let them pick the ticker

```
No graph-grounded answer for 'Apple Inc.' (no_verified_control_chain).
Issuer has no >=50% verified 13D control edge on a chain.
```

**What to point out — and hand them the keyboard.** Ten of ten mega-caps abstain. There is no ≥50%
holder of Apple, so the honest answer is "nothing here," and the tool says it rather than
assembling something plausible from a 6% stake. In a regulated setting that is the whole ballgame:
a system that never says "no" can't be trusted when it says "yes."

Then show the flip side — the tool volunteering its own weakness:

**Tool:** `control_chain("WPM")`

```
  [1h] GOLDCORP INC → Wheaton Precious Metals Corp. (75%)
  Evidence filed: 2006.
  ⚠ Newest supporting filing is over 5 years old. 13D carries no exit obligation
    below 5%, so this is a LAST-KNOWN stake, not a confirmed current one.
```

Goldcorp was absorbed by Newmont in 2019. The filing is real, the fact is 2006, and the tool says
so instead of letting you assume otherwise.

---

## Q4 — "What's heating up? Multiple activists on the same name?"

**Tool:** `activist_convergence(since='2023-01-01')`

Issuers where two or more recognised activist franchises filed an *original* 13D inside a bounded
180-day span, ranked by institutional size:

| Issuer | Franchises | Span | Size |
| --- | --- | --- | --- |
| **SION** Sionna Therapeutics | OrbiMed → RA Capital | 5 days | $1.9B |
| **GDV** Gabelli Dividend & Income Trust | Saba → GAMCO | 78 days | $707M |
| **MNRO** Monro, Inc. | GAMCO → Icahn | 96 days | $606M |
| **KTF** DWS Municipal Income Trust | Saba → Bulldog | 127 days | $75M |
| **PGZ** Principal Real Estate Income Fund | Saba → Bulldog | 13 days | $15M |

**What to point out:** three distinct patterns from one screen — closed-end fund raids
(Saba → Bulldog), biotech crossover clustering (OrbiMed ↔ RA Capital), and classic industrial
activism (GAMCO/Icahn). **GDV is Saba attacking a *Gabelli* fund with GAMCO showing up to defend
it** — that's a story, not a row.

Then drill in:

**Tool:** `campaign_timeline("MNRO")`

```
2025-01-23  13G           DIMENSIONAL FUND ADVISORS LP (passive_index)
2025-04-29  13G           BlackRock, Inc. (passive_index)
2025-05-15  13G           NOMURA HOLDINGS INC (custodian)
2025-08-01  13D    5.01%  GAMCO INVESTORS, INC. ET AL (activist)
2025-11-05  13D   14.79%  ICAHN CARL C (activist)

First mover: GAMCO on 2025-08-01 at 5.01% → ICAHN followed 96 days later at 14.79%
```

The tool separates signal from noise *inside one filing type*: BlackRock and Dimensional are index
money that holds everything, Nomura is a custodian, two filers are real activists. GAMCO takes a
5% toe-hold; Icahn arrives three months later at nearly 3× the stake.

---

## Q5 — "Do these activists work together?"

**Tool:** `activist_coalition("ICAHN CARL C")`

A connected component over shared 13D targets: **25 filing CIKs / 21 distinct actors, ~5 hops
across**. What it targets, largest first:

| Target | Size | Via |
| --- | --- | --- |
| **DE** Deere & Co | $112B | Cascade Investment |
| **FCX** Freeport-McMoRan | $77B | Icahn |
| **ECL** Ecolab | $63B | Cascade Investment |
| **OXY** Occidental Petroleum | $54B | Icahn |
| **LNG** Cheniere Energy | $52B | Icahn |

**What to point out:** the size and diameter are *emergent* — a warehouse can tell you who
co-filed on one name, but not hand you the cluster. Affiliated vehicles are collapsed for the
actor count (three Bulldog CIKs are one firm), and custodians and index funds are labelled and
excluded at query time rather than deleted, so the co-filing facts survive and the precision
choice stays auditable.

---

## What this cannot tell you

Say these before you're asked. They're part of why the rest is credible.

- **It does not predict anything.** The alpha question was tested and came back null. Efficient
  markets; 13Ds are public. This is a structural and temporal map, not a signal.
- **A stake is not voting power.** `percent_of_class` is percent of the class covered by the
  filing. Several of the largest names here are dual-class — Berkshire, the Liberty complex,
  Carvana, Sea — where economic and voting stakes diverge. So the 25%-of-*voting* test cannot be
  evaluated cleanly from 13D alone.
- **13D percentages are last-known, not current.** No exit obligation below 5%, so roughly half the
  control edges predate 2020. Answers report the filing year and flag evidence over 5 years old —
  but you should still treat an old stake as a lead, not a fact.
- **We cannot compute board *majority*.** `DIRECTOR_OF` comes from Form 3/4/5 *transactions*, not a
  roster: it lists 24 directors for Charter and 27 for Vertiv (both ~2× reality) while
  under-counting Berkshire. "Does this holder sit on the board" is answerable; "does this holder
  control the board" is not, and we don't pretend otherwise.
- **Size is a proxy.** `institutional_value_usd` sums one quarter of 13F *share* holdings — options
  are tracked separately, since a $14.8B put is a position, not a stake. It measures free float, is
  null for ~25% of issuers, and includes ETFs. No revenue, assets or true market cap.
- **Recall is deliberately capped.** Activist screens match a curated franchise list: precision over
  recall, so a first-time activist is missed by design.
- **CIK-keyed only.** Conservative: it understates family and affiliate structure rather than
  inventing links through fuzzy name matching. US SEC registrants with a ticker — no private
  companies, no foreign subsidiaries.

---

## The close

> "Two things here are hard to get elsewhere. One is the *conjunction* — a stake at the regulator's
> own threshold, cross-checked against a board seat from a completely different filing type, with
> both dates on screen. The other is the abstention: it tells you when it has nothing, and it tells
> you when its evidence is twenty years old. And the whole thing rebuilds from SEC source data with
> one command, so it's a live asset, not a slide."

**Reproduce:** `make demo` → [`results/activist_convergence.md`](../results/activist_convergence.md).
Full build: `python scripts/build_secgraph.py --database secgraph --as-of 2026-06-30 --execute`.
Architecture: [`docs/reference_architecture_secgraph.md`](reference_architecture_secgraph.md).
