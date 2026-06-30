"""
Pipeline orchestrator.

Coordinates the full ETL flow for one or more months:

1. Resolve the list of calendar month periods from the CLI args.
2. For each month:
   a. Check resume logic (skip if transactions CSV already exists and
      --force not set).
   b. Make three API calls — one per Freemarker template:
        reports.csv      — one row per report
        transactions.csv — one row per transaction (includes report_id, report_name)
        actions.csv      — one row per action log entry (includes report_id, report_name)
   c. Save all three raw CSV files to uploads/pending/.
      Files stay in pending until the DB insertion function is ready.

Error isolation: a failure in one month is logged and displayed but does
NOT abort the remaining months.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich import box

from scripts.cli import CLIArgs
from scripts.client import ExpensifyClient, ExpensifyAPIError
from scripts.config import AppConfig
from scripts.csv_exporter import (
    count_csv_rows,
    csv_already_exists,
    write_actions_csv,
    write_raw_csv,
)
from scripts.logger import get_logger
from scripts.rate_limiter import RateLimiter
from scripts.utils import month_name, months_in_range, format_expensify_date

log = get_logger(__name__)
console = Console()


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------


@dataclass
class MonthResult:
    year: int
    month: int
    skipped: bool = False
    reports: int = 0
    transactions: int = 0
    actions: int = 0
    csv_paths: list[Path] = field(default_factory=list)
    error: str | None = None
    duration_seconds: float = 0.0

    @property
    def success(self) -> bool:
        return self.error is None and not self.skipped


@dataclass
class PipelineResult:
    months: list[MonthResult] = field(default_factory=list)

    @property
    def total_reports(self) -> int:
        return sum(m.reports for m in self.months)

    @property
    def total_transactions(self) -> int:
        return sum(m.transactions for m in self.months)

    @property
    def total_actions(self) -> int:
        return sum(m.actions for m in self.months)

    @property
    def successful(self) -> int:
        return sum(1 for m in self.months if m.success)

    @property
    def skipped(self) -> int:
        return sum(1 for m in self.months if m.skipped)

    @property
    def failed(self) -> int:
        return sum(1 for m in self.months if m.error is not None)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class Pipeline:
    """ETL pipeline orchestrator.

    Args:
        config:  Loaded :class:`~scripts.config.AppConfig`.
        args:    Parsed :class:`~scripts.cli.CLIArgs`.
    """

    def __init__(self, config: AppConfig, args: CLIArgs) -> None:
        self._config = config
        self._args = args
        self._rate_limiter = RateLimiter()

    def run(self) -> PipelineResult:
        """Execute the pipeline and return a :class:`PipelineResult`."""
        periods = list(
            months_in_range(self._args.start, self._args.end)
        )

        if self._args.dry_run:
            return self._dry_run(periods)

        result = PipelineResult()

        console.print(
            Panel(
                f"[bold cyan]Expensify Data Pipeline[/bold cyan]\n"
                f"Period: [yellow]{self._args.start}[/yellow] → "
                f"[yellow]{self._args.end}[/yellow]\n"
                f"Months: [green]{len(periods)}[/green]",
                border_style="cyan",
            )
        )

        with ExpensifyClient(self._config, self._rate_limiter) as client:
            progress = self._build_progress()
            with progress:
                overall_task = progress.add_task(
                    "[cyan]Overall", total=len(periods)
                )

                for start_date, end_date in periods:
                    month_result = self._process_month(
                        client=client,
                        progress=progress,
                        overall_task=overall_task,
                        start_date=start_date,
                        end_date=end_date,
                    )
                    result.months.append(month_result)
                    progress.advance(overall_task)

        self._print_summary(result)
        return result

    # ------------------------------------------------------------------
    # Month processing
    # ------------------------------------------------------------------

    def _process_month(
        self,
        client: ExpensifyClient,
        progress: Progress,
        overall_task: TaskID,
        start_date: date,
        end_date: date,
    ) -> MonthResult:
        year = start_date.year
        month = start_date.month
        label = f"{year} {month_name(month)}"

        result = MonthResult(year=year, month=month)
        t0 = time.perf_counter()

        # --- Resume check --------------------------------------------------
        if not self._args.force and csv_already_exists(
            year=year,
            month=month,
            pending_dir=self._config.pending_dir,
            processed_dir=self._config.processed_dir,
        ):
            console.print(
                f"[yellow]SKIP[/yellow] {label} — CSV already exists "
                f"(use --force to overwrite)"
            )
            log.info("Skipping %s — CSV already exists.", label)
            result.skipped = True
            return result

        # --- API calls (3 templates) ----------------------------------------
        month_task = progress.add_task(f"[green]{label}", total=None)

        try:
            log.info("Processing %s: %s → %s", label, start_date, end_date)
            console.print(f"[bold]→[/bold] Fetching [cyan]{label}[/cyan] ...")

            all_csvs = client.fetch_all_csvs(
                start_date=format_expensify_date(start_date),
                end_date=format_expensify_date(end_date),
            )

            result.reports = count_csv_rows(all_csvs["reports"])
            result.transactions = count_csv_rows(all_csvs["transactions"])
            result.actions = count_csv_rows(all_csvs["actions"])

            if result.transactions > 0:
                for csv_type, content in all_csvs.items():
                    if csv_type == "actions":
                        # Dynamic schema: expand JSON column into wide CSV
                        csv_path = write_actions_csv(
                            content=content,
                            year=year,
                            month=month,
                            pending_dir=self._config.pending_dir,
                            restaurant_name=self._config.account_name,
                        )
                    else:
                        csv_path = write_raw_csv(
                            content=content,
                            year=year,
                            month=month,
                            pending_dir=self._config.pending_dir,
                            csv_type=csv_type,
                            restaurant_name=self._config.account_name,
                        )
                    result.csv_paths.append(csv_path)

                console.print(
                    f"  [green]✓[/green] {label}: "
                    f"[bold]{result.reports}[/bold] reports · "
                    f"[bold]{result.transactions}[/bold] transactions · "
                    f"[bold]{result.actions}[/bold] actions → "
                    f"[dim]uploads/pending/[/dim]"
                )
            else:
                console.print(
                    f"  [yellow]~[/yellow] {label}: no transactions found."
                )
                log.info("%s returned 0 transactions.", label)

        except ExpensifyAPIError as exc:
            result.error = str(exc)
            console.print(f"  [red]✗[/red] {label}: API error — {exc}")
            log.error("API error for %s: %s", label, exc)

        except Exception as exc:  # noqa: BLE001
            result.error = f"{type(exc).__name__}: {exc}"
            console.print(f"  [red]✗[/red] {label}: unexpected error — {exc}")
            log.exception("Unexpected error for %s", label)

        finally:
            result.duration_seconds = time.perf_counter() - t0
            progress.remove_task(month_task)

        log.info(
            "Month done: %s reports=%d transactions=%d actions=%d duration=%.2fs error=%s",
            label,
            result.reports,
            result.transactions,
            result.actions,
            result.duration_seconds,
            result.error,
        )
        return result

    # ------------------------------------------------------------------
    # Dry run
    # ------------------------------------------------------------------

    def _dry_run(self, periods: list[tuple[date, date]]) -> PipelineResult:
        console.print(
            Panel("[bold yellow]DRY RUN — no API calls will be made[/bold yellow]")
        )
        table = Table(title="Planned exports", box=box.SIMPLE)
        table.add_column("Month", style="cyan")
        table.add_column("Start", style="green")
        table.add_column("End", style="green")
        table.add_column("Existing?", style="yellow")

        result = PipelineResult()

        for start_date, end_date in periods:
            year, month = start_date.year, start_date.month
            existing = csv_already_exists(
                year=year,
                month=month,
                pending_dir=self._config.pending_dir,
                processed_dir=self._config.processed_dir,
            )
            mr = MonthResult(year=year, month=month, skipped=existing and not self._args.force)
            result.months.append(mr)
            table.add_row(
                f"{year} {month_name(month)}",
                str(start_date),
                str(end_date),
                "Yes (would skip)" if existing and not self._args.force
                else "Yes (would overwrite)" if existing
                else "No",
            )

        console.print(table)
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_progress() -> Progress:
        return Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            transient=False,
        )

    @staticmethod
    def _print_summary(result: PipelineResult) -> None:
        table = Table(title="Pipeline Summary", box=box.ROUNDED)
        table.add_column("Month", style="cyan", min_width=16)
        table.add_column("Reports", justify="right")
        table.add_column("Transactions", justify="right")
        table.add_column("Actions", justify="right")
        table.add_column("Duration", justify="right")
        table.add_column("Status", justify="center")

        for m in result.months:
            if m.skipped:
                status = "[yellow]SKIPPED[/yellow]"
            elif m.error:
                status = "[red]FAILED[/red]"
            else:
                status = "[green]OK[/green]"

            dash = "-"
            table.add_row(
                f"{m.year} {month_name(m.month)}",
                str(m.reports) if not m.skipped else dash,
                str(m.transactions) if not m.skipped else dash,
                str(m.actions) if not m.skipped else dash,
                f"{m.duration_seconds:.1f}s" if not m.skipped else dash,
                status,
            )

        console.print(table)
        console.print(
            f"\n[bold]Total:[/bold] "
            f"{result.total_reports} reports · "
            f"{result.total_transactions} transactions · "
            f"{result.total_actions} actions "
            f"| [green]{result.successful} OK[/green] "
            f"| [yellow]{result.skipped} skipped[/yellow] "
            f"| [red]{result.failed} failed[/red]"
        )
