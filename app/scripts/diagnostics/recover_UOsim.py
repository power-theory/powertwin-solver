import csv
import os
import shutil
import zipfile

from scripts.helper import initialize_logger
from scripts.simulation import initialize_uo
from scripts.diagnostics import asset_analysis

logger = initialize_logger('Recover UOSim')

############################################################################################################
# Name: process_status_file(status_file_path, assets_to_transfer, CORRUPTED_STATUS_DIR)
# Description: This function processes the status file and adds asset IDs to the assets_to_transfer set.
############################################################################################################
def process_status_file(status_file_path, assets_to_transfer, CORRUPTED_STATUS_DIR):
    with open(status_file_path, mode='r') as file:
        reader = csv.DictReader(file)
        for row in reader:
            asset_id = row['Asset ID']
            cleaned_report_dir = os.path.join(CORRUPTED_STATUS_DIR, 'cleaned_reports', f'{asset_id}')
            
            if row['Status'] == 'Processing' and os.path.exists(cleaned_report_dir) and os.listdir(cleaned_report_dir):
                logger.debug(f"Excluding asset {asset_id} with non-empty cleaned_reports directory: {cleaned_report_dir}")
                continue
            
            if row['Status'] in ['Processing', 'Not Processed Yet']:
                assets_to_transfer.add(asset_id)

############################################################################################################
# Name: search_asset_status(CORRUPTED_STATUS_DIR, batch_id)
# Description: This function searches the batch status files in the CORRUPTED_STATUS_DIR and returns a set of
#   asset IDs to transfer.
############################################################################################################
def search_asset_status(CORRUPTED_STATUS_DIR, batch_id):
    assets_to_transfer = set()
    
    if batch_id is not None:
        logger.debug(f"Collecting assets to transfer from batch {batch_id}")
        status_file_path = os.path.join(CORRUPTED_STATUS_DIR, f'{batch_id}_status.csv')
        if not os.path.exists(status_file_path):
            logger.error(f"Batch status file not found: {status_file_path}")
            return assets_to_transfer
        
        process_status_file(status_file_path, assets_to_transfer, CORRUPTED_STATUS_DIR)

    else:
        logger.debug(f"Collecting assets to transfer from {CORRUPTED_STATUS_DIR}")
        # Go through the entire CORRUPTED_STATUS_DIR and collect assets that are "Processing" or "Not Processed Yet"
        for root, dirs, files in os.walk(CORRUPTED_STATUS_DIR):
            for file_name in files:
                if file_name.endswith('_status.csv'):
                    status_file_path = os.path.join(root, file_name)
                    process_status_file(status_file_path, assets_to_transfer, CORRUPTED_STATUS_DIR)

    
    return assets_to_transfer

############################################################################################################
# Name: clean_corrupted_simulation(CORRUPTED_SIMULATION_DIR, asset_id)
# Description: This function deletes the asset directory or batch directory of an existing asset in the corrupted simulation.
# Purpose: This function is called when an asset is found to be "Processing" or "Not Processed Yet" in the status file.
############################################################################################################
def clean_corrupted_simulation(CORRUPTED_SIMULATION_DIR, asset_id):
    #TODO: CHANGE THE UOSIM_TIME_CSV TO INSTEAD BE A POSTGRES
    uosim_time_csv = os.path.join(CORRUPTED_SIMULATION_DIR, 'uosim_time.csv')
    if not os.path.exists(uosim_time_csv):
        logger.error(f"uosim_time.csv not found in {CORRUPTED_SIMULATION_DIR}")
        return

    batch_id = None
    remaining_rows = []
    with open(uosim_time_csv, mode='r') as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row['assetid'] == asset_id:
                batch_id = row['batch']
            else:
                remaining_rows.append(row)

    if batch_id is None:
        logger.error(f"Batch ID for asset {asset_id} not found in uosim_time.csv")
        return

    # Write the remaining rows back to the CSV file
    with open(uosim_time_csv, mode='w', newline='') as file:
        fieldnames = reader.fieldnames  # Use the original fieldnames from the reader
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(remaining_rows)

    BATCH_DIR = os.path.join(CORRUPTED_SIMULATION_DIR, 'urbanopt_simulation', f'batch_{batch_id}')
    ASSET_DIR = os.path.join(BATCH_DIR, 'run', 'powertwin_scenario', asset_id)
    
    if not os.path.exists(BATCH_DIR):
        logger.debug(f"Batch does not exist {asset_id}: {BATCH_DIR}")
    elif not os.path.exists(os.path.join(BATCH_DIR, 'run')):
        logger.debug(f"Deleting batch directory with no processed assets: {BATCH_DIR}")
        shutil.rmtree(BATCH_DIR)
    elif os.path.exists(ASSET_DIR):
        logger.debug(f"Deleting asset directory: {ASSET_DIR}")
        shutil.rmtree(ASSET_DIR)
    else:
        logger.debug(f"Asset {asset_id} was never proccesed")

