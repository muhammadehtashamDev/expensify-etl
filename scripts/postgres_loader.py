"""PostgreSQL load procedure executor."""

from __future__ import annotations

import csv
import re
import time
from pathlib import Path

import psycopg2
from psycopg2 import OperationalError

from scripts.config import AppConfig
from scripts.logger import get_logger

log = get_logger(__name__)

_PROC_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")
_FAIL_NOTICE_RE = re.compile(
    r"\b(error|fail|failed|failure|exception|cannot|could not)\b",
    re.IGNORECASE,
)

_TEMP_TABLE_BY_CSV_TYPE = {
    "reports": ("temp_data", "expensify_reports"),
    "transactions": ("temp_data", "expensify_transactions"),
    "actions": ("temp_data", "expensify_report_actions"),
}

_HEADER_ALIASES = {
    "filename": "file_name",
    "createdat": "created_at",
    "tag": "tag_name",
    "action_report_id": "reportid",
    "child_oldest_four_account_ids": "child_oldest_four_account_id",
}


def _csv_type_from_name(path: Path) -> str:
    name = path.name.lower()
    for csv_type in ("reports", "transactions", "actions"):
        if f"_{csv_type}_" in name:
            return csv_type
    raise ValueError(f"Cannot infer CSV type from filename: {path.name}")


def _to_snake_case(value: str) -> str:
    out: list[str] = []
    for index, ch in enumerate(value):
        if ch.isupper() and index > 0 and not value[index - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def _get_table_columns(conn, schema: str, table: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (schema, table),
        )
        return [row[0] for row in cur.fetchall()]


def _map_headers_to_columns(headers: list[str], table_columns: list[str]) -> list[str]:
    table_set = set(table_columns)
    mapped: list[str] = []
    missing: list[str] = []

    for header in headers:
        normalized = _to_snake_case(header)
        candidates = [
            header,
            header.lower(),
            normalized,
            normalized.replace("__", "_"),
            _HEADER_ALIASES.get(header.lower(), ""),
            _HEADER_ALIASES.get(normalized, ""),
        ]

        target = next((candidate for candidate in candidates if candidate and candidate in table_set), None)
        if target is None:
            missing.append(header)
        else:
            mapped.append(target)

    if missing:
        raise RuntimeError(
            "CSV header columns were not found in destination table: "
            + ", ".join(missing)
        )

    return mapped


def _load_csvs_to_temp_data(conn, csv_paths: list[Path]) -> None:
    for csv_path in csv_paths:
        csv_type = _csv_type_from_name(csv_path)
        schema, table = _TEMP_TABLE_BY_CSV_TYPE[csv_type]

        with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh)
            headers = next(reader, None)
        if not headers:
            raise RuntimeError(f"CSV has no header row: {csv_path}")

        table_columns = _get_table_columns(conn, schema, table)
        mapped_columns = _map_headers_to_columns(headers, table_columns)

        log.info("Loading %s into %s.%s", csv_path.name, schema, table)
        quoted_columns = ", ".join(f'"{col}"' for col in mapped_columns)
        copy_sql = (
            f'COPY "{schema}"."{table}" ({quoted_columns}) '
            "FROM STDIN WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')"
        )

        with conn.cursor() as cur:
            cur.execute(f'TRUNCATE TABLE "{schema}"."{table}"')
            with csv_path.open("r", encoding="utf-8-sig", newline="") as data_fh:
                cur.copy_expert(copy_sql, data_fh)


def _extract_failure_notices(notices: list[str]) -> list[str]:
    """Return DB notices that look like load-time failures."""
    failed: list[str] = []
    for notice in notices:
        text = notice.strip()
        if _FAIL_NOTICE_RE.search(text):
            failed.append(text)
    return failed


def run_load_procedure(config: AppConfig, csv_paths: list[Path] | None = None) -> None:
    """Execute the configured PostgreSQL procedure.

    The procedure name must be either:
    - function_name
    - schema.function_name
    """
    if not _PROC_NAME_RE.fullmatch(config.db_procedure):
        raise ValueError(
            "DB_PROCEDURE must match 'function_name' or 'schema.function_name'. "
            f"Got: {config.db_procedure!r}"
        )

    query = f"CALL {config.db_procedure}();"

    log.info(
        "Calling PostgreSQL procedure %s on %s:%s/%s",
        config.db_procedure,
        config.db_host,
        config.db_port,
        config.db_name,
    )

    attempts = max(1, config.db_connect_retries + 1)
    for attempt in range(1, attempts + 1):
        try:
            with psycopg2.connect(
                host=config.db_host,
                port=config.db_port,
                dbname=config.db_name,
                user=config.db_user,
                password=config.db_password,
                connect_timeout=config.db_connect_timeout,
            ) as conn:
                existing_notice_count = len(conn.notices)

                if csv_paths:
                    _load_csvs_to_temp_data(conn, csv_paths)

                with conn.cursor() as cur:
                    cur.execute(query)
                conn.commit()

                new_notices = conn.notices[existing_notice_count:]
                failed_notices = _extract_failure_notices(new_notices)
                if failed_notices:
                    raise RuntimeError(
                        "PostgreSQL procedure reported warning/error notices: "
                        + " | ".join(failed_notices[:3])
                    )
            break
        except OperationalError as exc:
            if attempt >= attempts:
                raise RuntimeError(
                    "PostgreSQL connection failed after "
                    f"{attempts} attempt(s) to {config.db_host}:{config.db_port}. "
                    "Verify host, port, firewall rules, and network/VPN access."
                ) from exc

            log.warning(
                "PostgreSQL connection attempt %d/%d failed: %s",
                attempt,
                attempts,
                exc,
            )
            time.sleep(max(0, config.db_connect_retry_delay_seconds))

    log.info("PostgreSQL procedure completed: %s", config.db_procedure)
