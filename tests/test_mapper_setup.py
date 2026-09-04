"""deploy_mapper/deploy_measures must SYNC persisted project dirs, not pin the first copy:
project dirs survive between runs (cleanup keeps mappers/ and measures/), so skip-if-present
would silently run a stale mapper or vendored reporting measure after a re-vendor."""

import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

_spec = importlib.util.spec_from_file_location(
    "mapper_setup", os.path.join(REPO, "solver", "app", "modules", "simulation", "mapper_setup.py")
)
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)


def _upload(tmp_path, mapper_body="mapper v1", measure_body="measure v1"):
    up = tmp_path / "upload"
    (up / "reference_data").mkdir(parents=True)
    (up / "PowerTwin.rb").write_text(mapper_body)
    (up / "powertwin_refs.rb").write_text("refs")
    (up / "reference_data" / "table.json").write_text("{}")
    meas = up / "measures" / "default_feature_reports"
    meas.mkdir(parents=True)
    (meas / "measure.rb").write_text(measure_body)
    return up


def test_deploy_mapper_updates_stale_copy(tmp_path):
    up = _upload(tmp_path)
    mappers = tmp_path / "project" / "mappers"
    ms.deploy_mapper(str(up), str(mappers))
    assert (mappers / "PowerTwin.rb").read_text() == "mapper v1"

    (up / "PowerTwin.rb").write_text("mapper v2")
    (up / "reference_data" / "table.json").write_text('{"v": 2}')
    ms.deploy_mapper(str(up), str(mappers))
    assert (mappers / "PowerTwin.rb").read_text() == "mapper v2"
    assert (mappers / "reference_data" / "table.json").read_text() == '{"v": 2}'


def test_deploy_measures_updates_stale_vendored_measure(tmp_path):
    up = _upload(tmp_path)
    proj = tmp_path / "project"
    ms.deploy_measures(str(up), str(proj))
    dst = proj / "measures" / "default_feature_reports" / "measure.rb"
    assert dst.read_text() == "measure v1"

    (up / "measures" / "default_feature_reports" / "measure.rb").write_text("measure v2")
    ms.deploy_measures(str(up), str(proj))
    assert dst.read_text() == "measure v2"


def test_deploy_is_noop_when_identical(tmp_path):
    up = _upload(tmp_path)
    proj = tmp_path / "project"
    ms.deploy_measures(str(up), str(proj))
    dst = proj / "measures" / "default_feature_reports" / "measure.rb"
    before = dst.stat().st_mtime_ns
    ms.deploy_measures(str(up), str(proj))
    assert dst.stat().st_mtime_ns == before


def test_deploy_calls_are_not_gated_on_destination_absence():
    """The whole point of content-syncing is that it runs on a PERSISTED project dir. A test that
    only calls deploy_* directly stays green while every production call site sits inside an
    `if not os.path.exists(...)` guard, so pin the call sites themselves: no deploy_mapper /
    deploy_measures call may be nested under a `not os.path.exists(...)` test."""
    import ast

    sites = [
        "solver/app/modules/simulation/initialize_UOsim.py",
        "solver/app/modules/simulation/run_UOsim.py",
    ]
    for rel in sites:
        tree = ast.parse(open(os.path.join(REPO, rel)).read())
        guards = []  # (node, is_absence_guard)
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            src = ast.unparse(node.test)
            if "not os.path.exists" in src or ("not " in src and "os.path.isdir" in src):
                guards.append(node)
        for g in guards:
            for sub in ast.walk(g):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                        and sub.func.id in ("deploy_mapper", "deploy_measures"):
                    raise AssertionError(
                        f"{rel}:{sub.lineno} calls {sub.func.id} inside an absence guard "
                        f"(`if {ast.unparse(g.test)}`) -- a persisted project dir would never resync"
                    )


def test_sync_never_removes_the_destination_tree(tmp_path):
    """Per-file atomic replace: a reader must never observe a missing mapper tree mid-sync, and a
    running measure directory is never rmtree'd out from under a concurrent sim."""
    up = _upload(tmp_path)
    mappers = tmp_path / "project" / "mappers"
    ms.deploy_mapper(str(up), str(mappers))
    refs = mappers / "reference_data"
    inode_before = refs.stat().st_ino

    (up / "reference_data" / "table.json").write_text('{"v": 2}')
    ms.deploy_mapper(str(up), str(mappers))
    assert refs.stat().st_ino == inode_before, "reference_data dir was recreated, not updated in place"
    assert (refs / "table.json").read_text() == '{"v": 2}'
    # a file only present in the destination survives (orphans are inert, never a destructive wipe)
    (refs / "runtime_artifact.json").write_text("{}")
    (up / "PowerTwin.rb").write_text("mapper v3")
    ms.deploy_mapper(str(up), str(mappers))
    assert (refs / "runtime_artifact.json").exists()


def test_concurrent_syncs_leave_a_complete_tree(tmp_path):
    """Multiple workers (and multiple HPC nodes sharing one mappers dir) deploy at once. No sync
    may fail or leave a partial tree, and no temp file may survive."""
    import threading

    up = _upload(tmp_path)
    for i in range(30):
        (up / "reference_data" / f"t{i}.json").write_text('{"i": %d}' % i)
    mappers = tmp_path / "project" / "mappers"
    errors = []

    def worker():
        try:
            for _ in range(5):
                ms.deploy_mapper(str(up), str(mappers))
        except BaseException as e:  # noqa: BLE001 - the assertion is that nothing escapes
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    refs = mappers / "reference_data"
    for i in range(30):
        assert (refs / f"t{i}.json").read_text() == '{"i": %d}' % i
    assert (mappers / "PowerTwin.rb").read_text() == "mapper v1"
    assert not [p for p in refs.iterdir() if p.name.startswith(".sync-")], "temp files left behind"
