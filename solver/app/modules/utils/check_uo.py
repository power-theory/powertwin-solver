# ======================================================================================
# UrbanOpt CLI Utilities Module
# Provides utilities for discovering and interacting with the UrbanOpt command-line tool
# ======================================================================================

import os
import subprocess
from modules.utils import initialize_logger

# Initialize logger for this module
external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('UrbanOpt CLI', external_log_dir)

<<<<<<< HEAD
# Discover and return the correct UrbanOpt CLI command
def get_urbanopt_command():
    # Find the UrbanOpt command in the system with fallback options
    # Tests multiple possible installation paths
    
    logger.debug("=== UrbanOpt CLI Discovery ===")
    logger.debug(f"PATH: {os.environ.get('PATH', 'NOT SET')}")
    logger.debug(f"GEM_HOME: {os.environ.get('GEM_HOME', 'NOT SET')}")
    logger.debug(f"GEM_PATH: {os.environ.get('GEM_PATH', 'NOT SET')}")
=======
def get_urbanopt_command(batch_index=None):
    """Get the correct UrbanOpt command with fallback options"""
    batch_prefix = f"BATCH {batch_index}: " if batch_index is not None else ""
    logger.debug(f"{batch_prefix}=== UrbanOpt CLI Discovery ===\n"
                f"{batch_prefix}PATH: {os.environ.get('PATH', 'NOT SET')}\n"
                f"{batch_prefix}GEM_HOME: {os.environ.get('GEM_HOME', 'NOT SET')}\n"
                f"{batch_prefix}GEM_PATH: {os.environ.get('GEM_PATH', 'NOT SET')}")
>>>>>>> 6ab867c4da51cd1432e6e1076eb7d257f69fa9d7
    
    # Try different command options
    test_commands = [
        'uo',  # Most common case - in PATH
        'urbanopt',  # Alternative command name
        '/usr/local/lib/ruby/gems/3.2.2/bin/uo',  # Ruby gems installation path
        '/usr/local/bin/uo'  # System binary path
    ]
    
    # Test each command to see if it works
    for cmd in test_commands:
        try:
            # Run command with version flag to verify it works
            result = subprocess.run(f"{cmd} --version", shell=True, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                logger.debug(f"{batch_prefix}Found working UrbanOpt command: {cmd}")
                logger.debug(f"{batch_prefix}Version output: {result.stdout.strip()}")
                return cmd
        except Exception as e:
<<<<<<< HEAD
            # Command failed, continue trying others
            logger.debug(f"Command '{cmd}' failed: {e}")
=======
            logger.debug(f"{batch_prefix}Command '{cmd}' failed: {e}")
>>>>>>> 6ab867c4da51cd1432e6e1076eb7d257f69fa9d7
            continue
    
    # Check if binary files exist
    potential_paths = [
        '/usr/local/lib/ruby/gems/3.2.2/bin/',  # Ruby gems path
        '/usr/local/bin/',  # System binaries
        '/usr/bin/'  # Alternative system binaries
    ]
    
    # List files in potential installation directories for debugging
    for path in potential_paths:
        try:
            if os.path.exists(path):
                files = os.listdir(path)
                logger.debug(f"{batch_prefix}Files in {path}: {files}")
        except Exception as e:
            logger.debug(f"{batch_prefix}Could not list directory {path}: {e}")
    
<<<<<<< HEAD
    # If we get here, UrbanOpt was not found
    raise RuntimeError("UrbanOpt CLI not found in any expected location. Please check gem installation.")
=======
    raise RuntimeError(f"{batch_prefix}UrbanOpt CLI not found in any expected location. Please check gem installation.")
>>>>>>> 6ab867c4da51cd1432e6e1076eb7d257f69fa9d7
