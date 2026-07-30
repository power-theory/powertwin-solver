import time
import subprocess
import os

from modules.utils import initialize_logger

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger("Run Command", external_log_dir)

############################################################################################################
# Name: run_command(command)
# Description: This function runs a command in the shell and returns the time it takes to execute the command.
############################################################################################################
def run_command(command):
    start_time = time.time()
    try:
        # Pass per-sim thread-local flags explicitly (URBANOPT_* / POWERTWIN_KEEP_DIRS)
        # instead of relying on inherited process env, so concurrent sims each hand the
        # uo subprocess their own flags without a shared-os.environ race.
        from modules.utils.sim_flags import sim_env
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True, env=sim_env())
        end_time = time.time()
        logger.info(f"Command '{command}' executed successfully.")
        logger.info(f"Output: {result.stdout}")
        return end_time - start_time
    except subprocess.CalledProcessError as e:
        end_time = time.time()
        logger.error(f"Command '{command}' failed with error: {e.stderr}")
        raise e