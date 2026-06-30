"""
Unit tests for the JSON → flat row transformer.

The Freemarker template produces records shaped as:
    {"report": {...}, "transactions": [...]}

Tests verify that flatten_records correctly unpacks this structure.
"""

from __future__ import annotations

import json

import pytest

from scripts.transformer import flatten_records, ALL_COLUMNS, REPORT_COLUMNS, TRANSACTION_COLUMNS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_report(**overrides) -> dict:
    base = {
        "reportID": "R001",
        "oldReportID": "",
        "reportName": "Q1 Expenses",
        "accountEmail": "alice@example.com",
        "accountID": "ACC123",
        "status": "APPROVED",
        "displayStatus": "Approved",
        "policyName": "Default Policy",
        "policyID": "P123",
        "entryID": "",
        "currency": "USD",
        "total": 150.0,
        "submitterFullName": "Alice Smith",
        "managerFullName": "Bob Jones",
        "created": "2026-01-01",
        "submitted": "2026-01-05",
        "approved": "2026-01-10",
        "reimbursed": "",
        "isACHReimbursed": "false",
        "actionList": [],
    }
    base.update(overrides)
    return base


def make_transaction(**overrides) -> dict:
    base = {
        "transactionID": "T001",
        "type": "cash",
        "merchant": "Starbucks",
        "modifiedMerchant": "",
        "created": "2026-01-03",
        "modifiedCreated": "",
        "amount": 5.50,
        "modifiedAmount": 0.0,
        "currency": "USD",
        "currencyConversionRate": "1",
        "convertedAmount": 5.50,
        "category": "Meals",
        "categoryGlCode": "6001",
        "categoryPayrollCode": "",
        "comment": "Team coffee",
        "tag": "",
        "tagGlCode": "",
        "reimbursable": True,
        "billable": False,
        "hasTax": True,
        "taxAmount": 0.45,
        "modifiedTaxAmount": 0.0,
        "taxName": "GST",
        "taxRate": "10%",
        "taxRateName": "Standard",
        "taxCode": "TAX01",
        "mcc": "5812",
        "modifiedMCC": "",
        "inserted": "2026-01-03T12:00:00",
        "bank": "Chase",
        "isDistance": False,
        "receiptID": "REC001",
        "receiptFilename": "receipt.png",
        "receiptObject": {
            "smallThumbnail": "https://example.com/small.png",
            "thumbnail": "https://example.com/thumb.png",
            "transactionID": "T001",
            "type": "image",
            "url": "https://example.com/receipt.png",
        },
        "attendees": [
            {"displayName": "Alice Smith", "email": "alice@example.com", "thumbnail": ""},
        ],
        "units": {
            "count": 0,
            "rate": 0,
            "unit": "",
            "name": "",
        },
    }
    base.update(overrides)
    return base


