import os
import subprocess
import time

from scripts.helper import initialize_logger

logger = initialize_logger('Stop UOSim')

########################################################################################
# Name: get_processes
# Description: Function to get the processes
########################################################################################
def get_processes():
    logger.info('Getting processes')
    result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
    lines = result.stdout.splitlines()
    processes = []
    for line in lines[1:]:
        logger.debug(f'Line: {line}')  
        columns = line.split()
        pid = columns[1]
        cmd = ' '.join(columns[10:])
        if 'ps aux' not in cmd:  # Exclude the ps aux command itself
            processes.append((pid, cmd))
            logger.debug(f'PID: {pid}, CMD: {cmd}')
    return processes

########################################################################################
# Name: kill_processes
# Description: Function to kill the processes
########################################################################################
def kill_processes():
    processes = get_processes()
    logger.info('Stopping simulation')
    for pid, cmd in processes:
        if 'app.py' not in cmd and 'ps aux' not in cmd:
            print(f'Killing PID {pid} with command {cmd}')
            os.kill(int(pid), 9)

########################################################################################
# Name: stop_UOsimulation
# Description: Function to stop the simulation
########################################################################################
def stop_UOsimulation():
    #TODO: temporary solution to stop the simulation
    kill_processes()
    time.sleep(5)  # Wait for 5 seconds
    kill_processes()  # Re-call the process killing function

if __name__ == '__main__':
    stop_UOsimulation()