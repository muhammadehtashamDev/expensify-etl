"""
CSV exporter.

Primary pipeline path — ``write_combined_csvs``:
    Accepts raw CSV byte chunks accumulated across all months of the requested
    range, merges them into three single files (reports, transactions, actions),
    and writes them flat under ``uploads/pending/<account>/``.

    File name format:
        ``2026-01-01_2026-12-31_transactions_20260630T103000Z.csv``

    One UTC run timestamp is shared across all three files so they sort
    together in the filesystem.

Secondary path — ``write_csv`` / ``write_raw_csv``:
    Low-level helpers kept for local tooling.
"""

from __future__ import annotations

import csv
import io
import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from scripts.logger import get_logger
from scripts.transformer import ALL_COLUMNS
from scripts.utils import pending_csv_path, range_csv_exists, range_csv_path

log = get_logger(__name__)

_UTF8_BOM = b"\xef\xbb\xbf"

CSV_TYPES = ("reports", "transactions", "actions")
_ACTION_BASE_COLUMNS = frozenset({"report_id", "old_report_id", "report_name", "restaurant_name", "filename", "created_at"})


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


def _run_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _file_ts(dt: datetime) -> str:
    """Filename-safe UTC timestamp: ``20260630T103000Z``."""
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _iso_ts(dt: datetime) -> str:
    """ISO-8601 UTC timestamp for the ``createdAt`` metadata column."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Metadata injection
# ---------------------------------------------------------------------------


def _add_metadata_columns(
    content: bytes,
    restaurant_name: str,
    filename: str,
    created_at: str,
) -> bytes:
    """Append ``restaurant_name``, ``filename``, and ``createdAt`` to every row."""
    lines = [ln for ln in content.splitlines() if ln.strip()]
    if not lines:
        return content

    text = b"\r\n".join(lines).decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")

    for i, row in enumerate(reader):
        if i == 0:
            writer.writerow(row + ["restaurant_name", "filename", "createdAt"])
        else:
            writer.writerow(row + [restaurant_name, filename, created_at])

    return buf.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# Chunk combiners
# ---------------------------------------------------------------------------


def combine_csv_chunks(chunks: list[bytes]) -> bytes:
    """Merge multiple raw CSV byte chunks, keeping only the first header row.

    Blank lines (emitted by Freemarker between directives) are stripped.
    Each chunk after the first has its header row removed so the output
    contains exactly one header followed by all data rows.

    Args:
        chunks: List of raw CSV byte strings, one per calendar month.

    Returns:
        Merged UTF-8 bytes (no BOM; the caller adds the BOM).
    """
    all_rows: list[list[str]] = []
    header_written = False

    for chunk in chunks:
        lines = [ln for ln in chunk.splitlines() if ln.strip()]
        if not lines:
            continue
        text = b"\r\n".join(lines).decode("utf-8-sig", errors="replace")
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        if not rows:
            continue
        if not header_written:
            all_rows.append(rows[0])
            header_written = True
        all_rows.extend(rows[1:])

    if not all_rows:
        return b""

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerows(all_rows)
    return buf.getvalue().encode("utf-8")


def _combine_actions_chunks(
    chunks: list[bytes],
    csv_path: Path,
    restaurant_name: str,
    created_at: str,
) -> bytes:
    """Combine compact action chunks from all months into a single wide CSV.

    Each chunk is a 4-column CSV (report_id, old_report_id, report_name, action_data JSON).
    All chunks are merged first, then the wide-key expansion is performed
    once so that every unique action key discovered across the whole date
    range becomes a column — even if it only appears in one month's data.
    """
    all_raw_rows: list[dict[str, str]] = []

    for chunk in chunks:
        lines = [ln for ln in chunk.splitlines() if ln.strip()]
        if not lines:
            continue
        text = b"\r\n".join(lines).decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        all_raw_rows.extend(reader)

    if not all_raw_rows:
        return b"report_id,old_report_id,report_name,restaurant_name,filename,createdAt\r\n"

    expanded: list[tuple[str, str, str, dict[str, Any]]] = []
    all_keys: list[str] = []
    seen_keys: set[str] = set()
    key_aliases: dict[str, str] = {}

    def normalize_header(value: str) -> str:
        out: list[str] = []
        for index, ch in enumerate(value):
            if ch.isupper() and index > 0 and not value[index - 1].isupper():
                out.append("_")
            out.append(ch.lower())
        return "".join(out)

    used_normalized_headers = set(_ACTION_BASE_COLUMNS)

    def unique_action_header(key: str) -> str:
        if key in key_aliases:
            return key_aliases[key]

        candidate = key
        normalized = normalize_header(candidate)
        if normalized in used_normalized_headers:
            candidate = f"action_{key}"
            normalized = normalize_header(candidate)

        suffix = 2
        while normalized in used_normalized_headers:
            candidate = f"action_{key}_{suffix}"
            normalized = normalize_header(candidate)
            suffix += 1

        used_normalized_headers.add(normalized)
        key_aliases[key] = candidate
        return candidate

    for row in all_raw_rows:
        report_id = row.get("report_id", "")
        old_report_id = row.get("old_report_id", "")
        report_name = row.get("report_name", "")
        raw_json = row.get("action_data", "").strip()

        try:
            action_obj: dict[str, Any] = json.loads(raw_json) if raw_json else {}
        except json.JSONDecodeError:
            action_obj = {"_raw": raw_json}

        for key in action_obj:
            if key == "reportID":
                continue
            output_key = unique_action_header(key)
            if output_key not in seen_keys:
                seen_keys.add(output_key)
                all_keys.append(output_key)

        expanded.append((report_id, old_report_id, report_name, action_obj))

    fieldnames = (
        ["report_id", "old_report_id", "report_name"] + all_keys + ["restaurant_name", "filename", "createdAt"]
    )

    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\r\n"
    )
    writer.writeheader()

    for report_id, old_report_id, report_name, action_obj in expanded:
        flat: dict[str, str] = {
            "report_id": report_id,
            "old_report_id": old_report_id,
            "report_name": report_name,
        }
        for source_key, output_key in key_aliases.items():
            val = action_obj.get(source_key, "")
            if isinstance(val, (dict, list)):
                val = json.dumps(val, ensure_ascii=False)
            elif val is None:
                val = ""
            else:
                val = str(val)
            flat[output_key] = val
        flat["restaurant_name"] = restaurant_name
        flat["filename"] = csv_path.name
        flat["createdAt"] = created_at
        writer.writerow(flat)

    return buf.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# Primary pipeline path: write one combined CSV per type for the full range
# ---------------------------------------------------------------------------


def write_combined_csvs(
    accumulated: dict[str, list[bytes]],
    start_date: date,
    end_date: date,
    pending_dir: Path,
    restaurant_name: str = "",
    run_at: datetime | None = None,
) -> list[Path]:
    """Merge all monthly chunks and write three combined CSV files.

    Each file covers the full requested date range and is written flat
    under *pending_dir* with a UTC timestamp in the filename.

    Args:
        accumulated:     Dict mapping ``"reports"``, ``"transactions"``,
                         ``"actions"`` to lists of raw CSV byte chunks
                         (one chunk per calendar month).
        start_date:      First day of the requested range.
        end_date:        Last day of the requested range.
        pending_dir:     Account pending directory
                         (``uploads/pending/<account>``).
        restaurant_name: Account name injected as a metadata column.
        run_at:          UTC datetime of this pipeline run.  All three files
                         share this timestamp so they sort together.

    Returns:
        List of three :class:`~pathlib.Path` objects in order:
        reports, transactions, actions.
    """
    dt = run_at or _run_now()
    ts = _file_ts(dt)
    created_at = _iso_ts(dt)
    written: list[Path] = []

    for csv_type in CSV_TYPES:
        chunks = accumulated.get(csv_type, [])
        csv_path = range_csv_path(pending_dir, start_date, end_date, csv_type, ts)

        if csv_type == "actions":
            content = _combine_actions_chunks(chunks, csv_path, restaurant_name, created_at)
        else:
            combined = combine_csv_chunks(chunks)
            content = _add_metadata_columns(combined, restaurant_name, csv_path.name, created_at)

        if not content.startswith(_UTF8_BOM):
            content = _UTF8_BOM + content

        csv_path.write_bytes(content)
        written.append(csv_path)

        log.info(
            "Combined CSV written: %s (%.1f KB) rows≈%s",
            csv_path.name,
            len(content) / 1024,
            _count_lines(content) - 1,
        )

    return written


def _count_lines(content: bytes) -> int:
    """Quick line count for logging (not CSV-aware, used only for INFO messages)."""
    return content.count(b"\n")


# ---------------------------------------------------------------------------
# Resume check — date-range level
# ---------------------------------------------------------------------------


def csv_already_exists(
    start_date: date,
    end_date: date,
    pending_dir: Path,
    processed_dir: Path,
) -> bool:
    """Return True if a combined transactions CSV for this date range already exists.

    Checks both pending and processed directories using a glob so files are
    found regardless of their timestamp suffix.

    Args:
        start_date:    Range start (matches the filename prefix).
        end_date:      Range end (matches the filename prefix).
        pending_dir:   Account pending directory.
        processed_dir: Account processed directory.
    """
    return (
        range_csv_exists(pending_dir, start_date, end_date, "transactions")
        or range_csv_exists(processed_dir, start_date, end_date, "transactions")
    )


# ---------------------------------------------------------------------------
# Promote pending → processed
# ---------------------------------------------------------------------------


def promote_to_processed(csv_path: Path, processed_dir: Path) -> Path:
    """Move a pending CSV to the processed directory, preserving its filename.

    Args:
        csv_path:      Source path (in pending).
        processed_dir: Account processed directory.

    Returns:
        New path in the processed directory.
    """
    processed_dir.mkdir(parents=True, exist_ok=True)
    dest = processed_dir / csv_path.name
    shutil.move(str(csv_path), str(dest))
    log.info("Promoted to processed: %s → %s", csv_path.name, dest)
    return dest


# ---------------------------------------------------------------------------
# Row counter (used by pipeline for per-month progress display)
# ---------------------------------------------------------------------------


def count_csv_rows(content: bytes) -> int:
    """Return the number of data rows in *content* (header excluded).

    Args:
        content: Raw CSV bytes from the API.

    Returns:
        Number of data rows (0 if header-only).
    """
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    return sum(1 for row in rows[1:] if any(cell.strip() for cell in row))


# ---------------------------------------------------------------------------
# Low-level helpers — kept for write_csv (local tooling)
# ---------------------------------------------------------------------------


def write_raw_csv(
    content: bytes,
    year: int,
    month: int,
    pending_dir: Path,
    csv_type: str = "",
    restaurant_name: str = "",
    run_at: datetime | None = None,
) -> Path:
    """Write a single raw CSV chunk to the pending directory (test / local use).

    The primary pipeline path is :func:`write_combined_csvs`.  This function
    is retained for unit tests that need to create individual files.
    """
    dt = run_at or _run_now()
    csv_path = pending_csv_path(pending_dir, year, month, csv_type, _file_ts(dt))
    created_at = _iso_ts(dt)

    enriched = _add_metadata_columns(content, restaurant_name, csv_path.name, created_at)
    if not enriched.startswith(_UTF8_BOM):
        enriched = _UTF8_BOM + enriched

    csv_path.write_bytes(enriched)
    log.info("Raw CSV written: %s (%.1f KB)", csv_path, len(enriched) / 1024)
    return csv_path


def write_csv(
    rows: list[dict[str, Any]],
    year: int,
    month: int,
    pending_dir: Path,
    run_at: datetime | None = None,
) -> Path:
    """Write flat transformer dicts to a pending CSV (test / local use).

    The primary pipeline path is :func:`write_combined_csvs`.
    """
    dt = run_at or _run_now()
    csv_path = pending_csv_path(pending_dir, year, month, file_ts=_file_ts(dt))

    log.info("Writing CSV: path=%s rows=%d", csv_path, len(rows))

    with csv_path.open(mode="w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=ALL_COLUMNS,
            extrasaction="ignore",
            quoting=csv.QUOTE_MINIMAL,
        )
        writer.writeheader()
        writer.writerows(rows)

    log.info("CSV written: %s (%.1f KB)", csv_path, csv_path.stat().st_size / 1024)
    return csv_path
