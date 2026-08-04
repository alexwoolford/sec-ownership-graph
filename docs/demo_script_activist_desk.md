# Demo script — activist / event-driven desk

*A five-question walkthrough of `secgraph` for one persona. Every answer is produced live by
curated read-only MCP tools and cited to an SEC accession number. Roughly 10 minutes.*

**Setup:** `python scripts/serve_ownership_mcp.py --database secgraph`, then connect from Claude
Desktop (see [`.mcp.json`](../.mcp.json)). Ask the questions in natural language — the model picks
the tool.

**Who this is for:** someone who watches 13D filings for a living — event-driven, activist
defense, or risk arbitrage. Not a quant looking for a signal (see the limits at the end).

---

## The frame (say this first, in 20 seconds)

> "This is every SEC ownership filing — 13D, 13G, Form 3/4/5, 13F — as one graph, keyed on CIK
> rather than name-matched. Not a filings search tool: the point is the questions that need you to
> follow relationships across filings and through time. Every answer cites its accession number,
> and when the data doesn't support an answer it says so rather than guessing."

---

## Q1 — "What's heating up? Where have multiple activists shown up on the same name recently?"

**Tool:** `activist_convergence(since='2023-01-01')`

Returns 5 issuers where two or more recognised activist franchises filed 13D within a bounded
180-day span — with the sequence and the day-gap:

| Issuer | Franchises | Span |
| --- | --- | --- |
| **MNRO** Monro, Inc. | GAMCO → Icahn | 96 days |
| **SION** Sionna Therapeutics | OrbiMed → RA Capital | 5 days |
| **GDV** Gabelli Dividend & Income Trust | Saba → GAMCO | 78 days |
| **JANX** Janux Therapeutics | RA Capital → OrbiMed | 156 days |
| **KTF** DWS Municipal Income Trust | Saba → Bulldog | 127 days |
| **PGZ** Principal Real Estate Income Fund | Saba → Bulldog | 13 days |

> **Note on the count (was 8).** Three of the previous eight were artifacts of dating an edge by
> whichever filing was written last rather than the **original**. Herc Holdings showed GAMCO and
> Icahn "arriving" 46 days apart in 2023 when their real originals are **2016-08-10** and
> **2014-08-20** — amendments to years-old stakes masquerading as fresh arrivals. Now that edges
> carry original-filing dates, those drop out correctly. Fewer hits, all of them real.

**What to point out:** distinct patterns fall out of one screen — closed-end fund raids
(Saba → Bulldog/Karpus), biotech crossover clustering (OrbiMed ↔ RA Capital), and classic
industrial activism (GAMCO/Icahn). **GDV is Saba attacking a *Gabelli* fund and GAMCO showing up
to defend it** — that is a story, not a row in a table.

---

## Q2 — "Walk me through Monro. Who moved first?"

**Tool:** `campaign_timeline("MNRO")`

```
2025-01-23  13G           DIMENSIONAL FUND ADVISORS LP (passive_index)
2025-04-29  13G           BlackRock, Inc. (passive_index)
2025-05-15  13G           NOMURA HOLDINGS INC (custodian)
2025-05-15  13G           COOPER CREEK PARTNERS MANAGEMENT LLC (other_holder)
2025-08-01  13D    5.01%  GAMCO INVESTORS, INC. ET AL (activist)
2025-11-05  13D   14.79%  ICAHN CARL C (activist)
2025-11-13  13G           Adage Capital Management, L.P. (passive_index)

First mover: GAMCO INVESTORS, INC. ET AL on 2025-08-01 at 5.01%
  → ICAHN CARL C followed 96 days later at 14.79%
```

**What to point out:** the tool separates *signal from noise inside the same filing type* —
BlackRock and Dimensional are index money that holds everything, Nomura is a custodian, and only
two filers are actual activists. GAMCO takes a 5.01% toe-hold; Icahn arrives three months later at
**14.79%**, nearly 4× the stake. Every line is citable.

---

## Q3 — "Do Icahn and GAMCO have a pattern of showing up together?"

**Tool:** `activist_coalition("ICAHN CARL C")`

Returns **25 CIKs / 21 distinct actors, ~5 hops across**, derived from shared 13D targets:

