"""Pytest wrapper so the Ruby golden runs under the one `pytest tests/` command.

test_powertwin_refs.rb is the PowerTwinRefs characterization golden (pure Ruby, runs
with plain `ruby`). pytest can't collect a .rb directly, so this thin wrapper subprocesses
it and asserts it passes -- keeping a single runner for the whole suite.
"""
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
RUBY = shutil.which("ruby")


@pytest.mark.skipif(RUBY is None, reason="ruby not installed")
def test_powertwin_refs_golden():
    rb = os.path.join(HERE, "test_powertwin_refs.rb")
    r = subprocess.run([RUBY, rb], capture_output=True, text=True)
    assert r.returncode == 0, f"PowerTwinRefs ruby golden failed:\n{r.stdout}\n{r.stderr}"
