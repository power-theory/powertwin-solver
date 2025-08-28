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

MAPPER_FILE = os.path.join('upload', 'PowerTwin.rb')


            

############################################################################################################
# Name: prepare_record(SIMULATION_DIR,LOCAL_DIR,simulation_name)
# Description: This function prepares the record for the simulation by creating the UrbanOpt project and copying the mapper file.
#   It then runs the simulations in parallel.
#   The function returns the total number of batches and assets.
############################################################################################################
def prepare_record(SIMULATION_DIR, LOCAL_DIR, simulation_name, hpc_mode=False):
    from modules.diagnostics import get_asset_total, get_batch_total

    
    batches = get_batch_total(simulation_name)
    assets = get_asset_total(simulation_name=simulation_name)

    logger.debug(f"Total batches: {batches}, Total assets in database: {assets}\n" 
                        f"Preparing to run simulations..."
    )
    
    # # Adjust paths for shared storage in HPC mode
    # if hpc_mode and shared_storage:
    #     # Keep the original SIMULATION_DIR which includes data in the path
    #     # We need to ensure data is in the path
    #     if 'data' not in SIMULATION_DIR:
    #         logger.warning(f"data not found in simulation path: {SIMULATION_DIR}")
    #         if simulation_name in SIMULATION_DIR:
    #             SIMULATION_DIR = os.path.join(os.path.dirname(SIMULATION_DIR), simulation_name, 'data')
    #             logger.info(f"Adjusted SIMULATION_DIR to include data: {SIMULATION_DIR}")
        
    #     LOCAL_DIR = os.path.join(shared_storage, 'local_work')
    #     os.makedirs(LOCAL_DIR, exist_ok=True)
    #     logger.info(f"HPC mode: Using shared storage at {shared_storage}")
    
    UO_SIMULATION_DIR = os.path.join(SIMULATION_DIR,'urbanopt_simulation')
    MAPPER_DESTINATION = os.path.join(UO_SIMULATION_DIR, 'mappers')
    WEATHER_DESTINATION = os.path.join(UO_SIMULATION_DIR, 'weather')

    # Create PowerTwin UrbanOpt Project if it doesn't exist (it shouldnt)
    if not os.path.exists(UO_SIMULATION_DIR):
        logger.debug(f"Creating UrbanOpt project at {UO_SIMULATION_DIR}")

        # Make sure current directory is writable
        current_dir = os.getcwd()
        logger.debug(f"Current working directory: {current_dir}")
        
        # Create the UrbanOpt simulation directory with explicit permissions
        os.makedirs(UO_SIMULATION_DIR, exist_ok=True)
        os.chmod(UO_SIMULATION_DIR, 0o777)  # Full permissions
        
        # Create a temporary directory for UrbanOpt project creation
        temp_dir = os.path.join(os.environ.get('TMPDIR', '/tmp'), f'uo_project_{os.getpid()}')
        os.makedirs(temp_dir, exist_ok=True)
        os.chmod(temp_dir, 0o777)  # Full permissions
        
        # Use absolute path with the uo create command and change to parent directory first
        os.chdir(temp_dir)
        try:
            # Create project in temporary directory first
            logger.debug(f"Creating temporary UrbanOpt project in: {temp_dir}")
            result = subprocess.run("uo create -p .", 
                             shell=True, check=True, capture_output=True, text=True)
            logger.debug(f"UrbanOpt create output: {result.stdout}")
            
            # Copy project files to final destination
            logger.debug(f"Copying project files to {UO_SIMULATION_DIR}")
            for item in os.listdir(temp_dir):
                item_path = os.path.join(temp_dir, item)
                dest_path = os.path.join(UO_SIMULATION_DIR, item)
                if os.path.isdir(item_path):
                    shutil.copytree(item_path, dest_path, dirs_exist_ok=True)
                else:
                    shutil.copy2(item_path, dest_path)
            
            # Ensure mappers directory exists
            os.makedirs(MAPPER_DESTINATION, exist_ok=True)
            os.chmod(MAPPER_DESTINATION, 0o777)  # Full permissions
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to create UrbanOpt project: {e}")
            logger.error(f"Command output: {e.stdout}")
            logger.error(f"Command error: {e.stderr}")
            raise
        finally:
            # Clean up temporary directory
            shutil.rmtree(temp_dir, ignore_errors=True)
            # Change back to original directory
            os.chdir(current_dir)
            
        # Recreate weather directory with proper permissions
        if os.path.exists(WEATHER_DESTINATION):
            shutil.rmtree(WEATHER_DESTINATION)
        os.makedirs(WEATHER_DESTINATION, exist_ok=True)
        os.chmod(WEATHER_DESTINATION, 0o777)  # Full permissions

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
def initialize_uo(SIMULATION_DIR, LOCAL_DIR, simulation_name, hpc_mode=False, shared_storage=None):
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

    # Update the database with simulation times
    prepare_record(SIMULATION_DIR, LOCAL_DIR, simulation_name, hpc_mode)

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
    
