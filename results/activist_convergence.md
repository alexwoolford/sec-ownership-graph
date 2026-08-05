# Activist convergence screen — `secgraph`

*Generated 2026-08-05, read-only. Issuers where two or more recognised activist*
*franchises filed Schedule 13D within a bounded span. Every line is citable to an*
*SEC accession number.*

> **Provenance.** Data as of `2026-06-30` · staging pinned to `2026-06-30` · Form 3/4/5 window `2022q3–2026q2` (16 quarters) · control figures from `reference_csv`. Figures below are specific to this window: a rebuild with a different `--as-of` will legitimately differ.

> **How to read the span.** The window is measured first-to-last filing, not rolling, so
> a *third* franchise filing later can push an issuer's span past the limit and drop it
> off this screen. New data can therefore remove a hit as well as add one.

**5 issuers** cleared the screen.

## MNRO — MONRO, INC. · 2 franchises within 96 days, $1.6B assets

| Date | Filer | % of class | Accession |
| --- | --- | --- | --- |
| 2025-08-01 | GAMCO INVESTORS, INC. ET AL | 5.01% | `0000807249-25-000101` |
| 2025-11-05 | ICAHN CARL C | 14.79% | `0001539497-25-002847` |

**First mover:** GAMCO INVESTORS, INC. ET AL (2025-08-01).
  - ICAHN CARL C followed 96 days later.

## GDV — GABELLI DIVIDEND & INCOME TRUST · 2 franchises within 78 days, $707M institutional

| Date | Filer | % of class | Accession |
| --- | --- | --- | --- |
| 2024-08-01 | Saba Capital Management, L.P. | 5.06% | `0001062993-24-014277` |
| 2024-10-18 | GAMCO INVESTORS, INC. ET AL | — | `0000807249-24-000141` |

**First mover:** Saba Capital Management, L.P. (2024-08-01).
  - GAMCO INVESTORS, INC. ET AL followed 78 days later.

## SION — Sionna Therapeutics, Inc. · 2 franchises within 5 days, $326M assets

| Date | Filer | % of class | Accession |
| --- | --- | --- | --- |
| 2025-02-13 | ORBIMED ADVISORS LLC | 8.4% | `0000947871-25-000138` |
| 2025-02-18 | RA CAPITAL MANAGEMENT, L.P. | — | `0001415889-25-004419` |

**First mover:** ORBIMED ADVISORS LLC (2025-02-13).
  - RA CAPITAL MANAGEMENT, L.P. followed 5 days later.

## KTF — DWS MUNICIPAL INCOME TRUST · 2 franchises within 127 days, $75M institutional

| Date | Filer | % of class | Accession |
| --- | --- | --- | --- |
| 2023-11-13 | Saba Capital Management, L.P. | 5.45% | `0001062993-23-020590` |
| 2024-03-19 | Bulldog Investors, LLP | — | `0001504304-24-000004` |

**First mover:** Saba Capital Management, L.P. (2023-11-13).
  - Bulldog Investors, LLP followed 127 days later.

## PGZ — Principal Real Estate Income Fund · 2 franchises within 13 days, $15M institutional

| Date | Filer | % of class | Accession |
| --- | --- | --- | --- |
| 2023-10-03 | Saba Capital Management, L.P. | 8.43% | `0001062993-23-018788` |
| 2023-10-16 | Bulldog Investors, LLP | — | `0001504304-23-000024` |

**First mover:** Saba Capital Management, L.P. (2023-10-03).
  - Bulldog Investors, LLP followed 13 days later.

## What this cannot tell you

- **No prediction.** This is a structural/temporal screen, not a signal; the alpha
  question was tested separately and came back null.
- **Size is a threshold, not a market cap.** Results are ranked by `size_usd` — filed
  total assets where a 10-K/10-Q reports one, else the 13F free-float proxy. Neither is
  market cap, assets are not comparable across sectors, and ~17% of issuers have
  neither figure.
- **Recall is deliberately limited.** Only recognised activist franchises are matched,
  so an unlisted or first-time activist will be missed.
- **13D/13G dates only.** Board and 13F layers are snapshots, not time series.
