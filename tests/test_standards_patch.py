"""solver/patches/patch_openstudio_standards.py: every fix lands once in each gem copy, a re-run changes
nothing, and a gem whose code moved fails loudly instead of being skipped."""
import importlib.util
import os
import sys

import pytest

_P = os.path.join(os.path.dirname(__file__), '..', 'solver', 'patches', 'patch_openstudio_standards.py')
_spec = importlib.util.spec_from_file_location('patch_openstudio_standards', _P)
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)


def _gem(root, version='0.7.1', moved=False):
    lib = os.path.join(root, 'gems', f'openstudio-standards-{version}', 'lib', 'openstudio-standards')
    for rel, edits in P.PATCHES:
        path = os.path.join(lib, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        body = 'before\n' + ''.join(bad + 'middle\n' for bad, _ in edits) + 'after\n'
        if moved:
            body = body.replace('122.0', '121.0').replace('#{wknd_op_hrs_start}', '#{x}')
        open(path, 'w').write(body)
    return lib


def test_patches_once_and_is_idempotent(tmp_path, capsys):
    lib = _gem(str(tmp_path))
    P.main([str(tmp_path)])
    hvac = open(os.path.join(lib, P.PATCHES[1][0])).read()
    assert hvac.count('powertwin patch') == 2
    assert hvac.count("unless hot_water_loop.nil? || dsgn_temps['zn_htg_dsgn_sup_air_temp_c'] < hw_temp_c") == 2
    assert '#{wknd_op_hrs_start_time}' in open(os.path.join(lib, P.PATCHES[0][0])).read()
    snapshot = {rel: open(os.path.join(lib, rel)).read() for rel, _ in P.PATCHES}
    capsys.readouterr()
    P.main([str(tmp_path)])
    assert capsys.readouterr().out.count('already patched') == 2
    assert snapshot == {rel: open(os.path.join(lib, rel)).read() for rel, _ in P.PATCHES}


def test_cap_keeps_upstream_value_on_hot_loops():
    """The injected rule only fires when the loop cannot supply the 122 F air: boiler loops keep it."""
    _, fix = P.PATCHES[1][1][0]
    assert "= 122.0\n" in fix and fix.index('= 122.0') < fix.index('powertwin patch')
    assert "standard_design_sizing_temperatures['zn_htg_dsgn_sup_air_temp_c']" in fix


def test_moved_code_fails_loudly(tmp_path):
    _gem(str(tmp_path), moved=True)
    with pytest.raises(SystemExit):
        P.main([str(tmp_path)])


def test_no_gem_fails(tmp_path):
    with pytest.raises(SystemExit):
        P.main([str(tmp_path)])


def test_writes_are_atomic_and_keep_the_mode(tmp_path):
    lib = _gem(str(tmp_path))
    path = os.path.join(lib, P.PATCHES[1][0])
    os.chmod(path, 0o640)
    P.main([str(tmp_path)])
    assert oct(os.stat(path).st_mode & 0o777) == '0o640'
    assert not [f for f in os.listdir(os.path.dirname(path)) if f.endswith('.tmp')]


def test_parallel_patchers_on_one_gem_home_agree(tmp_path):
    """Cluster batches patch one shared gem home at once: every file ends patched exactly once."""
    import subprocess as sp
    lib = _gem(str(tmp_path))
    procs = [sp.Popen([sys.executable, _P, str(tmp_path)], stdout=sp.DEVNULL, stderr=sp.PIPE) for _ in range(8)]
    assert all(p.wait() == 0 for p in procs)
    hvac = open(os.path.join(lib, P.PATCHES[1][0])).read()
    assert hvac.count('powertwin patch') == 2


def test_cluster_batches_patch_the_jobs_gem_home(tmp_path, monkeypatch):
    """ensure_standards_patched (direct_runner batch and recovery start) patches GEM_HOME / BUNDLE_PATH itself,
    so a campaign launched from a job script that predates the patch is still covered."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'solver', 'app'))
    os.environ.setdefault('POWERTWIN_LOG_DIR', '/tmp')
    from modules.simulation import initialize_UOsim as U
    monkeypatch.delenv('BUNDLE_PATH', raising=False)
    monkeypatch.delenv('GEM_HOME', raising=False)
    assert U.ensure_standards_patched() is False
    lib = _gem(str(tmp_path / 'shared_gems_1'))
    monkeypatch.setenv('GEM_HOME', str(tmp_path / 'shared_gems_1'))
    monkeypatch.setenv('BUNDLE_PATH', str(tmp_path / 'shared_gems_1'))
    assert U.ensure_standards_patched() is True
    assert '#{wknd_op_hrs_start_time}' in open(os.path.join(lib, P.PATCHES[0][0])).read()
    _gem(str(tmp_path / 'moved'), moved=True)
    monkeypatch.setenv('GEM_HOME', str(tmp_path / 'moved'))
    monkeypatch.setenv('BUNDLE_PATH', str(tmp_path / 'moved'))
    with pytest.raises(RuntimeError, match='patch failed'):
        U.ensure_standards_patched()
