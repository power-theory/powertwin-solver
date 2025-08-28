import os
import logging
import sys

from rich.logging import RichHandler
from rich.console import Console

############################################################################################################
# Name: initialize_logger(logger_name, external_log_dir=None)
# Description: This function initializes the logger with the given name.
# Parameters:
#   - logger_name: Name of the logger
#   - external_log_dir: Optional external directory for logs (for HPC environments)
############################################################################################################
def initialize_logger(logger_name, external_log_dir=None):
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)

    # Check if handlers are already added to the logger
    if not logger.handlers:
        # Check for environment variable if external_log_dir not provided
        if external_log_dir is None:
            external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
        
        # Use external log directory if provided (HPC mode), otherwise use default
        if external_log_dir:
            log_dir = external_log_dir
        else:
            log_dir = os.path.join('logs')
        
        # Try to create log directory if it doesn't exist
        try:
            os.makedirs(log_dir, exist_ok=True)
        except (PermissionError, OSError) as e:
            # If we can't create the directory, log to stderr and continue
            print(f"Warning: Could not create log directory {log_dir}: {str(e)}", file=sys.stderr)
            # Fall back to a directory that should be writable
            log_dir = os.path.join('/tmp', 'powertwin_logs')
            try:
                os.makedirs(log_dir, exist_ok=True)
            except:
                # Last resort - just use current directory
                log_dir = '.'

        # Create handlers
        console_handler = RichHandler(console=Console(), show_time=True, show_level=False, show_path=False)
        
        # Create file paths
        dev_log_path = os.path.join(log_dir, 'dev_logs.txt')
        user_log_path = os.path.join(log_dir, 'user_logs.txt')
        
        try:
            # Try to create file handlers
            file_handler = logging.FileHandler(dev_log_path)
            file_handler_no_debug = logging.FileHandler(user_log_path)
            
            # Set log levels
            console_handler.setLevel(logging.INFO)
            file_handler.setLevel(logging.DEBUG)
            file_handler_no_debug.setLevel(logging.INFO)  # This will exclude DEBUG messages

            # Create formatters and add them to the handlers
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

            file_handler.setFormatter(formatter)
            file_handler_no_debug.setFormatter(formatter)

            # Add handlers to the logger
            logger.addHandler(console_handler)
            logger.addHandler(file_handler)
            logger.addHandler(file_handler_no_debug)
            
            # Log the paths being used
            logger.info(f"Logging to: {dev_log_path} and {user_log_path}")
            
        except (PermissionError, OSError) as e:
            # If we can't write to the log files, just use console logging
            print(f"Warning: Could not create log files in {log_dir}: {str(e)}", file=sys.stderr)
            logger.addHandler(console_handler)
            logger.warning(f"File logging disabled - could not write to {log_dir}")

    return logger