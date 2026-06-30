"""
Unit tests for the CSV exporter.
"""

from __future__ import annotations

import csv
import io
import tempfile
from datetime import date
from pathlib import Path

import pytest

from scripts.csv_exporter import (
    combine_csv_chunks,
    count_csv_rows,
    csv_already_exists,
    promote_to_processed,
    write_combined_csvs,
    write_csv,
    write_raw_csv,
)
from scripts.transformer import ALL_COLUMNS


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_rows(n: int = 3) -> list[dict]:
    rows = []
    for i in range(n):
        row = {col: f"val_{col}_{i}" for col in ALL_COLUMNS}
        row["amount"] = i * 10.0
        row["report_total"] = 100.0
        rows.append(row)
    return rows


def _raw_csv(header: list[str], rows: list[list[str]]) -> bytes:
    """Build raw CSV bytes as the Expensify API would return."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


START = date(2026, 1, 1)
END = date(2026, 3, 31)


# ---------------------------------------------------------------------------
# write_csv (local / test path)
# ---------------------------------------------------------------------------

class TestWriteCSV:
    def test_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            path = write_csv(_make_rows(5), year=2026, month=7, pending_dir=pending)
            assert path.exists()

    def test_flat_path_under_pending_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            path = write_csv(_make_rows(), year=2026, month=1, pending_dir=pending)
            # Flat: file lives directly inside pending, no year/month subdirs
            assert path.parent == pending
            assert path.name.startswith("2026_01_January")
            assert path.suffix == ".csv"

    def test_row_count_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            path = write_csv(_make_rows(7), year=2026, month=3, pending_dir=pending)
            with path.open(encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                assert len(list(reader)) == 7

    def test_header_columns_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            path = write_csv(_make_rows(1), year=2026, month=4, pending_dir=pending)
            with path.open(encoding="utf-8-sig", newline="") as fh:
                assert list(csv.DictReader(fh).fieldnames) == ALL_COLUMNS

    def test_utf8_bom_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            path = write_csv(_make_rows(1), year=2026, month=5, pending_dir=pending)
            assert path.read_bytes()[:3] == b"\xef\xbb\xbf"

    def test_handles_commas_and_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            rows = _make_rows(1)
            rows[0]["merchant"] = 'Cafe "Le Monde", Paris'
            rows[0]["comment"] = "lunch,\nwith team"
            path = write_csv(rows, year=2026, month=6, pending_dir=pending)
            with path.open(encoding="utf-8-sig", newline="") as fh:
                written = list(csv.DictReader(fh))[0]
            assert written["merchant"] == 'Cafe "Le Monde", Paris'
            assert "lunch" in written["comment"]

    def test_empty_rows_creates_header_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            path = write_csv([], year=2026, month=2, pending_dir=pending)
            with path.open(encoding="utf-8-sig", newline="") as fh:
                assert list(csv.DictReader(fh)) == []


# ---------------------------------------------------------------------------
# combine_csv_chunks
# ---------------------------------------------------------------------------

class TestCombineCsvChunks:
    def test_single_chunk_returned_as_is(self):
        chunk = _raw_csv(["a", "b"], [["1", "2"], ["3", "4"]])
        result = combine_csv_chunks([chunk])
        text = result.decode("utf-8")
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        assert rows[0] == ["a", "b"]
        assert len(rows) == 3  # header + 2 data

    def test_two_chunks_merged_with_one_header(self):
        c1 = _raw_csv(["x", "y"], [["a", "b"]])
        c2 = _raw_csv(["x", "y"], [["c", "d"], ["e", "f"]])
        result = combine_csv_chunks([c1, c2])
        text = result.decode("utf-8")
        rows = list(csv.reader(io.StringIO(text)))
        assert rows[0] == ["x", "y"]
        assert len(rows) == 4  # header + 3 data rows

    def test_empty_chunks_skipped(self):
        c1 = _raw_csv(["col"], [["v1"]])
        result = combine_csv_chunks([b"", c1, b"   \r\n  "])
        rows = list(csv.reader(io.StringIO(result.decode("utf-8"))))
        assert len(rows) == 2  # header + 1 data

    def test_all_empty_returns_empty(self):
        assert combine_csv_chunks([b"", b"  "]) == b""


# ---------------------------------------------------------------------------
# write_combined_csvs
# ---------------------------------------------------------------------------

class TestWriteCombinedCsvs:
    def _make_accumulated(self, n_months: int = 2) -> dict[str, list[bytes]]:
        reports_chunk = _raw_csv(
            ["report_id", "total"],
            [[f"R{i}", str(i * 100)] for i in range(3)],
        )
        txn_chunk = _raw_csv(
            ["report_id", "amount"],
            [[f"R{i}", str(i * 10)] for i in range(5)],
        )
        actions_chunk = _raw_csv(
            ["report_id", "report_name", "action_data"],
            [["R1", "Exp", '{"actorEmail":"a@b.com","message":"hi"}']],
        )
        return {
            "reports": [reports_chunk] * n_months,
            "transactions": [txn_chunk] * n_months,
            "actions": [actions_chunk] * n_months,
        }

    def test_writes_three_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            acc = self._make_accumulated()
            paths = write_combined_csvs(acc, START, END, pending, "acme")
            assert len(paths) == 3
            for p in paths:
                assert p.exists()

    def test_filenames_contain_date_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            paths = write_combined_csvs(self._make_accumulated(), START, END, pending)
            for p in paths:
                assert "2026-01-01_2026-03-31" in p.name

    def test_filenames_contain_csv_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            paths = write_combined_csvs(self._make_accumulated(), START, END, pending)
            names = {p.name for p in paths}
            assert any("reports" in n for n in names)
            assert any("transactions" in n for n in names)
            assert any("actions" in n for n in names)

    def test_all_files_flat_under_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            paths = write_combined_csvs(self._make_accumulated(), START, END, pending)
            for p in paths:
                assert p.parent == pending

    def test_metadata_columns_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            paths = write_combined_csvs(
                self._make_accumulated(), START, END, pending, "testco"
            )
            txn_path = next(p for p in paths if "transactions" in p.name)
            with txn_path.open(encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                row = next(reader)
                assert row["restaurant_name"] == "testco"
                assert "createdAt" in row
                assert "filename" in row

    def test_two_months_data_combined(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            acc = self._make_accumulated(n_months=2)
            paths = write_combined_csvs(acc, START, END, pending)
            txn_path = next(p for p in paths if "transactions" in p.name)
            with txn_path.open(encoding="utf-8-sig", newline="") as fh:
                data = list(csv.DictReader(fh))
            # 5 rows per month × 2 months
            assert len(data) == 10

    def test_utf8_bom_on_all_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            paths = write_combined_csvs(self._make_accumulated(), START, END, pending)
            for p in paths:
                assert p.read_bytes()[:3] == b"\xef\xbb\xbf"

    def test_actions_expanded_to_wide_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            acc = self._make_accumulated()
            paths = write_combined_csvs(acc, START, END, pending)
            act_path = next(p for p in paths if "actions" in p.name)
            with act_path.open(encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                assert "actorEmail" in reader.fieldnames
                assert "message" in reader.fieldnames


# ---------------------------------------------------------------------------
# csv_already_exists (date-range level)
# ---------------------------------------------------------------------------

class TestCSVAlreadyExists:
    def test_returns_false_when_nothing_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            assert not csv_already_exists(START, END, tmp_path / "p", tmp_path / "r")

    def test_returns_true_when_transactions_in_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            acc = {
                "reports": [],
                "transactions": [_raw_csv(["report_id", "amount"], [["R1", "10"]])],
                "actions": [],
            }
            write_combined_csvs(acc, START, END, pending)
            assert csv_already_exists(START, END, pending, Path(tmp) / "proc")

    def test_returns_false_for_different_date_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            acc = {
                "reports": [],
                "transactions": [_raw_csv(["report_id"], [["R1"]])],
                "actions": [],
            }
            write_combined_csvs(acc, START, END, pending)
            other_start = date(2025, 1, 1)
            other_end = date(2025, 12, 31)
            assert not csv_already_exists(other_start, other_end, pending, Path(tmp) / "proc")

    def test_returns_true_when_transactions_in_processed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pending = tmp_path / "pending"
            processed = tmp_path / "processed"
            acc = {
                "reports": [],
                "transactions": [_raw_csv(["report_id"], [["R1"]])],
                "actions": [],
            }
            paths = write_combined_csvs(acc, START, END, pending)
            txn = next(p for p in paths if "transactions" in p.name)
            promote_to_processed(txn, processed)
            assert csv_already_exists(START, END, pending, processed)


# ---------------------------------------------------------------------------
# promote_to_processed
# ---------------------------------------------------------------------------

class TestPromoteToProcessed:
    def test_moves_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pending = tmp_path / "pending"
            processed = tmp_path / "processed"
            path = write_csv(_make_rows(2), year=2026, month=8, pending_dir=pending)
            dest = promote_to_processed(path, processed)
            assert dest.exists()
            assert not path.exists()

    def test_dest_is_flat_under_processed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pending = tmp_path / "pending"
            processed = tmp_path / "processed"
            path = write_csv(_make_rows(1), year=2026, month=9, pending_dir=pending)
            dest = promote_to_processed(path, processed)
            assert dest.parent == processed
            assert dest.name == path.name


# ---------------------------------------------------------------------------
# count_csv_rows
# ---------------------------------------------------------------------------

class TestCountCsvRows:
    def test_counts_data_rows_only(self):
        content = _raw_csv(["a", "b"], [["1", "2"], ["3", "4"], ["5", "6"]])
        assert count_csv_rows(content) == 3

    def test_header_only_returns_zero(self):
        content = _raw_csv(["a", "b"], [])
        assert count_csv_rows(content) == 0

    def test_blank_lines_ignored(self):
        content = b'"a","b"\r\n\r\n"1","2"\r\n\r\n"3","4"\r\n'
        assert count_csv_rows(content) == 2
