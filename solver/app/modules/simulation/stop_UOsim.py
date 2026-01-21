import os
import subprocess
import time

from modules.utils import initialize_logger

# Setup logging with external log directory support (for HPC logging)
external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Stop UOSim', external_log_dir)

########################################################################################
# Name: get_processes
# Description: Function to get the processes
########################################################################################
def get_processes():
    # Get list of all active processes with command information
    logger.info('Getting processes')
    
    try:
        # Execute ps aux to get full process listing
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=5)
        lines = result.stdout.splitlines()
        processes = []
        
        # Parse process list, skipping header row
        for line in lines[1:]:
            logger.debug(f'Line: {line}')  
            # Split line by whitespace to extract fields
            columns = line.split()
            if len(columns) >= 11:  # Ensure enough columns exist
                pid = columns[1]
                cmd = ' '.join(columns[10:])
                if 'ps aux' not in cmd:  # Exclude the ps aux command itself
                    processes.append((pid, cmd))
                    logger.debug(f'PID: {pid}, CMD: {cmd}')
        return processes
    except Exception as e:
        logger.error(f'Error getting processes: {str(e)}')
        return []

########################################################################################
# Name: kill_processes
# Description: Function to kill the processes and verify termination
########################################################################################
def kill_processes():
    processes = get_processes()
    logger.info('Stopping simulation - killing processes')
    
    if not processes:
        logger.warning('No processes found to kill')
        return
    
    killed_pids = []
    failed_pids = []
    
    # Iterate through processes and kill (skip Flask app process)
    for pid, cmd in processes:
        # Skip Flask API server and ps command itself
        if 'app.py' not in cmd and 'ps aux' not in cmd:
            try:
                pid_int = int(pid)
                logger.info(f'Killing PID {pid} with command {cmd}')
                os.kill(pid_int, 9)
                killed_pids.append(pid_int)
            except ProcessLookupError:
                logger.warning(f'Process {pid} not found (may have already exited)')
            except Exception as e:
                logger.error(f'Error killing process {pid}: {str(e)}')
                failed_pids.append(pid)
    
    logger.info(f'Killed {len(killed_pids)} processes: {killed_pids}')
    if failed_pids:
        logger.warning(f'Failed to kill {len(failed_pids)} processes: {failed_pids}')
    
    return len(killed_pids) > 0

########################################################################################
# Name: stop_UOsimulation
# Description: Function to stop the simulation with verification
########################################################################################
def stop_UOsimulation():
    """Stop the simulation and verify processes are terminated"""
    logger.info('===== STARTING SIMULATION STOP =====')
    
    try:
        # First kill attempt
        killed_first = kill_processes()
        logger.info('First kill attempt completed')
        
        # Wait for processes to terminate
        time.sleep(2)
        
        # Check if processes still exist
        remaining = get_processes()
        logger.info(f'Remaining processes after first kill: {len(remaining)}')
        
        # If processes still running, attempt second kill
        if remaining:
            logger.warning('Some processes still running, attempting second kill')
            time.sleep(3)  # Slightly longer delay
            killed_second = kill_processes()
            logger.info('Second kill attempt completed')
            
            # Final check
            time.sleep(2)
            final_remaining = get_processes()
            logger.info(f'Remaining processes after second kill: {len(final_remaining)}')
            
            if final_remaining:
                logger.warning(f'Warning: {len(final_remaining)} processes still running after kill attempts')
                for pid, cmd in final_remaining:
                    logger.warning(f'  Still running: PID {pid} - {cmd}')
        
        logger.info('===== SIMULATION STOP COMPLETED =====')
        
    except Exception as e:
        logger.error(f'Exception during stop_UOsimulation: {str(e)}')
        import traceback
        logger.error(f'Traceback: {traceback.format_exc()}')
        raise

if __name__ == '__main__':
    stop_UOsimulation()