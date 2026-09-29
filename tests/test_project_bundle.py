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


def fake(monkeypatch, procs, checks):
    """procs: FakeProc per ruby step; checks: `bundle check` return codes in order."""
    procs, checks, seen = list(procs), list(checks), []

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
