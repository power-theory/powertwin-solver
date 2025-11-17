import shutil
import os
import json
import datetime
import csv

from flask import request, jsonify, render_template, send_file

from modules.simulation import initialize_uo, create_featurefiles, stop_UOsimulation
from modules.diagnostics import read_simulation_status, simulation_recovery, create_table
from modules.utils import initialize_logger, send_error_to_mss

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Views', external_log_dir)


# Define the output directory for the simulation files
DATA_DIR = os.path.join('data')
LOCAL_DIR = os.path.join('powertwin-solver-pg', 'user_files')

def home():
    return render_template('base.html')

# 1. Simulation Managment

############################################################################################################
# Name: def start_simulation()
# Description: This function requires ASSET_GEOJSON, METADATA_CSV, config_data, and simulation_name, 
# location, and num_cores to start the simulation. Performs error checking and creates a directory 
# based on the given simulation name. Calls the create_featurefiles and initialize_uo functions to
# generate feature files and start the UrbanOpt simulation. This function parallelizes the 
# simulation after the feature files are created.
############################################################################################################
def start_simulation():
    logger.debug("Within start_simulation()")
    
    # Inputs
    ASSET_GEOJSON = request.files.get('asset_geojson_file')
    METADATA_CSV = request.files.get('metadata_csv_file')
    config_data = request.form.get('config_data')
    simulation_name = request.form.get('simulation_name')
    location = request.form.get('location')
    num_cores = int(request.form.get('num_cores', 1))
    hpc_mode = request.form.get('hpc_mode', 'false').lower() == 'true'
    shared_storage = request.form.get('shared_storage')
    keep_dirs = request.form.get('keep_dirs', 'false').lower() == 'true'

    # Set environment variable for keep directories flag
    if keep_dirs:
        os.environ['POWERTWIN_KEEP_DIRS'] = '1'
    else:
        os.environ.pop('POWERTWIN_KEEP_DIRS', None)

    # Reference the volume directory where the local files will be stored
    # TODO: Set as global variable for consistency across all LOCAL_DIR references 
    LOCAL_DIR = os.path.join('powertwin-solver-pg', 'user_files')
    
    # Error checking
    #TODO: Metadata csv should be optional since its ideally  only required for report cleaning
    if not ASSET_GEOJSON or not METADATA_CSV or not config_data or not simulation_name or not location or num_cores <= 0:
        logger.error("Error: missing or invalid parameter.")
        return jsonify({'error': 'missing or invalid parameter'}), 400
    
    # HPC mode validation
    if hpc_mode and not shared_storage:
        logger.error("Error: shared_storage is required when hpc_mode is enabled.")
        return jsonify({'error': 'shared_storage is required when hpc_mode is enabled'}), 400
    
    # Define and create Simulation directory (container) and Local directory (saved on host)
    # Local directory stores all necessary files for recovery and completed asset files
    # Simulation directory contains all files necessary for the simulation
    SIMULATION_DIR = os.path.join(DATA_DIR, f'{simulation_name}')
    LOCAL_DIR = os.path.join(LOCAL_DIR, f'{simulation_name}')
    if os.path.exists(SIMULATION_DIR) or os.path.exists(LOCAL_DIR):
        logger.error("Error: Simulation name already exists.")
        return jsonify({'error': 'Simulation name already exists.'}), 400
    
    os.makedirs(SIMULATION_DIR, exist_ok=True)
    os.makedirs(LOCAL_DIR, exist_ok=True)
    logger.info(f"Upload directory: {SIMULATION_DIR}")
    logger.info(f"Local directory: {LOCAL_DIR}")
    
    # Define and save the paths for the uploaded files in Local directory
    asset_geojson_path = os.path.join(LOCAL_DIR, f'{simulation_name}_asset.geojson')
    metadata_csv_path = os.path.join(LOCAL_DIR, f'{simulation_name}_metadata.csv')
    config_json_path = os.path.join(LOCAL_DIR, f'{simulation_name}_config.json')

    ASSET_GEOJSON.save(asset_geojson_path)
    METADATA_CSV.save(metadata_csv_path)
    with open(config_json_path, 'w') as config_file:
        json.dump(json.loads(config_data), config_file)

    # Call the create_feature_files and initialize_uo to run the simulation and 
    # delete the simulation directory (container)
    try:
        create_table()
        logger.debug("Calling create_feature_files from start_simulation()")
        create_featurefiles(SIMULATION_DIR, LOCAL_DIR, asset_geojson_path, metadata_csv_path, config_json_path, num_cores, location, simulation_name, hpc_mode)
        logger.debug("Exited create_feature_files to start_simulation()")
        
        logger.debug("Calling initialize_uo from start_simulation()")
        initialize_uo(SIMULATION_DIR, LOCAL_DIR, simulation_name, hpc_mode)
        logger.debug("Exited initialize_uo to start_simulation()")
        
        logger.debug("Deleting simulation directory, within the container")
        get_logs()
        shutil.rmtree(SIMULATION_DIR)
    except Exception as e:
        logger.error(f"Exception: {str(e)}")
        send_error_to_mss('start_simulation', str(e))
        return jsonify({'error': str(e)}), 500
    
    logger.debug("start_simulation() ran successfully")
    return jsonify({'confirmation': f'Simulation "{simulation_name}" ran successfully'})

