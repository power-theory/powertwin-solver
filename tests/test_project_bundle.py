"""prepare_project_bundle (review 2026-09-28 and 2026-09-29, both reproduced on the local image):
- a POST /api/simulation/stop during the pre-batch bundle install kills `ruby -e` (its command line carries the
  stop sweep's markers): the run must end 'stopped', not 'failed' (which the db listener requeues);
- a real failure reports Ruby's error, not the program text; a hung install is bounded and killed as a group;
- the extension Runner swallows a failed `bundle update` (corrupt or timed-out gem fetch) and exits 0 with a
  partial bundle, after which every building failed on a misleading Gem::ConflictError: the bundle is checked,
  reinstalled once from a clean state (the Runner keys the install on .bundle/config and Gemfile.lock, not on
  the gems), then fails loud with the fetch errors; gems come over https."""
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))
os.environ.setdefault("POWERTWIN_LOG_DIR", "/tmp")

from modules.simulation import initialize_UOsim as U  # noqa: E402
from modules.simulation.stop_UOsim import request_stop  # noqa: E402


class FakeProc:
    def __init__(self, rc=0, out="", err="", hang=False):
        self.returncode, self._out, self._err, self._hang, self.pid = rc, out, err, hang, 424242
        self.calls = 0

    def communicate(self, timeout=None):
        self.calls += 1
        if self._hang and self.calls == 1:
            raise subprocess.TimeoutExpired("ruby", timeout)
        return self._out, self._err


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "is_hpc_environment", lambda: False)
    sim_dir = tmp_path / "data" / "run1"
    p = sim_dir / "urbanopt_simulation"
    (p / ".bundle" / "install").mkdir(parents=True)
    (p / ".bundle" / "config").write_text('BUNDLE_PATH: "x"\n')
    (p / "Gemfile.lock").write_text("GEM\n")
    (p / "Gemfile").write_text("source 'http://rubygems.org'\nruby '3.2.2'\n")
    return sim_dir, p


def fake(monkeypatch, procs, checks, online=True):
    """procs: FakeProc per ruby step; checks: `bundle check` return codes in order. online: the offline bundle
    step (2026-09-29) reports a failure, so these tests exercise the online Runner path they were written for."""
    procs, checks, seen = list(procs), list(checks), []
    if online:
        monkeypatch.setattr(U, '_offline_project_bundle', lambda d: (False, 'test: online path'))

    def run(cmd, **kw):
        seen.append(cmd[0:2])
        if cmd[:2] == ["bundle", "check"]:
            return SimpleNamespace(returncode=checks.pop(0), stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="patched", stderr="")   # the standards patch
    monkeypatch.setattr(U.subprocess, "Popen", lambda *a, **k: procs.pop(0))
    monkeypatch.setattr(U.subprocess, "run", run)
    return seen


def ok_proc(p):
    return FakeProc(rc=0, out=f"Using runner options\n{p}/.bundle/install\n")


def test_stop_during_install_is_a_stop_not_a_failure(proj, monkeypatch):
    sim, p = proj
    fake(monkeypatch, [FakeProc(rc=-9, err="Killed")], [])
    request_stop(str(sim.parent))                       # what POST /api/simulation/stop writes
    assert U.prepare_project_bundle(str(p), str(sim)) is False


def test_real_failure_reports_rubys_error(proj, monkeypatch):
    sim, p = proj
    fake(monkeypatch, [FakeProc(rc=1, err="line 1\n/root/runner.conf: unexpected token (JSON::ParserError)")], [])
    with pytest.raises(RuntimeError) as e:
        U.prepare_project_bundle(str(p), str(sim))
    assert "JSON::ParserError" in str(e.value) and "require 'bundler'" not in str(e.value)


