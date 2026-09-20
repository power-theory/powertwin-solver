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
    #TODO: temporary solution to stop the simulation
    killed = kill_processes()
    time.sleep(5)  # Wait for 5 seconds
    killed += kill_processes()  # Re-call the process killing function
    return killed

if __name__ == '__main__':
    stop_UOsimulation()