############################################################################################################
# Name: def autorun_simulation()
# Description: This function reads the simulation.json file and starts the simulation based on the given parameters.
# Calls the create_featurefiles and initialize_uo functions to generate feature files and start the UrbanOpt simulation.
# This function parallelizes the simulation after the feature files are created.
############################################################################################################
def autorun_simulation():
    logger.debug("Within autorun_simulation()")
    
    # Reference the volume directory where the local files will be stored
    # TODO: Set as global variable for consistency across all LOCAL_DIR references 
    LOCAL_DIR = os.path.join('powertwin-solver-pg', 'user_files')


    SIMULATION_JSON = os.path.join('upload', 'simulation.json')
    
    # Error checking
    if not os.path.exists(SIMULATION_JSON):
        logger.error("Error: simulation.json file not found.")
        return jsonify({'error': 'simulation.json file not found.'}), 404
    
    
    # Get the parameters from the simulation.json file
    with open(SIMULATION_JSON, 'r') as file:
        data = json.load(file)
    
    simulation_name = data.get('simulation_name')
    asset_geojson_path = data.get('asset_geojson_path')
    metadata_csv_path = data.get('metadata_csv_path')
    config_json_path = data.get('config_json_path')
    location = data.get('location')
    num_cores = data.get('num_cores', 1)

    # Error checking
    #TODO: Metadata csv should be optional since ideally it should only required for report cleaning
    if not simulation_name or not asset_geojson_path or not metadata_csv_path or not config_json_path or not location or num_cores <= 0:
        logger.error("Error: Missing required fields in simulation.json")
        return jsonify({'error': 'Missing required fields in simulation.json'}), 400

    # Define and create Simulation directory (container) and Local directory (saved on host)
    # Local directory stores all necessary files for recovery and completed asset files
    # Simulation directory contains all files necessary for the simulation
    SIMULATION_DIR = os.path.join(DATA_DIR, f'{simulation_name}')
    LOCAL_DIR = os.path.join(LOCAL_DIR, f'{simulation_name}')
    if os.path.exists(SIMULATION_DIR) or os.path.exists(LOCAL_DIR):
        logger.error("Error: Simulation name already exists.")
        return jsonify({'error': 'Simulation name already exists.'}), 400
    
    os.makedirs(SIMULATION_DIR, exist_ok=True)
    os.makedirs(LOCAL_DIR, exist_ok=True)
    logger.info(f"Upload directory: {SIMULATION_DIR}")
    

    # Copy the files to the Local directory
    ASSET_GEOJSON = os.path.join(LOCAL_DIR, f'{simulation_name}_asset.geojson')
    METADATA_CSV = os.path.join(LOCAL_DIR, f'{simulation_name}_metadata.csv')
    CONFIG_JSON = os.path.join(LOCAL_DIR, f'{simulation_name}_config.json')
    
    shutil.copy(asset_geojson_path, ASSET_GEOJSON)
    shutil.copy(metadata_csv_path, METADATA_CSV)
    shutil.copy(config_json_path, CONFIG_JSON)

    # Call the create_feature_files and initialize_uo to run the simulation and 
    # delete the simulation directory (container)
    try:
        create_table()
        
        # Extract HPC-related parameters from the simulation.json
        hpc_mode = data.get('hpc_mode', False)
        shared_storage = data.get('shared_storage', None)
        
        logger.debug(f"HPC Mode: {hpc_mode}, Shared Storage: {shared_storage}")
        
        logger.debug("Calling create_feature_files from start_simulation_from_json()")
        create_featurefiles(SIMULATION_DIR, LOCAL_DIR, ASSET_GEOJSON, METADATA_CSV, CONFIG_JSON, num_cores, location, simulation_name, hpc_mode)
        logger.debug("Exited create_feature_files to start_simulation_from_json()")
                
        logger.debug("Calling initialize_uo from start_simulation_from_json()")
        initialize_uo(SIMULATION_DIR, LOCAL_DIR, simulation_name, hpc_mode)
        logger.debug("Exited initialize_uo to start_simulation_from_json()")
        
        logger.debug("Deleting simulation directory, within the container")
        get_logs()
        shutil.rmtree(SIMULATION_DIR)
        return jsonify({'message': 'Simulation completed successfully.'}), 200
    except Exception as e:
        logger.error(f"Exception: {str(e)}")
        send_error_to_mss('autorun_simulation', str(e))
        return jsonify({'error': f"Simulation failed: {str(e)}"}), 500

