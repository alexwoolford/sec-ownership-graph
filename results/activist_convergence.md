# Activist convergence screen — `secgraph`

*Generated 2026-08-04, read-only. Issuers where two or more recognised activist*
*franchises filed Schedule 13D within a bounded span. Every line is citable to an*
*SEC accession number.*

> **Provenance.** Data as of `2026-06-30` · staging pinned to `2026-06-30` · Form 3/4/5 window `2022q3–2026q2` (16 quarters) · control figures from `reference_csv`. Figures below are specific to this window: a rebuild with a different `--as-of` will legitimately differ.

> **How to read the span.** The window is measured first-to-last filing, not rolling, so
> a *third* franchise filing later can push an issuer's span past the limit and drop it
> off this screen. New data can therefore remove a hit as well as add one.

**5 issuers** cleared the screen.

## MNRO — MONRO, INC. · 2 franchises within 96 days

| Date | Filer | % of class | Accession |
| --- | --- | --- | --- |
| 2025-08-01 | GAMCO INVESTORS, INC. ET AL | 5.01% | `0000807249-25-000101` |
| 2025-11-05 | ICAHN CARL C | 14.79% | `0001539497-25-002847` |

**First mover:** GAMCO INVESTORS, INC. ET AL (2025-08-01).
  - ICAHN CARL C followed 96 days later.

## SION — Sionna Therapeutics, Inc. · 2 franchises within 5 days

| Date | Filer | % of class | Accession |
| --- | --- | --- | --- |
| 2025-02-13 | ORBIMED ADVISORS LLC | 8.4% | `0000947871-25-000138` |
| 2025-02-18 | RA CAPITAL MANAGEMENT, L.P. | — | `0001415889-25-004419` |

**First mover:** ORBIMED ADVISORS LLC (2025-02-13).
  - RA CAPITAL MANAGEMENT, L.P. followed 5 days later.

## GDV — GABELLI DIVIDEND & INCOME TRUST · 2 franchises within 78 days

| Date | Filer | % of class | Accession |
| --- | --- | --- | --- |
| 2024-08-01 | Saba Capital Management, L.P. | 5.06% | `0001062993-24-014277` |
| 2024-10-18 | GAMCO INVESTORS, INC. ET AL | — | `0000807249-24-000141` |

**First mover:** Saba Capital Management, L.P. (2024-08-01).
  - GAMCO INVESTORS, INC. ET AL followed 78 days later.

## KTF — DWS MUNICIPAL INCOME TRUST · 2 franchises within 127 days

| Date | Filer | % of class | Accession |
| --- | --- | --- | --- |
| 2023-11-13 | Saba Capital Management, L.P. | 5.45% | `0001062993-23-020590` |
| 2024-03-19 | Bulldog Investors, LLP | — | `0001504304-24-000004` |

**First mover:** Saba Capital Management, L.P. (2023-11-13).
  - Bulldog Investors, LLP followed 127 days later.

## PGZ — Principal Real Estate Income Fund · 2 franchises within 13 days

| Date | Filer | % of class | Accession |
| --- | --- | --- | --- |
| 2023-10-03 | Saba Capital Management, L.P. | 8.43% | `0001062993-23-018788` |
| 2023-10-16 | Bulldog Investors, LLP | — | `0001504304-23-000024` |

**First mover:** Saba Capital Management, L.P. (2023-10-03).
  - Bulldog Investors, LLP followed 13 days later.

## Full ownership timeline — MONRO, INC.

```
Ownership timeline — MONRO, INC. (17 dated filings):
  2010-01-29  13G           BlackRock Inc. (passive_index) [0001086364-10-008427]
  2012-02-10  13G           VANGUARD GROUP INC (passive_index) [0000932471-12-003113]
  2013-02-14  13G           JANUS CAPITAL MANAGEMENT LLC (other_holder) [0000812295-13-000064]
  2018-01-10  13G           WASATCH ADVISORS LP (other_holder) [0000814133-18-000002]
  2018-02-09  13G           ArrowMark Colorado Holdings LLC (other_holder) [0001172661-18-000314]
  2018-02-14  13G           PRICE T ROWE ASSOCIATES INC /MD/ (other_holder) [0000080255-18-002347]
  2018-02-14  13G           Clearbridge Investments, LLC (other_holder) [0001140361-18-007804]
  2019-02-13  13G           Arlington Value Capital, LLC (other_holder) [0001606587-19-000254]
  2022-08-10  13G           T. Rowe Price Investment Management, Inc. (passive_index) [0001897612-22-000249]
  2023-02-06  13G           WELLINGTON MANAGEMENT GROUP LLP (other_holder) [0000846087-23-000259]
  2025-01-23  13G           DIMENSIONAL FUND ADVISORS LP (passive_index) [0000354204-25-000511]
  2025-04-29  13G           BlackRock, Inc. (passive_index) [0002052113-25-001915]
  2025-05-15  13G           NOMURA HOLDINGS INC (custodian) [0000905148-25-001767]
  2025-05-15  13G           COOPER CREEK PARTNERS MANAGEMENT LLC (other_holder) [0001512162-25-000044]
  2025-08-01  13D    5.01%  GAMCO INVESTORS, INC. ET AL (activist) [0000807249-25-000101]
  2025-11-05  13D   14.79%  ICAHN CARL C (activist) [0001539497-25-002847]
  2025-11-13  13G           Adage Capital Management, L.P. (passive_index) [0000902664-25-004877]

First mover: GAMCO INVESTORS, INC. ET AL on 2025-08-01 at 5.01%
  → ICAHN CARL C followed 96 days later at 14.79%
```

## What this cannot tell you

- **No prediction.** This is a structural/temporal screen, not a signal; the alpha
  question was tested separately and came back null.
- **No materiality ranking.** The graph carries no market-cap or size data, so a
  nano-cap and a large cap appear side by side.
- **Recall is deliberately limited.** Only recognised activist franchises are matched,
  so an unlisted or first-time activist will be missed.
- **13D/13G dates only.** Board and 13F layers are snapshots, not time series.
