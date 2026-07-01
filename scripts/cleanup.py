"""
Cleanup utility for processed CSV files.

Deletes files in ``uploads/processed/`` that are older than the configured
retention period (default 30 days, configurable via ``RETENTION_DAYS`` in
.env).

After deleting files, any empty month or year directory is removed as well.
Example: if ``2026/January/`` had its last file deleted, both ``January/``
and (if now empty) ``2026/`` are removed automatically.

Usage::

    python cleanup.py
    python cleanup.py --dry-run
    python cleanup.py --retention-days 60
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running as a script: `python scripts/cleanup.py`
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rich.console import Console
from rich.table import Table
from rich import box

from scripts.config import load_config
from scripts.logger import get_logger, setup_logging

log = get_logger(__name__)
console = Console()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cleanup.py",
        description="Delete processed CSV exports older than the retention period.",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=None,
        metavar="DAYS",
        help=(
            "Override the retention period from .env.  "
            "Files older than this many days are deleted."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="List files and folders that would be deleted without actually deleting them.",
    )
    return parser.parse_args(argv)


def run_cleanup(
    processed_dir: Path,
    retention_days: int,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Delete CSV files (and their empty parent folders) from *processed_dir*.

    Logic:
    - Any ``.csv`` file whose modification time is older than *retention_days*
      is deleted.
    - After deletion, any empty month directory (e.g. ``2026/January/``) is
      removed.
    - After that, any empty year directory (e.g. ``2026/``) is removed.

    Files whose month/year folder still contains other recent files are NOT
    touched — only fully-emptied directories are removed.

    Args:
        processed_dir:   Root processed directory to scan recursively.
        retention_days:  Files older than this many days are eligible.
        dry_run:         If True, report what would happen without doing it.

    Returns:
        A tuple ``(deleted_files, freed_bytes)``.
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=retention_days)
    log.info(
        "Cleanup started: dir=%s retention_days=%d cutoff=%s dry_run=%s",
        processed_dir,
        retention_days,
        cutoff.isoformat(),
        dry_run,
    )

    # ------------------------------------------------------------------ #
    # Identify files to delete                                             #
    # ------------------------------------------------------------------ #
    csv_files = list(processed_dir.rglob("*.csv"))
    candidates: list[tuple[Path, datetime, int]] = []

    for csv_path in csv_files:
        try:
            stat = csv_path.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                candidates.append((csv_path, mtime, stat.st_size))
        except OSError as exc:
            log.warning("Could not stat %s: %s", csv_path, exc)

    if not candidates:
        console.print(
            f"[green]No CSV files older than {retention_days} days found.[/green]"
        )
        log.info("Cleanup complete — nothing to delete.")
        return 0, 0

    # ------------------------------------------------------------------ #
    # Display what will be removed                                         #
    # ------------------------------------------------------------------ #
    table = Table(
        title=f"{'[DRY RUN] ' if dry_run else ''}Files to delete (older than {retention_days} days)",
        box=box.SIMPLE,
    )
    table.add_column("File", style="dim")
    table.add_column("Last Modified", style="yellow")
    table.add_column("Size", justify="right")

    for csv_path, mtime, size in candidates:
        table.add_row(
            str(csv_path.relative_to(processed_dir)),
            mtime.strftime("%Y-%m-%d"),
            f"{size / 1024:.1f} KB",
        )

    console.print(table)

    # ------------------------------------------------------------------ #
    # Delete files                                                         #
    # ------------------------------------------------------------------ #
    deleted = 0
    freed = 0

    for csv_path, _, size in candidates:
        if dry_run:
            log.info("[DRY RUN] Would delete: %s", csv_path)
            deleted += 1
            freed += size
        else:
            try:
                csv_path.unlink()
                log.info("Deleted: %s", csv_path)
                deleted += 1
                freed += size
            except OSError as exc:
                log.error("Failed to delete %s: %s", csv_path, exc)
                console.print(f"[red]Error deleting {csv_path}: {exc}[/red]")

    # ------------------------------------------------------------------ #
    # Remove empty month and year directories                              #
    # ------------------------------------------------------------------ #
    if not dry_run:
        removed_dirs = _prune_empty_dirs(processed_dir)
        if removed_dirs:
            console.print(
                f"[dim]Removed {len(removed_dirs)} empty director"
                f"{'y' if len(removed_dirs) == 1 else 'ies'}:[/dim]"
            )
            for d in removed_dirs:
                console.print(f"  [dim]{d.relative_to(processed_dir)}[/dim]")
    else:
        # In dry-run mode, show which directories WOULD become empty
        would_empty = _find_would_be_empty(
            processed_dir,
            files_to_delete={p for p, _, _ in candidates},
        )
        if would_empty:
            console.print(
                f"[dim][DRY RUN] Would also remove {len(would_empty)} empty "
                f"director{'y' if len(would_empty) == 1 else 'ies'}[/dim]"
            )
            for d in would_empty:
                console.print(f"  [dim]{d.relative_to(processed_dir)}[/dim]")

    verb = "Would delete" if dry_run else "Deleted"
    console.print(
        f"\n[bold]{verb}[/bold] {deleted} file(s) "
        f"({freed / 1024:.1f} KB freed)"
    )
    log.info(
        "Cleanup complete: deleted=%d freed_bytes=%d dry_run=%s",
        deleted,
        freed,
        dry_run,
    )
    return deleted, freed


def _prune_empty_dirs(root: Path) -> list[Path]:
    """Remove empty subdirectories under *root*, deepest first.

    Returns the list of directories that were actually removed.
    """
    removed: list[Path] = []
    # Sort deepest paths first so month dirs are attempted before year dirs
    for dirpath in sorted(root.rglob("*"), reverse=True):
        if dirpath.is_dir() and dirpath != root:
            try:
                dirpath.rmdir()  # succeeds only if truly empty
                removed.append(dirpath)
                log.info("Removed empty directory: %s", dirpath)
            except OSError:
                pass  # still has contents — skip
    return removed


def _find_would_be_empty(root: Path, files_to_delete: set[Path]) -> list[Path]:
    """Return directories that would be empty after *files_to_delete* are removed."""
    would_empty: list[Path] = []
    for dirpath in sorted(root.rglob("*"), reverse=True):
        if not dirpath.is_dir() or dirpath == root:
            continue
        remaining = [
            p for p in dirpath.iterdir()
            if p not in would_empty and p not in files_to_delete
        ]
        if not remaining:
            would_empty.append(dirpath)
    return would_empty


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    config = load_config()
    setup_logging(log_dir=config.log_dir, log_level=config.log_level)

    args = _parse_args(argv)
    retention_days = (
        args.retention_days if args.retention_days is not None
        else config.retention_days
    )

    console.print(
        f"[bold cyan]Expensify Cleanup[/bold cyan] — "
        f"retention: [yellow]{retention_days}[/yellow] days"
        + (" [bold yellow](DRY RUN)[/bold yellow]" if args.dry_run else "")
    )

    run_cleanup(
        processed_dir=config.processed_dir,
        retention_days=retention_days,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
