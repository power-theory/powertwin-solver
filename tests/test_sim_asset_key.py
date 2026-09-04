#!/usr/bin/env python3
"""Guards the (asset_id, simulation_name) key on the solver tracking table.

A bare asset_id key gave concurrent sims of the SAME asset one shared row: the last writer
stamped its simulation_name and the others read back zero assets, then failed with
"produced no cleaned_reports output". Three users simulating one asset is a normal case
(sandboxes are per user), so the key must be per run.

Postgres is covered end to end by running three concurrent sims; this pins the SQLite/HPC
backend, which has no such harness.

    pytest tests/test_sim_asset_key.py
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "solver", "app"))


@pytest.fixture
def sqlite_ops(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "powertwin.db"))
    monkeypatch.setenv("PGDATABASE", "powertwin")
    from modules.database import sqlite_operations as ops
    from modules.database import sqlite_manager

    # get_sqlite_connection routes through the manager singleton; point it at the tmp db.
    monkeypatch.setattr(sqlite_manager, "_sqlite_manager", None, raising=False)
    mgr = sqlite_manager.get_sqlite_manager()
    monkeypatch.setattr(mgr, "db_path", str(tmp_path / "powertwin.db"), raising=False)
    if hasattr(ops.thread_local, "connection"):
        del ops.thread_local.connection
    if hasattr(ops.thread_local, "db_path"):
        del ops.thread_local.db_path
    return ops


def _rows(ops, asset_id):
    conn = ops.get_sqlite_connection()
    return conn.execute(
        "SELECT simulation_name, weather_file FROM powertwin WHERE asset_id = ? ORDER BY simulation_name",
        (asset_id,),
    ).fetchall()


def test_concurrent_sims_of_one_asset_keep_separate_rows(sqlite_ops):
    ops = sqlite_ops
    assert ops.create_table()
    for sim, wx in [("sim_a", "A.epw"), ("sim_b", "B.epw"), ("sim_c", "C.epw")]:
        ops.insert_asset(256956, "WY", wx, 100.0, 1, 1, "house", "Single-Family", sim)

    rows = _rows(ops, 256956)
    assert [r["simulation_name"] for r in rows] == ["sim_a", "sim_b", "sim_c"]
    # each run keeps its own inputs rather than the last writer's
    assert [r["weather_file"] for r in rows] == ["A.epw", "B.epw", "C.epw"]
    for sim in ("sim_a", "sim_b", "sim_c"):
        assert ops.get_asset_total(sim) == 1


def test_reinserting_the_same_run_still_upserts(sqlite_ops):
    ops = sqlite_ops
    assert ops.create_table()
    ops.insert_asset(1, "WY", "A.epw", 100.0, 1, 1, "h", "s", "sim_a")
    ops.insert_asset(1, "WY", "B.epw", 100.0, 1, 1, "h", "s", "sim_a")
    rows = _rows(ops, 1)
    assert len(rows) == 1 and rows[0]["weather_file"] == "B.epw"


LEGACY_DDL = """
    CREATE TABLE powertwin (
        asset_id INTEGER PRIMARY KEY,
        batch INTEGER, order_rank INTEGER, simulation_name VARCHAR(255),
        state VARCHAR(255), weather_file VARCHAR(255), floor_area REAL,
        number_of_stories INTEGER, complexity INTEGER, uorun_time REAL,
        uoprocess_time REAL, asset_name VARCHAR(255), subtype VARCHAR(255),
        status VARCHAR(255), total_time REAL,
        created_at DATETIME, updated_at DATETIME
    )
