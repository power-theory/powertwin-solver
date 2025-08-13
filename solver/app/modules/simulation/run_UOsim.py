import os
import shutil
import time
import csv
import json
import pandas as pd

from .clean_report import clean_single_report
from modules.utils import initialize_logger, run_command
from modules.utils.hpc_parallel import get_local_cores_per_task, get_hpc_environment
from joblib import Parallel, delayed


logger = initialize_logger('Run UOSim')

URBANOPT_DIR = os.path.join('app','urbanopt')

############################################################################################################
# Name: create_scenario_file(FEATURE_FILE_JSON, MAPPER_FILE, SCENARIO_FILE_CSV)
# Description: This function creates a scenario file for the feature file using the mapper file. The scenario
#   file is written to a CSV file.
############################################################################################################
def create_scenario_file(FEATURE_FILE_JSON, MAPPER_FILE, SCENARIO_FILE_CSV):
    # Read the feature JSON file
    with open(FEATURE_FILE_JSON, 'r') as f:
        feature_data = json.load(f)
    
    # Get the project information
    project = feature_data.get("project", {})
    feature_id = project.get("id", "")
    feature_name = project.get("name", "")
    
    # If project ID is empty, try to get it from the first feature
    if not feature_id and "features" in feature_data and len(feature_data["features"]) > 0:
        feature_id = feature_data["features"][0].get("properties", {}).get("id", "")
        if not feature_name:
            feature_name = feature_data["features"][0].get("properties", {}).get("name", "")
    
    # Ensure we have a feature ID
    if not feature_id:
        logger.error(f"No feature ID found in feature file: {FEATURE_FILE_JSON}")
        raise ValueError("Feature file is missing required ID field")
    
    # Process the mapper file name
    mapper_filename = os.path.basename(MAPPER_FILE)

    if mapper_filename.lower().endswith(".rb"):
        base_mapper = mapper_filename[:-3]
    else:
        base_mapper = mapper_filename

    mapper_class = f"URBANopt::Scenario::{base_mapper}Mapper"
    
    # Write the scenario CSV file
    with open(SCENARIO_FILE_CSV, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        # Include header row with required fields
        writer.writerow(["Feature Id", "Feature Name", "Mapper Class", "REopt Assumptions"])
        # Include feature data row with empty REopt assumptions (not required for basic simulation)
        writer.writerow([feature_id, feature_name, mapper_class, ""])

############################################################################################################
# Name: run_uosimulation(SIMULATION_DIR,LOCAL_DIR,FEATURE_FILE_JSON, METADATA_CSV, batch_index)
# Description: This function runs the UrbanOpt simulation for the feature file. The simulation is run using
#   the feature file and the scenario file. The simulation is processed and the metadata is cleaned.
#   The total time is calculated and the metadata is updated in the database.
############################################################################################################
def run_uosimulation(SIMULATION_DIR,LOCAL_DIR,FEATURE_FILE_JSON, batch_index):
    from modules.diagnostics import update_time, get_weather
    
    feature_start_time = time.time()
    
    feature_file_name = os.path.basename(FEATURE_FILE_JSON)
    asset_id = feature_file_name.split('_')[0]
    asset_name = '_'.join(feature_file_name.split('_')[1:]).replace('.json', '')

    logger.info(f"\n{'='*47}\n"
    f"Processing feature file: {feature_file_name}\n"
    f"Asset ID: {asset_id}\n"
    f"Asset Name: {asset_name}\n"
    f"Batch Index: {batch_index}\n"
    f"{'='*47}"
)

    city = get_weather(asset_id)
                
    SIMULATION_DIR = os.path.join(SIMULATION_DIR,'urbanopt_simulation')
    WEATHER_DESTINATION = os.path.join(SIMULATION_DIR, "weather")
    
    #TODO: change to read the location metadata rather then a csv file
    WEATHER_MAP_CSV = os.path.join(URBANOPT_DIR,'weather_map.csv')

    # TODO: Adjust so that copied weather files with the specified extensions are from the database server
    # Weather files should be selected dependent on geolocation of building which can be found in the feature file
    
    # Read the CSV file
    weather_df = pd.read_csv(WEATHER_MAP_CSV)
    city_data = weather_df[weather_df['City'].str.lower() == city.lower()]
    
    # Extract the relevant data
    city_data = city_data.iloc[0]
    weather_file = city_data['WeatherFile']
    
        
    if not os.path.exists(WEATHER_DESTINATION):
        os.makedirs(WEATHER_DESTINATION, exist_ok=True)

        WEATHER_BASE_NAME = os.path.join(URBANOPT_DIR, 'weather_files',weather_file, weather_file)
        for ext in ["ddy", "stat", "epw"]:
            shutil.copy(f"{WEATHER_BASE_NAME}.{ext}", WEATHER_DESTINATION)

    LOCAL_BATCH_SIMULATION_DIR = os.path.join(LOCAL_DIR, 'urbanopt_simulation', f'batch_{batch_index}')
    
    # Make sure the mappers directory exists
    MAPPERS_DIR = os.path.join(SIMULATION_DIR, 'mappers')
    if not os.path.exists(MAPPERS_DIR):
        os.makedirs(MAPPERS_DIR, exist_ok=True)
        
    # Check if PowerTwin.rb exists in the mappers directory, if not copy it from upload directory
    MAPPER_FILE = os.path.join(MAPPERS_DIR, 'PowerTwin.rb')
    if not os.path.exists(MAPPER_FILE):
        UPLOAD_MAPPER = os.path.join('upload', 'PowerTwin.rb')
        if os.path.exists(UPLOAD_MAPPER):
            shutil.copy(UPLOAD_MAPPER, MAPPER_FILE)
        else:
            logger.error(f"BATCH {batch_index}: PowerTwin.rb mapper file not found in upload directory")
            raise FileNotFoundError("PowerTwin.rb mapper file not found")
        
    
    # Move the feature file to the project directory
    try:
        logger.debug(f"BATCH {batch_index}: Moving feature file {FEATURE_FILE_JSON} to {SIMULATION_DIR}")
        shutil.move(FEATURE_FILE_JSON, SIMULATION_DIR)
    except shutil.Error as e:
        logger.error(f"BATCH {batch_index}: Failed to move feature file: {e}")
        raise e

    # Define the path to the scenario file
    FEATURE_FILE_JSON = os.path.join(SIMULATION_DIR, feature_file_name)
    SCENARIO_FILE_CSV = os.path.join(SIMULATION_DIR, f"powertwin_scenario_{batch_index}.csv")
    
    # Create the scenario
    # Created custom function to create the scenario file rather then using uo create -s
    try:
        logger.info(f"BATCH {batch_index}: Creating scenario for feature file: {feature_file_name}")
        create_scenario_file(FEATURE_FILE_JSON, MAPPER_FILE, SCENARIO_FILE_CSV)
    except Exception as e:
        logger.error(f"BATCH {batch_index}: Failed to create scenario: {e.stderr}")
        raise e
    

    # Run the run and process commands and record their times
    # FEATURE FILE MUST BE IN THE SIMULATION DIRECTORY ALONG WITH THE SCENARIO FILE
    logger.info(f"BATCH {batch_index}: Running UrbanOpt simulation for: {asset_id}")
    try:
        # Before running, let's verify all files exist
        if not os.path.exists(FEATURE_FILE_JSON):
            raise FileNotFoundError(f"Feature file {FEATURE_FILE_JSON} not found")
        if not os.path.exists(SCENARIO_FILE_CSV):
            raise FileNotFoundError(f"Scenario file {SCENARIO_FILE_CSV} not found")
        if not os.path.exists(MAPPER_FILE):
            raise FileNotFoundError(f"Mapper file {MAPPER_FILE} not found")
            
        # Log the contents of the scenario CSV for debugging
        logger.debug(f"BATCH {batch_index}: Scenario file contents:")
        with open(SCENARIO_FILE_CSV, 'r') as f:
            for line in f:
                logger.debug(line.strip())
                
        uo_run_time = run_command(f"uo run -s {SCENARIO_FILE_CSV} -f {FEATURE_FILE_JSON}")
        
        logger.info(f"BATCH {batch_index}: Processing UrbanOpt simulation for: {asset_id}")
        uo_process_time = run_command(f"uo process -d -f {FEATURE_FILE_JSON} -s {SCENARIO_FILE_CSV}")
        total_time = uo_run_time + uo_process_time
    except Exception as e:
        logger.error(f"BATCH {batch_index}: Error running UrbanOpt commands: {str(e)}")
        raise e
    
    # Remove the feature file after being processed
    os.remove(FEATURE_FILE_JSON)
    
    # Rename SIMULATION_DIR to locate the asset file
    ASSET_RUN_DIR = os.path.join(SIMULATION_DIR, 'run', f'powertwin_scenario_{batch_index}')
        
    # Clean Report 
    metadata_files = [f for f in os.listdir(LOCAL_DIR) if f.endswith('_metadata.csv')]
    if metadata_files:
        METADATA_CSV = os.path.join(LOCAL_DIR, metadata_files[0])
        logger.debug(f"BATCH {batch_index}: Cleaning report for {asset_id}:{asset_name}...") 
        clean_single_report(LOCAL_DIR,LOCAL_BATCH_SIMULATION_DIR,ASSET_RUN_DIR, METADATA_CSV, asset_id)
    else:
        logger.error("No metadata file found with pattern *_metadata.csv, not cleaning report")
    
    
    feature_end_time = time.time()
    feature_duration = feature_end_time - feature_start_time

    # Calculate hours, minutes, and seconds
    feature_hours = int(feature_duration // 3600)
    feature_minutes = int((feature_duration % 3600) // 60)
    feature_seconds = feature_duration % 60
    
    logger.info(f"BATCH {batch_index}: {asset_id} processed in {feature_hours} hours, {feature_minutes} minutes, and {feature_seconds:.2f} seconds.")
    
    # Update the postgres
    update_time(asset_id, uo_run_time, uo_process_time, total_time)

############################################################################################################
# Name: process_single_asset(asset_data, SIMULATION_DIR, LOCAL_DIR, batch_num)
# Description: Wrapper function to process a single asset - used for parallel processing within batches
############################################################################################################
def process_single_asset(asset_data, SIMULATION_DIR, LOCAL_DIR, batch_num):
    from modules.diagnostics import update_status
    
    asset_id, asset_name = asset_data
    new_asset_name = asset_name.replace(' ', '_')
    feature_file = os.path.join(SIMULATION_DIR, "feature_files", f"{asset_id}_{new_asset_name}.json")
    
    # Update status to Processing
    update_status("Processing", asset_id=asset_id)
    
    logger.debug(f"BATCH {batch_num}: Starting processing asset {asset_id}...")        
    try:
        run_uosimulation(SIMULATION_DIR, LOCAL_DIR, feature_file, batch_num)
        update_status("Finished", asset_id=asset_id)
        return True, asset_id, None
    except Exception as e:
        logger.error(f"BATCH {batch_num}: Failed to process asset {asset_id}: {str(e)}")
        update_status("Failed", asset_id=asset_id)
        return False, asset_id, str(e)

############################################################################################################
# Name: run_batch(batch_num, SIMULATION_DIR,LOCAL_DIR, simulation_name)
# Description: This function runs the batch of assets for the simulation. The function updates the status of
#   the assets to Processing and then runs the UrbanOpt simulation for each asset. The function then updates
#   the status of the assets to Finished or Failed. The function cleans up the batch directory after all assets
#   have been processed.
############################################################################################################
def run_batch(batch_num, SIMULATION_DIR,LOCAL_DIR, simulation_name):
    from modules.diagnostics import update_status,get_asset_total,get_bulk_assets

    # Change all assets in batch to be Not Processed Yet
    update_status("Not Processed Yet",simulation_name=simulation_name)
    total_assets = get_asset_total(simulation_name,batch_num)
    
    logger.debug(f"BATCH {batch_num}: Processing {total_assets} assets...")
    
    # Get all assets for this batch, ordered by order_rank
    assets = get_bulk_assets(simulation_name, batch_num)

    # In UrbanOpt, we need to process assets sequentially to avoid conflicts
    # Each batch can run in parallel, but within a batch, assets must run sequentially
    logger.info(f"BATCH {batch_num}: Processing {len(assets)} assets sequentially")
    
    successful = 0
    failed = 0
    for asset_data in assets:
        result, asset_id, error = process_single_asset(asset_data, SIMULATION_DIR, LOCAL_DIR, batch_num)
        if result:
            successful += 1
        else:
            failed += 1
    
    logger.info(f"BATCH {batch_num}: Completed - {successful} successful, {failed} failed")
    
    # Clean up - delete finished batch
    batch_dir = os.path.join(SIMULATION_DIR, 'run', f'powertwin_scenario_{batch_num}')
    if os.path.exists(batch_dir):
        logger.debug(f"BATCH {batch_num}: Cleaning up directory: {batch_dir}")
        shutil.rmtree(batch_dir)
    else:
        logger.warning(f"BATCH {batch_num}: Directory not found for cleanup: {batch_dir}")
    
    
    logger.info(f"\n{'='*47}\n"
    f"Batch {batch_num} finished processing.\n"
    f"Total assets processed: {total_assets}\n"
    f"{'='*47}"
)



############################################################################################################
# Name: main()
# Description: This function is the entry point for the script. Used for testing purposes.
############################################################################################################
if __name__ == "__main__":
    batch = 1
    SIMULATION_DIR = os.path.join(os.getcwd(), 'output')
    METADATA_CSV = os.path.join(os.getcwd(), 'metadata.csv')
    simulation_name = ''
    
    run_batch(batch, SIMULATION_DIR, METADATA_CSV, simulation_name)
    
    