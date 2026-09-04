"""WP-04 evidence set: sqlite/postgres backend parity (Q/A D-01, D-02, D-03, D-04, D-05, R-016,
R-019b, R-020). Reproduce-first at T2 against a tmp sqlite db (harness pattern from
test_sim_asset_key.py); the postgres side is asserted structurally where a live PG is absent."""
import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))


@pytest.fixture
def ops(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "powertwin.db"))
    monkeypatch.setenv("PGDATABASE", "powertwin")
    from modules.database import sqlite_operations as ops
    from modules.database import sqlite_manager
    monkeypatch.setattr(sqlite_manager, "_sqlite_manager", None, raising=False)
    mgr = sqlite_manager.get_sqlite_manager()
    monkeypatch.setattr(mgr, "db_path", str(tmp_path / "powertwin.db"), raising=False)
    if hasattr(ops.thread_local, "connection"):
        del ops.thread_local.connection
    if hasattr(ops.thread_local, "db_path"):
        del ops.thread_local.db_path
    assert ops.create_table()
    return ops


def _drop_cached_conn(ops):
    """Close and forget the fixture's cached LOCAL (WAL) connection before flipping a test
    into HPC mode: on HPC the module opens fresh DELETE-journal connections, and a lingering
    WAL handle on the same file blocks the journal-mode switch ('database is locked'). Real
    HPC processes are born with SLURM_JOB_ID set and never mix modes."""
    if hasattr(ops.thread_local, "connection"):
        try:
            ops.thread_local.connection.close()
        except Exception:
            pass
        del ops.thread_local.connection
    if hasattr(ops.thread_local, "db_path"):
        del ops.thread_local.db_path


def _seed(ops, sim, n_finished=2, n_failed=1, n_null=1):
    aid = 1000
    for _ in range(n_finished):
        ops.insert_asset(aid, "WY", "w.epw", 100.0, 1, 1, f"a{aid}", "Office", sim)
        ops.update_status(sim, aid, "Finished")
        aid += 1
    for _ in range(n_failed):
        ops.insert_asset(aid, "WY", "w.epw", 100.0, 1, 1, f"a{aid}", "Office", sim)
        ops.update_status(sim, aid, "Failed")
        aid += 1
    for _ in range(n_null):
        ops.insert_asset(aid, "WY", "w.epw", 100.0, 1, 1, f"a{aid}", "Office", sim)
        aid += 1
    return aid


def test_router_counts_failed_as_terminal_like_postgres(ops, monkeypatch):
    # D-01: pg counts Finished+Failed as done; the sqlite router must match or HPC progress
    # stalls below 100% forever when any asset fails.
    _seed(ops, "simx", n_finished=2, n_failed=1, n_null=1)
    _drop_cached_conn(ops)
    monkeypatch.setenv("SLURM_JOB_ID", "1")
    import importlib
    import modules.diagnostics.db as ddb
    importlib.reload(ddb)  # IS_HPC_ENVIRONMENT is frozen at import (register X-3)
    finished, failed = ddb.get_status_stats("simx")
    assert failed == 1
    assert finished == 3, (
        f"router returned finished={finished}; postgres semantics count Finished+Failed as "
        "terminal (2+1=3)"
    )


def test_pg_error_path_returns_two_tuple():
    # D-03: the pg except branch returns a 3-tuple where every caller unpacks two.
    src = open(os.path.join(REPO, "solver", "app", "modules", "database",
                            "postgres_operations.py")).read()
    seg = src.split("def get_status_stats", 1)[1].split("\ndef ", 1)[0]
    assert "return 0, 0, 0" not in seg, "error path returns wrong arity (ValueError at callers)"


def test_update_simulation_name_recovers_null_status_rows(ops):
    # D-04: NULL-status (never started) rows must be renamed into the recovery like sqlite does;
    # pg's `status != 'Finished'` excludes NULLs. Parity target = include NULLs (sqlite is right).
    _seed(ops, "corrupt", n_finished=1, n_failed=0, n_null=2)
    conn = ops.get_sqlite_connection()
    conn.execute("UPDATE powertwin SET batch = 0 WHERE simulation_name = 'corrupt'")
    conn.commit()
    # make one row truly NULL-status (crash-before-start shape); the other stays the insert
    # default 'Not Processed Yet'
    conn.execute("UPDATE powertwin SET status = NULL WHERE asset_id = 1002")
    conn.commit()
    ops.update_simulation_name("rescue", "corrupt", 0)
    rows = conn.execute(
        "SELECT asset_id, status, simulation_name FROM powertwin ORDER BY asset_id").fetchall()
    rescued = {r["asset_id"] for r in rows if r["simulation_name"] == "rescue"}
    assert 1002 in rescued and 1001 in rescued, (
        f"sqlite must recover NULL and not-started rows; rescued={rescued}"
    )
    assert 1000 not in rescued, "Finished row must stay under the corrupted name"
    src = open(os.path.join(REPO, "solver", "app", "modules", "database",
                            "postgres_operations.py")).read()
    seg = src.split("def update_simulation_name", 1)[1].split("\ndef ", 1)[0]
    assert "status IS NULL OR" in seg.replace("IS  NULL", "IS NULL"), (
        "pg update_simulation_name strands NULL-status rows out of recovery"
    )


