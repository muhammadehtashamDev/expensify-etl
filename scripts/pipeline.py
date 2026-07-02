"""
Pipeline orchestrator.

Coordinates the full ETL flow for one date range:

1. Resolve the list of calendar month periods from the CLI args.
2. Optionally skip the whole run if combined CSVs for this exact range
   already exist (resume support via --force to override).
3. For each month, make three API calls (reports, transactions, actions)
   and accumulate the raw CSV bytes — no files are written yet.
4. After all months are fetched, merge the accumulated chunks and write
   three combined CSV files to uploads/pending/<account>/:

       2026-01-01_2026-12-31_reports_20260630T103000Z.csv
       2026-01-01_2026-12-31_transactions_20260630T103000Z.csv
       2026-01-01_2026-12-31_actions_20260630T103000Z.csv

   One UTC timestamp is shared across all three files so they sort together.

Error isolation: a failure in one month is logged and displayed but does
NOT abort the remaining months.  If any month fails, no combined CSV is
written for the affected types (so the range is not marked as complete).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

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

from scripts.cleanup import run_cleanup
from scripts.cli import CLIArgs
from scripts.client import ExpensifyClient, ExpensifyAPIError
from scripts.config import AppConfig
from scripts.csv_exporter import (
    count_csv_rows,
    csv_already_exists,
    promote_to_processed,
    write_combined_csvs,
)
from scripts.logger import get_logger, prune_old_logs
from scripts.postgres_loader import run_load_procedure
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
    error: str | None = None
    duration_seconds: float = 0.0

    @property
    def success(self) -> bool:
        return self.error is None and not self.skipped


@dataclass
class PipelineResult:
    months: list[MonthResult] = field(default_factory=list)
    csv_paths: list[Path] = field(default_factory=list)

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
        periods = list(months_in_range(self._args.start, self._args.end))

        if self._args.dry_run:
            return self._dry_run(periods)

        result = PipelineResult()

        # ------------------------------------------------------------------ #
        # Range-level resume check                                             #
        # ------------------------------------------------------------------ #
        if not self._args.force and csv_already_exists(
            start_date=self._args.start,
            end_date=self._args.end,
            pending_dir=self._config.pending_dir,
            processed_dir=self._config.processed_dir,
        ):
            console.print(
                f"[yellow]SKIP[/yellow] {self._args.start} → {self._args.end} "
                f"— combined CSVs already exist (use --force to overwrite)"
            )
            log.info(
                "Skipping %s → %s — combined CSVs already exist.",
                self._args.start,
                self._args.end,
            )
            for start_date, _ in periods:
                result.months.append(
                    MonthResult(year=start_date.year, month=start_date.month, skipped=True)
                )
            return result

        # ------------------------------------------------------------------ #
        # Fetch phase — accumulate raw CSV bytes per type across all months   #
        # ------------------------------------------------------------------ #
        accumulated: dict[str, list[bytes]] = {t: [] for t in ("reports", "transactions", "actions")}
        run_at = datetime.now(tz=timezone.utc)

        console.print(
            Panel(
                f"[bold cyan]Expensify Data Pipeline[/bold cyan]\n"
                f"Account:  [white]{self._config.account_name}[/white]\n"
                f"Period:   [yellow]{self._args.start}[/yellow] → "
                f"[yellow]{self._args.end}[/yellow]\n"
                f"Months:   [green]{len(periods)}[/green]",
                border_style="cyan",
            )
        )

        with ExpensifyClient(self._config, self._rate_limiter) as client:
            progress = self._build_progress()
            with progress:
                overall_task = progress.add_task("[cyan]Fetching", total=len(periods))

                for start_date, end_date in periods:
                    month_result = self._fetch_month(
                        client=client,
                        progress=progress,
                        overall_task=overall_task,
                        start_date=start_date,
                        end_date=end_date,
                        accumulated=accumulated,
                    )
                    result.months.append(month_result)
                    progress.advance(overall_task)

        # ------------------------------------------------------------------ #
        # Write phase — one combined file per CSV type                        #
        # ------------------------------------------------------------------ #
        if result.total_transactions > 0 and result.failed == 0:
            csv_paths = write_combined_csvs(
                accumulated=accumulated,
                start_date=self._args.start,
                end_date=self._args.end,
                pending_dir=self._config.pending_dir,
                restaurant_name=self._config.account_name,
                run_at=run_at,
            )
            result.csv_paths = csv_paths

            console.print(
                f"\n[green]✓[/green] Combined CSVs written to "
                f"[dim]{self._config.pending_dir.relative_to(self._config.project_root)}/"
                f"[/dim]:"
            )
            for p in csv_paths:
                console.print(f"   [dim]→[/dim] {p.name}")

            try:
                run_load_procedure(self._config, csv_paths=csv_paths)
            except Exception as exc:  # noqa: BLE001
                console.print(
                    f"\n[red]✗[/red] PostgreSQL procedure failed — "
                    f"CSV files remain in pending. Error: {exc}"
                )
                log.exception("PostgreSQL load procedure failed")
                raise RuntimeError(
                    "PostgreSQL load procedure failed. "
                    "Pending CSV files were not moved to processed."
                ) from exc

            promoted_paths: list[Path] = []
            for csv_path in csv_paths:
                promoted_paths.append(
                    promote_to_processed(csv_path, self._config.processed_dir)
                )

            result.csv_paths = promoted_paths
            console.print(
                f"\n[green]✓[/green] Moved {len(promoted_paths)} CSV file(s) to "
                f"[dim]{self._config.processed_dir.relative_to(self._config.project_root)}/[/dim]"
            )

            self._run_retention_cleanup()

        elif result.failed > 0:
            console.print(
                f"\n[red]✗[/red] {result.failed} month(s) failed — "
                f"combined CSVs were NOT written. Fix the errors and re-run."
            )
        else:
            console.print(
                f"\n[yellow]~[/yellow] No transactions found for "
                f"{self._args.start} → {self._args.end}."
            )

        self._print_summary(result)
        return result

    # ------------------------------------------------------------------
    # Month fetch (API calls only — no file writing)
    # ------------------------------------------------------------------

    def _fetch_month(
        self,
        client: ExpensifyClient,
        progress: Progress,
        overall_task: TaskID,
        start_date: date,
        end_date: date,
        accumulated: dict[str, list[bytes]],
    ) -> MonthResult:
        year = start_date.year
        month = start_date.month
        label = f"{year} {month_name(month)}"

        result = MonthResult(year=year, month=month)
        t0 = time.perf_counter()

        month_task = progress.add_task(f"[green]{label}", total=None)

        try:
            log.info("Fetching %s: %s → %s", label, start_date, end_date)

            all_csvs = client.fetch_all_csvs(
                start_date=format_expensify_date(start_date),
                end_date=format_expensify_date(end_date),
            )

            result.reports = count_csv_rows(all_csvs["reports"])
            result.transactions = count_csv_rows(all_csvs["transactions"])
            result.actions = count_csv_rows(all_csvs["actions"])

            for csv_type, content in all_csvs.items():
                accumulated[csv_type].append(content)

            console.print(
                f"  [green]✓[/green] {label}: "
                f"[bold]{result.reports}[/bold] reports · "
                f"[bold]{result.transactions}[/bold] transactions · "
                f"[bold]{result.actions}[/bold] actions"
            )
            log.info(
                "Fetched %s: reports=%d transactions=%d actions=%d",
                label, result.reports, result.transactions, result.actions,
            )

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
            "Month done: %s duration=%.2fs error=%s",
            label, result.duration_seconds, result.error,
        )
        return result

    # ------------------------------------------------------------------
    # Retention cleanup — runs automatically after a successful load,
    # replacing the need for a separately scheduled cleanup job.
    # ------------------------------------------------------------------

    def _run_retention_cleanup(self) -> None:
        console.print(
            f"\n[cyan]↻[/cyan] Running retention cleanup "
            f"([yellow]{self._config.retention_days}[/yellow] days)…"
        )
        try:
            run_cleanup(
                processed_dir=self._config.processed_dir,
                retention_days=self._config.retention_days,
                dry_run=False,
            )
            prune_old_logs(self._config.log_dir, self._config.log_retention_days)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]![/yellow] Retention cleanup failed: {exc}")
            log.exception("Retention cleanup failed")

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

        result = PipelineResult()

        for start_date, end_date in periods:
            year, month = start_date.year, start_date.month
            mr = MonthResult(year=year, month=month)
            result.months.append(mr)
            table.add_row(
                f"{year} {month_name(month)}",
                str(start_date),
                str(end_date),
            )

        existing = csv_already_exists(
            start_date=self._args.start,
            end_date=self._args.end,
            pending_dir=self._config.pending_dir,
            processed_dir=self._config.processed_dir,
        )
        console.print(table)
        console.print(
            f"\nOutput: [dim]{self._config.pending_dir}/[/dim]\n"
            f"Combined CSVs exist: "
            + ("[yellow]Yes (would skip — use --force to overwrite)[/yellow]" if existing else "[green]No[/green]")
        )
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
