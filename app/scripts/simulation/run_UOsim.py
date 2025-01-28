import os
import subprocess
import glob
import shutil
import time
import csv

from .clean_report import clean_single_report
from scripts.helper import initialize_logger

ruo_logger = initialize_logger('Run UOSim')

############################################################################################################
# Name: clean_batch_dir(BATCH_SIMULATION_DIR)
# Description: This function cleans the batch directory by deleting all directories and files except the run
#   directory.
############################################################################################################
def clean_batch_dir(BATCH_SIMULATION_DIR):
    # Define the directory to keep
    keep_dirs = {'run'}

    # Iterate through the files and directories in BATCH_SIMULATION_DIR
    for item in os.listdir(BATCH_SIMULATION_DIR):
        item_path = os.path.join(BATCH_SIMULATION_DIR, item)
        
        # Check if the item is a directory and not in the keep_dirs set
        if os.path.isdir(item_path) and item not in keep_dirs:
            shutil.rmtree(item_path)
            ruo_logger.debug(f"Deleted directory: {item_path}")
        
        # Check if the item is a file
        elif os.path.isfile(item_path):
            os.remove(item_path)
            ruo_logger.debug(f"Deleted file: {item_path}")

############################################################################################################
# Name: run_command(command)
# Description: This function runs a command in the shell and returns the time it takes to execute the command.
############################################################################################################
def run_command(command):
    start_time = time.time()
    try:
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        end_time = time.time()
        ruo_logger.info(f"Command '{command}' executed successfully.")
        ruo_logger.info(f"Output: {result.stdout}")
        return end_time - start_time
    except subprocess.CalledProcessError as e:
        end_time = time.time()
        ruo_logger.error(f"Command '{command}' failed with error: {e.stderr}")
        raise e

