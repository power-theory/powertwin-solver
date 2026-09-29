import os
import subprocess
import time

from modules.utils import initialize_logger

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Stop UOSim', external_log_dir)

# Only the simulation toolchain is a kill target. The previous rule killed every process
# whose command lacked 'app.py'; the server is run.py (dev) or gunicorn (prod), so a stop
# SIGKILLed the worker and the container exited (proven 2026-09-20). A marker matches
# anywhere in the command line: 'uo run', the ruby gem shims (bin/uo, bundle), openstudio
# and energyplus binaries.
SIM_COMMAND_MARKERS = ('uo run', '/bin/uo', 'bundle', 'openstudio', 'energyplus')

# Killing the toolchain alone does not stop a campaign: run_batch marks the killed asset Failed
# and spawns the next one (review 2026-09-20). A stop therefore also drops a marker into every
# simulation working directory; the batch loop checks it before each asset. Per-simulation so
# nothing needs clearing (a new run gets a new directory), and visible to the loky workers.
STOP_MARKER = '.stop'
DATA_DIR = os.path.join('data')


def stop_marker_path(simulation_dir):
    return os.path.join(simulation_dir, STOP_MARKER)


def stop_requested(simulation_dir) -> bool:
    return os.path.exists(stop_marker_path(simulation_dir))


# A run cut short by a stop records it here: the batch loop skipped assets or an asset was killed mid-run. A stop
# marks every simulation directory, so the .stop marker alone also sat in a run whose assets had all finished,
# and its caller got 409 for a complete run (review 2026-09-28). Callers decide 'stopped' from this marker.
STOPPED_EARLY_MARKER = '.stopped_early'


def mark_stopped_early(simulation_dir):
    try:
        with open(os.path.join(simulation_dir, STOPPED_EARLY_MARKER), 'w') as fh:
            fh.write(str(time.time()))
    except OSError as e:
        logger.warning(f'Could not write stopped-early marker in {simulation_dir}: {e}')


def stopped_early(simulation_dir) -> bool:
    return os.path.exists(os.path.join(simulation_dir, STOPPED_EARLY_MARKER))


def request_stop(data_dir=DATA_DIR):
    """Write the marker into every simulation directory under data_dir; returns the paths."""
    marked = []
    if not os.path.isdir(data_dir):
        return marked
    for name in os.listdir(data_dir):
        sim_dir = os.path.join(data_dir, name)
        if not os.path.isdir(sim_dir):
            continue
        try:
            with open(stop_marker_path(sim_dir), 'w') as fh:
                fh.write(str(time.time()))
            marked.append(sim_dir)
        except OSError as e:
            logger.warning(f'Could not write stop marker in {sim_dir}: {e}')
    return marked

########################################################################################
# Name: get_processes
# Description: Function to get the processes
########################################################################################
def get_processes():
    logger.info('Getting processes')
    result = subprocess.run(['ps', '-eo', 'pid,args'], capture_output=True, text=True)
    processes = []
    for line in result.stdout.splitlines()[1:]:
        columns = line.split(None, 1)
        if len(columns) < 2 or not columns[0].isdigit():
            continue
        pid, cmd = int(columns[0]), columns[1]
        if 'ps -eo' in cmd:  # Exclude the ps command itself
            continue
        processes.append((pid, cmd))
        logger.debug(f'PID: {pid}, CMD: {cmd}')
    return processes

########################################################################################
# Name: is_simulation_process
# Description: True when the command line belongs to the urbanopt/openstudio toolchain
########################################################################################
def is_simulation_process(cmd):
    lowered = cmd.lower()
    return any(marker in lowered for marker in SIM_COMMAND_MARKERS)

########################################################################################
# Name: kill_processes
# Description: SIGKILL every simulation process; returns the pids killed
########################################################################################
def kill_processes():
    logger.info('Stopping simulation')
    me = os.getpid()
    killed = []
    for pid, cmd in get_processes():
        if pid in (1, me) or not is_simulation_process(cmd):
            continue
        try:
            os.kill(pid, 9)
            killed.append(pid)
            logger.info(f'Killed PID {pid}: {cmd[:120]}')
        except ProcessLookupError:
            pass
    return killed

########################################################################################
# Name: stop_UOsimulation
# Description: Function to stop the simulation
########################################################################################
def stop_UOsimulation():
    """Mark every simulation as stopped, then SIGKILL the toolchain twice five seconds apart
    (the second sweep catches a process that was mid-spawn). Returns {'killed': pids,
    'stopped': simulation dirs}."""
    stopped = request_stop()
    killed = kill_processes()
    time.sleep(5)  # Wait for 5 seconds
    killed += kill_processes()  # Re-call the process killing function
    # a killed-but-unreaped pid is still listed on the second sweep; report each once
    return {'killed': sorted(set(killed)), 'stopped': stopped}

if __name__ == '__main__':
    stop_UOsimulation()
