# ======================================================================================
# Storage Validation Module
# Purpose: Verifies available disk space before simulation execution
# ======================================================================================

import shutil
import os
from modules.utils import initialize_logger

# Setup logging with external log directory support (for HPC logging)
external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Storage', external_log_dir)

def check_storage(storage_path, min_free_gb=5):
    # Verify available storage space is sufficient for simulation
    # Halts batch processing if storage is below minimum threshold
    
    # Get disk usage statistics (total, used, free bytes)
    total, used, free = shutil.disk_usage(storage_path)
    free_gb = free // (1024 ** 3)
    
    # Check if free space meets minimum requirement
    if free_gb < min_free_gb:
        # Log error and raise exception to prevent batch execution
        logger.error(
            f"Insufficient storage in {storage_path}: {free_gb}GB free, {min_free_gb}GB required. Halting batch."
        )
        raise RuntimeError(
            f"Insufficient storage in {storage_path}: {free_gb}GB free, {min_free_gb}GB required."
        )