def make_record(report_overrides=None, txns=None) -> dict:
    """Build a single {"report": {...}, "transactions": [...]} record."""
    report = make_report(**(report_overrides or {}))
    transactions = txns if txns is not None else [make_transaction()]
    return {"report": report, "transactions": transactions}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFlattenRecords:
    def test_empty_input(self):
        assert flatten_records([]) == []

    def test_record_with_no_transactions_is_skipped(self):
        record = make_record(txns=[])
        result = flatten_records([record])
        assert result == []

    def test_single_transaction_row_count(self):
        rows = flatten_records([make_record()])
        assert len(rows) == 1

    def test_multiple_transactions_expand_correctly(self):
        txns = [make_transaction(transactionID=f"T{i}") for i in range(5)]
        rows = flatten_records([make_record(txns=txns)])
        assert len(rows) == 5

    def test_report_fields_on_every_row(self):
        txns = [make_transaction(transactionID=f"T{i}") for i in range(3)]
        record = make_record(report_overrides={"reportID": "R999"}, txns=txns)
        rows = flatten_records([record])
        for row in rows:
            assert row["report_id"] == "R999"
            assert row["account_email"] == "alice@example.com"
            assert row["submitter_full_name"] == "Alice Smith"
            assert row["manager_full_name"] == "Bob Jones"

    def test_transaction_fields_differ_per_row(self):
        txns = [
            make_transaction(transactionID="T1", merchant="Starbucks"),
            make_transaction(transactionID="T2", merchant="McDonald's"),
        ]
        rows = flatten_records([make_record(txns=txns)])
        assert rows[0]["merchant"] == "Starbucks"
        assert rows[1]["merchant"] == "McDonald's"
        assert rows[0]["transaction_id"] == "T1"
        assert rows[1]["transaction_id"] == "T2"

    def test_all_columns_present(self):
        rows = flatten_records([make_record()])
        for col in ALL_COLUMNS:
            assert col in rows[0], f"Missing column: {col}"

    def test_missing_report_fields_default_to_empty(self):
        record = {"report": {"reportID": "R1"}, "transactions": [make_transaction()]}
        rows = flatten_records([record])
        assert rows[0]["report_name"] == ""
        assert rows[0]["status"] == ""
        assert rows[0]["policy_name"] == ""

    def test_missing_transaction_fields_default_to_empty(self):
        record = make_record(txns=[{"transactionID": "T1"}])
        rows = flatten_records([record])
        assert rows[0]["merchant"] == ""
        assert rows[0]["category"] == ""
        assert rows[0]["tax_name"] == ""

    def test_numeric_amounts_preserved(self):
        txn = make_transaction(amount=12.50, taxAmount=1.25, convertedAmount=12.50)
        rows = flatten_records([make_record(txns=[txn])])
        assert rows[0]["amount"] == pytest.approx(12.50)
        assert rows[0]["tax_amount"] == pytest.approx(1.25)
        assert rows[0]["converted_amount"] == pytest.approx(12.50)

    def test_numeric_coercion_from_string(self):
        txn = make_transaction(amount="8.75", modifiedAmount="0")
        rows = flatten_records([make_record(txns=[txn])])
        assert rows[0]["amount"] == pytest.approx(8.75)
        assert rows[0]["modified_amount"] == pytest.approx(0.0)

    def test_bool_true_values(self):
        txn = make_transaction(reimbursable=True, billable=True, hasTax=True, isDistance=True)
        rows = flatten_records([make_record(txns=[txn])])
        assert rows[0]["reimbursable"] is True
        assert rows[0]["billable"] is True
        assert rows[0]["has_tax"] is True
        assert rows[0]["is_distance"] is True

    def test_bool_false_values(self):
        txn = make_transaction(reimbursable=False, billable=False, hasTax=False, isDistance=False)
        rows = flatten_records([make_record(txns=[txn])])
        assert rows[0]["reimbursable"] is False
        assert rows[0]["billable"] is False

    def test_is_ach_reimbursed_string_coercion(self):
        # The template outputs isACHReimbursed as a string "true" or "false"
        record = make_record(report_overrides={"isACHReimbursed": "true"})
        rows = flatten_records([record])
        assert rows[0]["is_ach_reimbursed"] is True

        record2 = make_record(report_overrides={"isACHReimbursed": "false"})
        rows2 = flatten_records([record2])
        assert rows2[0]["is_ach_reimbursed"] is False

    def test_receipt_object_flattened(self):
        txn = make_transaction()
        rows = flatten_records([make_record(txns=[txn])])
        assert rows[0]["receipt_url"] == "https://example.com/receipt.png"
        assert rows[0]["receipt_small_thumbnail"] == "https://example.com/small.png"
        assert rows[0]["receipt_thumbnail"] == "https://example.com/thumb.png"
        assert rows[0]["receipt_type"] == "image"
        assert rows[0]["receipt_transaction_id"] == "T001"

    def test_units_flattened(self):
        txn = make_transaction(units={"count": 5, "rate": 2.0, "unit": "km", "name": "Distance"})
        rows = flatten_records([make_record(txns=[txn])])
        assert rows[0]["units_count"] == pytest.approx(5.0)
        assert rows[0]["units_rate"] == pytest.approx(2.0)
        assert rows[0]["units_unit"] == "km"
        assert rows[0]["units_name"] == "Distance"

    def test_attendees_serialised_as_json(self):
        attendees = [
            {"displayName": "Alice", "email": "alice@example.com", "thumbnail": ""},
            {"displayName": "Bob", "email": "bob@example.com", "thumbnail": ""},
        ]
        txn = make_transaction(attendees=attendees)
        rows = flatten_records([make_record(txns=[txn])])
        parsed = json.loads(rows[0]["attendees_json"])
        assert len(parsed) == 2
        assert parsed[0]["email"] == "alice@example.com"

    def test_action_list_serialised_as_json(self):
        actions = [
            {"action": "submit", "accountEmail": "alice@example.com", "created": "2026-01-05", "details": {}}
        ]
        record = make_record(report_overrides={"actionList": actions})
        rows = flatten_records([record])
        parsed = json.loads(rows[0]["action_list_json"])
        assert len(parsed) == 1
        assert parsed[0]["action"] == "submit"

    def test_multiple_records_combined(self):
        r1 = make_record(report_overrides={"reportID": "R1"}, txns=[make_transaction(transactionID="T1")])
        r2 = make_record(report_overrides={"reportID": "R2"}, txns=[
            make_transaction(transactionID="T2"),
            make_transaction(transactionID="T3"),
        ])
        rows = flatten_records([r1, r2])
        assert len(rows) == 3
        assert [r["report_id"] for r in rows] == ["R1", "R2", "R2"]

    def test_missing_report_key_produces_no_crash(self):
        record = {"transactions": [make_transaction()]}
        rows = flatten_records([record])
        assert len(rows) == 1
        assert rows[0]["report_id"] == ""

    def test_missing_transactions_key_skips_record(self):
        record = {"report": make_report()}
        rows = flatten_records([record])
        assert rows == []

    def test_null_transactions_skips_record(self):
        record = {"report": make_report(), "transactions": None}
        rows = flatten_records([record])
        assert rows == []

    def test_report_total_coerced(self):
        record = make_record(report_overrides={"total": "250.75"})
        rows = flatten_records([record])
        assert rows[0]["report_total"] == pytest.approx(250.75)

    def test_column_order_matches_all_columns(self):
        assert ALL_COLUMNS == REPORT_COLUMNS + TRANSACTION_COLUMNS
