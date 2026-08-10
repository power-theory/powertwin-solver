#!/usr/bin/env python3
"""Guards reap_orphaned_sims, which frees the API concurrency slot held by a sim whose
solver process died. Both directions matter: an ungated reap shipped briefly and marked a
RUNNING sim failed (uo run live at 27% CPU), because it ran per gunicorn worker and a
respawned worker reaped its live sibling's sims.

    pytest tests/test_sim_reaper.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "solver", "app"))

from modules.utils.pack_results import proc_start, read_status, reap_orphaned_sims, write_status


def _sim(base, name, phase, pid=None, pid_start=None):
    d = os.path.join(base, name)
    os.makedirs(d, exist_ok=True)
    write_status(d, phase, simulation_name=name)
    if pid is not None:
        s = read_status(d)
        s['pid'], s['pid_start'] = pid, pid_start
        with open(os.path.join(d, 'status.json'), 'w') as f:
            import json
            json.dump(s, f)
    return d


def test_live_owner_is_never_reaped(tmp_path):
    """The false-kill regression: write_status stamps this process, which is alive."""
    base = str(tmp_path)
    d = _sim(base, 'sim_live', 'urbanopt_running')
    reap_orphaned_sims(base=base)
    assert read_status(d)['phase'] == 'urbanopt_running'


def test_dead_owner_is_reaped(tmp_path):
    base = str(tmp_path)
    d = _sim(base, 'sim_dead', 'urbanopt_running', pid=99999, pid_start='1')
    reap_orphaned_sims(base=base)
    s = read_status(d)
    assert s['phase'] == 'failed'
    assert s['simulation_name'] == 'sim_dead'


def test_recycled_pid_is_reaped(tmp_path):
    """Live pid but a different start time means a new process reused the number."""
    base = str(tmp_path)
    d = _sim(base, 'sim_recycled', 'urbanopt_running', pid=os.getpid(), pid_start='0')
    reap_orphaned_sims(base=base)
    assert read_status(d)['phase'] == 'failed'


def test_status_without_pid_is_reaped(tmp_path):
    """Predates the pid stamping, so it cannot belong to any live process."""
    base = str(tmp_path)
    d = os.path.join(base, 'sim_legacy')
    os.makedirs(d)
    with open(os.path.join(d, 'status.json'), 'w') as f:
        import json
        json.dump({'phase': 'urbanopt_running', 'simulation_name': 'sim_legacy'}, f)
    reap_orphaned_sims(base=base)
    assert read_status(d)['phase'] == 'failed'


def test_terminal_phases_are_left_alone(tmp_path):
    base = str(tmp_path)
    done = _sim(base, 'sim_done', 'completed', pid=99999, pid_start='1')
    failed = _sim(base, 'sim_failed', 'failed', pid=99999, pid_start='1')
    reap_orphaned_sims(base=base)
    assert read_status(done)['phase'] == 'completed'
    assert read_status(failed)['phase'] == 'failed'


def test_proc_start_identifies_processes(tmp_path):
    # a pid ABOVE pid_max can never be allocated; 99999 is an ordinary pid on a modern kernel
    # (pid_max is 4194304 here), so asserting it is dead would be flaky on a busy box
    dead = int(open("/proc/sys/kernel/pid_max").read().strip()) + 1
    assert proc_start(os.getpid()) is not None
    assert proc_start(dead) is None


def test_null_pid_start_is_still_reaped(tmp_path):
    """The hole between the two guards: proc_start returns None for a DEAD pid, and it also
    returned None at WRITE time if /proc was unreadable then (write_status swallows OSError).
    Comparing them bare made None == None true, so the run read as alive forever and kept
    holding the concurrency slot it was meant to free."""
    base = str(tmp_path)
    d = _sim(base, 'sim_nullstart', 'urbanopt_running', pid=99999, pid_start=None)
    reap_orphaned_sims(base=base)
    assert read_status(d)['phase'] == 'failed'


def test_reap_does_not_recreate_a_deleted_sim_dir(tmp_path):
    """delete_simulation may rmtree the dir between the glob and the write; recreating it would
    leave a terminal status.json nobody reads for a job the listener already finished."""
    import shutil

    base = str(tmp_path)
    d = _sim(base, 'sim_gone', 'urbanopt_running', pid=99999, pid_start='1')
    # simulate the delete landing after the glob by removing the dir but keeping the path
    shutil.rmtree(d)
    reap_orphaned_sims(base=base)
    assert not os.path.exists(d), "reaper recreated a deleted simulation directory"


def test_reaper_never_escapes_an_error(tmp_path, monkeypatch):
    """It runs at gunicorn worker boot inside create_app, so an escaping error crash-loops the
    whole app rather than just skipping one sim."""
    import modules.utils.pack_results as pr

    base = str(tmp_path)
    _sim(base, 'sim_boom', 'urbanopt_running', pid=99999, pid_start='1')

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(pr, 'atomic_write_json', boom)
    pr.reap_orphaned_sims(base=base)  # must not raise


def test_non_dict_status_json_does_not_escape(tmp_path):
    """read_status only rejects INVALID json, so a valid non-dict (a list) reached .get() and
    raised AttributeError -- outside the original try, so it escaped create_app and crash-looped
    every gunicorn worker at boot."""
    base = str(tmp_path)
    d = os.path.join(base, 'sim_listy')
    os.makedirs(d)
    with open(os.path.join(d, 'status.json'), 'w') as f:
        f.write('[1, 2, 3]')
    reap_orphaned_sims(base=base)  # must not raise