############################################################################################################
# Name: def stop_simulation()
# Description: This function stops the UrbanOpt simulation.
# Calls the stop_UOsimulation function to aggressively stop the simulation.
# WARNING: KILLS ALL PIDS WITHIN THE CONTAINER BESIDES ANY WITH app.py
############################################################################################################
def stop_simulation():
    logger.debug("Within stop_simulation()")
    # Stop the simulation
    try:
        logger.debug("Stopping the simulation")
        stop_UOsimulation()

        return jsonify({'message': 'Simulation stopped successfully'}), 200
    except Exception as e:
        logger.error(f"Exception while stopping the simulation: {str(e)}")
        send_error_to_mss('stop_simulation', str(e))
        return jsonify({'error': str(e)}), 500


############################################################################################################
# Name: def get_simulation_status()
# Description: This function reads the simulation status files and logs the status of the simulation.
# Calls the read_simulation_status function to read the simulation status files.
############################################################################################################ 
def get_simulation_status(simulation_name):
    logger.debug("Within simulation_status()")
    
    
    # Parameter checking
    batch_id = request.args.get('batch_id', default=None, type=int)

    if simulation_name is None:
        logger.error("Error: Simulation name is required.")
        return jsonify({'error': 'Simulation name is required.'}), 400
    
    # Read and log the simulation status files
    try:
        logger.debug(f"Entering read_simulation_status() from simulation_status() with batch_id={batch_id}")
        read_simulation_status(simulation_name, batch_id)
        return jsonify({'message': 'Simulation status files read successfully'}), 200
    except Exception as e:
        logger.error(f"Exception while reading simulation status files: {str(e)}")
        send_error_to_mss('get_simulation_status', str(e))
        return jsonify({'error': str(e)}), 500

############################################################################################################
# Name: def delete_simulation()
# Description: This function deletes the simulation directory based on the given simulation name.
# Calls the shutil.rmtree function to remove the simulation directory.
############################################################################################################
def delete_simulation(simulation_name):
    logger.debug("Within delete_simulation()")
    
    # Check param
    
    if simulation_name is None:
        logger.error("Error: Simulation name is required.")
        return jsonify({'error': 'Simulation name is required.'}), 400

    SIMULATION_DIR = os.path.join(DATA_DIR, simulation_name)    
    if not os.path.exists(SIMULATION_DIR):
        logger.error("Simulation status directory not found")
        return jsonify({'error': 'Simulation status directory not found'}), 404
    
    # Delete simulation directory
    try:
        shutil.rmtree(SIMULATION_DIR)
        logger.info(f"Simulation directory {SIMULATION_DIR} deleted successfully.")
        return jsonify({'message': f'Simulation directory {SIMULATION_DIR} deleted successfully.'}), 200
    except Exception as e:
        logger.error(f"Exception while trying to delete simulation: {str(e)}")
        send_error_to_mss('delete_simulation', str(e))
        return jsonify({'error': str(e)}), 500



