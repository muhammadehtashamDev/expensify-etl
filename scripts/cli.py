"""
Command-line interface definition (argparse).

Supports three usage modes:

    python export.py --year 2026
        Export every month of 2026.

    python export.py --start 2026-01-15 --end 2026-04-30
        Export only the specified date range.

    python export.py --month 7 --year 2026
        Export July 2026 only.

Flags:
    --force     Overwrite existing CSV files (skipped by default for resume support).
    --dry-run   Resolve date ranges and print what would be done, without calling the API.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date

from dateutil.parser import parse as parse_date


# ---------------------------------------------------------------------------
# CLI result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CLIArgs:
    """Parsed and validated CLI arguments."""

    start: date
    end: date
    force: bool
    dry_run: bool
    account: str | None  # None = run all configured accounts


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Return a fully-configured :class:`~argparse.ArgumentParser`."""
    parser = argparse.ArgumentParser(
        prog="export.py",
        description=(
            "Download Expensify expense reports and export them to CSV files "
            "organised by year and month."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python export.py --year 2026
  python export.py --month 7 --year 2026
  python export.py --start 2026-01-15 --end 2026-04-30
  python export.py --year 2026 --force
  python export.py --year 2026 --dry-run
""",
    )

    # Mode 1 & 3: --year / --month
    parser.add_argument(
        "--year",
        type=int,
        metavar="YEAR",
        help="Four-digit year.  Exports all months (or --month if specified).",
    )
    parser.add_argument(
        "--month",
        type=int,
        metavar="MONTH",
        choices=range(1, 13),
        help="Month number 1–12.  Requires --year.",
    )

    # Mode 2: --start / --end
    parser.add_argument(
        "--start",
        type=str,
        metavar="DATE",
        help="Start date (inclusive).  ISO-8601 format: YYYY-MM-DD.",
    )
    parser.add_argument(
        "--end",
        type=str,
        metavar="DATE",
        help="End date (inclusive).  ISO-8601 format: YYYY-MM-DD.",
    )

    # Account filter
    parser.add_argument(
        "--account",
        type=str,
        metavar="NAME",
        default=None,
        help=(
            "Run only for a specific account name from config/accounts.json.  "
            "Omit to run for all configured accounts."
        ),
    )

    # Flags
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help=(
            "Overwrite existing CSV files.  "
            "Without this flag, months that already have a CSV are skipped."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print the resolved date ranges without calling the API.",
    )

    return parser


# ---------------------------------------------------------------------------
# Validation & resolution
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> CLIArgs:
    """Parse *argv* and return validated :class:`CLIArgs`.

    Exits with a clear error message if the combination of arguments is invalid.

    Args:
        argv: Raw argument list (defaults to ``sys.argv[1:]``).
    """
    parser = build_parser()
    ns = parser.parse_args(argv)

    has_range = ns.start is not None or ns.end is not None
    has_year = ns.year is not None
    has_month = ns.month is not None

    # --- mutual exclusion -------------------------------------------------
    if has_range and (has_year or has_month):
        parser.error("--start/--end cannot be combined with --year or --month.")

    if has_month and not has_year:
        parser.error("--month requires --year.")

    if has_range and (ns.start is None or ns.end is None):
        parser.error("Both --start and --end must be provided together.")

    if not has_range and not has_year:
        parser.error(
            "Specify one of: --year, --year --month, or --start + --end."
        )

    # --- resolve to (start, end) -----------------------------------------
    try:
        if has_range:
            start = _parse_date(ns.start, "start")
            end = _parse_date(ns.end, "end")
            if start > end:
                parser.error(f"--start ({start}) must be before --end ({end}).")
        elif has_month:
            import calendar
            year, month = ns.year, ns.month
            start = date(year, month, 1)
            last_day = calendar.monthrange(year, month)[1]
            end = date(year, month, last_day)
        else:
            start = date(ns.year, 1, 1)
            end = date(ns.year, 12, 31)
    except ValueError as exc:
        parser.error(str(exc))

    return CLIArgs(
        start=start,
        end=end,
        force=ns.force,
        dry_run=ns.dry_run,
        account=ns.account,
    )


def _parse_date(value: str, label: str) -> date:
    """Parse a date string, raising :class:`ValueError` with a clear message."""
    try:
        return parse_date(value).date()
    except Exception:
        raise ValueError(
            f"Invalid --{label} date: {value!r}. Expected format: YYYY-MM-DD."
        )
