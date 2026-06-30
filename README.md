# Expensify Data Pipeline

A Python ETL pipeline that downloads expense reports from the Expensify Integration Server API and saves them as CSV files organised by account, year, and month for importing into any downstream system.

The pipeline calls three Freemarker templates per month. The Expensify server generates the CSVs; the pipeline downloads them as raw bytes and saves them to `uploads/pending/`.

---

## Features

- **Multi-account support** — credentials for all accounts live in `.env`; each account writes to its own subdirectory
- **Rate-limit aware** — dual sliding-window enforcer (3/10s, 12/60s); stays well below Expensify's published limits
- **Automatic retries** — exponential back-off on 429/5xx/timeout/connection errors via tenacity (separate schedules for 429 vs transient errors)
- **Resume support** — skips the entire run if combined CSVs for the requested date range already exist; use `--force` to overwrite
- **Rich terminal UI** — progress bars, spinners, per-month status table, summary
- **Structured logging** — rotating `app.log` + `error.log`; every request is timed and logged
- **Three CSV files per run** — one `reports`, one `transactions`, one `actions` covering the full requested date range, written flat under `uploads/pending/<account>/` with a UTC timestamp in the filename
- **UTF-8 BOM CSV** — Excel-compatible
- **Config via `.env`** — no secrets in source code
- **Cleanup utility** — configurable retention policy; removes stale processed files
- **Unit tested** — rate limiter, date utils, transformer, CSV exporter, CLI

---

## Project Structure

```
expensify-etl/
├── export.py                    # Main entry point
├── requirements.txt
├── .env.example                 # Safe template — copy to .env and fill in values
├── .gitignore
├── README.md
│
├── config/
│   ├── accounts.example.json    # Example format for the JSON fallback
│   ├── accounts.json            # Gitignored — not needed when using .env
│   └── templates/
│       ├── reports_template.ftl      # Freemarker template: one row per report
│       ├── transactions_template.ftl # Freemarker template: one row per transaction
│       └── actions_template.ftl      # Freemarker template: one row per action entry
│
├── scripts/
│   ├── __init__.py
│   ├── accounts.py              # Multi-account loader (env vars → JSON fallback)
│   ├── config.py                # Environment config loader
│   ├── logger.py                # Logging setup
│   ├── rate_limiter.py          # Dual-window token-bucket limiter
│   ├── retry.py                 # Tenacity retry decorator
│   ├── client.py                # Expensify HTTP client
│   ├── transformer.py           # JSON → flat dict transformer (local/test use)
│   ├── csv_exporter.py          # CSV writer
│   ├── pipeline.py              # ETL orchestrator
│   ├── cli.py                   # argparse CLI definitions
│   ├── cleanup.py               # Retention-based cleanup utility
│   └── utils.py                 # Date helpers, path helpers, coercions
│
├── tests/
│   ├── __init__.py
│   ├── test_rate_limiter.py
│   ├── test_utils.py
│   ├── test_transformer.py
│   ├── test_cli.py
│   └── test_csv_exporter.py
│
└── uploads/
    └── pending/                 # CSVs written here (flat per account)
        └── <account-name>/
            ├── 2026-01-01_2026-12-31_reports_20260630T103000Z.csv
            ├── 2026-01-01_2026-12-31_transactions_20260630T103000Z.csv
            └── 2026-01-01_2026-12-31_actions_20260630T103000Z.csv
```

---

## Installation

### 1. Clone and enter the project

```bash
git clone <your-repo-url>
cd expensify-etl
```

### 2. Create a virtual environment

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment

```bash
cp .env.example .env
```

Open `.env` and fill in your account credentials:

```
ACCOUNT_1_NAME=parkbars
ACCOUNT_1_PARTNER_USER_ID=aa_developer_parkbars_com
ACCOUNT_1_PARTNER_USER_SECRET=your-secret-here

# Add more accounts as ACCOUNT_2_*, ACCOUNT_3_*, …
```

---

## Configuration Reference

All settings live in `.env`. Never commit this file.

### Accounts

