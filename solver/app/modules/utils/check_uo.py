import os
import subprocess
from modules.utils import initialize_logger

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('UrbanOpt CLI', external_log_dir)

def get_urbanopt_command(batch_index=None):
    """Get the correct UrbanOpt command with fallback options"""
    batch_prefix = f"BATCH {batch_index}: " if batch_index is not None else ""
    logger.debug(f"{batch_prefix}=== UrbanOpt CLI Discovery ===\n"
                f"{batch_prefix}PATH: {os.environ.get('PATH', 'NOT SET')}\n"
                f"{batch_prefix}GEM_HOME: {os.environ.get('GEM_HOME', 'NOT SET')}\n"
                f"{batch_prefix}GEM_PATH: {os.environ.get('GEM_PATH', 'NOT SET')}")
    
    # Try different command options
    test_commands = [
        'uo',
        'urbanopt', 
        '/usr/local/lib/ruby/gems/3.2.2/bin/uo',
        '/usr/local/bin/uo'
    ]
    
    for cmd in test_commands:
        try:
            result = subprocess.run(f"{cmd} --version", shell=True, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                logger.debug(f"{batch_prefix}Found working UrbanOpt command: {cmd}")
                logger.debug(f"{batch_prefix}Version output: {result.stdout.strip()}")
                return cmd
        except Exception as e:
            logger.debug(f"{batch_prefix}Command '{cmd}' failed: {e}")
            continue
    
    # Check if binary files exist
    potential_paths = [
        '/usr/local/lib/ruby/gems/3.2.2/bin/',
        '/usr/local/bin/',
        '/usr/bin/'
    ]
    
    for path in potential_paths:
        try:
            if os.path.exists(path):
                files = os.listdir(path)
                logger.debug(f"{batch_prefix}Files in {path}: {files}")
        except Exception as e:
            logger.debug(f"{batch_prefix}Could not list directory {path}: {e}")
    
    raise RuntimeError(f"{batch_prefix}UrbanOpt CLI not found in any expected location. Please check gem installation.")