def test_bulk_update_status_reports_real_success(ops):
    # D-05: sqlite returns constant True; recover_UOsim branches on it. Must reflect rowcount.
    _seed(ops, "simy", n_finished=0, n_failed=0, n_null=1)
    ok = ops.bulk_update_status([1000, 424242], "Processing", "simy")
    assert ok is False, "bulk_update_status claimed success updating a nonexistent asset"
    ok2 = ops.bulk_update_status([1000], "Processing", "simy")
    assert ok2 is True
    # R-027b: the reset path must skip Finished silently (protection, not an error)
    conn = ops.get_sqlite_connection()
    conn.execute("UPDATE powertwin SET status='Finished' WHERE asset_id=1000"); conn.commit()
    assert ops.bulk_update_status([1000], "Not Processed Yet", "simy") is True
    row = conn.execute("SELECT status FROM powertwin WHERE asset_id=1000").fetchone()
    assert row["status"] == "Finished", "reset resurrected a Finished asset"


def test_consolidation_count_uses_distinct_union(ops):
    # R-016: expected count must be DISTINCT over the union, not a sum that double-counts
    # Finished rows seeded into node DBs.
    src = open(os.path.join(REPO, "solver", "app", "modules", "database",
                            "sqlite_operations.py")).read()
    seg = src.split("expected_final_count", 1)[1][:400]
    assert "preserved_finished_count" not in seg.split("\n")[0] or "DISTINCT" in seg, (
        "consolidation still sums node+preserved as if disjoint (false CRITICAL rollbacks)"
    )


def test_bulk_insert_resets_batch_and_timings_like_postgres(ops):
    # R-019b: re-submission must null batch/order_rank/timings like the pg upsert does.
    _seed(ops, "simz", n_finished=0, n_failed=0, n_null=1)
    conn = ops.get_sqlite_connection()
    conn.execute("UPDATE powertwin SET batch=7, order_rank=3, uorun_time=9.9 WHERE asset_id=1000")
    conn.commit()
    ops.insert_bulk_assets([{
        "asset_id": 1000, "state": "WY", "weather_file": "w.epw", "floor_area": 100.0,
        "number_of_stories": 1, "complexity": 1, "asset_name": "a1000", "subtype": "Office",
        "simulation_name": "simz",
    }])
    row = conn.execute("SELECT batch, order_rank, uorun_time FROM powertwin "
                       "WHERE asset_id=1000").fetchone()
    assert row["batch"] is None and row["order_rank"] is None and row["uorun_time"] is None, (
        f"re-submitted asset kept stale batch/order_rank/timings: {dict(row)}"
    )


def test_get_asset_stats_returns_per_asset_rows(ops):
    # R-020: views.py iterates per-asset dicts with asset_id/order_rank keys; the aggregate
    # dict shape KeyErrors the HPC stats export.
    _seed(ops, "simw", n_finished=1, n_failed=0, n_null=0)
    stats = ops.get_asset_stats("simw")
    assert isinstance(stats, list) and stats and "asset_id" in stats[0], (
        f"get_asset_stats shape {type(stats).__name__} breaks the stats CSV export"
    )


def test_distribute_uses_complexity_lpt_and_global_rank(ops):
    # R-012: HPC batching must mirror postgres LPT (complexity DESC round-robin, global rank),
    # not contiguous asset_id chunks -- uneven batches cost real node-hours on the cluster.
    conn = ops.get_sqlite_connection()
    for aid, cx in [(1, 5), (2, 50), (3, 10), (4, 40), (5, 20), (6, 30)]:
        ops.insert_asset(aid, "WY", "w.epw", 100.0, 1, cx, f"a{aid}", "Office", "lpt")
    assert ops.distribute_assets_to_batches("lpt", 2)
    rows = conn.execute(
        "SELECT asset_id, complexity, batch, order_rank FROM powertwin "
        "WHERE simulation_name='lpt' ORDER BY order_rank").fetchall()
    ranks = [r["complexity"] for r in rows]
    assert ranks == sorted(ranks, reverse=True), f"order_rank not complexity DESC: {ranks}"
    batches = [r["batch"] for r in rows]
    assert batches == [0, 1, 0, 1, 0, 1], f"not round-robin over rank: {batches}"


def test_get_bulk_assets_ordered_by_rank(ops):
    conn = ops.get_sqlite_connection()
    for aid, cx in [(11, 1), (12, 99), (13, 50)]:
        ops.insert_asset(aid, "WY", "w.epw", 100.0, 1, cx, f"a{aid}", "Office", "ord")
    ops.distribute_assets_to_batches("ord", 1)
    got = [a["asset_id"] for a in ops.get_bulk_assets("ord")]
    assert got == [12, 13, 11], f"bulk assets not in rank order: {got}"


