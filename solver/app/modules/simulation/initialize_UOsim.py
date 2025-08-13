import os
import glob
import time
import zipfile
import subprocess
import shutil

from .run_UOsim import run_batch
from modules.utils import initialize_logger
from modules.utils.hpc_parallel import run_parallel_batches

logger = initialize_logger('Initialize UOSim')

MAPPER_FILE = os.path.join('upload', 'PowerTwin.rb')


            

############################################################################################################
# Name: prepare_record(SIMULATION_DIR,LOCAL_DIR,simulation_name)
# Description: This function prepares the record for the simulation by creating the UrbanOpt project and copying the mapper file.
#   It then runs the simulations in parallel.
#   The function returns the total number of batches and assets.
############################################################################################################
def prepare_record(SIMULATION_DIR, LOCAL_DIR, simulation_name, hpc_mode=False, shared_storage=None):
    from modules.diagnostics import get_asset_total, get_batch_total

    
    batches = get_batch_total(simulation_name)
    assets = get_asset_total(simulation_name=simulation_name)

    logger.debug(f"Total batches: {batches}, Total assets in database: {assets}\n" 
                        f"Preparing to run simulations..."
    )
    
    # Adjust paths for shared storage in HPC mode
    if hpc_mode and shared_storage:
        SIMULATION_DIR = os.path.join(shared_storage, os.path.basename(SIMULATION_DIR))
        LOCAL_DIR = os.path.join(shared_storage, 'local_work')
        os.makedirs(LOCAL_DIR, exist_ok=True)
        logger.info(f"HPC mode: Using shared storage at {SIMULATION_DIR}")
    
    UO_SIMULATION_DIR = os.path.join(SIMULATION_DIR,'urbanopt_simulation')
    MAPPER_DESTINATION = os.path.join(UO_SIMULATION_DIR, 'mappers')
    WEATHER_DESTINATION = os.path.join(UO_SIMULATION_DIR, 'weather')

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
    
    # Run simulations in parallel (HPC or local mode)
    try:
        batch_range = list(range(batches))
        run_parallel_batches(
            run_batch, 
            batch_range, 
            SIMULATION_DIR, 
            LOCAL_DIR, 
            simulation_name, 
            hpc_mode=hpc_mode, 
            shared_storage=shared_storage
        )
    except Exception as e:
        logger.error(f"Error running simulations: {e}")
        return

############################################################################################################
# Name: initialize_uo(SIMULATION_DIR,LOCAL_DIR, simulation_name)
# Description: This function initializes the UrbanOpt simulation by extracting the feature files from the zip file.
#   It then prepares the record for the simulation by creating the UrbanOpt project and copying the mapper file.
#   The function then runs the simulations in parallel.
#   The function returns the total number of batches and assets.
############################################################################################################
def initialize_uo(SIMULATION_DIR, LOCAL_DIR, simulation_name, hpc_mode=False, shared_storage=None):
    start_time = time.time()
    
    FEATURE_FILE_ZIP = os.path.join(SIMULATION_DIR, 'feature_files.zip')
    OUTPUT_FEATURE_FILES_DIR = os.path.join(SIMULATION_DIR, "feature_files")
    LOCAL_UOSIMULATION_DIR = os.path.join(LOCAL_DIR, 'urbanopt_simulation')
    os.makedirs(LOCAL_UOSIMULATION_DIR, exist_ok=True)
        
    # Extract the feature files
    if FEATURE_FILE_ZIP.endswith('.zip'):
        logger.debug(f"Extracting feature files from {FEATURE_FILE_ZIP}")    
        with zipfile.ZipFile(FEATURE_FILE_ZIP, 'r') as zip_ref:
            zip_ref.extractall(OUTPUT_FEATURE_FILES_DIR)
        
        # Get a list of all feature files in the directory
        feature_files = glob.glob(os.path.join(OUTPUT_FEATURE_FILES_DIR, "*.json"))
        total_feature_files = len(feature_files)
        logger.info(f"Total feature files extracted: {total_feature_files}")
    else:
        logger.error(f"No zip file found named: {FEATURE_FILE_ZIP}")
        return

    # Update the CSV file with simulation times
    prepare_record(SIMULATION_DIR, LOCAL_DIR, simulation_name, hpc_mode, shared_storage)      

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
    LOCAL_DIR = ""
    initialize_uo(SIMULATION_DIR,LOCAL_DIR,feature_file_zip)
    
