import os
import glob
import time
import zipfile
import csv

from joblib import Parallel, delayed, parallel_backend
from .run_UOsim import run_batch
from scripts.helper import initialize_logger

inituo_logger = initialize_logger('Initialize UOSim')

############################################################################################################
# Name: prepare_record(SIMULATION_DIR, clean_report_flag, METADATA_CSV)
# Description: This function prepares the record for the simulation times. The simulation times are written to a
#   CSV file for data analysis.
############################################################################################################
def prepare_record(SIMULATION_DIR, clean_report_flag, METADATA_CSV):
    UOSIM_TIME_CSV = os.path.join(SIMULATION_DIR, "uosim_time.csv")
    
    # Read the existing CSV data
    with open(UOSIM_TIME_CSV, mode='r') as file:
        reader = csv.DictReader(file)
        data = list(reader)
    
    # Group assets by batch numbers
    inituo_logger.debug("Grouping assets by batch numbers...")
    batches = {}
    for row in data:
        batch = int(row['batch'])
        if batch not in batches:
            batches[batch] = []
        batches[batch].append(row)

    inituo_logger.debug(f"Total batches: {len(batches)}, Total assets: {len(data)}\n" 
                        f"Preparing to run simulations..."
    )
    
    # Run simulations in parallel
    try:
        with parallel_backend('loky', n_jobs=len(batches), verbose=10):
            Parallel()(delayed(run_batch)(batch, SIMULATION_DIR, clean_report_flag, METADATA_CSV, batch_index) for batch_index, batch in batches.items())
    except Exception as e:
        inituo_logger.error(f"Error running simulations: {e}")
        return


############################################################################################################
# Name: initialize_uo(SIMULATION_DIR,METADATA_CSV,feature_file_zip, clean_report_flag=False)
# Description: This function initializes the UrbanOpt simulation by creating the UrbanOpt project, copying the
#   weather files, and extracting the feature files. It then prepares the record for the simulation times.
#   The simulation times are written to a CSV file for data analysis.
############################################################################################################
def initialize_uo(SIMULATION_DIR,METADATA_CSV,feature_file_zip, clean_report_flag=False):
    start_time = time.time()
    
    
    OUTPUT_FEATURE_FILES_DIR = os.path.join(SIMULATION_DIR, "feature_files")
    UOSIMULATION_DIR = os.path.join(SIMULATION_DIR, 'urbanopt_simulation')
    os.makedirs(UOSIMULATION_DIR, exist_ok=True)
    
    # TODO: Adjust so that the feature file is located from the database server
    
    # Extract the feature files
    if feature_file_zip.endswith('.zip'):
        inituo_logger.debug(f"Extracting feature files from {feature_file_zip}")    
        with zipfile.ZipFile(feature_file_zip, 'r') as zip_ref:
            zip_ref.extractall(OUTPUT_FEATURE_FILES_DIR)
        
        # Get a list of all feature files in the directory
        feature_files = glob.glob(os.path.join(OUTPUT_FEATURE_FILES_DIR, "*.json"))
        total_feature_files = len(feature_files)
        inituo_logger.info(f"Total feature files: {total_feature_files}")
    else:
        pattern = os.path.join({OUTPUT_FEATURE_FILES_DIR}, f"{feature_file_zip}_*.json")
        feature_files = glob.glob(pattern)
        
        if feature_files:
            inituo_logger.info(f"Found feature file: {feature_files}")
        else:
            inituo_logger.error(f"No feature files found for asset ID: {feature_file_zip}")
            return

    # Update the CSV file with simulation times
    prepare_record(SIMULATION_DIR, clean_report_flag, METADATA_CSV)      

    end_time = time.time()
    duration_seconds = end_time - start_time

    # Calculate hours, minutes, and seconds
    hours = int(duration_seconds // 3600)
    minutes = int((duration_seconds % 3600) // 60)
    seconds = duration_seconds % 60
    
    inituo_logger.info(f"\n{'='*67}\n"
        "URBANOPT SIMULATION FINISHED\n"
        f"Completed after {hours} hours, {minutes} minutes, and {seconds:.2f} seconds.\n"
        f"{'='*67}")

############################################################################################################
# Name: main()
# Description: This function is the entry point for the script. Used for testing purposes.
############################################################################################################
if __name__ == "__main__":
    feature_file_zip = "app/powertwin-db/user_files/feature_files.zip"
    SIMULATION_DIR = "app/powertwin-db/user_files"
    METADATA_CSV = "app/powertwin-db/user_files/metadata.csv"
    clean_report_flag = False
    initialize_uo(SIMULATION_DIR,METADATA_CSV,feature_file_zip, clean_report_flag)
    