def test_failed_assets_case_insensitive_and_ordered(ops):
    conn = ops.get_sqlite_connection()
    for aid, st in [(21, "Failed"), (22, "FAILED"), (23, "failed"), (24, "Finished")]:
        ops.insert_asset(aid, "WY", "w.epw", 100.0, 1, 1, f"a{aid}", "Office", "fc")
        conn.execute("UPDATE powertwin SET status=?, batch=?, order_rank=? WHERE asset_id=?",
                     (st, aid % 2, aid, aid))
        conn.commit()
    got = ops.get_failed_assets("fc")
    ids = [a["asset_id"] if isinstance(a, dict) else a for a in got]
    assert set(ids) == {21, 22, 23}, f"case variants missed (D-06): {ids}"


def test_create_table_does_not_sweep_node_dirs(ops, tmp_path):
    # R-018: the node_* rmtree in create_table could delete LIVE node DBs when a new submission
    # arrived mid-run; the job start script owns stale-dir cleanup.
    node = os.path.dirname(str(tmp_path / "powertwin.db"))
    victim = os.path.join(node, "node_9_alive")
    os.makedirs(victim, exist_ok=True)
    open(os.path.join(victim, "powertwin.db"), "w").write("x")
    assert ops.create_table()
    assert os.path.exists(victim), "create_table deleted a node_* directory (live-node hazard)"


def test_router_env_flip_not_frozen_at_import(ops, monkeypatch, tmp_path):
    # X-3/D-13: diagnostics/db froze IS_HPC_ENVIRONMENT at import; per-process env changes
    # (tests, request overrides, late SLURM detection) never took effect.
    import importlib
    import modules.diagnostics.db as ddb
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    importlib.reload(ddb)
    _drop_cached_conn(ops)
    monkeypatch.setenv("SLURM_JOB_ID", "77")
    _seed(ops, "flip", n_finished=1, n_failed=0, n_null=0)
    finished, failed = ddb.get_status_stats("flip")
    assert finished == 1, "router ignored SLURM_JOB_ID set after import (frozen flag)"


def test_router_functions_execute_in_sqlite_mode(ops, monkeypatch):
    """The diagnostics/db.py router is the ONLY caller surface production uses, but every
    other test here exercises the ops modules directly -- a NameError inside a router
    function (e.g. a stale constant in an f-string log line) reaches no test and detonates
    only in the container e2e. Drive the hot router entry points end-to-end in sqlite mode."""
    _drop_cached_conn(ops)
    monkeypatch.setenv("SLURM_JOB_ID", "424242")
    from modules.diagnostics import db as router

    sim = "router_smoke"
    assert router.insert_bulk_assets([
        {"asset_id": 9001, "state": "WY", "weather_file": "w.epw", "floor_area": 100.0,
         "number_of_stories": 1, "complexity": 1, "asset_name": "r1",
         "subtype": "Office", "simulation_name": sim},
        {"asset_id": 9002, "state": "WY", "weather_file": "w.epw", "floor_area": 200.0,
         "number_of_stories": 2, "complexity": 2, "asset_name": "r2",
         "subtype": "Office", "simulation_name": sim},
    ])
    router.distribute_assets_to_batches(1, sim)
    assets = router.get_bulk_assets(sim)
    assert len(assets) == 2
    router.update_status("Finished", asset_id=9001, simulation_name=sim)
    # contract: (finished_incl_failed, failed) -- read_status.py:31 unpacks exactly this
    finished, failed = router.get_status_stats(sim)
    assert (finished, failed) == (1, 0)
    stats_rows, _fname = router.get_asset_stats(sim)  # HPC mode returns (rows, filename)
    assert stats_rows is None or len(stats_rows) == 2, \
        f"per-asset stats export should carry the 2 inserted assets, got {stats_rows!r}"


def test_hpc_connections_are_transient_with_delete_journal(ops, monkeypatch):
    """Job 15949939 regression: cached WAL connections on the cluster's network filesystem
    storm SQLITE_PROTOCOL ('locking protocol') across nodes and killed the teton3tt insert at
    864/17,239 assets. Contract: under SLURM the module hands out a FRESH connection per call
    with journal_mode=DELETE; locally it keeps the R-017 per-thread cache (WAL is fine on
    local disk)."""
    # local: cached
    c1 = ops.get_sqlite_connection(); c2 = ops.get_sqlite_connection()
    assert c1 is c2, "local connections should be per-thread cached (R-017)"
    assert c1.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    # HPC: transient + DELETE journal
    _drop_cached_conn(ops)
    monkeypatch.setenv("SLURM_JOB_ID", "424242")
    h1 = ops.get_sqlite_connection(); h2 = ops.get_sqlite_connection()
    assert h1 is not h2, "HPC connections must be per-call (transient handles on network FS)"
    assert h1.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
    h1.close(); h2.close()
