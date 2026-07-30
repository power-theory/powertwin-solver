#!/usr/bin/env python3
"""Concurrency guard for the per-request URBANOPT flag overrides.

The async simulation endpoint returns 202 and runs each sim on its own daemon
thread, so multiple sims are in flight on one gunicorn worker. Two properties
must hold:

  1. Isolation without serialization: a sim's per-request flags
     (URBANOPT_DYNAMIC_DEFAULTS / _STOCHASTIC_SAMPLING / _REPORTING_FREQUENCY /
     POWERTWIN_KEEP_DIRS) must NOT leak into a concurrent sim. The old design
     enforced this by serializing every sim under _SIM_ENV_LOCK (the flags lived
     in the shared os.environ); the flags are now thread-local
     (modules.utils.sim_flags), so sims run in PARALLEL with isolated flags --
     no lock, no serialization.
  2. No global mutation: a sim thread must not write the flags into the process
     os.environ (that shared write was the original race). Unset flags fall back
     to os.environ so a deployment-wide default still applies.

Run via `pytest tests/test_concurrency.py` or `python3 tests/test_concurrency.py`.
"""
import os
import sys
import threading

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))

from modules.utils.sim_flags import (  # noqa: E402
    set_sim_flags, get_sim_flag, clear_sim_flags, sim_env,
)


def test_flags_isolated_across_concurrent_threads():
    """Two threads set different flags and stay live at once; each must observe
    only its own value, and sim_env() (the uo subprocess env) must carry it."""
    os.environ.pop("URBANOPT_DYNAMIC_DEFAULTS", None)
    barrier = threading.Barrier(2)
    leaks = []

    def worker(val):
        set_sim_flags(dynamic_defaults=val)
        barrier.wait()  # maximize overlap so a shared-state bug would surface
        try:
            for _ in range(5000):
                if get_sim_flag("URBANOPT_DYNAMIC_DEFAULTS") != val:
                    leaks.append((val, "flag", get_sim_flag("URBANOPT_DYNAMIC_DEFAULTS")))
                    return
                if sim_env().get("URBANOPT_DYNAMIC_DEFAULTS") != val:
                    leaks.append((val, "env", sim_env().get("URBANOPT_DYNAMIC_DEFAULTS")))
                    return
        finally:
            clear_sim_flags()

    for _ in range(10):
        leaks.clear()
        a = threading.Thread(target=worker, args=("true",))
        b = threading.Thread(target=worker, args=("false",))
        a.start(); b.start(); a.join(); b.join()
        assert not leaks, f"per-request flag leaked across concurrent sims: {leaks[:3]}"


def test_no_global_env_mutation():
    """A sim thread's flags must never touch the shared process os.environ."""
    os.environ.pop("URBANOPT_DYNAMIC_DEFAULTS", None)

    def worker():
        set_sim_flags(dynamic_defaults="true")
        assert get_sim_flag("URBANOPT_DYNAMIC_DEFAULTS") == "true"
        clear_sim_flags()

    t = threading.Thread(target=worker)
    t.start(); t.join()
    assert os.environ.get("URBANOPT_DYNAMIC_DEFAULTS") is None, \
        "per-request flag leaked into the global os.environ"


def test_unset_falls_back_to_environ():
    """With no thread-local override, readers fall back to os.environ so a
    deployment-wide flag still applies (unchanged behavior for the common path)."""
    os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "true"
    try:
        assert get_sim_flag("URBANOPT_DYNAMIC_DEFAULTS") is None
        assert sim_env().get("URBANOPT_DYNAMIC_DEFAULTS") == "true"
    finally:
        os.environ.pop("URBANOPT_DYNAMIC_DEFAULTS", None)


def test_keep_dirs_mapping():
    """keep_dirs True -> '1' (clean_report reads == '1'), False -> '' (off),
    None -> no override (falls back to env)."""
    os.environ.pop("POWERTWIN_KEEP_DIRS", None)
    set_sim_flags(keep_dirs=True)
    assert get_sim_flag("POWERTWIN_KEEP_DIRS") == "1"
    assert sim_env().get("POWERTWIN_KEEP_DIRS") == "1"
    set_sim_flags(keep_dirs=False)
    assert get_sim_flag("POWERTWIN_KEEP_DIRS") != "1"
    set_sim_flags(keep_dirs=None)
    assert get_sim_flag("POWERTWIN_KEEP_DIRS") is None
    clear_sim_flags()


def main():
    tests = [
        ("flags isolated across concurrent threads", test_flags_isolated_across_concurrent_threads),
        ("no global env mutation",                   test_no_global_env_mutation),
        ("unset falls back to environ",              test_unset_falls_back_to_environ),
        ("keep_dirs mapping",                        test_keep_dirs_mapping),
    ]
    for name, fn in tests:
        fn()
        print(f"  OK  {name}")
    print(f"\n{len(tests)}/{len(tests)} concurrency guards passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
