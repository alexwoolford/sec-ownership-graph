#!/usr/bin/env python3
"""
Stage SEC bulk ownership datasets into the pipeline cache.

- ``--form 345``  Form 3/4/5 insider quarterly TSV zips (one download covers all
  filers) — required for Phase 1.
- ``--form 13f``  Form 13F institutional quarterly zips (~87 MB each) — Phase 2b.
- ``--form ftd``  Fails-to-deliver twice-monthly zips (the keyed CUSIP↔SYMBOL
  bridge for the crosswalk) — Phase 2b prereq. Use more periods for wider
  security coverage (each ~1.5 MB); the union of symbols is what matters.
- ``--form 13dg`` Schedule 13D/13G has no bulk set; the header crawl happens
  inside load_beneficial_owners.py, so this form only prints guidance.

Downloads the most-recent ``--quarters`` periods (default 4; for ftd these are
half-month files, so raise it — e.g. 14 covers ~7 months). URLs are discovered by
scraping the SEC landing page (never hard-coded). ``--refresh`` re-downloads even
if cached. This is a read-only staging step (no Neo4j), so no --execute gate.

Usage:
    python scripts/download_ownership_data.py --form 345 --quarters 8
    python scripts/download_ownership_data.py --form 13f --quarters 1
    python scripts/download_ownership_data.py --form ftd --quarters 14
    python scripts/download_ownership_data.py --form 345 --refresh
"""

import argparse
from datetime import date

from secgraph.cli import setup_logging
from secgraph.ingestion.ownership.bulk_datasets import (
    FORM_13F_LANDING,
    FORM_345_LANDING,
    FTD_LANDING,
    download_dataset,
)

_LANDINGS = {"345": FORM_345_LANDING, "13f": FORM_13F_LANDING, "ftd": FTD_LANDING}
_SUBDIRS = {"345": "form345", "13f": "form13f", "ftd": "ftd"}


def main():
    parser = argparse.ArgumentParser(description="Download SEC bulk ownership datasets")
    parser.add_argument(
        "--form",
        choices=["345", "13f", "ftd", "13dg"],
        required=True,
        help="Dataset family to stage",
    )
    parser.add_argument(
        "--quarters",
        type=int,
        default=4,
        help="Number of most-recent periods to download (default: 4)",
    )
    parser.add_argument("--refresh", action="store_true", help="Re-download even if cached")
    parser.add_argument(
        "--as-of",
        default=None,
        metavar="YYYY-MM-DD",
        help="Only stage periods ending on or before this date. Without it, 'most recent N' "
        "resolves against today, so the staged window slides forward between runs and a "
        "rebuild cannot reproduce an earlier one.",
    )
    args = parser.parse_args()

    if args.as_of:
        try:
            date.fromisoformat(args.as_of)
        except ValueError:
            print(f"ERROR: --as-of {args.as_of!r} must be YYYY-MM-DD")
            raise SystemExit(1) from None

    logger = setup_logging("download_ownership_data", execute=True)
    logger.info("=" * 80)
    logger.info(f"Download SEC ownership data — form {args.form}")
    logger.info("=" * 80)

    if args.form == "13dg":
        logger.info(
            "Schedule 13D/13G has no bulk dataset. It is crawled per-subject "
            "(header only) inside load_beneficial_owners.py. Nothing to stage here."
        )
        return

    landing = _LANDINGS[args.form]
    subdir = _SUBDIRS[args.form]

    paths = download_dataset(
        landing,
        subdir,
        num_quarters=args.quarters,
        refresh=args.refresh,
        as_of=args.as_of,
        log=logger,
    )
    logger.info(f"✓ Staged {len(paths)} zip(s) under data/sec_ownership/{subdir}/")
    for path in paths:
        logger.info(f"  {path.name}")


if __name__ == "__main__":
    main()