# 2. Model and Configuration Management

############################################################################################################
# Name: def get_asset_config()
# Description: This function reads the feature files and returns the configuration of the asset.
# Searches for the feature file based on the asset ID and simulation name.
# Returns the feature file as a response. Available in the request_files directory.
############################################################################################################
def get_asset_config(simulation_name, asset_id):
    logger.debug("Within get_asset_config()")
    
    if not asset_id or not simulation_name:
        logger.error("Error: Asset ID and Simulation Name are required")
        return jsonify({'error': 'Asset ID and Simulation Name are required'}), 400
           
    try:
        # Search to see if user_files directory exists, so that we can search for the feature file
        SIMULATION_DIR = os.path.join(LOCAL_DIR, f'{simulation_name}')
        if not os.path.exists(SIMULATION_DIR):
            logger.error("Simulation directory does not exist")
            return jsonify({'error': 'Simulation directory does not exist'}), 404

        # Search for feature file dir
        FEATURE_FILE_DIR = os.path.join(SIMULATION_DIR, 'feature_files')
        if not os.path.exists(FEATURE_FILE_DIR):
            logger.error(F"{FEATURE_FILE_DIR} directory found")
            return jsonify({'error': 'No feature files directory found'}), 404

        # Search for the asset ID in the feature files
        logger.debug(f"Searching for feature file in {FEATURE_FILE_DIR}")
        for file_name in os.listdir(FEATURE_FILE_DIR):
            if file_name.startswith(f"{asset_id}_") and file_name.endswith('.json'):
                file_path = os.path.join(FEATURE_FILE_DIR, file_name)
                
                # Define the path to save the requested configuration file
                DOWNLOAD_DIR = os.path.join(LOCAL_DIR, 'requested_files')
                os.makedirs(DOWNLOAD_DIR, exist_ok=True)
                requested_file_path = os.path.join(DOWNLOAD_DIR, file_name)
                
                # Copy the configuration file to the requested_files directory
                shutil.copy(file_path, requested_file_path)
                
                response = send_file(requested_file_path, as_attachment=True)
                return response
    
        logger.error(f"No feature file found for asset ID: {asset_id}")
        return jsonify({'error': f'No feature file found for asset ID: {asset_id}'}), 404
    
    except Exception as e:
        logger.error(f"Exception: {str(e)}")
        send_error_to_mss('get_asset_config', str(e))
        return jsonify({'error': str(e)}), 500


############################################################################################################
# Name: def get_simulation_data()
# Description: This function reads the simulation statistics and returns the statistics of the simulation.
# Calls the get_asset_stats function to get the asset statistics from the database.
# Returns the statistics as a CSV file in the requested_files directory.
############################################################################################################
def get_simulation_data():
    from modules.diagnostics import get_asset_stats
    
    logger.debug("Within get_simulation_data()")
    csv_path = None  # Define outside try block so it's available in except
    
    try:
        # Get asset statistics from the database
        assets_list, filename = get_asset_stats()
        
        if not assets_list:
            return jsonify({'error': 'No assets found for the specified simulation'}), 404
            
        # Create directory for requested files if it doesn't exist
        requested_files_dir = os.path.join(LOCAL_DIR, 'requested_files')
        os.makedirs(requested_files_dir, exist_ok=True)
        
        # Define CSV file path
        csv_path = os.path.join(requested_files_dir, filename)
        
        # Write data to CSV file
        with open(csv_path, 'w', newline='') as csvfile:
            # Create CSV writer
            csvwriter = csv.writer(csvfile)
            
            # Write header row
            csvwriter.writerow([
                'Asset ID', 'Batch', 'Order Rank', 'Simulation Name', 'Location', 
                'Floor Area', 'Number of Stories', 'Complexity', 'UO Run Time', 
                'UO Process Time', 'Asset Name', 'Subtype','Status', 'Total Time'
            ])
            
            # Write data rows - extract values from dictionaries in proper order
            for asset in assets_list:
                csvwriter.writerow([
                    asset['asset_id'],
                    asset['batch'],
                    asset['order_rank'],
                    asset['simulation_name'],
                    asset['location'],
                    asset['floor_area'],
                    asset['number_of_stories'],
                    asset['complexity'],
                    asset['uorun_time'],
                    asset['uoprocess_time'],
                    asset['asset_name'],
                    asset['subtype'],
                    asset['status'],
                    asset['total_time']
                ])
        
        logger.info(f"Successfully created CSV file with {len(assets_list)} assets at {csv_path}")
        
        # Return success response with file path
        return jsonify({
            'message': 'Simulation stats exported successfully',
            'file_path': csv_path,
            'asset_count': len(assets_list)
        }), 200  # Fixed status code
        
    except Exception as e:
        logger.error(f"Exception: {str(e)}")
        if csv_path and os.path.exists(csv_path):
            os.remove(csv_path)  # Use remove() for files, not rmdir()
        return jsonify({'error': str(e)}), 500
    

