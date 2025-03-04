import os
import shutil
import zipfile

from modules.utils import initialize_logger
from modules.simulation import initialize_uo
from .runtime_analysis import asset_analysis
from .db import get_weather, update_simulation_name, get_bulk_assets, get_bulk_batchids

logger = initialize_logger('Recover UOSim')

############################################################################################################
# Name: simulation_recovery(CORRUPTED_SIMULATION_DIR, RECOVERY_DIR, batch_id, num_cores, location)
# Description: This function recovers a corrupted simulation by extracting the feature files from the corrupted simulation
#   and zipping them into a new feature_files.zip in the recovery directory.
#   The function then continues with the recovery process by analyzing the assets in the feature files.
#   The function returns the total number of assets processed.
############################################################################################################
def simulation_recovery(RECOVERY_DIR, LOCAL_RECOVERY_DIR, CORRUPTED_DIR, CORRUPTED_SIMULATION_NAME, RECOVERY_SIMULATION_NAME, batch_id, num_cores):    
    logger.info(f"Recovering simulation: {CORRUPTED_SIMULATION_NAME} for batch {batch_id}")

    location = get_weather(simulation_name=CORRUPTED_SIMULATION_NAME)

    CORRUPTED_FEATURE_FILE_ZIP = os.path.join(CORRUPTED_DIR, 'feature_files.zip')

    FEATURE_FILE_ZIP_PATH = os.path.join(RECOVERY_DIR, 'feature_files.zip')
    FEATURE_FILE_ZIP_PATH_LOCAL = os.path.join(LOCAL_RECOVERY_DIR, 'feature_files.zip')
    
    TEMP_FEATURE_FILES_DIR = os.path.join(RECOVERY_DIR, 'temp_feature_files')
    FEATURE_FILES_DIR = os.path.join(RECOVERY_DIR, 'feature_files')
    os.makedirs(FEATURE_FILES_DIR, exist_ok=True)
    os.makedirs(TEMP_FEATURE_FILES_DIR, exist_ok=True)
        
    # Unzip feature_file.zip from the corrupted simulation into the recovery directory
    logger.info("Recovering feature files...")

    # Unzip the feature file into the temp_feature_files directory
    if os.path.exists(CORRUPTED_FEATURE_FILE_ZIP):
        logger.debug(f"Unzipping {CORRUPTED_FEATURE_FILE_ZIP} into {TEMP_FEATURE_FILES_DIR}")
        with zipfile.ZipFile(CORRUPTED_FEATURE_FILE_ZIP, 'r') as zip_ref:
            zip_ref.extractall(TEMP_FEATURE_FILES_DIR)
    else:
        logger.error(f"feature_file.zip not found in {CORRUPTED_SIMULATION_DIR}, stopping recovery process")
        return
    
    
    # Collect assets to transfer
    all_asset_ids = []  
    if batch_id is None:
        batch_list = get_bulk_batchids(CORRUPTED_SIMULATION_NAME)
        
        # Collect all assets from all batches
        for batch in batch_list:
            update_simulation_name(RECOVERY_SIMULATION_NAME, CORRUPTED_SIMULATION_NAME, batch)
            
            batch_assets = get_bulk_assets(RECOVERY_SIMULATION_NAME, batch)
            batch_asset_ids = [asset[0] for asset in batch_assets]
            all_asset_ids.extend(batch_asset_ids)
            
            logger.debug(f"Collected {len(batch_assets)} assets from batch {batch}")
            
    else:
        update_simulation_name(RECOVERY_SIMULATION_NAME, CORRUPTED_SIMULATION_NAME, batch_id)
        batch_assets = get_bulk_assets(RECOVERY_SIMULATION_NAME, batch_id)
        all_asset_ids = [asset[0] for asset in batch_assets]
        logger.info(f"Collected {len(batch_assets)} assets from batch {batch_id}")

    logger.info(f"Total assets to recover: {len(all_asset_ids)}")
    
    # Search for the asset ID in the feature files
    logger.debug(f"Searching for feature file in {TEMP_FEATURE_FILES_DIR}")
    
    for asset_id in all_asset_ids:
        for file_name in os.listdir(TEMP_FEATURE_FILES_DIR):
            if file_name.startswith(f"{asset_id}_") and file_name.endswith('.json'):
                ASSET_JSON = os.path.join(TEMP_FEATURE_FILES_DIR, file_name)
    
                # Copy the configuration file to the requested_files directory
                shutil.move(ASSET_JSON, FEATURE_FILES_DIR)
                
    
    shutil.rmtree(TEMP_FEATURE_FILES_DIR)
    
    # Zip the feature_files directory
    logger.debug(f"Zipping {FEATURE_FILES_DIR} into {FEATURE_FILE_ZIP_PATH} and {FEATURE_FILE_ZIP_PATH_LOCAL}")
    shutil.make_archive(os.path.splitext(FEATURE_FILE_ZIP_PATH)[0], 'zip', FEATURE_FILES_DIR)
    shutil.make_archive(os.path.splitext(FEATURE_FILE_ZIP_PATH_LOCAL)[0], 'zip', FEATURE_FILES_DIR)

    # Continue with the recovery process
    asset_analysis(RECOVERY_DIR, num_cores, location, RECOVERY_SIMULATION_NAME)
    
    initialize_uo(RECOVERY_DIR,LOCAL_RECOVERY_DIR,RECOVERY_SIMULATION_NAME)


if __name__ == "__main__":
    USERFILES_DIR = os.path.join('powertwin-solver-pg', 'user_files')
    CORRUPTED_SIMULATION_DIR = 'example_simulation'
    RECOVERY_DIR = 'example_recovery'
    RECOVERY_DIR_LOCAL = 'example_recovery'
    batch_id = 1
    num_cores = 4
    simulation_recovery(CORRUPTED_SIMULATION_DIR, RECOVERY_DIR, RECOVERY_DIR_LOCAL, batch_id, num_cores)
        
    
        
        
            