| Variable | Description |
|---|---|
| `ACCOUNT_<N>_NAME` | Short slug used as the output subdirectory name |
| `ACCOUNT_<N>_PARTNER_USER_ID` | Expensify partner user ID for this account |
| `ACCOUNT_<N>_PARTNER_USER_SECRET` | Expensify partner user secret for this account |

Add one numbered block per account (`ACCOUNT_1_*`, `ACCOUNT_2_*`, …). The pipeline scans in order until a gap is found.

### All settings

| Variable | Default | Description |
|---|---|---|
| `EXPENSIFY_API_URL` | Integration Server URL | Override for testing/staging |
| `EXPENSIFY_TIMEOUT` | `30` | HTTP timeout in seconds |
| `EXPENSIFY_TEMPLATE_DIR` | `config/templates` | Directory containing the three Freemarker templates |
| `UPLOAD_PENDING_DIR` | `uploads/pending` | Where CSVs are written |
| `UPLOAD_PROCESSED_DIR` | `uploads/processed` | Used by cleanup utility |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_DIR` | `logs` | Directory for log files |
| `RETENTION_DAYS` | `30` | Days to retain processed files |

---

## Usage

### Export a full year

```bash
python export.py --year 2026
```

Exports January through December 2026 for all configured accounts.

### Export a specific month

```bash
python export.py --month 7 --year 2026
```

Exports July 2026 only.

### Export for a single account

```bash
python export.py --year 2026 --account parkbars
```

### Export a custom date range

```bash
python export.py --start 2026-01-15 --end 2026-04-30
```

The first and last months are clamped to the exact dates given.

### Overwrite existing exports

By default, months that already have a CSV are skipped (resume support).
Use `--force` to overwrite:

```bash
python export.py --year 2026 --force
```

### Preview without calling the API

```bash
python export.py --year 2026 --dry-run
```

Prints the list of months that would be exported and whether they already exist.

---

## Cleanup

Remove processed CSV files older than the configured retention period:

```bash
python scripts/cleanup.py
```

Preview what would be deleted without actually deleting:

```bash
python scripts/cleanup.py --dry-run
```

Override the retention period:

```bash
python scripts/cleanup.py --retention-days 60
```

---

## Running Tests

```bash
pytest tests/ -v
```

With coverage:

```bash
pytest tests/ -v --cov=scripts --cov-report=term-missing
```

---

## Logging

Two rotating log files are written to `logs/`:

| File | Contents |
|---|---|
| `app.log` | All events at or above `LOG_LEVEL` (default INFO), rotating 10 MB × 5 |
| `error.log` | Errors only, rotating 10 MB × 5 |

Each log entry includes timestamp, level, module name, and message.

---

## CSV Output Format

The pipeline writes **three CSV files per date range per account** directly under `uploads/pending/<account-name>/` — no year/month subdirectories. All three files share the same UTC run timestamp so they sort together.

All files are UTF-8 with BOM for Excel compatibility. Three metadata columns are appended to every file: `restaurant_name`, `filename`, `createdAt`.

### File naming

```
2026-01-01_2026-12-31_reports_20260630T103000Z.csv
2026-01-01_2026-12-31_transactions_20260630T103000Z.csv
2026-01-01_2026-12-31_actions_20260630T103000Z.csv
```

### reports.csv — one row per expense report

| Column | Source field | Description |
|---|---|---|
| `report_id` | `reportID` | Unique report ID |
| `old_report_id` | `oldReportID` | Legacy report ID |
| `report_name` | `reportName` | Report name |
| `account_email` | `accountEmail` | Submitter account email |
| `account_id` | `accountID` | Account ID |
| `status` | `status` | Report status (e.g. `APPROVED`, `REIMBURSED`) |
| `display_status` | `displayStatus` | Human-readable status |
| `policy_name` | `policyName` | Expense policy name |
| `policy_id` | `policyID` | Policy ID |
| `entry_id` | `entryID` | Entry ID |
| `currency` | `currency` | Report currency code |
| `total` | `total` | Report total (in currency units, divided by 100) |
| `submitter_first_name` | `submitter.firstName` | Submitter first name |
| `submitter_last_name` | `submitter.lastName` | Submitter last name |
| `submitter_full_name` | `submitter.fullName` | Submitter full name |
| `manager_email` | `managerEmail` | Manager email address |
| `manager_user_id` | `managerUserID` | Manager user ID |
| `manager_payroll_id` | `managerPayrollID` | Manager payroll ID |
| `manager_first_name` | `manager.firstName` | Manager first name |
| `manager_last_name` | `manager.lastName` | Manager last name |
| `manager_full_name` | `manager.fullName` | Manager full name |
| `employee_custom_field1` | `employeeCustomField1` | Employee custom field 1 |
| `employee_custom_field2` | `employeeCustomField2` | Employee custom field 2 |
| `created` | `created` | Report creation date |
| `submitted` | `submitted` | Submission date |
| `approved` | `approved` | Approval date |
| `reimbursed` | `reimbursed` | Reimbursement date |
| `is_ach_reimbursed` | `isACHReimbursed` | `true` / `false` |
| `approvers_json` | `approvers[]` | JSON array of approvers — each object includes `email`, `fullName`, `date`, `employeeUserID`, `employeePayrollID` |
| `restaurant_name` | *(metadata)* | Account name from pipeline config |
| `filename` | *(metadata)* | Name of this CSV file |
| `createdAt` | *(metadata)* | UTC timestamp of the pipeline run |

### transactions.csv — one row per transaction

| Column | Source field | Description |
|---|---|---|
| `report_id` | `reportID` | Parent report ID |
| `report_name` | `reportName` | Parent report name |
| `transaction_id` | `transactionID` | Unique transaction ID |
| `transaction_type` | `type` | Transaction type |
| `merchant` | `merchant` | Merchant name |
| `modified_merchant` | `modifiedMerchant` | Modified merchant name |
| `transaction_created` | `created` | Transaction date |
| `modified_created` | `modifiedCreated` | Modified transaction date |
| `amount` | `amount` | Amount in currency units (divided by 100) |
| `modified_amount` | `modifiedAmount` | Modified amount |
| `currency` | `currency` | Currency code |
| `currency_conversion_rate` | `currencyConversionRate` | Conversion rate to report currency |
| `converted_amount` | `convertedAmount` | Amount converted to report currency |
| `category` | `category` | Expense category |
| `category_gl_code` | `categoryGlCode` | Category GL code |
| `category_payroll_code` | `categoryPayrollCode` | Category payroll code |
| `comment` | `comment` | Transaction comment |
| `tag` | `tag` | Tag / project code |
| `tag_gl_code` | `tagGlCode` | Tag GL code |
| `reimbursable` | `reimbursable` | `true` / `false` |
| `billable` | `billable` | `true` / `false` |
| `has_tax` | `hasTax` | `true` / `false` |
| `tax_amount` | `taxAmount` | Tax amount (divided by 100) |
| `modified_tax_amount` | `modifiedTaxAmount` | Modified tax amount |
| `tax_name` | `taxName` | Tax name |
| `tax_rate` | `taxRate` | Tax rate |
| `tax_rate_name` | `taxRateName` | Tax rate name |
| `tax_code` | `taxCode` | Tax code |
| `mcc` | `mcc` | Merchant category code |
| `modified_mcc` | `modifiedMCC` | Modified MCC |
| `inserted` | `inserted` | Insert timestamp |
| `bank` | `bank` | Bank or card name |
| `is_distance` | `isDistance` | `true` / `false` |
| `receipt_id` | `receiptID` | Receipt ID |
| `receipt_filename` | `receiptFilename` | Receipt original filename |
| `receipt_url` | `receiptObject.url` | Full-size receipt image URL |
| `receipt_small_thumbnail` | `receiptObject.smallThumbnail` | Small thumbnail URL |
| `receipt_thumbnail` | `receiptObject.thumbnail` | Thumbnail URL |
| `receipt_type` | `receiptObject.type` | Receipt file type |
| `receipt_transaction_id` | `receiptObject.transactionID` | Receipt transaction ID |
| `attendees_json` | `attendees[]` | JSON array of attendees — each object includes `email`, `displayName`, `thumbnail` |
| `units_count` | `units.count` | Distance / custom unit count |
| `units_rate` | `units.rate` | Rate per unit |
| `units_unit` | `units.unit` | Unit type (e.g. `mi`, `km`) |
| `units_name` | `units.name` | Unit name |
| `restaurant_name` | *(metadata)* | Account name from pipeline config |
| `filename` | *(metadata)* | Name of this CSV file |
| `createdAt` | *(metadata)* | UTC timestamp of the pipeline run |

### actions.csv — one row per report action entry

Compact format from the API: `report_id`, `report_name`, `action_data` (JSON string). The pipeline expands `action_data` into individual columns — one column per unique key found across all action objects in the full date range. The exact columns vary by export since different action types carry different fields. Common keys include `actorEmail`, `message`, `created`, `action`, `details`.

All three metadata columns (`restaurant_name`, `filename`, `createdAt`) are also present in actions.csv.

---

## Architecture

```
CLI (cli.py)
    │
    ▼