> Icahn · GAMCO Investors · Gabelli Marc · Bulldog Investors · Karpus Management · Saba Capital ·
> Cannell Capital · Royce Charles M · Fund 1 Investments · Deason Darwin · Malone John C ·
> Glenview Capital · Cascade Investment · Dolan Charles F · Dolan James Lawrence · B. Riley ·
> 180 Degree Capital · Star Equity · Albion River · MIRA · Value Catalyst Fund

**What to point out:** every name is a genuine activist or control person — no banks, no index
funds, no pensions. That is deliberate: custodians and passive holders are *labelled and excluded
at query time, not deleted*, because the co-filing fact is true but not evidence of coordination.

> **Two counts, and why this figure has moved.** `member_count` is 25 CIKs — what the traversal
> found. `distinct_actors` is 21, collapsing affiliated vehicles of one manager: **three Bulldog
> CIKs are one firm**, Phillip Goldstein is Bulldog's principal filing personally, and Digirad
> renamed to Star Equity. Quote 21 to anyone who knows the names; quote 25 only with the
> filing-group structure explained.
>
> The figure has moved twice, for two different reasons — worth separating:
> **22 → 16 was a precision fix** (the custodial scrub matched substring `RBC` but not
> `ROYAL BANK OF CANADA`, so banks were being counted as activists).
> **16 → 25 is coverage**: the crawl now prioritises *original* 13D filings over amendments, so
> filers whose originals predate the newest-40 window are visible for the first time. No scrub
> changed. A connected component's membership is emergent — that property is exactly what makes
> the query graph-native, and it means the number tracks the data window. The memo's provenance
> line records which window produced it.

**This is the graph-native question.** The coalition is a *connected component* — its size and
diameter are emergent, not a fixed join. A warehouse can tell you who co-filed on one name; it
can't hand you the cluster.

---

## Q4 — "Who else owns Monro?"

**Tool:** `ownership_snapshot("MNRO")`

Institutional context around the campaign — top holders with percent-of-class, director and
officer counts, and whether any holder has crossed 50% (nobody has, so no control edge).

**What to point out:** this is supporting context, and the honest use of the 13F/13G layer.

---

## Q5 — "Is Monro's board connected to anyone interesting?"

**Tool:** `board_interlock_path("MNRO", <peer>)`

**What to point out — and be upfront:** with 6 board interlocks, MNRO connects to plenty of
companies, but **that is true of nearly every company in the graph.** We measured it: every
well-connected pair links within 4 hops. So *"are these boards connected?"* is always yes and
means nothing. The informative output is the **named bridging director** — a specific person who
sits on both boards, which is who you'd actually call.

---

## What this cannot tell you (say this — don't wait to be asked)

- **It does not predict anything.** We backtested the alpha question and it came back null.
  Efficient markets; 13Ds are public. This is a structural and temporal map, not a signal.
- **No materiality ranking.** The graph carries no market-cap or size data, so a nano-cap and a
  large cap look identical in output. A user has to bring their own universe filter.
- **Recall is deliberately capped.** Activist screens match a curated franchise list. That buys
  precision — the alternative surfaces micro-cap founders crossing 5% and filing-group artifacts
  where one manager files through seven affiliated entities — but a first-time activist is missed.
- **Only 13D/13G dates are a time series.** Board and officer edges are a 2022–2026 keep-latest
  snapshot; 13F has a 2024 coverage step-up. Don't read trends into them.
- **Control chains are a small-cap tool.** Every verified ≥50% chain in this dataset is
  micro/nano-cap. Useful as a governance screen, not a large-cap feature.
- **CIK-keyed only.** Deliberately conservative: it understates family/affiliate structure rather
  than inventing links through fuzzy name matching.

---

## The close

> "Three of these questions can't be answered by a filings database or a warehouse without
> writing application code to walk the relationships: the coalition, the control chain, and the
> named bridge. They're all the same shape — follow a relationship an unknown number of steps
> and return the structure you find. And the whole thing rebuilds from SEC source data with one
> command, so it's a live asset, not a slide."

**Reproduce:** `python scripts/activist_convergence.py --database secgraph --since 2023-01-01
--timeline MNRO --markdown` → [`results/activist_convergence.md`](../results/activist_convergence.md).
Full build: `python scripts/build_secgraph.py --database secgraph --execute`. Architecture:
[`docs/reference_architecture_secgraph.md`](reference_architecture_secgraph.md).
