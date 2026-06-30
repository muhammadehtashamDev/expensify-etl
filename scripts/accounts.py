"""
Multi-account support.

Credentials are loaded from environment variables (via .env) using a numbered
scheme:

    ACCOUNT_1_NAME=parkbars
    ACCOUNT_1_PARTNER_USER_ID=aa_developer_parkbars_com
    ACCOUNT_1_PARTNER_USER_SECRET=<secret>

    ACCOUNT_2_NAME=wolfandcranebar
    ACCOUNT_2_PARTNER_USER_ID=aa_developer_wolfandcranebar_com
    ACCOUNT_2_PARTNER_USER_SECRET=<secret>

The loader scans ACCOUNT_1_*, ACCOUNT_2_*, … in order until no NAME is found.

If no environment variables are present it falls back to ``config/accounts.json``
for backward compatibility.  See ``config/accounts.example.json`` for the
expected JSON format.

Each account runs through its own pipeline pass and writes output to an
account-named subdirectory under the shared pending/processed roots.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AccountConfig:
    """Credentials and identifier for one Expensify account."""

    name: str
    partner_user_id: str
    partner_user_secret: str


def _load_from_env() -> list[AccountConfig]:
    """Scan ACCOUNT_N_* environment variables and return all accounts found."""
    accounts: list[AccountConfig] = []
    n = 1
    while True:
        prefix = f"ACCOUNT_{n}_"
        name = os.getenv(f"{prefix}NAME")
        if not name:
            break

        uid = os.getenv(f"{prefix}PARTNER_USER_ID")
        secret = os.getenv(f"{prefix}PARTNER_USER_SECRET")

        if not uid or not secret:
            raise ValueError(
                f"Account {n} ('{name}') is missing "
                f"{prefix}PARTNER_USER_ID or {prefix}PARTNER_USER_SECRET in .env."
            )

        accounts.append(
            AccountConfig(name=name, partner_user_id=uid, partner_user_secret=secret)
        )
        n += 1

    return accounts


def _load_from_file(accounts_file: Path) -> list[AccountConfig]:
    """Load accounts from a JSON file (backward-compat fallback)."""
    try:
        data = json.loads(accounts_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {accounts_file}: {exc}") from exc

    if not isinstance(data, list) or not data:
        raise ValueError(
            f"{accounts_file} must contain a non-empty JSON array of account objects."
        )

    accounts: list[AccountConfig] = []
    for i, entry in enumerate(data):
        for key in ("name", "partnerUserID", "partnerUserSecret"):
            if key not in entry:
                raise ValueError(
                    f"Account entry {i} in {accounts_file} is missing required key '{key}'."
                )
        accounts.append(
            AccountConfig(
                name=entry["name"],
                partner_user_id=entry["partnerUserID"],
                partner_user_secret=entry["partnerUserSecret"],
            )
        )

    return accounts


def load_accounts(accounts_file: Path) -> list[AccountConfig]:
    """Load all accounts, preferring environment variables over the JSON file.

    Priority:
    1. ``ACCOUNT_N_*`` environment variables defined in ``.env``
    2. ``accounts_file`` (JSON) as a backward-compatible fallback

    Args:
        accounts_file: Path to ``config/accounts.json`` (used only as fallback).

    Returns:
        List of :class:`AccountConfig`, one per account.

    Raises:
        FileNotFoundError: If neither env vars nor the JSON file provide accounts.
        ValueError: If credentials are incomplete or the JSON is malformed.
    """
    env_accounts = _load_from_env()
    if env_accounts:
        return env_accounts

    if accounts_file.exists():
        return _load_from_file(accounts_file)

    raise FileNotFoundError(
        "No account credentials found.\n"
        "Option 1 (recommended): set ACCOUNT_1_NAME, ACCOUNT_1_PARTNER_USER_ID, "
        "ACCOUNT_1_PARTNER_USER_SECRET (and ACCOUNT_2_*, …) in your .env file.\n"
        f"Option 2 (legacy): copy config/accounts.example.json to {accounts_file} "
        "and fill in your credentials."
    )