export.py                       ← loads accounts from ACCOUNT_N_* env vars
    │
    ▼
Pipeline (pipeline.py)          ← orchestrates months, handles errors in isolation
    │
    ├── ExpensifyClient (client.py)
    │       ├── RateLimiter (rate_limiter.py)   ← dual sliding-window (3/10s, 12/60s)
    │       └── @retryable_request (retry.py)   ← tenacity exponential back-off
    │       └── 3 Freemarker templates → raw CSV bytes (reports, transactions, actions)
    │
    └── write_combined_csvs (csv_exporter.py)   ← merges all months, writes 3 combined
          CSVs flat under uploads/pending/<account>/
```

**Key design principles:**
- Each module has a single responsibility.
- The client knows nothing about files; the exporter knows nothing about HTTP.
- Configuration is injected via `AppConfig`; no module reads `os.environ` directly.
- Failures are isolated per month; one bad month never aborts the pipeline.
- All account credentials live in `.env`; no secrets in source code or JSON files.

---

## Rate Limiting

The Expensify Integration Server's documented limits are 5 requests per 10 seconds and 20 requests per 60 seconds.

This pipeline uses a **dual sliding-window token-bucket** limiter (`rate_limiter.py`) with **conservative defaults of 3/10s and 12/60s** — intentionally below the maximums to avoid 429 responses entirely.

Before every HTTP request, `RateLimiter.acquire()` checks both windows and sleeps only as long as necessary to stay under both limits simultaneously. Every sleep is logged at INFO level.

---

## Retry Policy

| Condition | Retried? |
|---|---|
| HTTP 429 | Yes |
| HTTP 500, 502, 503, 504 | Yes |
| Timeout | Yes |
| Connection error | Yes |
| HTTP 4xx (other) | No |

Two different back-off schedules are used (4 retries maximum, then raises):

| Trigger | Back-off schedule |
|---|---|
| HTTP 429 | 30s → 60s → 120s → 120s |
| 5xx / timeout / connection | 2s → 4s → 8s → 16s |

---

## Troubleshooting

**`FileNotFoundError: No account credentials found`**  
→ Set `ACCOUNT_1_NAME`, `ACCOUNT_1_PARTNER_USER_ID`, `ACCOUNT_1_PARTNER_USER_SECRET` in `.env`.

**`FileNotFoundError: Freemarker template(s) not found`**  
→ Check `EXPENSIFY_TEMPLATE_DIR` in `.env` points to the directory containing `reports_template.ftl`, `transactions_template.ftl`, and `actions_template.ftl`.

**`HTTP 401` or `responseCode: 500` from API**  
→ Verify the `PARTNER_USER_ID` and `PARTNER_USER_SECRET` for the failing account are correct.

**CSV opens with garbled characters in Excel**  
→ Files are UTF-8 BOM encoded. If still garbled, open via Data → From Text/CSV in Excel and select UTF-8.

**Pipeline skipping months I want to re-export**  
→ Run with `--force` to overwrite existing files.

---

## Future Improvements

- Parallel month fetching (asyncio or ThreadPoolExecutor) with shared rate limiter
- Database insertion step to promote files from `uploads/pending/` to `uploads/processed/`
- Webhook / Slack notification on pipeline completion or failure
- Configurable field selection (export only selected columns)
- Delta export (only new/updated reports since last run via report modification date)
- Docker container + cron scheduling
- Prometheus metrics endpoint for pipeline observability
