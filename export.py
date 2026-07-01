#!/usr/bin/env python3
"""
Expensify Data Pipeline — main entry point.

Usage:
    python export.py --year 2026                          # all accounts
    python export.py --year 2026 --account parkbars       # one account
    python export.py --month 7 --year 2026
    python export.py --start 2026-01-15 --end 2026-04-30
    python export.py --year 2026 --force
    python export.py --year 2026 --dry-run
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

from rich.console import Console
from rich.rule import Rule

console = Console()

# Location of the accounts credentials file
_ACCOUNTS_FILE = Path(__file__).parent / "config" / "accounts.json"


def main() -> int:
    try:
        from scripts.accounts import load_accounts
        from scripts.cli import parse_args
        from scripts.config import load_config
        from scripts.logger import setup_logging
        from scripts.pipeline import Pipeline
    except ImportError as exc:
        console.print(f"[red]Import error:[/red] {exc}")
        console.print("Run [bold]pip install -r requirements.txt[/bold] first.")
        return 1

    # Parse CLI first so --help works even without config
    args = parse_args()

    # ------------------------------------------------------------------ #
    # Load shared base config (API URL, templates, log dir, etc.)         #
    # ------------------------------------------------------------------ #
    try:
        base_config = load_config()
    except (EnvironmentError, FileNotFoundError) as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        return 1

    setup_logging(log_dir=base_config.log_dir, log_level=base_config.log_level)

    # ------------------------------------------------------------------ #
    # Load accounts                                                        #
    # ------------------------------------------------------------------ #
    try:
        all_accounts = load_accounts(_ACCOUNTS_FILE)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Accounts error:[/red] {exc}")
        return 1

    # Filter to a single account if --account was specified
    if args.account:
        matched = [a for a in all_accounts if a.name == args.account]
        if not matched:
            available = ", ".join(a.name for a in all_accounts)
            console.print(
                f"[red]Account '{args.account}' not found.[/red] "
                f"Available: {available}"
            )
            return 1
        accounts = matched
    else:
        accounts = all_accounts

    # ------------------------------------------------------------------ #
    # Run pipeline for each account                                        #
    # ------------------------------------------------------------------ #
    any_failed = False

    for account in accounts:
        if len(accounts) > 1:
            console.print(Rule(f"[bold cyan]{account.name}[/bold cyan]"))

        # Build account-specific config:
        # - Override credentials with this account's values
        # - Put output in a named subdirectory so accounts don't collide
        account_config = dataclasses.replace(
            base_config,
            account_name=account.name,
            partner_user_id=account.partner_user_id,
            partner_user_secret=account.partner_user_secret,
            pending_dir=base_config.pending_dir / account.name,
            processed_dir=base_config.processed_dir / account.name,
        )

        try:
            result = Pipeline(config=account_config, args=args).run()
            if result.failed > 0:
                any_failed = True
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted by user.[/yellow]")
            return 130
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Fatal error ({account.name}):[/red] {exc}")
            any_failed = True

    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
