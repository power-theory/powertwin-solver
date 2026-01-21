import os
import logging
import sys
import json
from logging.handlers import RotatingFileHandler
from datetime import datetime

from rich.logging import RichHandler
from rich.console import Console

############################################################################################################
# Name: initialize_logger(logger_name, external_log_dir=None)
# Description: Modern logging system with rotation, JSON output, and structured logging support.
# Features:
#   - Rotating file handlers (10MB per file, 10 backup files)
#   - Separate debug and user log streams
#   - JSON structured logging for better parsing
#   - Rich console output
#   - High-performance file I/O with buffering
# Parameters:
#   - logger_name: Name of the logger
#   - external_log_dir: Optional external directory for logs (for HPC environments)
############################################################################################################

class JSONFormatter(logging.Formatter):
    """Custom formatter that outputs structured JSON logs."""
    
    def format(self, record):
        log_data = {
            'timestamp': datetime.fromtimestamp(record.created).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'line': record.lineno
        }
        
        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        return json.dumps(log_data)


def initialize_logger(logger_name, external_log_dir=None):
    """Initialize a modern, high-performance logger."""
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

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
        # Check for SLURM environment (HPC detection)
        slurm_job_id = os.environ.get('SLURM_JOB_ID')
        
        if slurm_job_id:
            # HPC environment detected - prefix dev.log with SLURM job ID
            dev_log_filename = f"{slurm_job_id}_dev.log"
            user_log_filename = f"{slurm_job_id}_user.log"
            error_log_filename = f"{slurm_job_id}_error.log"
        else:
            # Standard environment
            dev_log_filename = "dev.log"
            user_log_filename = "user.log"
            error_log_filename = "error.log"
        
        dev_log_path = os.path.join(log_dir, dev_log_filename)
        user_log_path = os.path.join(log_dir, user_log_filename)
        error_log_path = os.path.join(log_dir, error_log_filename)
        
        try:
            # Try to create file handlers
            file_handler = logging.FileHandler(dev_log_path)
            file_handler_no_debug = logging.FileHandler(user_log_path)
            error_handler = logging.FileHandler(error_log_path)
            
            # Set log levels
            console_handler.setLevel(logging.INFO)
            file_handler.setLevel(logging.DEBUG)
            file_handler_no_debug.setLevel(logging.INFO)  # This will exclude DEBUG messages
            error_handler.setLevel(logging.ERROR)  # Only ERROR and CRITICAL messages

            # USER-FACING log file handler with rotation (INFO and above only)
            user_log_path = os.path.join(log_dir, 'user_logs.txt')
            file_handler_no_debug = RotatingFileHandler(
                user_log_path,
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=10,
                encoding='utf-8'
            )
            file_handler_no_debug.setLevel(logging.INFO)
            file_handler_no_debug.setFormatter(file_formatter)

<<<<<<< HEAD
            # JSON structured log file handler for machine parsing
            json_log_path = os.path.join(log_dir, 'structured_logs.jsonl')
            json_handler = RotatingFileHandler(
                json_log_path,
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=10,
                encoding='utf-8'
            )
            json_handler.setLevel(logging.DEBUG)
            json_handler.setFormatter(JSONFormatter())
=======
            file_handler.setFormatter(formatter)
            file_handler_no_debug.setFormatter(formatter)
            error_handler.setFormatter(formatter)
>>>>>>> 9c17126313aa70ee83965538680babdb325ba35d

            # Add all handlers to logger
            logger.addHandler(console_handler)
            logger.addHandler(file_handler)
            logger.addHandler(file_handler_no_debug)
<<<<<<< HEAD
            logger.addHandler(json_handler)
=======
            logger.addHandler(error_handler)
>>>>>>> 9c17126313aa70ee83965538680babdb325ba35d
            
        except (PermissionError, OSError) as e:
            # If we can't write to the log files, just use console logging
            print(f"Warning: Could not create log files in {log_dir}: {str(e)}", file=sys.stderr)
            logger.addHandler(console_handler)
            logger.warning(f"File logging disabled - could not write to {log_dir}")

    return logger