"""
JSON → flat row transformer.

The Freemarker template produces a list of records shaped as:

    [
      {
        "report": { ...report fields... },
        "transactions": [ ...transaction objects... ]
      },
      ...
    ]

This module flattens that structure into one flat dict per transaction,
repeating all report-level fields on every row.

Design rules:
- Never lose data: complex nested objects (attendees, actionList) are
  serialised as JSON strings so no information is discarded.
- receiptObject and units are fully flattened into prefixed columns.
- Monetary amounts are already in dollars (the template divides by 100).
- Boolean fields are normalised to Python bool.
- Missing / null fields default to empty string for clean CSV output.
"""

from __future__ import annotations

import json
from typing import Any

from scripts.logger import get_logger
from scripts.utils import coerce_bool, coerce_float, safe_get

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Column ordering — defines the CSV column order
# ---------------------------------------------------------------------------

REPORT_COLUMNS = [
    "report_id",
    "old_report_id",
    "report_name",
    "account_email",
    "account_id",
    "status",
    "display_status",
    "policy_name",
    "policy_id",
    "entry_id",
    "report_currency",
    "report_total",
    "submitter_full_name",
    "manager_full_name",
    "report_created",
    "report_submitted",
    "report_approved",
    "report_reimbursed",
    "is_ach_reimbursed",
    "action_list_json",
]

TRANSACTION_COLUMNS = [
    "transaction_id",
    "transaction_type",
    "merchant",
    "modified_merchant",
    "transaction_created",
    "modified_created",
    "amount",
    "modified_amount",
    "currency",
    "currency_conversion_rate",
    "converted_amount",
    "category",
    "category_gl_code",
    "category_payroll_code",
    "comment",
    "tag",
    "tag_gl_code",
    "reimbursable",
    "billable",
    "has_tax",
    "tax_amount",
    "modified_tax_amount",
    "tax_name",
    "tax_rate",
    "tax_rate_name",
    "tax_code",
    "mcc",
    "modified_mcc",
    "inserted",
    "bank",
    "is_distance",
    "receipt_id",
    "receipt_filename",
    "receipt_small_thumbnail",
    "receipt_thumbnail",
    "receipt_url",
    "receipt_type",
    "receipt_transaction_id",
    "attendees_json",
    "units_count",
    "units_rate",
    "units_unit",
    "units_name",
]

