"""Multi-account support loaded from environment variables (.env).

Credentials are loaded using a numbered scheme:

    ACCOUNT_1_NAME=demo_account_one
    ACCOUNT_1_PARTNER_USER_ID=demo_partner_user_one
    ACCOUNT_1_PARTNER_USER_SECRET=demo_secret_one

    ACCOUNT_2_NAME=demo_account_two
    ACCOUNT_2_PARTNER_USER_ID=demo_partner_user_two
    ACCOUNT_2_PARTNER_USER_SECRET=demo_secret_two

The loader scans ACCOUNT_1_*, ACCOUNT_2_*, … in order until no NAME is found.

Each account runs through its own pipeline pass and writes output to an
account-named subdirectory under the shared pending/processed roots.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


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


def load_accounts() -> list[AccountConfig]:
    """Load all accounts from ``ACCOUNT_N_*`` environment variables.

    Returns:
        List of :class:`AccountConfig`, one per account.

    Raises:
        FileNotFoundError: If no ``ACCOUNT_N_*`` credentials are configured.
        ValueError: If any account block is incomplete.
    """
    env_accounts = _load_from_env()
    if env_accounts:
        return env_accounts

    raise FileNotFoundError(
        "No account credentials found in environment variables.\n"
        "Set ACCOUNT_1_NAME, ACCOUNT_1_PARTNER_USER_ID, "
        "ACCOUNT_1_PARTNER_USER_SECRET (and ACCOUNT_2_*, …) in your .env file."
    )
