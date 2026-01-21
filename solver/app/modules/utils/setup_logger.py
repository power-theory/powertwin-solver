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

        # Create rich console handler for terminal output
        console_handler = RichHandler(
            console=Console(),
            show_time=True,
            show_level=True,
            show_path=False,
            rich_tracebacks=True,
            omit_repeated_times=False
        )
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        console_handler.setFormatter(console_formatter)
        
        try:
            # DEBUG log file handler with rotation (all messages)
            dev_log_path = os.path.join(log_dir, 'dev_logs.txt')
            file_handler = RotatingFileHandler(
                dev_log_path,
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=10,
                encoding='utf-8'
            )
            file_handler.setLevel(logging.DEBUG)
            file_formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            file_handler.setFormatter(file_formatter)

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

            # Add all handlers to logger
            logger.addHandler(console_handler)
            logger.addHandler(file_handler)
            logger.addHandler(file_handler_no_debug)
            logger.addHandler(json_handler)
            
        except (PermissionError, OSError) as e:
            # If we can't write to the log files, just use console logging
            print(f"Warning: Could not create log files in {log_dir}: {str(e)}", file=sys.stderr)
            logger.addHandler(console_handler)
            logger.warning(f"File logging disabled - could not write to {log_dir}")

    return logger