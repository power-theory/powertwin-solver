#!/usr/bin/env python3
"""A locked SQLite database must be retried, never read as an empty answer.

The SQLite reads caught every error and returned [] / 0 / None, so the retry decorator never saw
a lock: in the nat34h local probe (2026-10-15, 38 workers on one database) one batch read no
assets, logged "Completed - 0 successful, 0 failed" and left its 8 buildings unrun, and a weather
lookup returned None ("cannot unpack non-iterable NoneType"). The reads now raise a lock error to
the decorator, and a batch that still cannot read its assets logs it and returns on its own.

    pytest tests/test_sqlite_lock_retry.py
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "solver", "app"))


@pytest.fixture
def sqlite_ops(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "powertwin.db"))
    monkeypatch.setenv("PGDATABASE", "powertwin")
    from modules.database import sqlite_operations as ops
    from modules.database import sqlite_manager

    monkeypatch.setattr(sqlite_manager, "_sqlite_manager", None, raising=False)
    mgr = sqlite_manager.get_sqlite_manager()
    monkeypatch.setattr(mgr, "db_path", str(tmp_path / "powertwin.db"), raising=False)
    for attr in ("connection", "db_path"):
        if hasattr(ops.thread_local, attr):
            delattr(ops.thread_local, attr)
    monkeypatch.setattr(ops.time, "sleep", lambda s: None)
    assert ops.create_table()
    for aid in (11, 12):
        ops.insert_asset(aid, "WY", "A.epw", 100.0, 1, 1, f"b{aid}", "Office", "sim")
    return ops


class _LockedConn:
    """Wraps a connection; its first `fails` execute calls raise the lock error."""

    def __init__(self, conn, state):
        self._conn, self._state = conn, state

    def execute(self, *a, **k):
        if self._state["fails"] > 0:
            self._state["fails"] -= 1
            raise sqlite3.OperationalError("database is locked")
        return self._conn.execute(*a, **k)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _lock(ops, monkeypatch, fails):
    real = ops.get_sqlite_connection
    state = {"fails": fails}
    monkeypatch.setattr(ops, "get_sqlite_connection", lambda: _LockedConn(real(), state))
    return state


def test_reads_retry_a_lock_instead_of_returning_empty(sqlite_ops, monkeypatch):
    ops = sqlite_ops
    _lock(ops, monkeypatch, fails=2)
    assert [a["asset_id"] for a in ops.get_bulk_assets("sim")] == [11, 12]
    _lock(ops, monkeypatch, fails=2)
    assert ops.get_weather(11, "sim") == ("WY", "A.epw")
    _lock(ops, monkeypatch, fails=2)
    assert ops.get_asset_total("sim") == 2


def test_a_lock_that_outlasts_the_retries_raises(sqlite_ops, monkeypatch):
    ops = sqlite_ops
    _lock(ops, monkeypatch, fails=ops.MAX_RETRIES)
    with pytest.raises(sqlite3.OperationalError):
        ops.get_bulk_assets("sim")


def test_other_read_errors_keep_their_empty_answer(sqlite_ops, monkeypatch):
    ops = sqlite_ops
    monkeypatch.setenv("PGDATABASE", "no_such_table")
    assert ops.get_bulk_assets("sim") == []


def test_a_batch_that_cannot_read_its_assets_returns_alone(monkeypatch):
    from modules.simulation import run_UOsim
    import modules.diagnostics as diag

    monkeypatch.delenv("SLURM_JOB_ID", raising=False)

    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(diag, "get_asset_total", locked)
    ran = []
    monkeypatch.setattr(run_UOsim, "run_single_asset", lambda *a, **k: ran.append(a), raising=False)
    assert run_UOsim.run_batch(25, "/nonexistent", "/nonexistent", "sim") is None
    assert ran == []
