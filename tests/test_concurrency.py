#!/usr/bin/env python3
"""Concurrency regression guard for the per-request URBANOPT env override.

The async simulation endpoint returns 202 and runs the sim on a daemon thread,
so two sims can be in flight on one gunicorn worker and share the process's
os.environ. Before the fix, _run_asset_update_simulation mutated the global
URBANOPT_DYNAMIC_DEFAULTS / _STOCHASTIC_SAMPLING with no lock, so sim A's
resolver could observe sim B's flags -> non-deterministic resolved values
across runs (the class of race documented in the project root-cause notes).

views.py can't be imported standalone (flask/psycopg/app deps), so we extract
the actual _SIM_ENV_LOCK + _sim_env_override source and exec it -- this tests
the real fix code, not a reimplementation. If someone reverts to unguarded
os.environ mutation, the concurrency assertion below fails.

    python3 tests/test_sim_env_override.py
"""
import os
import re
import threading
import contextlib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIEWS = os.path.join(REPO, "solver", "app", "views.py")


def _load_override():
    src = open(VIEWS).read()
    lock_def = re.search(r"^_SIM_ENV_LOCK = threading\.Lock\(\)", src, re.M)
    cm = re.search(r"^@contextlib\.contextmanager\ndef _sim_env_override.*?(?=\n\n\n|\n#####)",
                   src, re.S | re.M)
    assert lock_def, "views.py must define _SIM_ENV_LOCK (concurrency guard removed?)"
    assert cm, "views.py must define _sim_env_override context manager"

    class _StubLogger:
        def info(self, *a, **k):
            pass

    ns = {"os": os, "threading": threading, "contextlib": contextlib, "logger": _StubLogger()}
    exec(lock_def.group(0) + "\n" + cm.group(0), ns)
    return ns["_sim_env_override"]


def test_override_serializes_under_concurrency():
    _sim_env_override = _load_override()
    os.environ.pop("URBANOPT_DYNAMIC_DEFAULTS", None)
    barrier = threading.Barrier(2)
    leaks = []

    def worker(val):
        barrier.wait()
        for _ in range(2000):
            with _sim_env_override(dynamic_defaults=val):
                if os.environ.get("URBANOPT_DYNAMIC_DEFAULTS") != val:
                    leaks.append((val, os.environ.get("URBANOPT_DYNAMIC_DEFAULTS")))
                    return

    for _ in range(10):
        leaks.clear()
        a = threading.Thread(target=worker, args=("true",))
        b = threading.Thread(target=worker, args=("false",))
        a.start(); b.start(); a.join(); b.join()
        assert not leaks, f"cross-thread env leak -> lock not serializing: {leaks[:3]}"


def test_override_restores_prior_value():
    _sim_env_override = _load_override()
    os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "preexisting"
    with _sim_env_override(dynamic_defaults="temp"):
        assert os.environ["URBANOPT_DYNAMIC_DEFAULTS"] == "temp"
    assert os.environ["URBANOPT_DYNAMIC_DEFAULTS"] == "preexisting"


def test_override_none_leaves_env_untouched():
    _sim_env_override = _load_override()
    os.environ.pop("URBANOPT_STOCHASTIC_SAMPLING", None)
    with _sim_env_override(dynamic_defaults=None, stochastic=None):
        assert "URBANOPT_STOCHASTIC_SAMPLING" not in os.environ


def test_keep_dirs_mapping():
    _sim_env_override = _load_override()
    os.environ.pop("POWERTWIN_KEEP_DIRS", None)
    # True -> '1' (clean_report reads == '1'); restored to absent on exit.
    with _sim_env_override(keep_dirs=True):
        assert os.environ.get("POWERTWIN_KEEP_DIRS") == "1"
    assert "POWERTWIN_KEEP_DIRS" not in os.environ
    # False -> '' (reads as off, not '1'); restored on exit.
    with _sim_env_override(keep_dirs=False):
        assert os.environ.get("POWERTWIN_KEEP_DIRS") != "1"
    assert "POWERTWIN_KEEP_DIRS" not in os.environ
    # None -> untouched.
    with _sim_env_override(keep_dirs=None):
        assert "POWERTWIN_KEEP_DIRS" not in os.environ


def test_override_restores_on_exception():
    _sim_env_override = _load_override()
    os.environ.pop("URBANOPT_DYNAMIC_DEFAULTS", None)
    try:
        with _sim_env_override(dynamic_defaults="temp"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert "URBANOPT_DYNAMIC_DEFAULTS" not in os.environ, "env not restored after exception"


def main():
    tests = [
        ("serializes under concurrency", test_override_serializes_under_concurrency),
        ("restores prior value",         test_override_restores_prior_value),
        ("none leaves env untouched",    test_override_none_leaves_env_untouched),
        ("keep_dirs mapping",            test_keep_dirs_mapping),
        ("restores on exception",        test_override_restores_on_exception),
    ]
    for name, fn in tests:
        fn()
        print(f"  OK  {name}")
    print(f"\n{len(tests)}/{len(tests)} concurrency guards passed")
    return 0


# Run via `pytest tests/test_concurrency.py`.
