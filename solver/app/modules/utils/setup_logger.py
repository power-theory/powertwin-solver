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
            'line': record.lineno,
        }

        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)

        return json.dumps(log_data)


class ImprovedFormatter(logging.Formatter):
    """Enhanced formatter with context awareness, section headers, and ASCII symbols."""

    # Shared state for context tracking
    _current_simulation = None
    _current_phase = None
    _phase_started = False

    def format(self, record):
        message = record.getMessage()
        timestamp = datetime.fromtimestamp(record.created).strftime('%H:%M:%S')
        level = record.levelname

        # Extract simulation name from message patterns
        if "Starting autorun simulation" in message or "AUTORUN BACKGROUND THREAD STARTED" in message:
            sim_match = message.split("'")[1] if "'" in message else None
            if sim_match:
                ImprovedFormatter._current_simulation = sim_match
                ImprovedFormatter._current_phase = "SETUP"
                ImprovedFormatter._phase_started = False
                return f"\n{'=' * 70}\n[{ImprovedFormatter._current_simulation}] SIMULATION START\n{'=' * 70}\n[{timestamp}] → Starting autorun simulation"

        # Detect phase transitions
        phase_keywords = {
            'WEATHER': ['Weather files', 'Downloading', 'downloaded', 'weather'],
            'FEATURES': ['feature', 'Creating feature files', 'Processing features'],
            'ASSETS': ['Processing assets', 'insert_bulk_assets', 'Bulk inserting', 'inserted/updated'],
            'ANALYSIS': ['asset_analysis', 'analysis', 'running'],
            'CLEANUP': ['cleanup', 'complete', 'finished', 'success'],
        }

        current_phase = ImprovedFormatter._current_phase
        for phase, keywords in phase_keywords.items():
            if any(kw.lower() in message.lower() for kw in keywords):
                if phase != current_phase:
                    ImprovedFormatter._current_phase = phase
                    ImprovedFormatter._phase_started = False
                break

        # Add phase header if transitioning
        prefix = ""
        if not ImprovedFormatter._phase_started and ImprovedFormatter._current_phase:
            prefix = f"\n[{ImprovedFormatter._current_phase}]\n"
            ImprovedFormatter._phase_started = True

        # Format message with symbols based on keywords
        symbol = "  •"
        if level == "ERROR" or "error" in message.lower() or "failed" in message.lower():
            symbol = "  ✗"
        elif level == "DEBUG" or "Within" in message:
            # Suppress 'Within' debug messages or indent them
            if "Within" in message:
                return ""
            symbol = "  •"
        elif "Successfully" in message or "✓" in message or "completed" in message.lower():
            symbol = "  ✓"
        elif "Downloading" in message or "downloading" in message:
            symbol = "  ↓"
        elif "Starting" in message or "start" in message or "→" in message:
            symbol = "  →"
        elif "Processing" in message or "Running" in message:
            symbol = "  ↻"

        # Format the output line
        return f"{prefix}[{timestamp}]{symbol} {message}"


def initialize_logger(logger_name, external_log_dir=None, batch_index=None):
    """Initialize a modern, high-performance logger.
    
    Args:
        logger_name: Name of the logger
        external_log_dir: Optional external directory for logs (for HPC environments)
        batch_index: Optional batch number for batch-specific logs (e.g., 0, 1, 2)
    """
    logger = logging.getLogger(logger_name if batch_index is None else f"{logger_name}_Batch{batch_index}")
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
        console_handler = RichHandler(
            console=Console(),
            show_time=False,
            show_level=False,
            show_path=False,
        )
        
        # Create file paths
        # Check for SLURM environment (HPC detection)
        slurm_job_id = os.environ.get('SLURM_JOB_ID')
        
        if batch_index is not None:
            # Batch-specific logging
            dev_log_filename = f"batch_{batch_index}_logs.log"
            user_log_filename = f"batch_{batch_index}_logs.log"
            error_log_filename = f"batch_{batch_index}_logs.log"
            structured_log_filename = f"batch_{batch_index}_logs.jsonl"
        elif slurm_job_id:
            # HPC environment detected - prefix dev_logs.log with SLURM job ID
            dev_log_filename = f"{slurm_job_id}_dev_logs.log"
            user_log_filename = f"{slurm_job_id}_user_logs.log"
            error_log_filename = f"{slurm_job_id}_error_logs.log"
            structured_log_filename = f"{slurm_job_id}_structured_logs.jsonl"
        else:
            # Standard environment
            dev_log_filename = "dev_logs.log"
            user_log_filename = "user_logs.log"
            error_log_filename = "error_logs.log"
            structured_log_filename = "structured_logs.jsonl"
        
        dev_log_path = os.path.join(log_dir, dev_log_filename)
        user_log_path = os.path.join(log_dir, user_log_filename)
        error_log_path = os.path.join(log_dir, error_log_filename)
        structured_log_path = os.path.join(log_dir, structured_log_filename)
        
        try:
            # Create formatters
            text_formatter = logging.Formatter(
                fmt="%(asctime)s %(levelname)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            improved_formatter = ImprovedFormatter()
            json_formatter = JSONFormatter()

            # Try to create file handlers with rotation
            dev_handler = RotatingFileHandler(
                dev_log_path,
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=10,
                encoding='utf-8',
            )
            user_handler = RotatingFileHandler(
                user_log_path,
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=10,
                encoding='utf-8',
            )
            error_handler = RotatingFileHandler(
                error_log_path,
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=10,
                encoding='utf-8',
            )
            structured_handler = RotatingFileHandler(
                structured_log_path,
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=10,
                encoding='utf-8',
            )

            # Set log levels
            console_handler.setLevel(logging.INFO)
            dev_handler.setLevel(logging.DEBUG)
            user_handler.setLevel(logging.INFO)
            error_handler.setLevel(logging.ERROR)
            structured_handler.setLevel(logging.DEBUG)

            # Set formatters
            console_handler.setFormatter(text_formatter)
            dev_handler.setFormatter(text_formatter)
            user_handler.setFormatter(improved_formatter)
            error_handler.setFormatter(text_formatter)
            structured_handler.setFormatter(json_formatter)

            # Add all handlers to logger
            logger.addHandler(console_handler)
            logger.addHandler(dev_handler)
            logger.addHandler(user_handler)
            logger.addHandler(error_handler)
            logger.addHandler(structured_handler)

        except (PermissionError, OSError) as e:
            # If we can't write to the log files, just use console logging
            print(f"Warning: Could not create log files in {log_dir}: {str(e)}", file=sys.stderr)
            logger.addHandler(console_handler)
            logger.warning(f"File logging disabled - could not write to {log_dir}")

    return logger