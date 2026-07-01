"""
Central configuration loader.

Reads all settings from environment variables (populated via .env).
All other modules import from here — never from os.environ directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap: resolve project root and load .env
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"

load_dotenv(dotenv_path=_ENV_FILE, override=False)


def _require(key: str) -> str:
    """Return the value of *key* or raise a clear error if missing."""
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            f"Copy .env.example to .env and fill in your values."
        )
    return value


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _get_int(key: str, default: int = 0) -> int:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(
            f"Environment variable '{key}' must be an integer, got: {raw!r}"
        )


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppConfig:
    """Immutable application configuration loaded from environment."""

    # Expensify credentials
    partner_user_id: str
    partner_user_secret: str

    # API
    api_url: str
    timeout: int

    # Templates directory (contains reports_template.ftl, transactions_template.ftl, actions_template.ftl)
    template_dir: Path

    # Output directories (absolute paths)
    pending_dir: Path
    processed_dir: Path

    # Logging
    log_level: str
    log_dir: Path
    log_retention_days: int

    # Retention
    retention_days: int

    # PostgreSQL load procedure
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    db_procedure: str
    db_connect_timeout: int
    db_connect_retries: int
    db_connect_retry_delay_seconds: int

    # Account identity (set per-account in export.py; empty for single-account runs)
    account_name: str = ""

    # Computed at load time
    project_root: Path = field(default_factory=lambda: _PROJECT_ROOT)

    @property
    def reports_template_path(self) -> Path:
        return self.template_dir / "reports_template.ftl"

    @property
    def transactions_template_path(self) -> Path:
        return self.template_dir / "transactions_template.ftl"

    @property
    def actions_template_path(self) -> Path:
        return self.template_dir / "actions_template.ftl"

    def __post_init__(self) -> None:
        # Ensure critical directories exist at startup
        for directory in (self.pending_dir, self.processed_dir, self.log_dir):
            directory.mkdir(parents=True, exist_ok=True)

        missing = [
            p for p in (
                self.reports_template_path,
                self.transactions_template_path,
                self.actions_template_path,
            )
            if not p.exists()
        ]
        if missing:
            raise FileNotFoundError(
                f"Freemarker template(s) not found:\n"
                + "\n".join(f"  {p}" for p in missing)
                + f"\nCheck EXPENSIFY_TEMPLATE_DIR in your .env file."
            )


def _resolve(path_str: str) -> Path:
    """Resolve a path relative to the project root if not absolute."""
    p = Path(path_str)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p.resolve()


def load_config() -> AppConfig:
    """Load and validate configuration from environment variables.

    Returns a fully validated :class:`AppConfig` instance.
    Raises :class:`EnvironmentError` or :class:`FileNotFoundError` on bad config.
    """
    return AppConfig(
        partner_user_id=_get("EXPENSIFY_PARTNER_USER_ID"),
        partner_user_secret=_get("EXPENSIFY_PARTNER_USER_SECRET"),
        api_url=_get(
            "EXPENSIFY_API_URL",
            "https://integrations.expensify.com/Integration-Server/ExpensifyIntegrations",
        ),
        timeout=_get_int("EXPENSIFY_TIMEOUT", 30),
        template_dir=_resolve(
            _get("EXPENSIFY_TEMPLATE_DIR", "config/templates")
        ),
        pending_dir=_resolve(_get("UPLOAD_PENDING_DIR", "uploads/pending")),
        processed_dir=_resolve(_get("UPLOAD_PROCESSED_DIR", "uploads/processed")),
        log_level=_get("LOG_LEVEL", "INFO").upper(),
        log_dir=_resolve(_get("LOG_DIR", "logs")),
        log_retention_days=_get_int("LOG_RETENTION_DAYS", 30),
        retention_days=_get_int("RETENTION_DAYS", 30),
        db_host=_require("DB_HOST"),
        db_port=_get_int("DB_PORT", 5432),
        db_name=_require("DB_NAME"),
        db_user=_require("DB_USER"),
        db_password=_get("DB_PASSWORD", ""),
        db_procedure=_get("DB_PROCEDURE", "public.proc_load_expensify"),
        db_connect_timeout=_get_int("DB_CONNECT_TIMEOUT", 10),
        db_connect_retries=_get_int("DB_CONNECT_RETRIES", 2),
        db_connect_retry_delay_seconds=_get_int("DB_CONNECT_RETRY_DELAY_SECONDS", 2),
    )
