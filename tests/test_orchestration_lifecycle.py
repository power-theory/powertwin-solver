"""WP-05 evidence set: orchestration lifecycle (Q/A R-022..R-027, D-43..D-51).
Reproduce-first; views-level behaviors pinned with the repo's established contract-string
pattern (see test_solver_contracts.py), function-level ones executed at T2."""
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))

VIEWS = open(os.path.join(REPO, "solver", "app", "views.py")).read()
RUNUO = open(os.path.join(REPO, "solver", "app", "modules", "simulation", "run_UOsim.py")).read()
CLEAN = open(os.path.join(REPO, "solver", "app", "modules", "simulation", "clean_report.py")).read()
RECOV = open(os.path.join(REPO, "solver", "app", "modules", "diagnostics", "recover_UOsim.py")).read()
INIT = open(os.path.join(REPO, "solver", "app", "modules", "simulation", "initialize_UOsim.py")).read()
WAIT = open(os.path.join(REPO, "solver", "app", "modules", "utils", "wait_on_nodes.py")).read()


def test_metadata_guard_matches_consumer_predicate():
    # R-024: producer guard and consumer glob must use ONE predicate; a bare metadata.csv used
    # to pass the guard then silently no-op the cleaning while the asset went Finished.
    assert "f.endswith('metadata.csv')" not in RUNUO, (
        "run_UOsim guard still uses endswith while clean_report requires *_metadata.csv"
    )
    assert "_metadata.csv" in RUNUO


def test_clean_failure_fails_the_asset():
    # R-024b: a cleaning failure must not leave the asset Finished with its outputs destroyed.
    assert "clean_single_report" in RUNUO
    assert "cleaned_ok = clean_single_report" in RUNUO, (
        "cleaning result is ignored; silent data loss under a Finished status"
    )


def test_deleted_tombstone_does_not_poison_reuse():
    # D-43: a dir holding only the terminal 'deleted' tombstone must not 400 the next run
    # of the same name.
    assert "_is_deleted_tombstone" in VIEWS, (
        "name-exists guard has no tombstone escape; deleted sim names are unusable forever"
    )


def test_recovery_dirs_cleaned_on_failure():
    # D-44: a failed recovery must not leave dirs that hard-reject the retry with 400.
    seg = VIEWS.split("Recovery directory already exists", 1)[0]
    assert "_cleanup_recovery_dirs(RECOVERY_DIR_LOCAL" in VIEWS.split("def recovery():", 1)[1], (
        "failed recovery leaves dirs; retry is rejected"
    )


def test_geojson_checked_like_metadata():
    # R-025
    seg = VIEWS.split("def recovery():", 1)[1].split("\ndef ", 1)[0]
    assert "geojson_path" in seg and "os.path.exists(geojson_path)" in seg, (
        "missing GeoJSON raises a bare 500 instead of the 404 the metadata gets"
    )


def test_zero_batch_distribution_fails_loud():
    # D-48: prepare_record returning [] must abort, not run a zero-batch campaign to success.
    assert "refusing to run an empty campaign" in INIT, (
        "empty batch range proceeds silently; campaign reports success doing nothing"
    )


def test_weather_ensure_checks_real_store_and_tells_truth():
    # R-026: the recovery weather check probed the wrong directory and logged 'ensured'
    # while downloading nothing.
    assert "urbanopt', 'weather'" not in RECOV.replace('"', "'"), (
        "recovery still probes app/urbanopt/weather instead of the weather_files store"
    )
    assert "ensured" not in RECOV, "the 'ensured' lie is still logged"


def test_null_batch_group_recovered():
    # D-51: never-distributed rows (batch NULL) must be renamed into the recovery.
    assert "IS NULL" in RECOV or "batch is None" in RECOV, (
        "NULL-batch assets silently dropped from recovery"
    )


def test_resubmission_protects_finished_like_the_upsert_claims():
    # R-027b: init reset + distribute must exclude Finished, matching the upsert's
    # 'HPC re-submission protection' comment instead of defeating it two lines later.
    # R-027b lives INSIDE bulk_update_status on both backends; assert the protect
    # clause in the CODE of each (the old caller-side anchor was satisfied by a
    # comment and could not fail -- code-review).
    for mod in ("postgres_operations", "sqlite_operations"):
        src = open(os.path.join(REPO, "solver", "app", "modules", "database", f"{mod}.py")).read()
        fn = src.split("def bulk_update_status", 1)[1].split("\ndef ", 1)[0]
        code_lines = "\n".join(l for l in fn.splitlines() if not l.strip().startswith("#"))
        assert "status != 'Finished'" in code_lines and "'Not Processed Yet'" in code_lines, (
            f"{mod}.bulk_update_status lost the R-027b Finished protection"
        )
    PG = open(os.path.join(REPO, "solver", "app", "modules", "database",
                           "postgres_operations.py")).read()
    dseg = PG.split("def distribute_assets_to_batches", 1)[1].split("\ndef ", 1)[0]
    assert "Finished" in dseg, "pg distribute re-batches Finished rows"
    SQ = open(os.path.join(REPO, "solver", "app", "modules", "database",
                           "sqlite_operations.py")).read()
    dseg2 = SQ.split("def distribute_assets_to_batches", 1)[1].split("\ndef ", 1)[0]
    assert "Finished" in dseg2, "sqlite distribute re-batches Finished rows"


def test_node_ready_markers_are_job_scoped():
    # R-023a: a prior job's markers must not satisfy the next job's barrier.
    assert "node_ready_" in WAIT, (
        "marker dir not job-scoped (needs e.g. node_ready_<job_id>); stale barrier no-op persists"
    )


def test_uo_process_failure_not_recorded_as_zero_time():
    # D-49: a failed post-process must not write 0 as a legitimate timing.
    assert "uo_process_time = None" in RUNUO, (
        "failed uo process recorded as timing 0"
    )


def test_weather_precopy_guard_covers_all_extensions():
    # R-022 addendum: guard keyed on .epw alone let .ddy/.stat be missing -- the exact
    # create_typical abort condition.
    assert "if not os.path.exists(WEATHER_EPW_FILE):" not in RUNUO, (
        "weather-present guard checks only .epw; .ddy/.stat can be silently absent"
    )


def test_pernode_is_gone():
    # D-52 (owner-approved WP-05 decision): the NOT-IN-USE pernode.py had drifted ~6 ways
    # from run_UOsim (lost failure_reason, lost TIMEOUT enrichment); zero importers. Deleted.
    assert not os.path.exists(os.path.join(REPO, "solver", "app", "modules", "simulation",
                                           "pernode.py")), "pernode.py still present and drifting"
