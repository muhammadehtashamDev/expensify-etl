"""
Unit tests for the CSV exporter.
"""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path

import pytest

from scripts.csv_exporter import (
    csv_already_exists,
    promote_to_processed,
    write_csv,
    write_raw_csv,
)
from scripts.transformer import ALL_COLUMNS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_rows(n: int = 3) -> list[dict]:
    rows = []
    for i in range(n):
        row = {col: f"val_{col}_{i}" for col in ALL_COLUMNS}
        row["amount"] = i * 10.0
        row["report_total"] = 100.0
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# write_csv
# ---------------------------------------------------------------------------

class TestWriteCSV:
    def test_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            rows = _make_rows(5)
            path = write_csv(rows, year=2026, month=7, pending_dir=pending)
            assert path.exists()

    def test_correct_path_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            path = write_csv(_make_rows(), year=2026, month=1, pending_dir=pending)
            assert path.parent.name == "January"
            assert path.parent.parent.name == "2026"
            assert path.name == "2026_01_January.csv"

    def test_row_count_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            rows = _make_rows(7)
            path = write_csv(rows, year=2026, month=3, pending_dir=pending)
            with path.open(encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                written_rows = list(reader)
            assert len(written_rows) == 7

    def test_header_columns_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            path = write_csv(_make_rows(1), year=2026, month=4, pending_dir=pending)
            with path.open(encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                fieldnames = reader.fieldnames
            assert list(fieldnames) == ALL_COLUMNS

    def test_utf8_bom_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            path = write_csv(_make_rows(1), year=2026, month=5, pending_dir=pending)
            raw = path.read_bytes()
            assert raw[:3] == b"\xef\xbb\xbf", "UTF-8 BOM missing"

    def test_handles_commas_and_quotes_in_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            rows = _make_rows(1)
            rows[0]["merchant"] = 'Cafe "Le Monde", Paris'
            rows[0]["comment"] = "lunch,\nwith team"
            path = write_csv(rows, year=2026, month=6, pending_dir=pending)
            with path.open(encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                written = list(reader)[0]
            assert written["merchant"] == 'Cafe "Le Monde", Paris'
            assert "lunch" in written["comment"]

    def test_empty_rows_creates_header_only_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            path = write_csv([], year=2026, month=2, pending_dir=pending)
            with path.open(encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                assert list(reader) == []


# ---------------------------------------------------------------------------
# promote_to_processed
# ---------------------------------------------------------------------------

class TestPromoteToProcessed:
    def test_moves_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pending = tmp_path / "pending"
            processed = tmp_path / "processed"

            csv_path = write_csv(_make_rows(2), year=2026, month=8, pending_dir=pending)
            assert csv_path.exists()

            dest = promote_to_processed(csv_path, year=2026, month=8, processed_dir=processed)
            assert dest.exists()
            assert not csv_path.exists()

    def test_dest_has_correct_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pending = tmp_path / "pending"
            processed = tmp_path / "processed"

            csv_path = write_csv(_make_rows(1), year=2026, month=9, pending_dir=pending)
            dest = promote_to_processed(csv_path, year=2026, month=9, processed_dir=processed)

            assert dest.parent.name == "September"
            assert dest.parent.parent.name == "2026"
            assert dest.name == "2026_09_September.csv"


# ---------------------------------------------------------------------------
# csv_already_exists
# ---------------------------------------------------------------------------

class TestCSVAlreadyExists:
    # csv_already_exists uses the "transactions" file as the canonical marker

    def test_returns_false_when_nothing_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            assert not csv_already_exists(2026, 7, tmp_path / "p", tmp_path / "r")

    def test_returns_true_when_transactions_pending_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pending = tmp_path / "pending"
            content = b'"report_id","report_name"\r\n"R1","Jan expenses"\r\n'
            write_raw_csv(content, year=2026, month=7, pending_dir=pending, csv_type="transactions")
            assert csv_already_exists(2026, 7, pending, tmp_path / "proc")

    def test_returns_false_when_only_reports_pending_exists(self):
        # Only the transactions file triggers the skip; other types alone do not
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pending = tmp_path / "pending"
            content = b'"report_id"\r\n"R1"\r\n'
            write_raw_csv(content, year=2026, month=7, pending_dir=pending, csv_type="reports")
            assert not csv_already_exists(2026, 7, pending, tmp_path / "proc")

    def test_returns_true_when_transactions_processed_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pending = tmp_path / "pending"
            processed = tmp_path / "processed"
            content = b'"report_id","report_name"\r\n"R1","Jan"\r\n'
            csv_path = write_raw_csv(
                content, year=2026, month=7, pending_dir=pending, csv_type="transactions"
            )
            promote_to_processed(csv_path, year=2026, month=7, processed_dir=processed, csv_type="transactions")
            assert csv_already_exists(2026, 7, pending, processed)
