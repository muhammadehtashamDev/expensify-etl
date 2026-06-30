"""
Unit tests for CLI argument parsing.
"""

from __future__ import annotations

from datetime import date

import pytest

from scripts.cli import parse_args


class TestCLIYearMode:
    def test_full_year(self):
        args = parse_args(["--year", "2026"])
        assert args.start == date(2026, 1, 1)
        assert args.end == date(2026, 12, 31)
        assert args.force is False
        assert args.dry_run is False

    def test_year_with_force(self):
        args = parse_args(["--year", "2026", "--force"])
        assert args.force is True

    def test_year_with_dry_run(self):
        args = parse_args(["--year", "2026", "--dry-run"])
        assert args.dry_run is True


class TestCLIMonthMode:
    def test_month_and_year(self):
        args = parse_args(["--month", "7", "--year", "2026"])
        assert args.start == date(2026, 7, 1)
        assert args.end == date(2026, 7, 31)

    def test_february_non_leap(self):
        args = parse_args(["--month", "2", "--year", "2026"])
        assert args.end == date(2026, 2, 28)

    def test_february_leap(self):
        args = parse_args(["--month", "2", "--year", "2024"])
        assert args.end == date(2024, 2, 29)

    def test_month_without_year_fails(self, capsys):
        with pytest.raises(SystemExit):
            parse_args(["--month", "7"])


class TestCLIRangeMode:
    def test_explicit_range(self):
        args = parse_args(["--start", "2026-01-15", "--end", "2026-04-30"])
        assert args.start == date(2026, 1, 15)
        assert args.end == date(2026, 4, 30)

    def test_range_with_force(self):
        args = parse_args(["--start", "2026-03-01", "--end", "2026-03-31", "--force"])
        assert args.force is True


class TestCLIValidation:
    def test_no_args_fails(self):
        with pytest.raises(SystemExit):
            parse_args([])

    def test_start_without_end_fails(self):
        with pytest.raises(SystemExit):
            parse_args(["--start", "2026-01-01"])

    def test_range_and_year_fails(self):
        with pytest.raises(SystemExit):
            parse_args(["--start", "2026-01-01", "--end", "2026-01-31", "--year", "2026"])

    def test_start_after_end_fails(self):
        with pytest.raises(SystemExit):
            parse_args(["--start", "2026-06-01", "--end", "2026-01-31"])

    def test_invalid_date_format_fails(self):
        with pytest.raises(SystemExit):
            parse_args(["--start", "not-a-date", "--end", "2026-01-31"])
