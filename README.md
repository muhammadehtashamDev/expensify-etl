# Expensify Data Pipeline

A production-ready, enterprise-grade Python ETL pipeline that downloads expense reports from the Expensify Integration Server API, flattens nested JSON into clean CSV files, and organises them by year and month for importing into Supabase (or any downstream system).

---

## Features

- **Rate-limit aware** — dual sliding-window enforcer (5/10s, 20/60s); never triggers HTTP 429 from our own code
- **Automatic retries** — exponential back-off on 429/5xx/timeout/connection errors via tenacity
- **Resume support** — skips months that already have a CSV; use `--force` to overwrite
- **Rich terminal UI** — progress bars, spinners, per-month status table, summary
- **Structured logging** — rotating `app.log` + `error.log`; every request is timed and logged
- **Clean folder hierarchy** — `uploads/pending/YYYY/Month/` → `uploads/processed/YYYY/Month/`
- **UTF-8 BOM CSV** — Excel-compatible; handles embedded commas, quotes, and newlines
- **Config via `.env`** — no secrets in source code
- **Cleanup utility** — configurable retention policy; removes stale processed files
- **Unit tested** — rate limiter, date utils, transformer, CSV exporter, CLI

---

## Project Structure

```
expensify-etl/
├── export.py                    # Main entry point
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
│
├── config/
│   └── templates/
│       └── export_template.ftl  # Freemarker template for the API request
│
├── scripts/
│   ├── __init__.py
│   ├── config.py                # Environment config loader
│   ├── logger.py                # Logging setup
│   ├── rate_limiter.py          # Dual-window token-bucket limiter
│   ├── retry.py                 # Tenacity retry decorator
│   ├── client.py                # Expensify HTTP client
│   ├── transformer.py           # JSON → flat dict transformer
│   ├── csv_exporter.py          # CSV writer + file promotion
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
├── uploads/
│   ├── pending/                 # CSVs written here first
│   │   └── 2026/
│   │       └── January/
│   │           └── 2026_01_January.csv
│   └── processed/               # Moved here after successful write
│       └── 2026/
│           └── January/
│               └── 2026_01_January.csv
│
└── logs/
    ├── app.log                  # All log levels (rotating, 10 MB × 5)
    └── error.log                # ERROR and above only
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

Open `.env` and fill in your Expensify credentials:

```
EXPENSIFY_PARTNER_USER_ID=your_partner_user_id
EXPENSIFY_PARTNER_USER_SECRET=your_partner_user_secret
```

---

## Configuration Reference

All settings live in `.env`. Never commit this file.

| Variable | Default | Description |
|---|---|---|
| `EXPENSIFY_PARTNER_USER_ID` | *(required)* | Expensify partner user ID |
| `EXPENSIFY_PARTNER_USER_SECRET` | *(required)* | Expensify partner user secret |
| `EXPENSIFY_API_URL` | Integration Server URL | Override for testing/staging |
| `EXPENSIFY_TIMEOUT` | `30` | HTTP timeout in seconds |
| `EXPENSIFY_TEMPLATE_PATH` | `config/templates/export_template.ftl` | Path to Freemarker template |
| `UPLOAD_PENDING_DIR` | `uploads/pending` | Where CSVs are written first |
| `UPLOAD_PROCESSED_DIR` | `uploads/processed` | Where CSVs land after success |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_DIR` | `logs` | Directory for log files |
| `RETENTION_DAYS` | `30` | Days to retain processed files |

---

## Usage

### Export a full year

```bash
python export.py --year 2026
```

Exports January through December 2026.

### Export a specific month

```bash
python export.py --month 7 --year 2026
```

Exports July 2026 only.

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
| `app.log` | All events at or above `LOG_LEVEL` (default INFO) |
| `error.log` | Errors only |

Each log entry includes timestamp, level, module name, and message.  
Every API request logs: start time, duration, report count, transaction count, CSV path.

