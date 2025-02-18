import os
import glob
import time
import zipfile
import csv
import subprocess
import shutil

from joblib import Parallel, delayed, parallel_backend
from .run_UOsim import run_batch
from scripts.helper import initialize_logger

logger = initialize_logger('Initialize UOSim')

############################################################################################################
# Name: prepare_record(SIMULATION_DIR,LOCAL_DIR)
# Description: This function prepares the record for the simulation times. The simulation times are written to a
#   CSV file for data analysis.
############################################################################################################
def prepare_record(SIMULATION_DIR,LOCAL_DIR):
    
    # Read the existing uosim time CSV data
    UOSIM_TIME_CSV = os.path.join(SIMULATION_DIR, "uosim_time.csv")
    with open(UOSIM_TIME_CSV, mode='r') as file:
        reader = csv.DictReader(file)
        data = list(reader)
    
    # Group assets by batch numbers
    logger.debug("Grouping assets by batch numbers...")
    batches = {}
    for row in data:
        batch = int(row['batch'])
        if batch not in batches:
            batches[batch] = []
        batches[batch].append(row)

    logger.debug(f"Total batches: {len(batches)}, Total assets: {len(data)}\n" 
                        f"Preparing to run simulations..."
    )
     
    UO_SIMULATION_DIR = os.path.join(SIMULATION_DIR,'urbanopt_simulation')

    MAPPER_FILE = os.path.join('urbanopt', "PowerTwin.rb")
    MAPPER_DESTINATION = os.path.join(UO_SIMULATION_DIR, "mappers")
    WEATHER_DESTINATION = os.path.join(UO_SIMULATION_DIR, "weather")
    


    # Create PowerTwin UrbanOpt Project if it doesn't exist (it shouldnt)
    if not os.path.exists(UO_SIMULATION_DIR):
        logger.debug(f"Creating UrbanOpt project at {UO_SIMULATION_DIR}")
        subprocess.run(f"uo create -p {UO_SIMULATION_DIR}", shell=True, check=True, capture_output=True, text=True)

        
        os.makedirs(MAPPER_DESTINATION, exist_ok=True)
        
        # Deleting the pre loaded content of the weather dir
        shutil.rmtree(WEATHER_DESTINATION)

        # WARNING: Baseline ruby file should never be deleted, it is the parent file
        for rb_file in glob.glob(os.path.join(MAPPER_DESTINATION, "*.rb")):
                if os.path.basename(rb_file) != "Baseline.rb":
                    os.remove(rb_file)

        # Adding custom mapper (map be modified to include more features)
        logger.debug(f"Copying mapper file to {MAPPER_DESTINATION}")
        shutil.copy(MAPPER_FILE, MAPPER_DESTINATION)
    
    # Run simulations in parallel
    try:
        with parallel_backend('loky', n_jobs=len(batches), verbose=10):
            Parallel()(delayed(run_batch)(batch, SIMULATION_DIR,LOCAL_DIR, batch_index) for batch_index, batch in batches.items())
    except Exception as e:
        logger.error(f"Error running simulations: {e}")
        return


############################################################################################################
# Name: initialize_uo(SIMULATION_DIR,feature_file_zip)
# Description: This function initializes the UrbanOpt simulation by creating the UrbanOpt project, copying the
#   weather files, and extracting the feature files. It then prepares the record for the simulation times.
#   The simulation times are written to a CSV file for data analysis.
############################################################################################################
def initialize_uo(SIMULATION_DIR,LOCAL_DIR,feature_file_zip):
    start_time = time.time()
    
    
    OUTPUT_FEATURE_FILES_DIR = os.path.join(SIMULATION_DIR, "feature_files")
    LOCAL_UOSIMULATION_DIR = os.path.join(LOCAL_DIR, 'urbanopt_simulation')
    os.makedirs(LOCAL_UOSIMULATION_DIR, exist_ok=True)
        
    # Extract the feature files
    if feature_file_zip.endswith('.zip'):
        logger.debug(f"Extracting feature files from {feature_file_zip}")    
        with zipfile.ZipFile(feature_file_zip, 'r') as zip_ref:
            zip_ref.extractall(OUTPUT_FEATURE_FILES_DIR)
        
        # Get a list of all feature files in the directory
        feature_files = glob.glob(os.path.join(OUTPUT_FEATURE_FILES_DIR, "*.json"))
        total_feature_files = len(feature_files)
        logger.info(f"Total feature files: {total_feature_files}")
    else:
        pattern = os.path.join({OUTPUT_FEATURE_FILES_DIR}, f"{feature_file_zip}_*.json")
        feature_files = glob.glob(pattern)
        
        if feature_files:
            logger.info(f"Found feature file: {feature_files}")
        else:
            logger.error(f"No feature files found for asset ID: {feature_file_zip}")
            return

    # Update the CSV file with simulation times
    prepare_record(SIMULATION_DIR, LOCAL_DIR)      

    end_time = time.time()
    duration_seconds = end_time - start_time

    # Calculate hours, minutes, and seconds
    hours = int(duration_seconds // 3600)
    minutes = int((duration_seconds % 3600) // 60)
    seconds = duration_seconds % 60
    
    logger.info(f"\n{'='*67}\n"
        "URBANOPT SIMULATION FINISHED\n"
        f"Completed after {hours} hours, {minutes} minutes, and {seconds:.2f} seconds.\n"
        f"{'='*67}")

############################################################################################################
# Name: main()
# Description: This function is the entry point for the script. Used for testing purposes.
############################################################################################################
if __name__ == "__main__":
    feature_file_zip = "powertwin-solver-pg/user_files/feature_files.zip"
    SIMULATION_DIR = "powertwin-solver-pg/user_files"
    initialize_uo(SIMULATION_DIR,feature_file_zip)
    
