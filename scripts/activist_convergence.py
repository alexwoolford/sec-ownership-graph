#!/usr/bin/env python3
"""
Screen `secgraph` for activist convergence — issuers several activists moved on together.

The "what is heating up" screen, plus the per-issuer timeline behind any hit. Restricted to a
curated list of recognised activist franchises (see
``ingestion/ownership/campaign_timeline.ACTIVIST_FRANCHISES``): precision over recall, because
ungated detection is dominated by micro-cap founders crossing 5% and by filing-group artifacts
where one manager files through several affiliated entities.

Read-only. Nothing is written to the graph.

Run:
    python scripts/activist_convergence.py --database secgraph --since 2023-01-01
    python scripts/activist_convergence.py --database secgraph --timeline MNRO
    python scripts/activist_convergence.py --database secgraph --since 2023-01-01 --markdown
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from secgraph.cli import (
    add_database_argument,
    get_driver_and_database,
    setup_logging,
    verify_neo4j_connection,
)
from secgraph.ingestion.ownership.campaign_timeline import (
    DEFAULT_MIN_ACTIVISTS,
    DEFAULT_WINDOW_DAYS,
    CampaignTimelineEngine,
)
from secgraph.ingestion.ownership.pipeline import provenance_line


def render_markdown(scan, timelines: list, generated: str) -> str:
    """Render the convergence screen as a memo a desk can read."""
    lines = [
        "# Activist convergence screen — `secgraph`",
        "",
        f"*Generated {generated}, read-only. Issuers where two or more recognised activist*",
        "*franchises filed Schedule 13D within a bounded span. Every line is citable to an*",
        "*SEC accession number.*",
        "",
        provenance_line(),
        "",
        "> **How to read the span.** The window is measured first-to-last filing, not rolling, so",
        "> a *third* franchise filing later can push an issuer's span past the limit and drop it",
        "> off this screen. New data can therefore remove a hit as well as add one.",
        "",
    ]
    if scan.abstained:
        lines += [f"**No convergence found** ({scan.result.get('reason')}).", ""]
        return "\n".join(lines)

    lines += [f"**{scan.result['target_count']} issuers** cleared the screen.", ""]
    for h in scan.result["targets"]:
        c = h["company"]
        lines.append(
            f"## {c['ticker'] or c['cik']} — {c['name']}"
            f" · {h['franchise_count']} franchises within {h['span_days']} days"
        )
        lines.append("")
        lines.append("| Date | Filer | % of class | Accession |")
        lines.append("| --- | --- | --- | --- |")
        for f in h["filings"]:
            pct = f"{f['percent_of_class']}%" if f.get("percent_of_class") else "—"
            lines.append(
                f"| {f['filing_date']} | {f['filer']} | {pct} | `{f.get('accession_number') or '—'}` |"
            )
        seq = h.get("sequence") or {}
        if seq.get("first_mover") and seq.get("followers"):
            fm = seq["first_mover"]
            lines.append("")
            lines.append(f"**First mover:** {fm['filer']} ({fm['filing_date']}).")
            for fo in seq["followers"]:
                gap = (
                    f"{fo['days_after_first']} days later" if fo["days_after_first"] else "same day"
                )
                lines.append(f"  - {fo['filer']} followed {gap}.")
        lines.append("")

    for t in timelines:
        if t.abstained:
            continue
        lines += [f"## Full ownership timeline — {t.anchor}", "", "```"]
        lines.append(CampaignTimelineEngine.format_answer(t))
        lines += ["```", ""]

    lines += [
        "## What this cannot tell you",
        "",
        "- **No prediction.** This is a structural/temporal screen, not a signal; the alpha",
        "  question was tested separately and came back null.",
        "- **No materiality ranking.** The graph carries no market-cap or size data, so a",
        "  nano-cap and a large cap appear side by side.",
        "- **Recall is deliberately limited.** Only recognised activist franchises are matched,",
        "  so an unlisted or first-time activist will be missed.",
        "- **13D/13G dates only.** Board and 13F layers are snapshots, not time series.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Screen for activist convergence on secgraph")
    add_database_argument(parser)
    parser.add_argument(
        "--since", help="Only 13D filings on/after this ISO date (e.g. 2023-01-01)."
    )
    parser.add_argument(
        "--min-activists",
        type=int,
        default=DEFAULT_MIN_ACTIVISTS,
        help="Minimum distinct activist franchises on one issuer (default: %(default)s).",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=DEFAULT_WINDOW_DAYS,
        help="Filings must fall within this many days (default: %(default)s).",
    )
    parser.add_argument(
        "--timeline",
        action="append",
        help="Also print the full ownership timeline for this ticker (repeatable).",
    )
    parser.add_argument(
        "--markdown",
        nargs="?",
        const="results/activist_convergence.md",
        help="Write a markdown memo (default path if flag given without a value).",
    )
    args = parser.parse_args()

    logger = setup_logging("activist_convergence", execute=False)
    driver, database = get_driver_and_database(logger, database=args.database)
    try:
        if not verify_neo4j_connection(driver, database, logger):
            return 1
        eng = CampaignTimelineEngine(driver, database=database)
        scan = eng.convergence_scan(
            since=args.since,
            min_activists=args.min_activists,
            window_days=args.window_days,
        )
        print()
        print(CampaignTimelineEngine.format_answer(scan))
        print()

        timelines = []
        for ticker in args.timeline or []:
            t = eng.campaign_timeline(ticker)
            timelines.append(t)
            print(CampaignTimelineEngine.format_answer(t))
            print()

        if args.markdown:
            out = Path(args.markdown)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(render_markdown(scan, timelines, date.today().isoformat()))
            logger.info(f"wrote {out}")
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
