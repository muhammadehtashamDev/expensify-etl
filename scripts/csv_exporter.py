"""
CSV exporter.

Two export paths are supported:

1. ``write_raw_csv`` — saves raw CSV bytes downloaded directly from the
   Expensify API (Freemarker template generates CSV server-side).  This is
   the primary pipeline path.  Pass ``csv_type`` to distinguish the three
   output files (``"reports"``, ``"transactions"``, ``"actions"``).

2. ``write_csv`` — kept for testing and local use; writes flat dicts via
   Python's csv module (requires transformer output).

Files are written to ``uploads/pending/`` and stay there until the
database developer provides an insertion function.  ``promote_to_processed``
is retained but not called by the main pipeline.
"""

from __future__ import annotations

import csv
import io
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.logger import get_logger
from scripts.transformer import ALL_COLUMNS
from scripts.utils import pending_csv_path, processed_csv_path

log = get_logger(__name__)

_UTF8_BOM = b"\xef\xbb\xbf"

# The three CSV types produced per month
CSV_TYPES = ("reports", "transactions", "actions")


# ---------------------------------------------------------------------------
# Metadata injection
# ---------------------------------------------------------------------------


def _add_metadata_columns(
    content: bytes,
    restaurant_name: str,
    filename: str,
    created_at: str,
) -> bytes:
    """Append ``restaurant_name``, ``filename``, and ``createdAt`` columns to every row.

    Blank lines (Freemarker whitespace) are stripped before parsing.
    The returned bytes are UTF-8 encoded with CRLF line endings (no BOM —
    the caller adds the BOM).

    Args:
        content:         Raw CSV bytes from the API.
        restaurant_name: Account/restaurant name (e.g. ``"parkbars"``).
        filename:        Output file base name (e.g. ``2026_01_January_transactions.csv``).
        created_at:      ISO-8601 UTC timestamp string for the ``createdAt`` column.
    """
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


