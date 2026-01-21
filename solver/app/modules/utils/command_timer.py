# ======================================================================================
# Command Execution Timer Module
# Provides utilities for running shell commands and measuring execution time
# ======================================================================================

import time
import subprocess
import os

from modules.utils import initialize_logger

# Initialize logger for this module
external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger("Run Command", external_log_dir)

############################################################################################################
# Name: run_command(command)
# Description: This function runs a command in the shell and returns the time it takes to execute the command.
############################################################################################################
def run_command(command):
    start_time = time.time()
    try:
        # Execute command in shell with output capture
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        end_time = time.time()
        
        # Log successful execution with output
        logger.info(f"Command '{command}' executed successfully.")
        logger.info(f"Output: {result.stdout}")
        # Return elapsed time in seconds
        return end_time - start_time
    except subprocess.CalledProcessError as e:
        end_time = time.time()
        # Log command failure with error message
        logger.error(f"Command '{command}' failed with error: {e.stderr}")
        raise e