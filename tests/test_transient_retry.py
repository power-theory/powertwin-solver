"""run_UOsim.run_with_transient_retries: a building whose OpenStudio/EnergyPlus process crashed (heap corruption,
segfault, out of memory) is rerun from a clean run dir, up to three attempts; a model error, a stop request or a
clean failure is not retried (2026-10-08: one native abort lost a residential Teton building, rerun clean)."""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'solver', 'app'))
os.environ.setdefault('POWERTWIN_LOG_DIR', '/tmp')
from modules.simulation import run_UOsim as R  # noqa: E402

CRASH = ("Error running command: openstudio run -w in.osw\nstdout: malloc(): unaligned tcache chunk detected\n"
         "Aborted (core dumped)\nstderr:\nDONE, result = false\n")


def test_classifies_crashes_not_model_errors():
    assert R.transient_failure(CRASH) == 'malloc()'
    assert R.transient_failure('... Segmentation fault (core dumped)') == 'Segmentation fault'
    assert R.transient_failure('std::bad_alloc') == 'std::bad_alloc'
    assert R.transient_failure('**  Fatal  ** GetSurfaceData: errors found\n core dumped') is None   # model error
    assert R.transient_failure('EnergyPlus Terminated--Fatal Error Detected. 2 Warning; 1 Severe Errors') is None
    assert R.transient_failure('NoMethodError: undefined method') is None
    assert R.transient_failure('') is None and R.transient_failure(None) is None


class _Runs:
    """A fake `uo run`: crashes on the first `crash_times` calls, then writes the feature report."""
    def __init__(self, tmp_path, crash_times, crash_text=CRASH, raise_on_crash=False):
        self.dir = tmp_path / 'run' / 'powertwin_scenario_3' / '28045'
        self.report = self.dir / 'feature_reports' / 'default_feature_report.csv'
        self.calls, self.crash_times, self.text, self.raise_ = 0, crash_times, crash_text, raise_on_crash

    def __call__(self):
        self.calls += 1
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f'attempt_{self.calls}').write_text('partial run dir')
        if self.calls <= self.crash_times:
            if self.raise_:
                raise subprocess.CalledProcessError(1, 'uo run', '', self.text)
            return 10.0, self.text, ''
        self.report.parent.mkdir(parents=True, exist_ok=True)
        self.report.write_text('Datetime,Electricity:Facility(kWh)\n')
        return 42.0, 'DONE, result = true', ''


def test_a_crash_is_rerun_from_a_clean_run_dir(tmp_path):
    runs, notes = _Runs(tmp_path, crash_times=1), []
    t = R.run_with_transient_retries(runs, str(runs.report), str(runs.dir), on_retry=lambda a, s: notes.append((a, s)))
    assert t == 42.0 and runs.calls == 2 and runs.report.exists()
    assert notes == [(1, 'malloc()')]
    assert not (runs.dir / 'attempt_1').exists()        # the crashed attempt's files were removed before the rerun


def test_at_most_three_attempts(tmp_path):
    runs = _Runs(tmp_path, crash_times=5)
    R.run_with_transient_retries(runs, str(runs.report), str(runs.dir))
    assert runs.calls == R.TRANSIENT_ATTEMPTS == 3 and not runs.report.exists()   # the caller then fails the asset


def test_model_errors_and_clean_failures_are_not_retried(tmp_path):
    runs = _Runs(tmp_path, crash_times=5, crash_text='** Fatal ** Out of range\nEnergyPlus Terminated--Fatal Error')
    R.run_with_transient_retries(runs, str(runs.report), str(runs.dir))
    assert runs.calls == 1
    quiet = _Runs(tmp_path / 'q', crash_times=5, crash_text='DONE, result = false')
    R.run_with_transient_retries(quiet, str(quiet.report), str(quiet.dir))
    assert quiet.calls == 1


def test_a_stop_request_is_not_retried(tmp_path):
    runs = _Runs(tmp_path, crash_times=5)
    R.run_with_transient_retries(runs, str(runs.report), str(runs.dir), stopped=lambda: True)
    assert runs.calls == 1


def test_a_nonzero_exit_with_a_crash_is_retried_and_a_model_error_raises(tmp_path):
    runs = _Runs(tmp_path, crash_times=1, raise_on_crash=True)
    assert R.run_with_transient_retries(runs, str(runs.report), str(runs.dir)) == 42.0 and runs.calls == 2
    bad = _Runs(tmp_path / 'b', crash_times=5, crash_text='** Fatal ** bad geometry', raise_on_crash=True)
    with pytest.raises(subprocess.CalledProcessError):
        R.run_with_transient_retries(bad, str(bad.report), str(bad.dir))
    assert bad.calls == 1


def test_run_command_capture_returns_output():
    from modules.utils.command_timer import run_command
    t, out, err = run_command('echo hello; echo oops 1>&2', capture=True)
    assert out.strip() == 'hello' and err.strip() == 'oops' and t >= 0
    assert isinstance(run_command('true'), float)
