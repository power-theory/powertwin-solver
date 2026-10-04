"""Request-boundary guards added 2026-09-20 after an external static-analysis report was
verified empirically in a throwaway container:
- a bare '..' simulation_name passed Flask's converter and DELETE /api/simulation/delete/..
  emptied the whole user_files volume and removed /solver/app under the running server;
- POST /api/simulation/stop was unauthenticated and its kill filter spared only 'app.py',
  so it SIGKILLed the run.py worker and the container exited;
- run_parallel_batches gave every node every batch when SLURM_NODEID was unset (warning only).
Function-level checks execute the real code; route wiring is pinned with the repo's
contract-string pattern (see test_orchestration_lifecycle.py)."""
import os
import re
import sys
import types

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(REPO, "solver", "app")
sys.path.insert(0, APP)

VIEWS = open(os.path.join(APP, "views.py")).read()


def _views_namespace():
    """The name validator without importing views (its module imports pull the flask app
    graph). Executes the validator block verbatim so the test runs the shipped code."""
    block = VIEWS.split("_SIM_NAME_RE = ", 1)[1].split("# Reporting-frequency translation", 1)[0]
    ns = {"os": os, "re": re, "DATA_DIR": os.path.join("data"),
          "jsonify": lambda *a, **kw: kw, "logger": types.SimpleNamespace(error=lambda *a, **k: None)}
    exec("_SIM_NAME_RE = " + block, ns)
    return ns


@pytest.mark.parametrize("name", ["..", ".", "../../tmp/x", "a/b", ".hidden", "", "a b", "a\\b", "x" * 300, None, 7])
def test_traversal_and_junk_names_rejected(name):
    ns = _views_namespace()
    assert ns["_valid_sim_name"](name) is False
    resp = ns["_reject_sim_name"](name)
    assert resp is not None and resp[1] == 400


@pytest.mark.parametrize("name", ["asset_442104_20260920_184950_000630", "natv3b", "qa_matrix_fx_arm",
                                  "simtest_res-a.v2", "asu_true", "7"])
def test_real_names_accepted(name):
    ns = _views_namespace()
    assert ns["_valid_sim_name"](name) is True
    assert ns["_reject_sim_name"](name) is None


def test_every_simulation_name_route_validates():
    # Each handler that joins a caller-supplied simulation_name must call the validator
    # before touching the filesystem. Pinned per handler body, and no handler may join a
    # simulation-name variable without appearing in this list.
    joins = re.findall(r"\ndef (\w+)\(.*?(?=\ndef |\Z)", VIEWS, re.S)
    joiners = {m.group(1) for m in re.finditer(r"\ndef (\w+)\((?:(?!\ndef ).)*?os\.path\.join\([^)]*simulation_name", VIEWS, re.S)}
    handlers_expected = {"start_simulation", "autorun_simulation", "get_simulation_status", "delete_simulation",
                         "get_simulation_results", "get_asset_config", "recovery", "process_asset_update"}
    assert joiners <= handlers_expected, f"unlisted handlers join a simulation name: {joiners - handlers_expected}"
    handlers = ["start_simulation", "autorun_simulation", "get_simulation_status",
                "delete_simulation", "get_simulation_results", "get_asset_config",
                "recovery", "process_asset_update"]
    for h in handlers:
        body = VIEWS.split(f"\ndef {h}(", 1)[1].split("\ndef ", 1)[0]
        assert "_reject_sim_name(simulation_name" in body, f"{h} joins simulation_name unvalidated"
        # validation precedes the first join of the name into a path
        first_join = re.search(r"os\.path\.join\([^)]*simulation_name", body, re.IGNORECASE).start()
        assert body.index("_reject_sim_name(simulation_name") < first_join, (
            f"{h} joins simulation_name before validating it")


def test_stop_endpoint_is_token_gated():
    body = VIEWS.split("\ndef stop_simulation(", 1)[1].split("\ndef ", 1)[0]
    assert "_check_api_token()" in body and body.index("_check_api_token()") < body.index("stop_UOsimulation()")


def test_stop_kills_only_the_simulation_toolchain():
    from modules.simulation import stop_UOsim as S
    assert S.is_simulation_process("/usr/local/bin/ruby /usr/local/lib/ruby/gems/3.2.2/bin/uo run --scenario s.csv")
    assert S.is_simulation_process("/usr/local/lib/ruby/gems/3.2.2/bin/bundle update")
    assert S.is_simulation_process("/usr/local/openstudio-3.9.0/bin/openstudio --bundle x")
    assert S.is_simulation_process("/usr/local/openstudio-3.9.0/EnergyPlus/energyplus -w x.epw")
    for server in ("python run.py", "/usr/bin/python run.py", "gunicorn -w 2 -b 0.0.0.0:8080 run:app",
                   "postgres: checkpointer", "ruby -e sleep", "sleep 3600", "ps -eo pid,args"):
        assert not S.is_simulation_process(server), server


