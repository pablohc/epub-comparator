#!/usr/bin/env python3
"""
epub-comparator — Compare EPUB files across ORIGINAL, WEB-OPTIMIZED and
INTEGRATE-OPTIMIZED versions.

Usage:
  python epub_comparator.py list
  python epub_comparator.py validate [<book>] [--all] [--errors-only]
  python epub_comparator.py diff [<book>] [--all] [--detail summary|full] [--version web|integrate|both]
  python epub_comparator.py report [--output report.html] [--json report.json]
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parent

# Ensure the package is importable when run directly
sys.path.insert(0, str(ROOT))

from epub_comparator import discovery, differ, reporter, validator
from epub_comparator.models import VersionLabel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_triplets():
    triplets = discovery.discover(ROOT)
    if not triplets:
        print("No EPUB files found. Check that the three version directories exist.")
        sys.exit(1)
    return triplets


def _resolve_book(triplets, query: str):
    tri = discovery.find_triplet(triplets, query)
    if tri is None:
        print(f"No book matching '{query}' found.")
        sys.exit(1)
    return tri


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list(args):
    triplets = _load_triplets()
    reporter.print_list(triplets)


def cmd_validate(args):
    triplets = _load_triplets()

    if args.all or not args.book:
        book_list = triplets
    else:
        book_list = [_resolve_book(triplets, args.book)]

    # Group results per book: [[orig, web, intg], [orig, web, intg], ...]
    grouped = [
        [validator.validate(src) for src in tri.versions()]
        for tri in book_list
    ]
    reporter.print_validation(grouped, errors_only=args.errors_only)


def cmd_diff(args):
    triplets = _load_triplets()
    detail = args.detail

    if args.all or not args.book:
        book_triplets = triplets
    else:
        book_triplets = [_resolve_book(triplets, args.book)]

    version_arg = getattr(args, "version", "both")

    for tri in book_triplets:
        if version_arg in ("web", "both") and tri.web_optimized and tri.original:
            result = differ.diff_pair(tri, VersionLabel.WEB_OPTIMIZED)
            reporter.print_diff(result, detail=detail)
        if version_arg in ("integrate", "both") and tri.integrate_optimized and tri.original:
            result = differ.diff_pair(tri, VersionLabel.INTEGRATE_OPTIMIZED)
            reporter.print_diff(result, detail=detail)


def cmd_report(args):
    triplets = _load_triplets()
    output_html = Path(args.output) if args.output else ROOT / "report.html"
    output_json = Path(args.json) if args.json else None

    # Run validation for all — grouped by book (same as print_validation expects)
    val_results = [
        [validator.validate(src) for src in tri.versions()]
        for tri in triplets
    ]

    # Run diffs for all
    diff_results = []
    for tri in triplets:
        diff_results.extend(differ.diff_all(tri))

    reporter.export_html(triplets, val_results, diff_results, output_html)

    if output_json:
        reporter.export_json({
            "triplets": triplets,
            "validation": val_results,
            "diffs": diff_results,
        }, output_json)


def cmd_pr_summary(args):
    triplets = _load_triplets()
    output = Path(args.output) if args.output else ROOT / "pr_summary.json"

    val_results = [
        [validator.validate(src) for src in tri.versions()]
        for tri in triplets
    ]

    diff_results = []
    for tri in triplets:
        diff_results.extend(differ.diff_all(tri))

    reporter.export_pr_summary(triplets, val_results, diff_results, output)


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="epub_comparator",
        description="Compare EPUB files across Original / Web-Optimized / Integrate-Optimized versions.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # list
    sub.add_parser("list", help="List all books and their match status")

    # validate
    val_p = sub.add_parser("validate", help="Validate EPUB spec compliance")
    val_p.add_argument("book", nargs="?", help="Book name (substring match)")
    val_p.add_argument("--all", action="store_true", help="Validate all books")
    val_p.add_argument("--errors-only", action="store_true", help="Show only errors and warnings")

    # diff
    diff_p = sub.add_parser("diff", help="Show differences between versions")
    diff_p.add_argument("book", nargs="?", help="Book name (substring match)")
    diff_p.add_argument("--all", action="store_true", help="Diff all books")
    diff_p.add_argument(
        "--detail",
        choices=["summary", "full"],
        default="summary",
        help="summary: tables only (default); full: include CSS/XHTML text diffs",
    )
    diff_p.add_argument(
        "--version",
        choices=["web", "integrate", "both"],
        default="both",
        help="Which optimized version to compare against (default: both)",
    )

    # report
    rep_p = sub.add_parser("report", help="Generate a full HTML/JSON report")
    rep_p.add_argument("--output", metavar="FILE", help="HTML output path (default: report.html)")
    rep_p.add_argument("--json", metavar="FILE", help="Optional JSON output path")

    # pr-summary
    pr_p = sub.add_parser("pr-summary", help="Generate a compact JSON for PR description agents")
    pr_p.add_argument("--output", metavar="FILE", help="JSON output path (default: pr_summary.json)")

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    {
        "list":       cmd_list,
        "validate":   cmd_validate,
        "diff":       cmd_diff,
        "report":     cmd_report,
        "pr-summary": cmd_pr_summary,
    }[args.command](args)


if __name__ == "__main__":
    main()
