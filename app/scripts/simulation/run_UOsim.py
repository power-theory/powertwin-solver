import os
import subprocess
import shutil
import time
import csv
import random
import pandas as pd

from .clean_report import clean_single_report
from scripts.helper import initialize_logger

logger = initialize_logger('Run UOSim')

############################################################################################################
# Name: clean_batch_dir(SIMULATION_DIR)
# Description: This function cleans the batch directory by deleting all directories and files except the run
#   directory.
############################################################################################################
def clean_batch_dir(SIMULATION_DIR):
    # Define the directory to keep
    keep_dirs = {'run'}

    # Iterate through the files and directories in SIMULATION_DIR
    for item in os.listdir(SIMULATION_DIR):
        item_path = os.path.join(SIMULATION_DIR, item)
        
        # Check if the item is a directory and not in the keep_dirs set
        if os.path.isdir(item_path) and item not in keep_dirs:
            shutil.rmtree(item_path)
            logger.debug(f"Deleted directory: {item_path}")
        
        # Check if the item is a file
        elif os.path.isfile(item_path):
            os.remove(item_path)
            logger.debug(f"Deleted file: {item_path}")

############################################################################################################
# Name: run_command(command)
# Description: This function runs a command in the shell and returns the time it takes to execute the command.
############################################################################################################
def run_command(command):
    start_time = time.time()
    try:
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        end_time = time.time()
        logger.info(f"Command '{command}' executed successfully.")
        logger.info(f"Output: {result.stdout}")
        return end_time - start_time
    except subprocess.CalledProcessError as e:
        end_time = time.time()
        logger.error(f"Command '{command}' failed with error: {e.stderr}")
        raise e

