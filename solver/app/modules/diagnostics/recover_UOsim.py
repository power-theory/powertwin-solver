import os
import shutil
import zipfile

from modules.utils import initialize_logger
from modules.simulation import initialize_uo
from .runtime_analysis import asset_analysis
from .db import get_weather, update_simulation_name, get_bulk_assets, get_bulk_batchids, get_failed_assets, update_status

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Recover UOSim', external_log_dir)

############################################################################################################
# Name: simulation_recovery(CORRUPTED_SIMULATION_DIR, RECOVERY_DIR, batch_id, num_cores, location)
# Description: This function recovers a corrupted simulation by extracting the feature files from the corrupted simulation
#   and zipping them into a new feature_files.zip in the recovery directory.
#   The function then continues with the recovery process by analyzing the assets in the feature files.
#   The function returns the total number of assets processed.
############################################################################################################
def simulation_recovery(RECOVERY_DIR, LOCAL_RECOVERY_DIR, CORRUPTED_DIR, CORRUPTED_SIMULATION_NAME, RECOVERY_SIMULATION_NAME, batch_id, num_cores):  
    from modules.simulation import create_single_featurefile  
    logger.info(f"Recovering simulation: {CORRUPTED_SIMULATION_NAME} for batch {batch_id}")

    location = get_weather(simulation_name=CORRUPTED_SIMULATION_NAME)

    CORRUPTED_FEATURE_FILE_ZIP = os.path.join(CORRUPTED_DIR, 'feature_files.zip')

    FEATURE_FILE_ZIP_PATH = os.path.join(RECOVERY_DIR, 'feature_files.zip')
    FEATURE_FILE_ZIP_PATH_LOCAL = os.path.join(LOCAL_RECOVERY_DIR, 'feature_files.zip')
    
    FEATURE_FILES_DIR = os.path.join(RECOVERY_DIR, 'feature_files')
    os.makedirs(FEATURE_FILES_DIR, exist_ok=True)
        
    # Unzip feature_file.zip from the corrupted simulation into the recovery directory
    logger.info("Recovering feature files...")    
    if os.path.exists(CORRUPTED_FEATURE_FILE_ZIP):
        logger.info(f"Extracting {CORRUPTED_FEATURE_FILE_ZIP} to {FEATURE_FILES_DIR}")
        try:
            with zipfile.ZipFile(CORRUPTED_FEATURE_FILE_ZIP, 'r') as zip_ref:
                # Extract the contents, preserving paths
                for file_info in zip_ref.infolist():
                    # Strip any leading directories (like 'feature_files/')
                    filename = file_info.filename
                    # If the file is inside a 'feature_files' directory, extract just the file
                    if '/' in filename:
                        filename = filename.split('/', 1)[1]
                        if filename:  # Only extract if there's a filename after the directory
                            source = zip_ref.read(file_info.filename)
                            target_path = os.path.join(FEATURE_FILES_DIR, filename)
                            with open(target_path, 'wb') as f:
                                f.write(source)
                    else:
                        # Direct files in the zip root
                        zip_ref.extract(file_info, FEATURE_FILES_DIR)
            logger.info("Feature files extracted successfully")
        except Exception as e:
            logger.error(f"Error extracting feature files: {str(e)}")
    else:
        logger.warning(f"Corrupted feature file zip not found: {CORRUPTED_FEATURE_FILE_ZIP}")
    
    
    # Check if feature_files exist
    feature_files = [f for f in os.listdir(FEATURE_FILES_DIR) if f.endswith('.json')]    
    if not feature_files:
        logger.error(f"No feature files found in {FEATURE_FILES_DIR}. Recovery cannot proceed. No changes made to database")
        shutil.rmtree(RECOVERY_DIR)
        shutil.rmtree(LOCAL_RECOVERY_DIR) 
        return False
    
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
    

    # Get failed assets
    failed_assets = []
    failed_assets = get_failed_assets(simulation_name=RECOVERY_SIMULATION_NAME)
    logger.info(f"Total failed assets: {len(failed_assets)}")
        
    # Remove feature files that aren't in the specified asset IDs
    for file_name in feature_files:
        # Extract asset_id from filename (assuming format "assetID_name.json")
        file_asset_id = int(file_name.split('_')[0])
        
        # If this asset ID is not in our list of assets to keep delete it
        if file_asset_id not in all_asset_ids:
            asset_path = os.path.join(FEATURE_FILES_DIR, file_name)
            os.remove(asset_path)
        
        # If this asset ID is in our list of failed assets update its content
        # TODO: Workout a way to bulk update failed assets, current method is slow
        if file_asset_id in failed_assets:
            asset_path = os.path.join(FEATURE_FILES_DIR, file_name)
            logger.info(f"Updating failed asset {file_name}")
            #create_single_featurefile(file_asset_id, RECOVERY_DIR, LOCAL_RECOVERY_DIR, RECOVERY_SIMULATION_NAME)
            update_status("Processing", asset_id=file_asset_id)
            logger.info(f"Failed asset {file_name} updated")
        
    # Zip the feature_files directory
    logger.debug(f"Zipping {FEATURE_FILES_DIR} into {FEATURE_FILE_ZIP_PATH} and {FEATURE_FILE_ZIP_PATH_LOCAL}")
    shutil.make_archive(os.path.splitext(FEATURE_FILE_ZIP_PATH)[0], 'zip', FEATURE_FILES_DIR)
    shutil.make_archive(os.path.splitext(FEATURE_FILE_ZIP_PATH_LOCAL)[0], 'zip', FEATURE_FILES_DIR)
    
    # Enable HPC mode if running within a SLURM job
    if os.environ.get('SLURM_JOB_ID'):
        hpc_mode = True
    else:
        hpc_mode = False

    # Continue with the recovery process
    asset_analysis(RECOVERY_DIR, num_cores, location, RECOVERY_SIMULATION_NAME, hpc_mode)

    batch_range = initialize_uo(RECOVERY_DIR, LOCAL_RECOVERY_DIR, RECOVERY_SIMULATION_NAME, hpc_mode)
    
    if hpc_mode:
        return batch_range
    


if __name__ == "__main__":
    USERFILES_DIR = os.path.join('powertwin-solver-pg', 'user_files')
    CORRUPTED_SIMULATION_DIR = 'example_simulation'
    RECOVERY_DIR = 'example_recovery'
    RECOVERY_DIR_LOCAL = 'example_recovery'
    batch_id = 1
    num_cores = 4
    simulation_recovery(CORRUPTED_SIMULATION_DIR, RECOVERY_DIR, RECOVERY_DIR_LOCAL, batch_id, num_cores)
        
    
        
        
            