############################################################################################################
# Name: simulation_recovery(CORRUPTED_SIMULATION_DIR, RECOVERY_DIR, METADATA_CSV_PATH, batch_id, num_cores)
# Description: This function recovers a corrupted simulation by removing assets that are "Processing" or "Not Processed Yet"
#   from the feature_files directory and re-running the UO simulation.
############################################################################################################
def simulation_recovery(CORRUPTED_SIMULATION_DIR, RECOVERY_DIR, RECOVERY_DIR_LOCAL, METADATA_CSV_PATH, batch_id, num_cores):    
    logger.info(f"Recovering simulation: {CORRUPTED_SIMULATION_DIR}")


    CORRUPTED_FEATURE_FILE_ZIP = os.path.join(CORRUPTED_SIMULATION_DIR, 'feature_files.zip')
    CORRUPTED_STATUS_DIR = os.path.join(CORRUPTED_SIMULATION_DIR, 'batch_status')

    FEATURE_FILE_ZIP_PATH = os.path.join(RECOVERY_DIR, 'feature_files.zip')
    FEATURE_FILE_ZIP_PATH_LOCAL = os.path.join(RECOVERY_DIR_LOCAL, 'feature_files.zip')
    FEATURE_FILES_DIR = os.path.join(RECOVERY_DIR, 'feature_files')
    os.makedirs(FEATURE_FILES_DIR, exist_ok=True)
        
    # Unzip feature_file.zip from the corrupted simulation into the recovery directory
    logger.info("Recovering feature files...")

    if os.path.exists(CORRUPTED_FEATURE_FILE_ZIP):
        logger.debug(f"Unzipping {CORRUPTED_FEATURE_FILE_ZIP} into {FEATURE_FILES_DIR}")
        with zipfile.ZipFile(CORRUPTED_FEATURE_FILE_ZIP, 'r') as zip_ref:
            zip_ref.extractall(FEATURE_FILES_DIR)
    else:
        logger.error(f"feature_file.zip not found in {CORRUPTED_SIMULATION_DIR}, stopping recovery process")
        return
        
    # Collect assets to transfer
    assets_to_transfer = search_asset_status(CORRUPTED_STATUS_DIR, batch_id)
    
    # Determine which feature files to transfer
    logger.info(f"Collecting feature files to transfer and cleaning corrupted simulation...")
    for root, dirs, files in os.walk(FEATURE_FILES_DIR):
        for file_name in files:
            asset_id = file_name.split('_')[0]
            if asset_id not in assets_to_transfer:
                file_path = os.path.join(root, file_name)
                os.remove(file_path)
            # else:
            #     clean_corrupted_simulation(CORRUPTED_SIMULATION_DIR, asset_id)
            
    
    # Zip the feature_files directory
    logger.debug(f"Zipping {FEATURE_FILES_DIR} into {FEATURE_FILE_ZIP_PATH} and {FEATURE_FILE_ZIP_PATH_LOCAL}")
    shutil.make_archive(os.path.splitext(FEATURE_FILE_ZIP_PATH)[0], 'zip', FEATURE_FILES_DIR)
    shutil.make_archive(os.path.splitext(FEATURE_FILE_ZIP_PATH_LOCAL)[0], 'zip', FEATURE_FILES_DIR)
    
    
    # TODO: modify the following code to use the postgres database instead of the uosim_time.csv
    UOSIM_TIME_CSV = os.path.join(CORRUPTED_SIMULATION_DIR, 'uosim_time.csv')
    with open(UOSIM_TIME_CSV, mode='r') as file:
        reader = csv.DictReader(file)
        first_row = next(reader, None)
        if first_row:
            logger.debug(f"location: {first_row['location']}")
            location = first_row['location']
        else:
            logger.error(f"location not found in {UOSIM_TIME_CSV}, stopping recovery process")
            return

    # Continue with the recovery process
    asset_analysis(RECOVERY_DIR, RECOVERY_DIR_LOCAL, num_cores, location)
    
    initialize_uo(RECOVERY_DIR,RECOVERY_DIR_LOCAL,METADATA_CSV_PATH,FEATURE_FILE_ZIP_PATH, clean_report_flag=True)


if __name__ == "__main__":
    USERFILES_DIR = os.path.join('powertwin-solver-pg', 'user_files')
    METADATA_CSV_PATH = os.path.join(USERFILES_DIR, 'example_simulation_recovery_metadata.csv')
    CORRUPTED_SIMULATION_DIR = 'example_simulation'
    RECOVERY_DIR = 'example_recovery'
    RECOVERY_DIR_LOCAL = 'example_recovery'
    batch_id = 1
    num_cores = 4
    simulation_recovery(CORRUPTED_SIMULATION_DIR, RECOVERY_DIR, RECOVERY_DIR_LOCAL, METADATA_CSV_PATH, batch_id, num_cores)
        
    
        
        
            