"""


def test_rekey_helper_migrates_a_master_shaped_db(sqlite_ops, tmp_path):
    """The master db accumulates every simulation and is NOT reached by create_table, so the
    consolidation path calls the helper directly. This covers that call on the host, since
    a real multi-node HPC run cannot be exercised here."""
    import sqlite3
    ops = sqlite_ops
    conn = sqlite3.connect(str(tmp_path / "master.db"))
    conn.row_factory = sqlite3.Row
    conn.execute(LEGACY_DDL)
    conn.execute("INSERT INTO powertwin (asset_id, simulation_name, status) VALUES (7, 'sim_old', 'Finished')")
    conn.commit()

    assert ops._rekey_if_needed(conn, "powertwin") is True
    cols = conn.execute("PRAGMA table_info(powertwin)").fetchall()
    assert sum(1 for c in cols if c["pk"]) == 2
    # the earlier simulation's record survives a later one repeating the asset
    conn.execute("INSERT INTO powertwin (asset_id, simulation_name) VALUES (7, 'sim_new')")
    conn.commit()
    got = conn.execute("SELECT simulation_name FROM powertwin WHERE asset_id=7 ORDER BY 1").fetchall()
    assert [r["simulation_name"] for r in got] == ["sim_new", "sim_old"]
    # the widened schema now carries the columns node rows are copied with
    assert {"failure_reason", "node_name", "process_id"} <= {c["name"] for c in cols}
    # second call is a no-op
    assert ops._rekey_if_needed(conn, "powertwin") is False


def _legacy_db(path, extra=""):
    import sqlite3
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.execute(f"""CREATE TABLE powertwin (asset_id INTEGER PRIMARY KEY,
        simulation_name VARCHAR(255), status VARCHAR(255){extra})""")
    c.execute("INSERT INTO powertwin (asset_id, simulation_name, status) VALUES (7, 'sim_a', 'Finished')")
    c.commit()
    return c


def test_rekey_survives_a_column_the_new_schema_dropped(sqlite_ops, tmp_path):
    """Copying every old column blindly aborted the INSERT and stranded the rows in _old
    behind an empty live table, which the next run skipped because the key looked right."""
    conn = _legacy_db(str(tmp_path / "extra.db"), ", legacy_extra TEXT")
    assert sqlite_ops._rekey_if_needed(conn, "powertwin") is True
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"powertwin"}
    assert conn.execute("SELECT count(*) FROM powertwin").fetchone()[0] == 1


def test_failed_rekey_leaves_the_original_intact(sqlite_ops, tmp_path, monkeypatch):
    """The rekey must be atomic. Without an explicit BEGIN the rename commits on its own and
    a failed copy strands the rows behind an empty table the next run skips."""
    conn = _legacy_db(str(tmp_path / "fail.db"))
    monkeypatch.setattr(sqlite_ops, "_table_ddl",
                        lambda t, if_not_exists=True: f"CREATE TABLE {t} (wrong INTEGER)")
    with pytest.raises(Exception):
        sqlite_ops._rekey_if_needed(conn, "powertwin")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"powertwin"}
    assert conn.execute("SELECT count(*) FROM powertwin").fetchone()[0] == 1
    # with the real schema back, the next run migrates it rather than staying stuck
    monkeypatch.undo()
    assert sqlite_ops._rekey_if_needed(conn, "powertwin") is True
    assert conn.execute("SELECT count(*) FROM powertwin").fetchone()[0] == 1


def test_legacy_single_key_table_is_rekeyed(sqlite_ops):
    """A database predating the change must migrate rather than stay collapsed."""
    ops = sqlite_ops
    conn = ops.get_sqlite_connection()
    conn.execute("""
        CREATE TABLE powertwin (
            asset_id INTEGER PRIMARY KEY,
            batch INTEGER, order_rank INTEGER, simulation_name VARCHAR(255),
            state VARCHAR(255), weather_file VARCHAR(255), floor_area REAL,
            number_of_stories INTEGER, complexity INTEGER, uorun_time REAL,
            uoprocess_time REAL, asset_name VARCHAR(255), subtype VARCHAR(255),
            status VARCHAR(255), total_time REAL,
            created_at DATETIME, updated_at DATETIME
        )
    """)
    conn.execute("INSERT INTO powertwin (asset_id, simulation_name) VALUES (7, 'old_sim')")
    conn.execute("INSERT INTO powertwin (asset_id, simulation_name) VALUES (8, NULL)")
    conn.commit()

    assert ops.create_table()

    cols = ops.get_sqlite_connection().execute("PRAGMA table_info(powertwin)").fetchall()
    assert sum(1 for c in cols if c["pk"]) == 2, "table was not rekeyed"
    # the real run survives, the unattributable row does not
    assert len(_rows(ops, 7)) == 1
    assert len(_rows(ops, 8)) == 0
    # and the rekeyed table now accepts a second run for the same asset
    ops.insert_asset(7, "WY", "B.epw", 1.0, 1, 1, "h", "s", "new_sim")
    assert len(_rows(ops, 7)) == 2


def test_rekey_migrates_a_table_with_no_primary_key(sqlite_ops, tmp_path):
    """A no-PK table has pk flags all 0. The original `!= 1` probe read that as 'already
    migrated' and skipped it, leaving a table where INSERT OR REPLACE degenerates to a plain
    INSERT and silently accumulates duplicate rows for one (asset, run)."""
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "nopk.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE powertwin (asset_id INTEGER, simulation_name VARCHAR(255), status VARCHAR(255))")
    conn.execute("INSERT INTO powertwin VALUES (7, 'sim_a', 'Finished')")
    conn.commit()
    assert sum(1 for c in conn.execute("PRAGMA table_info(powertwin)") if c["pk"]) == 0

    assert sqlite_ops._rekey_if_needed(conn, "powertwin") is True
    cols = conn.execute("PRAGMA table_info(powertwin)").fetchall()
    assert sum(1 for c in cols if c["pk"]) == 2
    assert conn.execute("SELECT count(*) FROM powertwin").fetchone()[0] == 1
    # and the key now actually rejects a duplicate (asset, run)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO powertwin (asset_id, simulation_name) VALUES (7, 'sim_a')")


def test_a_missing_simulation_name_never_broadens_the_match(sqlite_ops):
    """No ambiguous path back. The loud rejection lives at the router (the only production call
    path; it cannot be imported here because it pulls in psycopg). What must hold at the backend
    is the safety property: a None run name matches NOTHING rather than every run of the asset."""
    ops = sqlite_ops
    assert ops.create_table()
    for sim in ("sim_a", "sim_b"):
        ops.insert_asset(42, "WY", f"{sim}.epw", 1.0, 1, 1, "h", "s", sim)

    assert ops.get_weather(42, None) is None          # not "whichever row came first"
    ops.update_batch(42, 3, None)

    rows = _rows(ops, 42)
    assert [r["weather_file"] for r in rows] == ["sim_a.epw", "sim_b.epw"]
    conn = ops.get_sqlite_connection()
    batches = [r[0] for r in conn.execute("SELECT batch FROM powertwin WHERE asset_id=42")]
    assert batches == [None, None], "a null run name updated rows it does not identify"


def test_router_rejects_a_missing_simulation_name():
    """The router is where the loud guard lives; pin it by source so the duplicates removed from
    the backends cannot be the only thing standing between us and the old behaviour."""
    import ast
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tree = ast.parse(open(os.path.join(repo, "solver/app/modules/diagnostics/db.py")).read())
    guarded = set()
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        src = ast.unparse(fn)
        if "needs simulation_name" in src and "raise ValueError" in src:
            guarded.add(fn.name)
    assert {"update_time", "update_status"} <= guarded, f"router guards missing: {guarded}"


def test_no_ambiguous_sql_remains_in_either_backend():
    """The `(? IS NULL OR simulation_name = ?)` shim silently restored the pre-key behaviour."""
    import re
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ("solver/app/modules/database/sqlite_operations.py",
                "solver/app/modules/database/postgres_operations.py",
                "solver/app/modules/diagnostics/db.py"):
        src = open(os.path.join(repo, rel)).read()
        assert not re.search(r"IS NULL OR simulation_name", src), f"{rel} reintroduced the shim"