def _now_utc() -> str:
    """Return the current UTC time as an ISO-8601 string (e.g. ``2026-06-30T10:30:00Z``)."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Primary path: raw CSV bytes from the API
# ---------------------------------------------------------------------------


def write_raw_csv(
    content: bytes,
    year: int,
    month: int,
    pending_dir: Path,
    csv_type: str = "",
    restaurant_name: str = "",
) -> Path:
    """Write raw CSV bytes (from the Expensify download) to the pending directory.

    Prepends a UTF-8 BOM so Excel opens the file correctly without a
    manual encoding selection step.

    Blank lines that Freemarker may emit between template directives are
    stripped before saving.

    Args:
        content:     Raw CSV bytes as returned by the API download step.
        year:        Four-digit year (used to build the folder path).
        month:       Month number 1–12.
        pending_dir: Root pending directory (``uploads/pending``).
        csv_type:    File type suffix — ``"reports"``, ``"transactions"``,
                     or ``"actions"``.  Produces e.g.
                     ``2026_01_January_transactions.csv``.

    Returns:
        The absolute path of the written CSV file.
    """
    csv_path = pending_csv_path(pending_dir, year, month, csv_type)
    created_at = _now_utc()

    enriched = _add_metadata_columns(content, restaurant_name, csv_path.name, created_at)

    if not enriched.startswith(_UTF8_BOM):
        enriched = _UTF8_BOM + enriched

    csv_path.write_bytes(enriched)

    log.info(
        "Raw CSV written: %s (%.1f KB) createdAt=%s",
        csv_path,
        len(enriched) / 1024,
        created_at,
    )
    return csv_path


def count_csv_rows(content: bytes) -> int:
    """Return the number of data rows in *content* (excludes the header).

    Skips the header row and any blank lines.  Uses Python's ``csv`` module
    to correctly handle multi-line values (e.g. comments with embedded
    newlines).

    Args:
        content: Raw CSV bytes from the API.

    Returns:
        Number of data rows (0 if the file contains only a header).
    """
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    # First row is the header; count non-empty remaining rows
    return sum(1 for row in rows[1:] if any(cell.strip() for cell in row))


# ---------------------------------------------------------------------------
# Actions path: expand compact JSON-column CSV into wide dynamic-column CSV
# ---------------------------------------------------------------------------


def write_actions_csv(
    content: bytes,
    year: int,
    month: int,
    pending_dir: Path,
    restaurant_name: str = "",
) -> Path:
    """Expand the compact actions CSV into a wide CSV with all action keys as columns.

    The actions Freemarker template serializes each entire action object as JSON
    in a single ``action_data`` column, because different action types carry
    different keys (submit, approve, comment, etc.).  This function:

    1. Reads the compact 3-column CSV (``report_id``, ``report_name``,
       ``action_data``).
    2. Parses the ``action_data`` JSON for every row.
    3. Discovers the full set of keys across *all* action objects in the file.
    4. Writes a wide CSV where ``report_id`` + ``report_name`` come first,
       followed by every unique action key as its own column.

    Nested sub-objects and arrays inside an action key are serialised back to
    a JSON string so no data is ever lost.

    Args:
        content:     Raw CSV bytes from the API (compact 3-column format).
        year:        Four-digit year.
        month:       Month number 1–12.
        pending_dir: Root pending directory (``uploads/pending``).

    Returns:
        The absolute path of the written CSV file.
    """
    # Strip blank lines emitted by Freemarker whitespace between directives
    lines = [ln for ln in content.splitlines() if ln.strip()]
    clean = b"\r\n".join(lines)
    text = clean.decode("utf-8-sig", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    raw_rows = list(reader)

    csv_path = pending_csv_path(pending_dir, year, month, "actions")

    created_at = _now_utc()

    if not raw_rows:
        csv_path.write_bytes(_UTF8_BOM + b"report_id,report_name,restaurant_name,filename,createdAt\r\n")
        log.info("Actions CSV written (no data): %s", csv_path)
        return csv_path

    # --- Parse each action_data JSON and discover all unique keys ----------
    expanded: list[tuple[str, str, dict[str, Any]]] = []
    all_keys: list[str] = []   # ordered, de-duped
    seen_keys: set[str] = set()

    for row in raw_rows:
        report_id = row.get("report_id", "")
        report_name = row.get("report_name", "")
        raw_json = row.get("action_data", "").strip()

        try:
            action_obj: dict[str, Any] = json.loads(raw_json) if raw_json else {}
        except json.JSONDecodeError:
            action_obj = {"_raw": raw_json}

        for key in action_obj:
            if key not in seen_keys:
                seen_keys.add(key)
                all_keys.append(key)

        expanded.append((report_id, report_name, action_obj))

    # --- Write wide CSV ----------------------------------------------------
    fieldnames = ["report_id", "report_name"] + all_keys + ["restaurant_name", "filename", "createdAt"]

    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=fieldnames,
        extrasaction="ignore",
        lineterminator="\r\n",
    )
    writer.writeheader()

    for report_id, report_name, action_obj in expanded:
        flat: dict[str, str] = {"report_id": report_id, "report_name": report_name}
        for key in all_keys:
            val = action_obj.get(key, "")
            if isinstance(val, (dict, list)):
                val = json.dumps(val, ensure_ascii=False)
            elif val is None:
                val = ""
            else:
                val = str(val)
            flat[key] = val
        flat["restaurant_name"] = restaurant_name
        flat["filename"] = csv_path.name
        flat["createdAt"] = created_at
        writer.writerow(flat)

    csv_path.write_bytes(_UTF8_BOM + buf.getvalue().encode("utf-8"))

    log.info(
        "Actions CSV written: %s (%d rows, %d columns: %s…)",
        csv_path,
        len(expanded),
        len(fieldnames),
        ", ".join(all_keys[:5]),
    )
    return csv_path


# ---------------------------------------------------------------------------
# Secondary path: write from flat dicts (used by tests / local tooling)
# ---------------------------------------------------------------------------


def write_csv(
    rows: list[dict[str, Any]],
    year: int,
    month: int,
    pending_dir: Path,
) -> Path:
    """Write *rows* to a pending CSV file and return its path.

    UTF-8 BOM is prepended so Excel opens the file without garbling
    non-ASCII characters.

    Args:
        rows:        Flat transaction dicts from :func:`~scripts.transformer.flatten_reports`.
        year:        Four-digit year (used to build the path).
        month:       Month number 1–12.
        pending_dir: Root pending directory (``uploads/pending``).

    Returns:
        The absolute :class:`~pathlib.Path` of the written file.

    Raises:
        OSError: If the file cannot be written.
    """
    csv_path = pending_csv_path(pending_dir, year, month)

    log.info(
        "Writing CSV: path=%s rows=%d columns=%d",
        csv_path,
        len(rows),
        len(ALL_COLUMNS),
    )

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


def promote_to_processed(
    csv_path: Path,
    year: int,
    month: int,
    processed_dir: Path,
    csv_type: str = "",
) -> Path:
    """Move a pending CSV to the processed directory.

    After the move, any empty month or year directory left behind in pending
    is removed automatically (``rmdir`` is a no-op on non-empty directories
    so it is always safe to attempt).

    Args:
        csv_path:      The pending CSV path returned by :func:`write_raw_csv`.
        year:          Four-digit year.
        month:         Month number 1–12.
        processed_dir: Root processed directory (``uploads/processed``).
        csv_type:      File type suffix (``"reports"``, ``"transactions"``,
                       ``"actions"``).

    Returns:
        The new path in the processed directory.
    """
    dest = processed_csv_path(processed_dir, year, month, csv_type)

    shutil.move(str(csv_path), str(dest))
    log.info("Promoted to processed: %s → %s", csv_path, dest)

    # Remove empty month and year directories left behind in pending.
    # Walking from deepest to shallowest: month_dir → year_dir.
    # rmdir() silently fails if the directory still has files (other types
    # not yet moved), so it is always safe to call.
    month_dir = csv_path.parent        # e.g. uploads/pending/2026/January
    year_dir = month_dir.parent        # e.g. uploads/pending/2026

    for directory in (month_dir, year_dir):
        try:
            directory.rmdir()
            log.debug("Removed empty pending directory: %s", directory)
        except OSError:
            break  # still has files — stop, don't touch parent either

    return dest


def csv_already_exists(
    year: int,
    month: int,
    pending_dir: Path,
    processed_dir: Path,
) -> bool:
    """Return True if the transactions CSV for this month already exists.

    The transactions file is used as the canonical completion marker.
    If it exists (pending or processed) the month is considered done and
    will be skipped unless ``--force`` was passed.

    Args:
        year:          Four-digit year.
        month:         Month number 1–12.
        pending_dir:   Root pending directory.
        processed_dir: Root processed directory.
    """
    pending = pending_csv_path(pending_dir, year, month, "transactions")
    processed = processed_csv_path(processed_dir, year, month, "transactions")
    return pending.exists() or processed.exists()
