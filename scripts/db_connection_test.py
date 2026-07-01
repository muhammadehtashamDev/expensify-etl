"""Simple PostgreSQL connectivity check for the ETL project."""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2 import OperationalError

# Project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load .env file
load_dotenv(PROJECT_ROOT / ".env")


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@contextmanager
def _db_target(db_host: str, db_port: int):
    use_ssh_tunnel = _get_bool("DB_USE_SSH_TUNNEL", False)
    if not use_ssh_tunnel:
        yield db_host, db_port
        return

    try:
        from sshtunnel import SSHTunnelForwarder
    except ImportError as exc:
        raise RuntimeError(
            "DB_USE_SSH_TUNNEL=true but 'sshtunnel' is not installed. "
            "Run: pip install sshtunnel"
        ) from exc

    ssh_host = os.getenv("SSH_HOST", "")
    ssh_port = int(os.getenv("SSH_PORT", "22"))
    ssh_user = os.getenv("SSH_USER", "")
    ssh_key = os.getenv("SSH_PRIVATE_KEY_PATH", "")
    ssh_key_passphrase = os.getenv("SSH_PRIVATE_KEY_PASSPHRASE", "") or None
    remote_bind_host = os.getenv("SSH_REMOTE_BIND_HOST", "127.0.0.1")
    remote_bind_port = int(os.getenv("SSH_REMOTE_BIND_PORT", str(db_port)))

    if not ssh_host or not ssh_user or not ssh_key:
        raise RuntimeError(
            "SSH tunnel is enabled but required settings are missing. "
            "Set SSH_HOST, SSH_USER, and SSH_PRIVATE_KEY_PATH."
        )

    tunnel = SSHTunnelForwarder(
        (ssh_host, ssh_port),
        ssh_username=ssh_user,
        ssh_pkey=ssh_key,
        ssh_private_key_password=ssh_key_passphrase,
        remote_bind_address=(remote_bind_host, remote_bind_port),
        local_bind_address=("127.0.0.1", 0),
    )

    try:
        tunnel.start()
        print(
            f"SSH tunnel established: {ssh_host}:{ssh_port} -> "
            f"{remote_bind_host}:{remote_bind_port}"
        )
        yield "127.0.0.1", int(tunnel.local_bind_port)
    finally:
        tunnel.stop()
        print("SSH tunnel closed")


def check_db_connection() -> int:
    """Attempt a real DB connection and print a clear status message."""

    db_host = os.getenv("DB_HOST")
    db_port = int(os.getenv("DB_PORT", "5432"))
    db_name = os.getenv("DB_NAME")
    db_user = os.getenv("DB_USER")
    db_password = os.getenv("DB_PASSWORD")
    db_connect_timeout = int(os.getenv("DB_CONNECT_TIMEOUT", "10"))
    db_connect_retries = int(os.getenv("DB_CONNECT_RETRIES", "2"))
    db_connect_retry_delay_seconds = int(
        os.getenv("DB_CONNECT_RETRY_DELAY_SECONDS", "2")
    )

    last_error = None

    for attempt in range(1, db_connect_retries + 1):
        try:
            with _db_target(db_host, db_port) as (target_host, target_port):
                with psycopg2.connect(
                    host=target_host,
                    port=target_port,
                    dbname=db_name,
                    user=db_user,
                    password=db_password,
                    connect_timeout=db_connect_timeout,
                ) as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT current_database(), version();")
                        database, version = cur.fetchone()

            print("\n" + "=" * 70)
            print("✅ DATABASE CONNECTION SUCCESSFUL")
            print("=" * 70)
            print(f"Host      : {db_host}")
            print(f"Port      : {db_port}")
            print(f"Database  : {database}")
            print(f"User      : {db_user}")
            print(f"Version   : {version.split(',')[0]}")
            print("=" * 70)

            return 0

        except OperationalError as exc:
            last_error = exc

            if attempt < db_connect_retries:
                print(
                    f"⚠️ Connection attempt {attempt}/{db_connect_retries} failed."
                )
                print(
                    f"Retrying in {db_connect_retry_delay_seconds} second(s)...\n"
                )
                time.sleep(db_connect_retry_delay_seconds)

    print("\n" + "=" * 70)
    print("❌ DATABASE CONNECTION FAILED")
    print("=" * 70)
    print(f"Host      : {db_host}")
    print(f"Port      : {db_port}")
    print(f"Database  : {db_name}")
    print(f"Reason    : {last_error}")
    print("=" * 70)

    return 1


if __name__ == "__main__":
    raise SystemExit(check_db_connection())