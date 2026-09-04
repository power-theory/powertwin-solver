"""Tripwire for the CI gate itself (Q/A campaign WP-01 / register row C-1).

Until 2026-09-02 the only CI test job ran `python tests/test_resolvers.py` -- a file with no
__main__ whose assertions live in an autouse pytest fixture -- so the job imported the module,
executed zero checks, and could not fail. Every build shipped behind that gate.

These checks pin the repaired contract: the test stage must invoke pytest over tests/, and the
resolver suite must actually register its checks under pytest. They are deliberately structural
(string/AST level) so they run everywhere, including inside the very CI job they guard.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def test_ci_test_job_invokes_pytest():
    path = os.path.join(REPO, ".gitlab-ci.yml")
    if not os.path.exists(path):
        import pytest
        pytest.skip("no .gitlab-ci.yml here (staged container copy) -- tripwire runs on host/CI")
    ci = open(path).read()
    job = ci.split("test_resolvers:", 1)[1].split("\nrules:", 1)[0]
    assert re.search(r"-m pytest\b", job), (
        "the CI test job must run `python -m pytest ...`; a bare `python tests/<file>.py` "
        "executes zero checks (no __main__, fixture-based assertions) and cannot fail"
    )
    assert "pytest tests/" in job, "the CI job must collect the tests/ suite, not a single file"


def test_bare_python_invocation_stays_inert_and_documented():
    # The defect mechanism, pinned: running the resolver suite as a script must remain a no-op
    # (nobody should ever rely on it), and pytest must collect real tests from it.
    r = subprocess.run([sys.executable, os.path.join(HERE, "test_resolvers.py")],
                       capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0 and not r.stdout.strip(), (
        "bare-python invocation grew behavior; the CI contract assumes pytest is the only runner"
    )
    r2 = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q",
                         os.path.join(HERE, "test_resolvers.py")],
                        capture_output=True, text=True, cwd=REPO)
    n = len([l for l in r2.stdout.splitlines() if "::" in l])
    assert n >= 40, f"pytest collects {n} resolver tests; expected the full suite (>=40)"