---

## CSV Output Format

Files are UTF-8 with BOM for Excel compatibility.  
Each row represents one transaction, with report-level fields repeated.

| Column | Source |
|---|---|
| `report_id` | Report ID |
| `report_name` | Report name |
| `employee_email` | Report owner |
| `manager_email` | Approving manager |
| `status` | `APPROVED`, `CLOSED`, `REIMBURSED` |
| `policy_name` | Expense policy |
| `report_total` | Total in report currency |
| `transaction_id` | Transaction ID |
| `merchant` | Merchant name |
| `amount` | Transaction amount |
| `currency` | Transaction currency |
| `category` | Expense category |
| `tag` | Tag / project code |
| `billable` | `True` / `False` |
| `reimbursable` | `True` / `False` |
| `receipt_url` | Receipt image URL |
| `comment` | Transaction comment |
| `tax_amount` | Tax amount |
| `tax_name` | Tax name |
| `transaction_created` | Transaction date |
| ... | (see `ALL_COLUMNS` in `scripts/transformer.py`) |

---

## Architecture

```
CLI (cli.py)
    │
    ▼
Pipeline (pipeline.py)          ← orchestrates months, handles errors in isolation
    │
    ├── ExpensifyClient (client.py)
    │       ├── RateLimiter (rate_limiter.py)   ← dual sliding-window
    │       └── @retryable_request (retry.py)   ← tenacity exponential back-off
    │
    ├── flatten_reports (transformer.py)        ← nested JSON → flat dicts
    │
    └── write_csv + promote_to_processed        ← UTF-8 BOM CSV, pending → processed
          (csv_exporter.py)
```

**Key design principles:**
- Each module has a single responsibility.
- The client knows nothing about files; the exporter knows nothing about HTTP.
- Configuration is injected via `AppConfig`; no module reads `os.environ` directly.
- Failures are isolated per month; one bad month never aborts the pipeline.

---

## Rate Limiting

The Expensify Integration Server enforces:
- 5 requests per 10 seconds
- 20 requests per 60 seconds

This pipeline uses a **dual sliding-window token-bucket** limiter (`rate_limiter.py`).  
Before every HTTP request, `RateLimiter.acquire()` checks both windows and sleeps only as long as necessary to stay under both limits simultaneously.  
Every sleep is logged at INFO level.

---

## Retry Policy

| Condition | Retried? |
|---|---|
| HTTP 429 | Yes |
| HTTP 500, 502, 503, 504 | Yes |
| Timeout | Yes |
| Connection error | Yes |
| HTTP 4xx (other) | No |

Back-off schedule: 2s → 4s → 8s → 16s (4 retries, then raises).

---

## Troubleshooting

**`EnvironmentError: Required environment variable … is not set`**  
→ Copy `.env.example` to `.env` and fill in your credentials.

**`FileNotFoundError: Freemarker template not found`**  
→ Check `EXPENSIFY_TEMPLATE_PATH` in `.env` points to `config/templates/export_template.ftl`.

**`HTTP 401` or `responseCode: 500` from API**  
→ Verify your `EXPENSIFY_PARTNER_USER_ID` and `EXPENSIFY_PARTNER_USER_SECRET` are correct.

**CSV opens with garbled characters in Excel**  
→ Files are UTF-8 BOM encoded. If still garbled, open via Data → From Text/CSV in Excel and select UTF-8.

**Pipeline skipping months I want to re-export**  
→ Run with `--force` to overwrite existing files.

---

## Future Improvements

- Parallel month fetching (asyncio or ThreadPoolExecutor) with shared rate limiter
- Supabase direct upload via `supabase-py` after CSV generation
- Webhook / Slack notification on pipeline completion or failure
- Configurable field selection (export only selected columns)
- Delta export (only new/updated reports since last run via report modification date)
- Docker container + cron scheduling
- Prometheus metrics endpoint for pipeline observability