############################################################################################################
# Name: update_status(status_file, asset_id, status, message)
# Description: This function updates the status of an asset in the status file.
############################################################################################################
def update_status(status_file, asset_id, status, message):
    # Read the current status file
    with open(status_file, 'r', newline='') as f:
        reader = csv.reader(f)
        lines = list(reader)
    
    # Update the status for the given asset_id
    for line in lines:
        if line[0] == asset_id:
            line[1] = status
            line[2] = message
            break
    
    # Write the updated status file
    with open(status_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(lines)


############################################################################################################
# Name: run_uosimulation(SIMULATION_DIR,FEATURE_FILE_JSON, BATCH_SIMULATION_DIR, clean_report_flag, METADATA_CSV)
# Description: This function runs the UrbanOpt simulation for a single feature file. It creates the scenario,
#   runs the simulation, processes the simulation, and cleans the report if necessary. It records the time it
#   takes to run the simulation and process the simulation.
############################################################################################################
def run_uosimulation(SIMULATION_DIR,FEATURE_FILE_JSON, clean_report_flag, METADATA_CSV, batch_index):
    feature_start_time = time.time()
    
    feature_file_name = os.path.basename(FEATURE_FILE_JSON)
    asset_id = feature_file_name.split('_')[0]
    asset_name = '_'.join(feature_file_name.split('_')[1:]).replace('.json', '')

    ruo_logger.info(f"\n{'='*47}\n"
    f"Processing feature file: {feature_file_name}\n"
    f"Asset ID: {asset_id}\n"
    f"Asset Name: {asset_name}\n"
    f"Batch Index: {batch_index}\n"
    f"{'='*47}"
)
    
    #TODO: Adjust database paths to point to the actual database server
    DATABASE_DIR = os.path.join(os.getcwd(), 'app','powertwin-db')
    WEATHER_BASE_NAME = os.path.join(DATABASE_DIR, 'weather_files','USA_AZ_Phoenix-Sky.Harbor.Intl.AP.722780_TMY3')
    MAPPER_FILE = os.path.join(DATABASE_DIR, "PowerTwin.rb")
    
    # Set UrbanOpt Simulation Paths
    BATCH_SIMULATION_DIR = os.path.join(SIMULATION_DIR,'urbanopt_simulation', f'batch_{batch_index}')
    MAPPER_DESTINATION = os.path.join(BATCH_SIMULATION_DIR, "mappers")
    WEATHER_DESTINATION = os.path.join(BATCH_SIMULATION_DIR, "weather")

    # Create PowerTwin UrbanOpt Project if it doesn't exist
    if not os.path.exists(BATCH_SIMULATION_DIR):
        try:
            ruo_logger.debug(f"BATCH {batch_index}: Creating UrbanOpt project at {BATCH_SIMULATION_DIR}.")
            subprocess.run(f"uo create -p {BATCH_SIMULATION_DIR}", shell=True, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            ruo_logger.error(f"BATCH {batch_index}: Failed to create UrbanOpt project: {e.stderr}")
            raise e
        
        os.makedirs(MAPPER_DESTINATION, exist_ok=True)
        os.makedirs(WEATHER_DESTINATION, exist_ok=True)

        # Baseline ruby file should not be deleted, it is the parent file
        for rb_file in glob.glob(os.path.join(MAPPER_DESTINATION, "*.rb")):
                if os.path.basename(rb_file) != "Baseline.rb":
                    os.remove(rb_file)

        ruo_logger.debug(f"BATCH {batch_index}: Copying mapper file to {MAPPER_DESTINATION}")
        shutil.copy(MAPPER_FILE, MAPPER_DESTINATION)

        # TODO: Adjust so that copied weather files with the specified extensions are from the database server
        # Weather files should be selected dependent on geolocation of building which can be found in the feature file
        for ext in ["ddy", "stat", "epw"]:
            shutil.copy(f"{WEATHER_BASE_NAME}.{ext}", WEATHER_DESTINATION)


    # Move the feature file to the project directory
    try:
        ruo_logger.debug(f"BATCH {batch_index}: Moving feature file {FEATURE_FILE_JSON} to {BATCH_SIMULATION_DIR}")
        shutil.copy(FEATURE_FILE_JSON, BATCH_SIMULATION_DIR)
    except shutil.Error as e:
        ruo_logger.error(f"BATCH {batch_index}: Failed to move feature file: {e}")
        raise e

    # Create the scenario
    try:
        ruo_logger.info(f"BATCH {batch_index}: Creating scenario for feature file: {feature_file_name}")
        subprocess.run(f"uo create -s {BATCH_SIMULATION_DIR}/{feature_file_name}", shell=True, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        ruo_logger.error(f"BATCH {batch_index}: Failed to create scenario: {e.stderr}")
        raise e
    
    # Define the path to the scenario file
    SCENARIO_FILE_CSV = os.path.join(BATCH_SIMULATION_DIR, "powertwin_scenario.csv")
    FEATURE_FILE_JSON = os.path.join(BATCH_SIMULATION_DIR, feature_file_name)

    # Run the run and process commands and record their times
    # FEATURE FILE MUST BE IN THE SIMULATION DIRECTORY ALONG WITH THE SCENARIO FILE
    ruo_logger.info(f"BATCH {batch_index}: Running UrbanOpt simulation for: {asset_id}")
    uo_run_time = run_command(f"uo run -s {SCENARIO_FILE_CSV} -f {FEATURE_FILE_JSON}")
    
    ruo_logger.info(f"BATCH {batch_index}: Processing UrbanOpt simulation for: {asset_id}")
    uo_process_time = run_command(f"uo process -d -f {FEATURE_FILE_JSON} -s {SCENARIO_FILE_CSV}")
    total_time = uo_run_time + uo_process_time
    
    if(clean_report_flag):
        ruo_logger.debug(f"BATCH {batch_index}: Cleaning report for {asset_id}:{asset_name}...") 
        clean_single_report(SIMULATION_DIR,BATCH_SIMULATION_DIR, METADATA_CSV, asset_id)
        
    
    feature_end_time = time.time()
    feature_duration = feature_end_time - feature_start_time

    # Calculate hours, minutes, and seconds
    feature_hours = int(feature_duration // 3600)
    feature_minutes = int((feature_duration % 3600) // 60)
    feature_seconds = feature_duration % 60
    ruo_logger.info(f"BATCH {batch_index}: {asset_id} processed in {feature_hours} hours, {feature_minutes} minutes, and {feature_seconds:.2f} seconds.")
    
    # Update the CSV data with the results
    UOSIM_TIME_CSV = os.path.join(SIMULATION_DIR, "uosim_time.csv")

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
    

############################################################################################################
# Name: run_batch(batch, SIMULATION_DIR, clean_report_flag, METADATA_CSV, batch_index)
# Description: This function runs the UrbanOpt simulation for a batch of feature files. It creates the scenario,
#   runs the simulation, processes the simulation, and cleans the report if necessary. It records the time it
#   takes to run the simulation and process the simulation.
############################################################################################################
def run_batch(batch, SIMULATION_DIR, clean_report_flag, METADATA_CSV, batch_index):
    # Create a status file for the batch
    status_file = os.path.join(SIMULATION_DIR, 'batch_status', f"{batch_index}_status.csv")
    os.makedirs(os.path.dirname(status_file), exist_ok=True)
    
    # Initialize the status file with headers
    with open(status_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["Asset ID", "Name","Status"])
        for row in batch:
            asset_id = row['assetid']
            asset_name = row['name'].replace(' ', '_').replace(',', '')
            writer.writerow([asset_id, asset_name,"Not Processed Yet"])
    
    ruo_logger.debug(f"BATCH {batch_index}: Processing {len(batch)} assets...")
    for row in batch:
        asset_id = row['assetid']
        asset_name = row['name'].replace(' ', '_').replace(',', '')
        feature_file = os.path.join(SIMULATION_DIR, "feature_files", f"{asset_id}_{asset_name}.json")

        # Update status to Processing
        update_status(status_file, asset_id, asset_name, "Processing")
        
        ruo_logger.debug(f"BATCH {batch_index}: Starting processing asset {asset_id}...")
        try:
            run_uosimulation(SIMULATION_DIR, feature_file, clean_report_flag, METADATA_CSV, batch_index)
            # Update status to Finished
            update_status(status_file, asset_id, asset_name, "Finished")
        except Exception as e:
            ruo_logger.error(f"BATCH {batch_index}: Failed to process asset {asset_id}: {str(e)}")
            # Update status to Failed
            update_status(status_file, asset_id, asset_name, "Failed")
        
    # Clean up the batch simulation directory
    BATCH_SIMULATION_DIR = os.path.join(SIMULATION_DIR, 'urbanopt_simulation', f'batch_{batch_index}')
    clean_batch_dir(BATCH_SIMULATION_DIR)
    
    ruo_logger.info(f"\n{'='*47}\n"
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
    clean_report_flag = True
    METADATA_CSV = os.path.join(os.getcwd(), 'metadata.csv')
    batch_index = 1
    
    run_batch(batch, SIMULATION_DIR, clean_report_flag, METADATA_CSV, batch_index)
    
    