def test_stop_survives_short_ps_lines(monkeypatch):
    # M4 from the report: a short or blank ps line must be skipped, not crash the stop.
    import subprocess
    from modules.simulation import stop_UOsim as S
    fake = types.SimpleNamespace(stdout="  PID COMMAND\n\n   42 sleep 1\nnotapid x\n 43\n")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake)
    assert S.get_processes() == [(42, "sleep 1")]


def test_kill_never_targets_init_or_self(monkeypatch):
    from modules.simulation import stop_UOsim as S
    monkeypatch.setattr(S, "get_processes", lambda: [(1, "uo run pid1"), (os.getpid(), "uo run me"), (99999, "python run.py")])
    sent = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append(pid))
    assert S.kill_processes() == [] and sent == []


def test_missing_node_id_on_multi_node_job_refuses(monkeypatch):
    seen = {}

    class FakeMgr:
        def ensure_db_exists(self, name, batches):
            seen["batches"] = list(batches)
            return False

        def get_db_path(self):
            return "x"

    monkeypatch.setitem(sys.modules, "modules.database.sqlite_manager",
                        types.SimpleNamespace(get_sqlite_manager=lambda: FakeMgr()))
    # SLURM variables only: get_hpc_environment would import mpi4py and MPI_Init the shared pytest process, after
    # which any fork aborts in the image's libfabric EFA provider (a later joblib pool hung the suite, 2026-10-03)
    monkeypatch.setitem(sys.modules, "mpi4py", None)
    for k in ("SLURM_NODEID", "SLURM_NTASKS", "SLURM_PROCID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SLURM_JOB_ID", "1")
    monkeypatch.setenv("SLURM_JOB_NUM_NODES", "4")
    from modules.simulation import parallel as P
    with pytest.raises(RuntimeError, match="SLURM_NODEID is unset"):
        P.run_parallel_batches(list(range(40)), "/tmp", "/tmp", "probe")
    monkeypatch.setenv("SLURM_NODEID", "2")
    P.run_parallel_batches(list(range(40)), "/tmp", "/tmp", "probe")
    assert seen["batches"] == list(range(20, 30))
    monkeypatch.setenv("SLURM_JOB_NUM_NODES", "1")
    monkeypatch.delenv("SLURM_NODEID")
    P.run_parallel_batches(list(range(40)), "/tmp", "/tmp", "probe")
    assert seen["batches"] == list(range(40))


def _convert_metadata_to_csv():
    """Execute the shipped converter without importing views (flask app graph)."""
    import json
    block = VIEWS.split("\ndef convert_metadata_to_csv(", 1)[1].split("\ndef ", 1)[0]
    ns = {"json": json}
    exec("def convert_metadata_to_csv(" + block, ns)
    return ns["convert_metadata_to_csv"]


def test_asset_update_keeps_one_row_per_footprint():
    # Review 2026-09-20: grouping by asset alone simulated one footprint of a multi-footprint
    # asset (ASU asset 169 has 20) and reported the asset as done.
    import csv
    import io
    import json
    rows = []
    for gid in (11, 12, 13):
        for st, sid in ((1, 101), (2, 102)):
            rows.append({"asset_id": 67, "asset_name": "Campus", "asset_subtype_id": 17,
                         "sensor_type_id": st, "sensor_id": sid,
                         "asset_metadata": {"area": 1000.0}, "asset_geometries_properties": {"id": gid}})
    out = list(csv.DictReader(io.StringIO(_convert_metadata_to_csv()(rows))))
    assert [json.loads(r["asset_geometries_properties"])["id"] for r in out] == [11, 12, 13]
    assert all(json.loads(r["sensors"]) == {"1": 101, "2": 102} for r in out)
    assert all(r["asset_id"] == "67" for r in out)


def test_stop_marks_every_simulation_and_the_loop_honours_it(tmp_path, monkeypatch):
    # Review 2026-09-20: killing the toolchain alone let run_batch spawn the next asset; the
    # stop now drops a marker per simulation dir and the batch loop leaves the rest unprocessed.
    from modules.simulation import stop_UOsim as S
    data = tmp_path / "data"
    (data / "simA").mkdir(parents=True)
    (data / "simB").mkdir()
    (data / "stray.txt").write_text("x")
    monkeypatch.setattr(S, "kill_processes", lambda: [])
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    marked = S.request_stop(str(data))
    assert sorted(os.path.basename(m) for m in marked) == ["simA", "simB"]
    assert S.stop_requested(str(data / "simA")) and not S.stop_requested(str(data / "new_sim"))

    from modules.simulation import run_UOsim as R
    calls = {"processed": [], "reset": None}
    monkeypatch.setattr(R, "process_single_asset", lambda a, *rest: (calls["processed"].append(a[0]) or (True, a[0], None)))
    fake_diag = types.SimpleNamespace(
        get_asset_total=lambda *a: 3,
        get_bulk_assets=lambda *a: [(1, "a"), (2, "b"), (3, "c")],
        bulk_update_status=lambda ids, status, sim: calls.__setitem__("reset", (list(ids), status)),
    )
    monkeypatch.setitem(sys.modules, "modules.diagnostics", fake_diag)
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    R.run_batch(0, str(data / "simA"), str(tmp_path), "simA")
    assert calls["processed"] == [] and calls["reset"] == ([1, 2, 3], "Not Processed Yet")
    assert S.stopped_early(str(data / "simA"))                      # cut short: its caller reports stopped
    R.run_batch(0, str(data / "new_sim"), str(tmp_path), "new_sim")
    assert calls["processed"] == [1, 2, 3]


