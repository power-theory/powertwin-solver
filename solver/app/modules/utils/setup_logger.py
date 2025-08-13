import os
import logging

from rich.logging import RichHandler
from rich.console import Console

############################################################################################################
# Name: initialize_logger(logger_name)
# Description: This function initializes the logger with the given name.
############################################################################################################
def initialize_logger(logger_name):
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)

    # Check if handlers are already added to the logger
    if not logger.handlers:
        log_dir = os.path.join('logs')
        os.makedirs(log_dir, exist_ok=True)

        # Create handlers
        console_handler = RichHandler(console=Console(), show_time=True, show_level=False, show_path=False)
        file_handler = logging.FileHandler(os.path.join(log_dir, 'dev_logs.txt'))
        file_handler_no_debug = logging.FileHandler(os.path.join(log_dir, 'user_logs.txt'))

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

    return logger