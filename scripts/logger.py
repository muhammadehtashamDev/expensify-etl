"""
Logging setup for the Expensify pipeline.

Provides:
- Rotating file handler → logs/app.log  (all levels)
- Rotating file handler → logs/error.log (ERROR and above)
- Console handler (WARNING and above, so Rich handles INFO/DEBUG output)

Call :func:`setup_logging` once at application start before importing
any other module that logs.
"""

from __future__ import annotations

import logging
import logging.handlers
from datetime import datetime, timedelta, timezone
from pathlib import Path


_FORMATTER = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_MAX_BYTES = 10 * 1024 * 1024   # 10 MB per log file
_BACKUP_COUNT = 5                # keep 5 rotated files


def prune_old_logs(log_dir: Path, retention_days: int) -> None:
    """Delete log files older than *retention_days* from *log_dir*."""
    if retention_days < 0:
        return

    if retention_days == 0:
        for path in log_dir.glob("*.log*"):
            try:
                path.unlink()
            except OSError:
                continue
        return

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=retention_days)
    for path in log_dir.glob("*.log*"):
        try:
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if modified_at < cutoff:
            try:
                path.unlink()
            except OSError:
                continue


def setup_logging(
    log_dir: Path,
    log_level: str = "INFO",
    log_retention_days: int = 30,
) -> None:
    """Configure the root logger with file and console handlers.

    Args:
        log_dir: Directory where log files will be written.
        log_level: Minimum level for app.log (e.g. ``"INFO"`` or ``"DEBUG"``).
        log_retention_days: Number of days to retain log files before deleting them.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    prune_old_logs(log_dir, log_retention_days)

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)   # capture everything; handlers filter

    # Remove any handlers added before setup_logging was called
    root.handlers.clear()

    # --- app.log: all messages at configured level -------------------------
    app_handler = logging.handlers.RotatingFileHandler(
        filename=log_dir / "app.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    app_handler.setLevel(numeric_level)
    app_handler.setFormatter(_FORMATTER)
    root.addHandler(app_handler)

    # --- error.log: ERROR and above ----------------------------------------
    error_handler = logging.handlers.RotatingFileHandler(
        filename=log_dir / "error.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(_FORMATTER)
    root.addHandler(error_handler)

    # --- stderr console: WARNING and above ---------------------------------
    # Rich handles INFO/DEBUG rendering; we only push warnings/errors to
    # the console logger so they appear even without Rich.
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(
        logging.Formatter("%(levelname)s: %(message)s")
    )
    root.addHandler(console_handler)

    logging.getLogger(__name__).debug(
        "Logging initialised. level=%s log_dir=%s retention_days=%s",
        log_level,
        log_dir,
        log_retention_days,
    )


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger.

    Usage::

        log = get_logger(__name__)
        log.info("doing something")
    """
    return logging.getLogger(name)