def test_a_stop_for_another_run_does_not_stop_a_finished_one(tmp_path, monkeypatch):
    """Review 2026-09-28: a stop writes .stop into EVERY simulation dir, so a run whose assets had all finished
    (it was in uo process or cleanup) answered 409. Only a run the stop actually cut short reports stopped."""
    from modules.simulation import stop_UOsim as S
    from modules.simulation import run_UOsim as R
    data = tmp_path / "data"
    (data / "simB").mkdir(parents=True)
    monkeypatch.setattr(R, "process_single_asset", lambda a, *rest: (True, a[0], None))
    fake_diag = types.SimpleNamespace(get_asset_total=lambda *a: 2, get_bulk_assets=lambda *a: [(1, "a"), (2, "b")],
                                      bulk_update_status=lambda *a: None)
    monkeypatch.setitem(sys.modules, "modules.diagnostics", fake_diag)
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    R.run_batch(0, str(data / "simB"), str(tmp_path), "simB")         # every asset finished
    S.request_stop(str(data))                                          # a stop aimed at another run lands now
    assert S.stop_requested(str(data / "simB")) and not S.stopped_early(str(data / "simB"))


def test_stop_endpoint_reports_marker_and_worker_writes_stopped():
    body = VIEWS.split("\ndef stop_simulation(", 1)[1].split("\ndef ", 1)[0]
    assert "outcome = stop_UOsimulation()" in body
    worker = VIEWS.split("\ndef _run_asset_update_simulation(", 1)[1].split("\ndef ", 1)[0]
    assert "stopped_early(simulation_dir)" in worker and "'stopped'" in worker
    assert worker.index("stopped_early(simulation_dir)") < worker.index("produced no cleaned_reports")


def test_name_length_leaves_room_for_the_files_named_after_it():
    """The name is the stem of <name>_asset.geojson and friends; a file name caps at 255 bytes, so a
    245-character name passed validation, then failed the save and left both dirs behind, blocking
    the name for good (review 2026-09-24)."""
    ns = _views_namespace()
    assert ns["_valid_sim_name"]("a" * 200) is True
    assert ns["_valid_sim_name"]("a" * 201) is False and ns["_valid_sim_name"]("a" * 245) is False


def test_a_stopped_batch_run_does_not_report_success():
    """start, autorun and recovery deleted the sim dir (and the stop marker in it) and answered
    'ran successfully' after a stop that simulated nothing (review 2026-09-24)."""
    for handler, sim_dir in (("\ndef start_simulation(", "SIMULATION_DIR"), ("\ndef autorun_simulation(", "SIMULATION_DIR"),
                             ("\ndef recovery(", "RECOVERY_DIR_CONTAINER")):
        body = VIEWS.split(handler, 1)[1].split("\ndef ", 1)[0]
        check, rmtree = body.index(f"stopped_early({sim_dir})"), body.index(f"shutil.rmtree({sim_dir})")
        assert check < rmtree and "_stopped_response(" in body, handler
    ns = _views_namespace()
    ns["logger"] = types.SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None)
    ns["jsonify"] = lambda *a, **kw: a[0] if a else kw
    body, code = ns["_stopped_response"]("sim3")
    assert code == 409 and body["stopped"] is True


def test_long_names_from_before_the_cap_stay_reachable():
    """Review 2026-09-29: the 200-character cap is for creating runs; lookups keep 255 so older runs can be
    queried and deleted."""
    ns = _views_namespace()
    assert ns["_valid_sim_name"]("a" * 230, creating=False) is True
    assert ns["_valid_sim_name"]("a" * 230) is False and ns["_valid_sim_name"]("a" * 256, creating=False) is False
    for lookup in ("get_simulation_status", "delete_simulation", "get_asset_config", "get_simulation_results"):
        body = VIEWS.split(f"\ndef {lookup}(", 1)[1].split("\ndef ", 1)[0]
        assert "_reject_sim_name(simulation_name, creating=False)" in body, lookup
    for creator in ("start_simulation", "process_asset_update", "autorun_simulation"):
        body = VIEWS.split(f"\ndef {creator}(", 1)[1].split("\ndef ", 1)[0]
        assert "_reject_sim_name(simulation_name)" in body, creator