def test_hung_install_is_bounded_and_killed(proj, monkeypatch):
    sim, p = proj
    killed = []
    fake(monkeypatch, [FakeProc(hang=True)], [])
    monkeypatch.setattr(U.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setenv("URBANOPT_RUN_TIMEOUT_S", "5")
    with pytest.raises(RuntimeError, match="exceeded 5 s"):
        U.prepare_project_bundle(str(p), str(sim))
    assert killed == [(424242, U.signal.SIGKILL)]


def test_gems_come_over_https(proj, monkeypatch):
    sim, p = proj
    fake(monkeypatch, [ok_proc(p)], [0])
    assert U.prepare_project_bundle(str(p), str(sim)) is True
    assert (p / "Gemfile").read_text().startswith("source 'https://rubygems.org'")


def test_partial_bundle_is_reinstalled_from_a_clean_state(proj, monkeypatch):
    sim, p = proj
    bad = FakeProc(rc=0, out=f"Error running command: 'bundle update'\nZlib::DataError: invalid distance code\n{p}/.bundle/install\n")
    seen = fake(monkeypatch, [bad, ok_proc(p)], [1, 0])
    assert U.prepare_project_bundle(str(p), str(sim)) is True
    assert seen.count(["bundle", "check"]) == 2
    # the reset the Runner needs to actually install again (removed before the second install recreated nothing here)
    assert not (p / ".bundle").exists() and not (p / "Gemfile.lock").exists()


def test_partial_bundle_twice_fails_loud_with_the_fetch_errors(proj, monkeypatch):
    sim, p = proj
    bad = lambda: FakeProc(rc=0, out=f"Error running command: 'bundle update'\nstderr: Gem::RemoteFetcher::FetchError Net::OpenTimeout\n{p}/.bundle/install\n")
    fake(monkeypatch, [bad(), bad()], [1, 1])
    with pytest.raises(RuntimeError) as e:
        U.prepare_project_bundle(str(p), str(sim))
    assert "incomplete after two installs" in str(e.value) and "FetchError" in str(e.value)


def test_empty_runner_output_is_a_named_failure(proj, monkeypatch):
    sim, p = proj
    fake(monkeypatch, [FakeProc(rc=0, out="", err="nothing printed")], [])
    with pytest.raises(RuntimeError, match="printed no bundle path"):
        U.prepare_project_bundle(str(p), str(sim))


def test_a_stopped_install_marks_the_run_stopped_early(tmp_path, monkeypatch):
    """prepare_record records the stop itself: a run with no assets left to skip would otherwise answer 200."""
    src = open(U.__file__).read()
    body = src.split("def prepare_record(", 1)[1].split("\ndef ", 1)[0]
    assert "if not prepare_project_bundle(UO_SIMULATION_DIR, SIMULATION_DIR):" in body and "mark_stopped_early(SIMULATION_DIR)" in body


def _offline_fake(monkeypatch, fail_on=None, timeout_on=None):
    """Record the offline steps; `fail_on` is the bundle subcommand ('lock', 'config', 'install') that fails and
    `timeout_on` the one that times out."""
    seen = []

    def run(cmd, cwd):
        seen.append(cmd)
        if timeout_on and cmd[:2] == ['bundle', timeout_on]:
            return None, '', ''
        rc = 1 if fail_on and cmd[:2] == ['bundle', fail_on] else 0
        return rc, '', 'Could not find gem' if rc else ''
    monkeypatch.setattr(U, '_run_group', run)
    return seen


def test_offline_bundle_resolves_before_setting_the_path_and_installs_local(tmp_path, monkeypatch):
    """Requested from Al's benchmark session 2026-09-29: no rubygems.org. With a project path configured `--local`
    sees only that path, so the lock resolves first; the image's cached .gem files serve the install and the
    linked app cache is removed afterwards."""
    gems = tmp_path / 'gemhome' / 'cache'
    gems.mkdir(parents=True)
    (gems / 'parallel-1.19.1.gem').write_text('x')
    monkeypatch.setenv('GEM_HOME', str(tmp_path / 'gemhome'))
    monkeypatch.delenv('GEM_PATH', raising=False)
    proj = tmp_path / 'proj'
    proj.mkdir()
    seen = _offline_fake(monkeypatch)
    ok, path = U._offline_project_bundle(str(proj))
    assert ok and path == str(proj / '.bundle' / 'install')
    assert [c[:2] for c in seen] == [['bundle', 'lock'], ['bundle', 'config'], ['bundle', 'install']]
    assert '--local' in seen[0] and '--local' in seen[2] and all('update' not in c for c in seen)
    assert not (proj / 'vendor').exists()


@pytest.mark.parametrize('step', ['lock', 'install'])
def test_offline_failure_clears_its_state_and_reports(tmp_path, monkeypatch, step):
    proj = tmp_path / 'proj'
    (proj / '.bundle').mkdir(parents=True)
    (proj / 'Gemfile.lock').write_text('GEM\n')
    _offline_fake(monkeypatch, fail_on=step)
    ok, why = U._offline_project_bundle(str(proj))
    assert not ok and step in why and 'Could not find gem' in why
    assert not (proj / '.bundle').exists() and not (proj / 'Gemfile.lock').exists() and not (proj / 'vendor').exists()


def test_a_custom_bundle_path_is_left_to_the_runner(tmp_path, monkeypatch):
    proj = tmp_path / 'proj'
    proj.mkdir()
    (proj / 'runner.conf').write_text('{"bundle_install_path": "/elsewhere"}')
    seen = _offline_fake(monkeypatch)
    ok, why = U._offline_project_bundle(str(proj))
    assert not ok and '/elsewhere' in why and seen == []


def test_prepare_falls_back_online_when_offline_fails(proj, monkeypatch):
    sim, p = proj
    monkeypatch.setattr(U, '_offline_project_bundle', lambda d: (False, '`bundle lock` no cache'))
    fake(monkeypatch, [ok_proc(p)], [0])
    assert U.prepare_project_bundle(str(p), str(sim)) is True     # the online Runner path ran and passed its check


def test_prepare_uses_the_offline_bundle_when_it_builds(proj, monkeypatch):
    sim, p = proj
    calls = []
    monkeypatch.setattr(U, '_offline_project_bundle', lambda d: (calls.append(d), (True, f'{d}/.bundle/install'))[1])
    fake(monkeypatch, [ok_proc(p)], [0], online=False)
    assert U.prepare_project_bundle(str(p), str(sim)) is True and calls == [str(p)]


def test_offline_bundle_path_is_absolute_for_a_relative_project(tmp_path, monkeypatch):
    """The solver passes data/<sim>/urbanopt_simulation relative to /solver: a relative BUNDLE_PATH put the gems in
    a nested tree and every building failed on Gem::ConflictError (reproduced 2026-09-29)."""
    (tmp_path / 'data' / 'run' / 'urbanopt_simulation').mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    seen = _offline_fake(monkeypatch)
    ok, path = U._offline_project_bundle('data/run/urbanopt_simulation')
    assert ok and os.path.isabs(path) and path == str(tmp_path / 'data/run/urbanopt_simulation/.bundle/install')
    assert seen[1][-1] == path


def test_offline_timeout_or_any_error_cleans_up_and_falls_back(tmp_path, monkeypatch):
    """Review 2026-09-30: only a non-zero exit was handled; a timeout must kill the group (in _run_group) and any other
    error (a bad link, a full disk) must clear the partial state and report, so the online install runs."""
    proj = tmp_path / 'proj'
    (proj / '.bundle').mkdir(parents=True)
    _offline_fake(monkeypatch, timeout_on='install')
    ok, why = U._offline_project_bundle(str(proj))
    assert not ok and 'timed out' in why and not (proj / '.bundle').exists() and not (proj / 'vendor').exists()
    _offline_fake(monkeypatch)
    monkeypatch.setattr(U.os, 'symlink', lambda *a: (_ for _ in ()).throw(OSError(28, 'No space left on device')))
    gems = tmp_path / 'gh' / 'cache'
    gems.mkdir(parents=True)
    (gems / 'x-1.gem').write_text('x')
    monkeypatch.setenv('GEM_HOME', str(tmp_path / 'gh'))
    ok, why = U._offline_project_bundle(str(proj))
    assert not ok and 'OSError' in why and not (proj / 'vendor').exists()


def test_a_non_object_runner_conf_is_not_a_crash(tmp_path, monkeypatch):
    proj = tmp_path / 'proj'
    proj.mkdir()
    (proj / 'runner.conf').write_text('[1, 2]')
    _offline_fake(monkeypatch)
    assert U._offline_project_bundle(str(proj))[0]
