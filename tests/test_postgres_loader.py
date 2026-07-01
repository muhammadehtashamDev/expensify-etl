"""Unit tests for PostgreSQL load procedure execution."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from psycopg2 import OperationalError

from scripts.postgres_loader import _extract_failure_notices, run_load_procedure


class _FakeCursor:
    def __init__(self, on_execute=None) -> None:
        self.executed: list[str] = []
        self._on_execute = on_execute

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query: str) -> None:
        self.executed.append(query)
        if self._on_execute is not None:
            self._on_execute()


class _FakeConn:
    def __init__(self, on_execute=None) -> None:
        self.cursor_obj = _FakeCursor(on_execute=on_execute)
        self.committed = False
        self.notices: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self.cursor_obj

    def commit(self) -> None:
        self.committed = True


def _config(**overrides):
    values = {
        "db_host": "localhost",
        "db_port": 5432,
        "db_name": "test_db",
        "db_user": "postgres",
        "db_password": "secret",
        "db_procedure": "public.proc_load_expensify",
        "db_connect_timeout": 10,
        "db_connect_retries": 2,
        "db_connect_retry_delay_seconds": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_calls_expected_procedure(monkeypatch):
    fake_conn = _FakeConn()
    captured = {}

    def _fake_connect(**kwargs):
        captured.update(kwargs)
        return fake_conn

    monkeypatch.setattr("scripts.postgres_loader.psycopg2.connect", _fake_connect)

    run_load_procedure(_config())

    assert captured == {
        "host": "localhost",
        "port": 5432,
        "dbname": "test_db",
        "user": "postgres",
        "password": "secret",
        "connect_timeout": 10,
    }
    assert fake_conn.cursor_obj.executed == ["CALL public.proc_load_expensify();"]
    assert fake_conn.committed is True


def test_rejects_invalid_procedure_name():
    with pytest.raises(ValueError):
        run_load_procedure(_config(db_procedure="public.proc_load_expensify; DROP TABLE x"))


def test_retries_then_succeeds(monkeypatch):
    fake_conn = _FakeConn()
    calls = {"count": 0}

    def _fake_connect(**kwargs):
        calls["count"] += 1
        if calls["count"] < 3:
            raise OperationalError("timeout")
        return fake_conn

    monkeypatch.setattr("scripts.postgres_loader.psycopg2.connect", _fake_connect)

    run_load_procedure(_config(db_connect_retries=2, db_connect_retry_delay_seconds=0))

    assert calls["count"] == 3
    assert fake_conn.committed is True


def test_raises_runtime_error_after_retries(monkeypatch):
    def _fake_connect(**kwargs):
        raise OperationalError("timeout")

    monkeypatch.setattr("scripts.postgres_loader.psycopg2.connect", _fake_connect)

    with pytest.raises(RuntimeError, match="PostgreSQL connection failed after"):
        run_load_procedure(_config(db_connect_retries=1, db_connect_retry_delay_seconds=0))


def test_extract_failure_notices_detects_error_terms():
    notices = [
        "NOTICE: started loader",
        "WARNING: Failed to load reports CSV",
        "NOTICE: finished",
    ]
    failed = _extract_failure_notices(notices)
    assert failed == ["WARNING: Failed to load reports CSV"]


def test_raises_when_procedure_emits_failure_notice(monkeypatch):
    fake_conn = _FakeConn(on_execute=lambda: fake_conn.notices.append("WARNING: Could not COPY file"))

    def _fake_connect(**kwargs):
        return fake_conn

    monkeypatch.setattr("scripts.postgres_loader.psycopg2.connect", _fake_connect)

    with pytest.raises(RuntimeError, match="reported warning/error notices"):
        run_load_procedure(_config())