# 3. Diagnostics and Logs

############################################################################################################
# Name: def recovery()
# Description: This function recovers a corrupted simulation by removing assets that are "Processing" or "Not Processed Yet"
#   from the feature_files directory and re-running the UO simulation.
# Calls the simulation_recovery function to recover the corrupted simulation.
############################################################################################################
def recovery():
    
    logger.debug("Within recovery()")
    corrupted_simulation_name = request.form.get('corrupted_simulation_name')
    recover_simulation_name = request.form.get('recover_simulation_name')
    batch_id = request.form.get('recover_batch_id', default=None, type=int)
    num_cores = int(request.form.get('recover_num_cores', 1))
    keep_dirs = request.form.get('keep_dirs', 'false').lower() == 'true'

    # Set environment variable for keep directories flag
    if keep_dirs:
        os.environ['POWERTWIN_KEEP_DIRS'] = '1'
    else:
        os.environ.pop('POWERTWIN_KEEP_DIRS', None)

    if not corrupted_simulation_name:
        logger.error("Error: Simulation name is required.")
        return jsonify({'error': 'Simulation name is required.'}), 400

    if not recover_simulation_name:
        logger.error("Error: Recover simulation name is required.")
        return jsonify({'error': 'Recover simulation name is required.'}), 400
    
    # TODO: Set as global variable for consistency across all LOCAL_DIR references 
    LOCAL_DIR = os.path.join('powertwin-solver-pg', 'user_files')
    
    CORRUPTED_SIMULATION_DIR = os.path.join(LOCAL_DIR, corrupted_simulation_name)
    if not os.path.exists(CORRUPTED_SIMULATION_DIR):
        logger.error("Simulation directory not found")
        return jsonify({'error': 'Simulation directory not found'}), 404

    RECOVERY_DIR_LOCAL = os.path.join(LOCAL_DIR, f'{recover_simulation_name}')
    RECOVERY_DIR_CONTAINER = os.path.join(DATA_DIR, f'{recover_simulation_name}')
    if os.path.exists(RECOVERY_DIR_LOCAL) or os.path.exists(RECOVERY_DIR_CONTAINER):
        logger.debug("Recovery directory already exists")
        return jsonify({'error': 'Recovery directory already exists'}), 400

    os.makedirs(RECOVERY_DIR_CONTAINER, exist_ok=True)
    os.makedirs(RECOVERY_DIR_LOCAL, exist_ok=True)

    metadata_csv_path = os.path.join(CORRUPTED_SIMULATION_DIR, f'{corrupted_simulation_name}_metadata.csv')
    geojson_path = os.path.join(CORRUPTED_SIMULATION_DIR, f'{corrupted_simulation_name}_asset.geojson')
    config_path = os.path.join(CORRUPTED_SIMULATION_DIR, f'{corrupted_simulation_name}_config.json')

    # Only this file requires error handling
    if not os.path.exists(metadata_csv_path):
        logger.error("Metadata CSV file not found in the corrupted simulation directory")
        return jsonify({'error': 'Metadata CSV file not found in the corrupted simulation directory'}), 404

    # Copy and rename the metadata CSV, geojson, and config file to the recovery directory
    new_metadata_csv_path = os.path.join(RECOVERY_DIR_LOCAL, f'{recover_simulation_name}_metadata.csv')
    new_geojson_name_path = os.path.join(RECOVERY_DIR_LOCAL, f'{recover_simulation_name}_asset.geojson')
    new_config_name_path = os.path.join(RECOVERY_DIR_LOCAL, f'{recover_simulation_name}_config.json')
    
    shutil.copy(metadata_csv_path, new_metadata_csv_path)
    shutil.copy(geojson_path, new_geojson_name_path)
    shutil.copy(config_path, new_config_name_path)
    
    try:
        
        logger.debug("Calling simulation_recovery from recovery()")
        simulation_recovery(RECOVERY_DIR_CONTAINER, RECOVERY_DIR_LOCAL, CORRUPTED_SIMULATION_DIR, corrupted_simulation_name, recover_simulation_name, num_cores, batch_id)
        
        logger.debug("Exited simulation_recovery to recovery(), deleting recovery directory within the container")
        get_logs()
        shutil.rmtree(RECOVERY_DIR_CONTAINER)
        return jsonify({'message': 'Simulation recovery process completed successfully'}), 200


    except Exception as e:
        logger.error(f"Exception during simulation recovery: {str(e)}")
        send_error_to_mss('recovery', str(e))
        return jsonify({'error': str(e)}), 500



