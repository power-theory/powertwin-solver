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
# Name: run_command(command, batch_index=None)
# Description: This function runs a command in the shell and returns the time it takes to execute the command.
############################################################################################################
def run_command(command, batch_index=None):
    start_time = time.time()
    
    # Use batch-specific logger if batch_index is provided
    if batch_index is not None:
        cmd_logger = initialize_logger("Run Command", external_log_dir, batch_index=batch_index)
    else:
        cmd_logger = logger
    
    try:
        # Execute command in shell with output capture
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        end_time = time.time()
        
        # Log successful execution with output
        cmd_logger.info(f"Command '{command}' executed successfully.")
        
        # Extract batch number from output if available
        output = result.stdout
        batch_prefix = ""
        if batch_index is not None:
            batch_prefix = f"Batch {batch_index}: "
        elif "powertwin_scenario_" in output:
            # Try to extract batch number from scenario name in output
            import re
            match = re.search(r'powertwin_scenario_(\d+)', output)
            if match:
                batch_num = match.group(1)
                batch_prefix = f"Batch {batch_num}: "
        
        cmd_logger.info(f"{batch_prefix}Output: {output}")
        # Return elapsed time in seconds
        return end_time - start_time
    except subprocess.CalledProcessError as e:
        end_time = time.time()
        # Log command failure with error message
        if batch_index is not None:
            err_logger = initialize_logger("Run Command", external_log_dir, batch_index=batch_index)
        else:
            err_logger = logger
        err_logger.error(f"Command '{command}' failed with error: {e.stderr}")
        raise e