############################################################################################################
# Name: update_status(BATCH_STATUS_CSV, asset_id, status, message)
# Description: This function updates the status of an asset in the status file.
############################################################################################################
def update_status(BATCH_STATUS_CSV, asset_id, status, message):
    # Read the current status file
    with open(BATCH_STATUS_CSV, 'r', newline='') as f:
        reader = csv.reader(f)
        lines = list(reader)
    
    # Update the status for the given asset_id
    for line in lines:
        if line[0] == asset_id:
            line[1] = status
            line[2] = message
            break
    
    # Write the updated status file
    with open(BATCH_STATUS_CSV, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(lines)


############################################################################################################
# Name: run_uosimulation(SIMULATION_DIR,LOCAL_DIR,FEATURE_FILE_JSON, METADATA_CSV, batch_index)
# Description: This function runs the UrbanOpt simulation for a single feature file. It creates the scenario,
#   runs the simulation, processes the simulation, and cleans the report if necessary. It records the time it
#   takes to run the simulation and process the simulation.
############################################################################################################
def run_uosimulation(SIMULATION_DIR,LOCAL_DIR,FEATURE_FILE_JSON, batch_index):
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

    UOSIM_TIME_CSV = os.path.join(SIMULATION_DIR, "uosim_time.csv")
    with open(UOSIM_TIME_CSV, mode='r') as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row['assetid'] == asset_id:
                city = row['location']
                
    SIMULATION_DIR = os.path.join(SIMULATION_DIR,'urbanopt_simulation')
    WEATHER_DESTINATION = os.path.join(SIMULATION_DIR, "weather")
    
    #TODO: change to read the location metadata rather then a csv file
    URBANOPT_DIR = os.path.join('urbanopt')
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

    # Move the feature file to the project directory
    try:
        logger.debug(f"BATCH {batch_index}: Moving feature file {FEATURE_FILE_JSON} to {SIMULATION_DIR}")
        shutil.copy(FEATURE_FILE_JSON, SIMULATION_DIR)
    except shutil.Error as e:
        logger.error(f"BATCH {batch_index}: Failed to move feature file: {e}")
        raise e

    # Add a random delay between 1 to 5 seconds between each asset to avoid overloading the scenario file overwrite
    sleep_time = random.randint(1, 5)
    time.sleep(sleep_time) 
    # Create the scenario
    try:
        logger.info(f"BATCH {batch_index}: Creating scenario for feature file: {feature_file_name}")
        subprocess.run(f"uo create --scenario-file {SIMULATION_DIR}/{feature_file_name}", shell=True, check=True, capture_output=True, text=True)
        #TODO: Could potentially rename the incorect powertwin_scenario.csv file if many cores running at once, further testing required 
        # Seems to be a problem, a potential fix is to manually create the scenario file instead of calling the command
        shutil.move(f"{SIMULATION_DIR}/powertwin_scenario.csv", f"{SIMULATION_DIR}/powertwin_scenario_{batch_index}.csv")
    except subprocess.CalledProcessError as e:
        logger.error(f"BATCH {batch_index}: Failed to create scenario: {e.stderr}")
        raise e
    
    # Define the path to the scenario file
    SCENARIO_FILE_CSV = os.path.join(SIMULATION_DIR, f"powertwin_scenario_{batch_index}.csv")
    FEATURE_FILE_JSON = os.path.join(SIMULATION_DIR, feature_file_name)

    # Run the run and process commands and record their times
    # FEATURE FILE MUST BE IN THE SIMULATION DIRECTORY ALONG WITH THE SCENARIO FILE
    logger.info(f"BATCH {batch_index}: Running UrbanOpt simulation for: {asset_id}")
    uo_run_time = run_command(f"uo run -s {SCENARIO_FILE_CSV} -f {FEATURE_FILE_JSON}")
    
    logger.info(f"BATCH {batch_index}: Processing UrbanOpt simulation for: {asset_id}")
    uo_process_time = run_command(f"uo process -d -f {FEATURE_FILE_JSON} -s {SCENARIO_FILE_CSV}")
    total_time = uo_run_time + uo_process_time
    
    
    # Rename SIMULATION_DIR to correct locate the asset file
    SIMULATION_DIR = os.path.join(SIMULATION_DIR, 'run', f'powertwin_scenario_{batch_index}')
        
    # Clean Report 
    metadata_files = [f for f in os.listdir(LOCAL_DIR) if f.endswith('_metadata.csv')]
    if metadata_files:
        METADATA_CSV = os.path.join(LOCAL_DIR, metadata_files[0])
        logger.debug(f"BATCH {batch_index}: Cleaning report for {asset_id}:{asset_name}...") 
        clean_single_report(LOCAL_DIR,LOCAL_BATCH_SIMULATION_DIR,SIMULATION_DIR, METADATA_CSV, asset_id)
    else:
        logger.error("No metadata file found with pattern *_metadata.csv, not cleaning report")
    
    
    feature_end_time = time.time()
    feature_duration = feature_end_time - feature_start_time

    # Calculate hours, minutes, and seconds
    feature_hours = int(feature_duration // 3600)
    feature_minutes = int((feature_duration % 3600) // 60)
    feature_seconds = feature_duration % 60
    logger.info(f"BATCH {batch_index}: {asset_id} processed in {feature_hours} hours, {feature_minutes} minutes, and {feature_seconds:.2f} seconds.")
    
    # Update the CSV data with the results
    # Read the existing CSV data
    with open(UOSIM_TIME_CSV, mode='r') as file:
        reader = csv.DictReader(file)
        data = list(reader)

    # Update the CSV data with the results
    for row in data:
        if row['assetid'] == asset_id:
            row['uo_run'] = uo_run_time
            row['uo_process'] = uo_process_time
            row['total_time'] = total_time

    # Write the updated data back to the CSV file
    with open(UOSIM_TIME_CSV, mode='w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=reader.fieldnames)
        writer.writeheader()
        writer.writerows(data)
        
    # Copy the UOSIM_TIME_CSV to the local directory
    LOCAL_UOSIM_TIME_CSV = os.path.join(LOCAL_DIR, 'uosim_time.csv')
    shutil.copy(UOSIM_TIME_CSV, LOCAL_UOSIM_TIME_CSV)
    

############################################################################################################
# Name: run_batch(batch, SIMULATION_DIR, METADATA_CSV, batch_index)
# Description: This function runs the UrbanOpt simulation for a batch of feature files. It creates the scenario,
#   runs the simulation, processes the simulation, and cleans the report if necessary. It records the time it
#   takes to run the simulation and process the simulation.
############################################################################################################
def run_batch(batch, SIMULATION_DIR,LOCAL_DIR, batch_index):
    # Create a status file for the batch
    # TODO: Move to using postgrs db
    BATCH_STATUS_CSV = os.path.join(SIMULATION_DIR, 'batch_status', f"{batch_index}_status.csv")
    LOCAL_BATCH_STATUS_CSV = os.path.join(LOCAL_DIR, 'batch_status',f"{batch_index}_status.csv")
    os.makedirs(os.path.dirname(BATCH_STATUS_CSV), exist_ok=True)
    os.makedirs(os.path.dirname(LOCAL_BATCH_STATUS_CSV), exist_ok=True)
    
    # Initialize the status file with headers
    with open(BATCH_STATUS_CSV, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["Asset ID", "Name","Status"])
        for row in batch:
            asset_id = row['assetid']
            asset_name = row['name'].replace(' ', '_').replace(',', '')
            writer.writerow([asset_id, asset_name,"Not Processed Yet"])
    
    logger.debug(f"BATCH {batch_index}: Processing {len(batch)} assets...")
    # Iterate through the batch and process each asset
    for row in batch:
        asset_id = row['assetid']
        asset_name = row['name'].replace(' ', '_').replace(',', '')
        feature_file = os.path.join(SIMULATION_DIR, "feature_files", f"{asset_id}_{asset_name}.json")

        # Update status to Processing
        update_status(BATCH_STATUS_CSV, asset_id, asset_name, "Processing")
        shutil.copy(BATCH_STATUS_CSV, LOCAL_BATCH_STATUS_CSV)
        
        logger.debug(f"BATCH {batch_index}: Starting processing asset {asset_id}...")
        try:
            run_uosimulation(SIMULATION_DIR, LOCAL_DIR,feature_file, batch_index)
            # Update status to Finished
            update_status(BATCH_STATUS_CSV, asset_id, asset_name, "Finished")

        except Exception as e:
            logger.error(f"BATCH {batch_index}: Failed to process asset {asset_id}: {str(e)}")
            # Update status to Failed
            update_status(BATCH_STATUS_CSV, asset_id, asset_name, "Failed")

        # Copy to local
        shutil.copy(BATCH_STATUS_CSV, LOCAL_BATCH_STATUS_CSV)
    
    
    # Clean up the batch simulation directory
    SIMULATION_DIR = os.path.join(SIMULATION_DIR, 'urbanopt_simulation')
    clean_batch_dir(SIMULATION_DIR)
    
    logger.info(f"\n{'='*47}\n"
    f"Batch {batch_index} finished processing.\n"
    f"Total assets processed: {len(batch)}\n"
    f"{'='*47}"
)



############################################################################################################
# Name: main()
# Description: This function is the entry point for the script. Used for testing purposes.
############################################################################################################
if __name__ == "__main__":
    batch = [
        {
            "assetid": "1",
            "name": "Building 1"
        },
        {
            "assetid": "2",
            "name": "Building 2"
        }
    ]
    
    SIMULATION_DIR = os.path.join(os.getcwd(), 'output')
    METADATA_CSV = os.path.join(os.getcwd(), 'metadata.csv')
    batch_index = 1
    
    run_batch(batch, SIMULATION_DIR, METADATA_CSV, batch_index)
    
    