############################################################################################################
# Name: def get_logs()
# Description: This function zips the logs directory and sends the zip file as a response for download.
# Supports HPC mode by using shared storage when specified.
############################################################################################################
def get_logs(hpc_mode=False, shared_storage=None):
    logger.debug("Within get_logs()")
    
    # Set up paths differently based on whether we're in HPC mode
    if hpc_mode and shared_storage:
        logger.debug(f"Running in HPC mode with shared storage: {shared_storage}")
        # In HPC mode, we'll write logs to the shared storage directory
        REQUESTED_FILES_DIR = os.path.join(shared_storage, 'logs', 'requested_files')
        LOGS_DIR = os.path.join(shared_storage, 'logs')
    else:
        # Regular container mode
        REQUESTED_FILES_DIR = os.path.join(LOCAL_DIR, 'requested_files')
        LOGS_DIR = os.path.join('logs')
    
    # Create directories if they don't exist
    os.makedirs(REQUESTED_FILES_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)
    
    REQUESTED_LOG_FILE = os.path.join(REQUESTED_FILES_DIR, 'dev_logs.txt')
    LOG_FILE = os.path.join(LOGS_DIR, 'dev_logs.txt')
    
    if not os.path.exists(LOG_FILE):
        logger.warning(f"Log file does not exist at {LOG_FILE}, creating empty file")
        # Create empty log file if it doesn't exist
        with open(LOG_FILE, 'w') as file:
            file.write(f"Log file created at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    try:
        with open(LOG_FILE, 'r') as file:
            logs = file.read()
        # Save the log file to the requested_files directory
        with open(REQUESTED_LOG_FILE, 'w') as file:
            file.write(logs)

        logger.debug(f"Log file saved to {REQUESTED_LOG_FILE}")
    except Exception as e:
        logger.error(f"Exception while reading/writing log file: {str(e)}")
        if not hpc_mode:  # Only return response in non-HPC mode
            return jsonify({'error': str(e)}), 500
    
    # Only try to render template in non-HPC mode
    if not hpc_mode:
        with open(REQUESTED_LOG_FILE, 'r') as file:
            logs = file.read()
        # Render the logs in the template
        return render_template('logs.html', logs=logs)


############################################################################################################
# Name: def log_message()
# Description: This function logs a message to the dev_logs.txt file.
# Calls the log_message function to log a message to the dev_logs.txt file.
############################################################################################################
def log_message():
    data = request.get_json()
    message = data.get('message')
    log_type = data.get('type', 'log')
    
    # Create a timestamp for the log entry
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Define the log file path
    log_txt = os.path.join('logs','dev_logs.txt')
    os.makedirs(os.path.dirname(log_txt), exist_ok=True)

    # Append the log entry to the log file
    with open(log_txt, 'a') as log_file:
        log_file.write(f"[{timestamp}] [{log_type.upper()}] {message}\n")

    return jsonify({'status': 'success', 'log_file': log_txt}), 200


