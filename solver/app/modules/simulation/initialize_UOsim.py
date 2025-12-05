import os
import glob
import time
import zipfile
import subprocess
import shutil

from .run_UOsim import run_batch
from modules.utils import initialize_logger
from modules.utils.hpc_parallel import run_parallel_batches

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Initialize UOSim', external_log_dir)

# Set the mapper file path based on the environment
if os.environ.get('SLURM_JOB_ID'):
    MAPPER_FILE = os.path.join('/solver', 'upload', 'PowerTwin.rb')
else:
    MAPPER_FILE = os.path.join('upload', 'PowerTwin.rb')


############################################################################################################
# Name: prepare_record(SIMULATION_DIR,LOCAL_DIR,simulation_name)
# Description: This function prepares the record for the simulation by creating the UrbanOpt project and copying the mapper file.
#   It then runs the simulations in parallel.
#   The function returns the total number of batches and assets.
############################################################################################################
def prepare_record(SIMULATION_DIR, LOCAL_DIR, simulation_name, hpc_mode=False):
    from modules.diagnostics import get_asset_total, get_batch_total, update_status
    

    
    batches = get_batch_total(simulation_name)
    assets = get_asset_total(simulation_name=simulation_name)

    logger.debug(f"Total batches: {batches}, Total assets in database: {assets}\n" 
                        f"Preparing to run simulations..."
    )
    
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

    
    update_status("Not Processed Yet",simulation_name=simulation_name)

    # In HPC mode, we'll just return the batch range and let the caller handle parallelization
    if hpc_mode: return list(range(batches))
        
    # Run simulations in parallel (local mode only - HPC mode is handled separately)
    try:
        logger.info(f"Running {batches} batches of simulations in local mode...")
        batch_range = list(range(batches))
        run_parallel_batches(
            run_batch, 
            batch_range, 
            SIMULATION_DIR, 
            LOCAL_DIR, 
            simulation_name, 
            hpc_mode=hpc_mode
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
def initialize_uo(SIMULATION_DIR, LOCAL_DIR, simulation_name, hpc_mode=False):
    start_time = time.time()
    

    FEATURE_FILE_ZIP = os.path.join(SIMULATION_DIR, 'feature_files.zip')
    OUTPUT_FEATURE_FILES_DIR = os.path.join(SIMULATION_DIR, "feature_files")
    LOCAL_UOSIMULATION_DIR = os.path.join(LOCAL_DIR, 'urbanopt_simulation')
    os.makedirs(LOCAL_UOSIMULATION_DIR, exist_ok=True)
        
    # Extract the feature files
    if FEATURE_FILE_ZIP.endswith('.zip'):
        logger.debug(f"Extracting feature files from {FEATURE_FILE_ZIP}")
        os.makedirs(OUTPUT_FEATURE_FILES_DIR, exist_ok=True)
        # Ensure write permissions
        os.chmod(OUTPUT_FEATURE_FILES_DIR, 0o777)
        
        with zipfile.ZipFile(FEATURE_FILE_ZIP, 'r') as zip_ref:
            zip_ref.extractall(OUTPUT_FEATURE_FILES_DIR)
        
        # Get a list of all feature files in the directory
        feature_files = glob.glob(os.path.join(OUTPUT_FEATURE_FILES_DIR, "*.json"))
        total_feature_files = len(feature_files)
        logger.info(f"Total feature files extracted: {total_feature_files}")
    else:
        logger.error(f"No zip file found named: {FEATURE_FILE_ZIP}")
        return

    # Prepare the database and setup UrbanOpt project
    batch_range = prepare_record(SIMULATION_DIR, LOCAL_DIR, simulation_name, hpc_mode)
    
    
    # In HPC mode, we return the batch range for external parallel execution
    if hpc_mode:
        logger.info(f"HPC mode active - returning batch range for external parallel execution")
        
        return batch_range

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
    
