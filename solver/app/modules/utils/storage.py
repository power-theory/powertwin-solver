import shutil
import os
from modules.utils import initialize_logger

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Storage', external_log_dir)

def check_storage(storage_path, min_free_gb=5):
    """
    Checks if the available storage at storage_path is above min_free_gb.
    If not, logs an error and raises RuntimeError to halt processing.
    """
    total, used, free = shutil.disk_usage(storage_path)
    free_gb = free // (1024 ** 3)
    if free_gb < min_free_gb:
        logger.error(
            f"Insufficient storage in {storage_path}: {free_gb}GB free, {min_free_gb}GB required. Halting batch."
        )
        raise RuntimeError(
            f"Insufficient storage in {storage_path}: {free_gb}GB free, {min_free_gb}GB required."
        )
