"""
Unit tests for date utilities and path helpers in scripts.utils.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile

import pytest

from scripts.utils import (
    coerce_bool,
    coerce_float,
    format_expensify_date,
    month_name,
    month_period,
    months_in_range,
    months_in_year,
    pending_csv_path,
    processed_csv_path,
    safe_get,
)


# ---------------------------------------------------------------------------
# month_period
# ---------------------------------------------------------------------------

class TestMonthPeriod:
    def test_january(self):
        first, last = month_period(2026, 1)
        assert first == date(2026, 1, 1)
        assert last == date(2026, 1, 31)

    def test_february_non_leap(self):
        first, last = month_period(2026, 2)
        assert last == date(2026, 2, 28)

    def test_february_leap(self):
        first, last = month_period(2024, 2)
        assert last == date(2024, 2, 29)

    def test_december(self):
        first, last = month_period(2026, 12)
        assert first == date(2026, 12, 1)
        assert last == date(2026, 12, 31)


# ---------------------------------------------------------------------------
# months_in_year
# ---------------------------------------------------------------------------

class TestMonthsInYear:
    def test_yields_twelve_months(self):
        periods = list(months_in_year(2026))
        assert len(periods) == 12

    def test_first_and_last(self):
        periods = list(months_in_year(2026))
        assert periods[0] == (date(2026, 1, 1), date(2026, 1, 31))
        assert periods[-1] == (date(2026, 12, 1), date(2026, 12, 31))


# ---------------------------------------------------------------------------
# months_in_range
# ---------------------------------------------------------------------------

class TestMonthsInRange:
    def test_single_month(self):
        periods = list(months_in_range(date(2026, 3, 1), date(2026, 3, 31)))
        assert len(periods) == 1
        assert periods[0] == (date(2026, 3, 1), date(2026, 3, 31))

    def test_partial_first_month(self):
        periods = list(months_in_range(date(2026, 1, 15), date(2026, 2, 28)))
        assert len(periods) == 2
        assert periods[0] == (date(2026, 1, 15), date(2026, 1, 31))
        assert periods[1] == (date(2026, 2, 1), date(2026, 2, 28))

    def test_partial_last_month(self):
        periods = list(months_in_range(date(2026, 3, 1), date(2026, 3, 15)))
        assert len(periods) == 1
        assert periods[0] == (date(2026, 3, 1), date(2026, 3, 15))

    def test_cross_year(self):
        periods = list(months_in_range(date(2025, 11, 1), date(2026, 1, 31)))
        assert len(periods) == 3
        assert periods[0][0].year == 2025

    def test_invalid_range_raises(self):
        with pytest.raises(ValueError):
            list(months_in_range(date(2026, 6, 1), date(2026, 1, 1)))

    def test_same_day(self):
        periods = list(months_in_range(date(2026, 6, 15), date(2026, 6, 15)))
        assert len(periods) == 1
        assert periods[0] == (date(2026, 6, 15), date(2026, 6, 15))


# ---------------------------------------------------------------------------
# month_name
# ---------------------------------------------------------------------------

class TestMonthName:
    @pytest.mark.parametrize("month,expected", [
        (1, "January"), (6, "June"), (12, "December"),
    ])
    def test_names(self, month, expected):
        assert month_name(month) == expected


# ---------------------------------------------------------------------------
# format_expensify_date
# ---------------------------------------------------------------------------

class TestFormatExpensifyDate:
    def test_format(self):
        assert format_expensify_date(date(2026, 7, 4)) == "2026-07-04"


# ---------------------------------------------------------------------------
# safe_get
# ---------------------------------------------------------------------------

class TestSafeGet:
    def test_nested_hit(self):
        d = {"a": {"b": {"c": "value"}}}
        assert safe_get(d, "a", "b", "c") == "value"

    def test_missing_key(self):
        d = {"a": {}}
        assert safe_get(d, "a", "b") == ""

    def test_custom_default(self):
        assert safe_get({}, "x", default=42) == 42

    def test_none_value(self):
        d = {"a": None}
        assert safe_get(d, "a") == ""

    def test_non_dict_intermediate(self):
        d = {"a": "string"}
        assert safe_get(d, "a", "b") == ""


# ---------------------------------------------------------------------------
# coerce_float
# ---------------------------------------------------------------------------

class TestCoerceFloat:
    def test_from_int(self):
        assert coerce_float(100) == 100.0

    def test_from_string(self):
        assert coerce_float("3.14") == pytest.approx(3.14)

    def test_from_none(self):
        assert coerce_float(None) == 0.0

    def test_from_invalid(self):
        assert coerce_float("abc") == 0.0

    def test_custom_default(self):
        assert coerce_float("bad", default=-1.0) == -1.0


# ---------------------------------------------------------------------------
# coerce_bool
# ---------------------------------------------------------------------------

class TestCoerceBool:
    def test_bool_passthrough(self):
        assert coerce_bool(True) is True
        assert coerce_bool(False) is False

    def test_string_true(self):
        assert coerce_bool("true") is True
        assert coerce_bool("True") is True
        assert coerce_bool("1") is True
        assert coerce_bool("yes") is True

    def test_string_false(self):
        assert coerce_bool("false") is False
        assert coerce_bool("no") is False
        assert coerce_bool("0") is False

    def test_int(self):
        assert coerce_bool(1) is True
        assert coerce_bool(0) is False


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

class TestPathHelpers:
    def test_pending_csv_path_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = pending_csv_path(base, 2026, 7)
            assert path.parent.name == "July"
            assert path.parent.parent.name == "2026"
            assert path.name == "2026_07_July.csv"
            assert path.parent.exists()

    def test_pending_csv_path_with_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            assert pending_csv_path(base, 2026, 1, "reports").name == "2026_01_January_reports.csv"
            assert pending_csv_path(base, 2026, 1, "transactions").name == "2026_01_January_transactions.csv"
            assert pending_csv_path(base, 2026, 1, "actions").name == "2026_01_January_actions.csv"

    def test_processed_csv_path_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = processed_csv_path(base, 2026, 1)
            assert path.name == "2026_01_January.csv"

    def test_processed_csv_path_with_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            assert processed_csv_path(base, 2026, 3, "transactions").name == "2026_03_March_transactions.csv"
