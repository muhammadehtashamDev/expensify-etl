"""
Shared utility functions.

Covers:
- Date range generation
- Month-name helpers
- Safe nested dictionary access
- Path helpers for the upload folder hierarchy
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Generator, Tuple


# ---------------------------------------------------------------------------
# Date utilities
# ---------------------------------------------------------------------------

MonthPeriod = Tuple[date, date]  # (first_day, last_day) of a calendar month


def month_period(year: int, month: int) -> MonthPeriod:
    """Return the first and last :class:`date` of a calendar month.

    Args:
        year:  Four-digit year (e.g. ``2026``).
        month: Month number 1–12.

    Returns:
        A tuple ``(first_day, last_day)``.
    """
    first = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    last = date(year, month, last_day)
    return first, last


def months_in_year(year: int) -> Generator[MonthPeriod, None, None]:
    """Yield :class:`MonthPeriod` for every month of *year*."""
    for month in range(1, 13):
        yield month_period(year, month)


def months_in_range(start: date, end: date) -> Generator[MonthPeriod, None, None]:
    """Yield :class:`MonthPeriod` for every calendar month that overlaps [start, end].

    The yielded first/last days are clamped to the requested range, meaning
    a partial first or last month is returned with the clamped boundary.

    Args:
        start: Inclusive start date.
        end:   Inclusive end date.
    """
    if start > end:
        raise ValueError(f"start ({start}) must be <= end ({end})")

    current_year, current_month = start.year, start.month
    end_year, end_month = end.year, end.month

    while (current_year, current_month) <= (end_year, end_month):
        first, last = month_period(current_year, current_month)
        yield max(first, start), min(last, end)

        if current_month == 12:
            current_year += 1
            current_month = 1
        else:
            current_month += 1


def month_name(month: int) -> str:
    """Return the English month name for month number 1–12."""
    return calendar.month_name[month]


def format_expensify_date(d: date) -> str:
    """Format a :class:`date` as ``YYYY-MM-DD`` for the Expensify API."""
    return d.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Nested data helpers
# ---------------------------------------------------------------------------

def safe_get(data: dict[str, Any], *keys: str, default: Any = "") -> Any:
    """Safely traverse nested dicts, returning *default* if any key is missing.

    Args:
        data:    The root dictionary.
        *keys:   Sequence of keys to traverse.
        default: Value returned if any key is absent or the value is ``None``.

    Example::

        safe_get(report, "address", "city")  # report["address"]["city"] or ""
    """
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def coerce_float(value: Any, default: float = 0.0) -> float:
    """Convert *value* to float, returning *default* on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def coerce_bool(value: Any, default: bool = False) -> bool:
    """Convert *value* to bool.  Handles strings like ``"true"``/``"1"``."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return default


# ---------------------------------------------------------------------------
# Upload path helpers
# ---------------------------------------------------------------------------

def _csv_stem(year: int, month: int, csv_type: str, file_ts: str) -> str:
    """Build the filename stem: ``<year>_<MM>_<MonthName>[_<csv_type>][_<file_ts>]``."""
    name = month_name(month)
    month_str = f"{month:02d}"
    stem = f"{year}_{month_str}_{name}"
    if csv_type:
        stem += f"_{csv_type}"
    if file_ts:
        stem += f"_{file_ts}"
    return stem


def pending_csv_path(
    base_dir: Path, year: int, month: int, csv_type: str = "", file_ts: str = ""
) -> Path:
    """Return the flat path for a pending CSV file directly under *base_dir*.

    No year/month subdirectories are created — all files for an account land
    in one folder, distinguished by their name and timestamp.

    Args:
        base_dir: Account pending directory (``uploads/pending/<account>``).
        year:     Four-digit year.
        month:    Month number 1–12.
        csv_type: File type — ``"reports"``, ``"transactions"``, or ``"actions"``.
        file_ts:  UTC run timestamp in ``YYYYMMDDTHHMMSSz`` format (e.g. ``20260630T103000Z``).

    Returns:
        ``uploads/pending/<account>/2026_01_January_transactions_20260630T103000Z.csv``
    """
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / f"{_csv_stem(year, month, csv_type, file_ts)}.csv"


def processed_csv_path(
    base_dir: Path, year: int, month: int, csv_type: str = "", file_ts: str = ""
) -> Path:
    """Return the flat path for a processed CSV file directly under *base_dir*.

    Args:
        base_dir: Account processed directory (``uploads/processed/<account>``).
        year:     Four-digit year.
        month:    Month number 1–12.
        csv_type: File type — ``"reports"``, ``"transactions"``, or ``"actions"``.
        file_ts:  UTC run timestamp in ``YYYYMMDDTHHMMSSz`` format.

    Returns:
        ``uploads/processed/<account>/2026_01_January_transactions_20260630T103000Z.csv``
    """
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / f"{_csv_stem(year, month, csv_type, file_ts)}.csv"


def month_csv_exists(base_dir: Path, year: int, month: int, csv_type: str = "transactions") -> bool:
    """Return True if any CSV matching this month and type already exists in *base_dir*.

    Uses a glob pattern so it finds files regardless of their timestamp suffix.

    Args:
        base_dir: Directory to search (account pending or processed dir).
        year:     Four-digit year.
        month:    Month number 1–12.
        csv_type: File type to look for (default ``"transactions"``).
    """
    if not base_dir.exists():
        return False
    name = month_name(month)
    month_str = f"{month:02d}"
    return any(base_dir.glob(f"{year}_{month_str}_{name}_{csv_type}*.csv"))


def range_csv_path(
    base_dir: Path, start: date, end: date, csv_type: str, file_ts: str = ""
) -> Path:
    """Return the flat path for a combined date-range CSV directly under *base_dir*.

    Args:
        base_dir: Account directory (``uploads/pending/<account>``).
        start:    First day of the exported range (inclusive).
        end:      Last day of the exported range (inclusive).
        csv_type: ``"reports"``, ``"transactions"``, or ``"actions"``.
        file_ts:  UTC run timestamp in ``YYYYMMDDTHHMMSSz`` format.

    Returns:
        ``uploads/pending/<account>/2026-01-01_2026-12-31_transactions_20260630T103000Z.csv``
    """
    base_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{start}_{end}_{csv_type}"
    if file_ts:
        stem += f"_{file_ts}"
    return base_dir / f"{stem}.csv"


def range_csv_exists(
    base_dir: Path, start: date, end: date, csv_type: str = "transactions"
) -> bool:
    """Return True if any combined CSV for this date range and type exists in *base_dir*.

    Globs for ``{start}_{end}_{csv_type}*.csv`` so it matches any timestamp suffix.

    Args:
        base_dir: Account directory to search.
        start:    Range start date.
        end:      Range end date.
        csv_type: File type to look for (default ``"transactions"``).
    """
    if not base_dir.exists():
        return False
    return any(base_dir.glob(f"{start}_{end}_{csv_type}*.csv"))