ALL_COLUMNS = REPORT_COLUMNS + TRANSACTION_COLUMNS


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def flatten_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten a list of Expensify export records into per-transaction rows.

    Each element of *records* has the shape::

        {"report": {...}, "transactions": [...]}

    A record with no transactions is skipped (logged at DEBUG level).

    Args:
        records: Raw export records as returned by
                 :meth:`~scripts.client.ExpensifyClient.fetch_reports`.

    Returns:
        A list of flat dicts, each representing one transaction, with all
        keys in :data:`ALL_COLUMNS`.
    """
    rows: list[dict[str, Any]] = []

    for record in records:
        report_data: dict[str, Any] = record.get("report") or {}
        transactions: list[dict[str, Any]] = record.get("transactions") or []

        if not transactions:
            log.debug(
                "Report '%s' (%s) has no transactions — skipping.",
                report_data.get("reportName", ""),
                report_data.get("reportID", ""),
            )
            continue

        report_fields = _extract_report_fields(report_data)

        for txn in transactions:
            txn_fields = _extract_transaction_fields(txn)
            rows.append({**report_fields, **txn_fields})

    log.debug(
        "Transformer: %d record(s) → %d transaction row(s).",
        len(records),
        len(rows),
    )
    return rows


# ---------------------------------------------------------------------------
# Private extractors
# ---------------------------------------------------------------------------


def _extract_report_fields(report: dict[str, Any]) -> dict[str, Any]:
    """Pull all report-level fields into a flat dict."""
    action_list = report.get("actionList") or []
    try:
        action_list_json = json.dumps(action_list, ensure_ascii=False)
    except (TypeError, ValueError):
        action_list_json = "[]"

    return {
        "report_id": safe_get(report, "reportID", default=""),
        "old_report_id": safe_get(report, "oldReportID", default=""),
        "report_name": safe_get(report, "reportName", default=""),
        "account_email": safe_get(report, "accountEmail", default=""),
        "account_id": safe_get(report, "accountID", default=""),
        "status": safe_get(report, "status", default=""),
        "display_status": safe_get(report, "displayStatus", default=""),
        "policy_name": safe_get(report, "policyName", default=""),
        "policy_id": safe_get(report, "policyID", default=""),
        "entry_id": safe_get(report, "entryID", default=""),
        "report_currency": safe_get(report, "currency", default=""),
        "report_total": coerce_float(safe_get(report, "total", default=0)),
        "submitter_full_name": safe_get(report, "submitterFullName", default=""),
        "manager_full_name": safe_get(report, "managerFullName", default=""),
        "report_created": safe_get(report, "created", default=""),
        "report_submitted": safe_get(report, "submitted", default=""),
        "report_approved": safe_get(report, "approved", default=""),
        "report_reimbursed": safe_get(report, "reimbursed", default=""),
        "is_ach_reimbursed": coerce_bool(
            safe_get(report, "isACHReimbursed", default=False)
        ),
        "action_list_json": action_list_json,
    }


def _extract_transaction_fields(txn: dict[str, Any]) -> dict[str, Any]:
    """Pull all transaction-level fields into a flat dict."""
    # Nested objects
    receipt_obj: dict[str, Any] = txn.get("receiptObject") or {}
    units: dict[str, Any] = txn.get("units") or {}
    attendees: list[Any] = txn.get("attendees") or []

    try:
        attendees_json = json.dumps(attendees, ensure_ascii=False)
    except (TypeError, ValueError):
        attendees_json = "[]"

    return {
        "transaction_id": safe_get(txn, "transactionID", default=""),
        "transaction_type": safe_get(txn, "type", default=""),
        "merchant": safe_get(txn, "merchant", default=""),
        "modified_merchant": safe_get(txn, "modifiedMerchant", default=""),
        "transaction_created": safe_get(txn, "created", default=""),
        "modified_created": safe_get(txn, "modifiedCreated", default=""),
        "amount": coerce_float(safe_get(txn, "amount", default=0)),
        "modified_amount": coerce_float(safe_get(txn, "modifiedAmount", default=0)),
        "currency": safe_get(txn, "currency", default=""),
        "currency_conversion_rate": safe_get(txn, "currencyConversionRate", default=""),
        "converted_amount": coerce_float(safe_get(txn, "convertedAmount", default=0)),
        "category": safe_get(txn, "category", default=""),
        "category_gl_code": safe_get(txn, "categoryGlCode", default=""),
        "category_payroll_code": safe_get(txn, "categoryPayrollCode", default=""),
        "comment": safe_get(txn, "comment", default=""),
        "tag": safe_get(txn, "tag", default=""),
        "tag_gl_code": safe_get(txn, "tagGlCode", default=""),
        "reimbursable": coerce_bool(safe_get(txn, "reimbursable", default=False)),
        "billable": coerce_bool(safe_get(txn, "billable", default=False)),
        "has_tax": coerce_bool(safe_get(txn, "hasTax", default=False)),
        "tax_amount": coerce_float(safe_get(txn, "taxAmount", default=0)),
        "modified_tax_amount": coerce_float(safe_get(txn, "modifiedTaxAmount", default=0)),
        "tax_name": safe_get(txn, "taxName", default=""),
        "tax_rate": safe_get(txn, "taxRate", default=""),
        "tax_rate_name": safe_get(txn, "taxRateName", default=""),
        "tax_code": safe_get(txn, "taxCode", default=""),
        "mcc": safe_get(txn, "mcc", default=""),
        "modified_mcc": safe_get(txn, "modifiedMCC", default=""),
        "inserted": safe_get(txn, "inserted", default=""),
        "bank": safe_get(txn, "bank", default=""),
        "is_distance": coerce_bool(safe_get(txn, "isDistance", default=False)),
        "receipt_id": safe_get(txn, "receiptID", default=""),
        "receipt_filename": safe_get(txn, "receiptFilename", default=""),
        # receiptObject sub-fields
        "receipt_small_thumbnail": safe_get(receipt_obj, "smallThumbnail", default=""),
        "receipt_thumbnail": safe_get(receipt_obj, "thumbnail", default=""),
        "receipt_url": safe_get(receipt_obj, "url", default=""),
        "receipt_type": safe_get(receipt_obj, "type", default=""),
        "receipt_transaction_id": safe_get(receipt_obj, "transactionID", default=""),
        # attendees → JSON string
        "attendees_json": attendees_json,
        # units sub-fields
        "units_count": coerce_float(units.get("count", 0)),
        "units_rate": coerce_float(units.get("rate", 0)),
        "units_unit": str(units.get("unit", "")),
        "units_name": str(units.get("name", "")),
    }
