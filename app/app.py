import shutil
import os
import json
import datetime
import psycopg

from flask import Flask, request, jsonify, render_template, send_file
from flask_cors import CORS

from scripts.simulation import initialize_uo, create_featurefiles, stop_UOsimulation
from scripts.diagnostics import read_simulation_status, simulation_recovery
from scripts.helper import initialize_logger, send_error_to_mss

username = "postgres"
password = "admin"

logger = initialize_logger('App Main')
server = Flask(__name__)
CORS(server)

conn = psycopg.connect(
    dbname='powertwin',
    user=username,
    password=password,
    host='powertwin-solver-pg',  # This must match the hostname of the container
    port='5432'
)

# Define the output directory for the simulation files
USER_FILES_DIR = os.path.join('data', 'user_files')
LOCAL_DIR = os.path.join('powertwin-solver-pg', 'user_files')


@server.route('/')
def home():
    return render_template('base.html')


# @server.route('/test_db', methods=['GET'])
# def test_db():
#     try:
#         with conn.cursor() as cur:
#             cur.execute("SELECT 1 AS test;")
#             result = cur.fetchone()
#             if result and result['test'] == 1:
#                 return jsonify({'message': 'Database connection is working correctly.'}), 200
#             else:
#                 return jsonify({'error': 'Unexpected result from database query.'}), 500
#     except Exception as e:
#         logger.error(f"Exception while testing database connection: {str(e)}")
#         return jsonify({'error': str(e)}), 500


# 1. Simulation Managment

############################################################################################################
# Name: def start_simulation()
# Description: This function requires ASSET_GEOJSON, METADATA_CSV, config_data, and simulation_name, 
# location, and num_cores to start the simulation. Performs error checking and creates a directory 
# based on the given simulation name. Calls the create_featurefiles and initialize_uo functions to
# generate feature files and start the UrbanOpt simulation. This function parallelizes the 
# simulation after the feature files are created.
############################################################################################################
@server.route('/api/simulation/start', methods=['POST'])
def start_simulation():
    logger.debug("Within start_simulation()")
    
    # Inputs
    ASSET_GEOJSON = request.files.get('asset_geojson_file')
    METADATA_CSV = request.files.get('metadata_csv_file')
    config_data = request.form.get('config_data')
    simulation_name = request.form.get('simulation_name')
    location = request.form.get('location')
    num_cores = int(request.form.get('num_cores', 1))

    # Reference the volume directory where the local files will be stored
    # TODO: Set as global variable for consistency across all LOCAL_DIR references 
    LOCAL_DIR = os.path.join('powertwin-solver-pg', 'user_files')
    
    # Error checking
    if not ASSET_GEOJSON or not METADATA_CSV or not config_data or not simulation_name or not location or num_cores <= 0:
        logger.error("Error: missing or invalid parameter.")
        return jsonify({'error': 'missing or invalid parameter'}), 400
    
    # Define and create Simulation directory (container) and Local directory (saved on host)
    # Local directory stores all necessary files for recovery and completed asset files
    # Simulation directory contains all files necessary for the simulation
    SIMULATION_DIR = os.path.join(USER_FILES_DIR, f'{simulation_name}')
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
        logger.debug("Calling create_feature_files from start_simulation()")
        create_featurefiles(SIMULATION_DIR, LOCAL_DIR, asset_geojson_path, metadata_csv_path, config_json_path, num_cores, location)
        logger.debug("Exited create_feature_files to start_simulation()")
        
        featurefile_zip_path = os.path.join(SIMULATION_DIR,'feature_files.zip')
        
        logger.debug("Calling initialize_uo from start_simulation()")
        initialize_uo(SIMULATION_DIR, LOCAL_DIR,metadata_csv_path,featurefile_zip_path, clean_report_flag=True)
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
@server.route('/api/simulation/autorun_simulation', methods=['POST'])
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
    if not simulation_name or not asset_geojson_path or not metadata_csv_path or not config_json_path or not location or num_cores <= 0:
        logger.error("Error: Missing required fields in simulation.json")
        return jsonify({'error': 'Missing required fields in simulation.json'}), 400

    # Define and create Simulation directory (container) and Local directory (saved on host)
    # Local directory stores all necessary files for recovery and completed asset files
    # Simulation directory contains all files necessary for the simulation
    SIMULATION_DIR = os.path.join(USER_FILES_DIR, f'{simulation_name}')
    LOCAL_DIR = os.path.join(LOCAL_DIR, f'{simulation_name}')
    if os.path.exists(SIMULATION_DIR) or os.path.exists(LOCAL_DIR):
        logger.error("Error: Simulation name already exists.")
        return jsonify({'error': 'Simulation name already exists.'}), 400
    
    os.makedirs(SIMULATION_DIR, exist_ok=True)
    os.makedirs(LOCAL_DIR, exist_ok=True)
    logger.info(f"Upload directory: {SIMULATION_DIR}")
    logger.info(f"Local directory: {LOCAL_DIR}")
    

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
        logger.debug("Calling create_feature_files from start_simulation_from_json()")
        create_featurefiles(SIMULATION_DIR, LOCAL_DIR, ASSET_GEOJSON, METADATA_CSV, CONFIG_JSON, num_cores, location)
        logger.debug("Exited create_feature_files to start_simulation_from_json()")
        
        featurefile_zip_path = os.path.join(SIMULATION_DIR, 'feature_files.zip')
        
        logger.debug("Calling initialize_uo from start_simulation_from_json()")
        initialize_uo(SIMULATION_DIR, LOCAL_DIR, METADATA_CSV, featurefile_zip_path, clean_report_flag=True)
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
@server.route('/api/simulation/stop', methods=['POST'])
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
@server.route('/api/simulation/status/<simulation_name>', methods=['GET'])
def get_simulation_status(simulation_name):
    logger.debug("Within simulation_status()")
    
    
    # Parameter checking
    batch_id = request.args.get('batch_id', default=None, type=int)

    if simulation_name is None:
        logger.error("Error: Simulation name is required.")
        return jsonify({'error': 'Simulation name is required.'}), 400
    
    SIMULATION_STATUS_DIR = os.path.join(USER_FILES_DIR, simulation_name, 'batch_status')    
    if not os.path.exists(SIMULATION_STATUS_DIR):
        logger.error("Simulation status directory not found")
        return jsonify({'error': 'Simulation status directory not found'}), 404
    
    # Read and log the simulation status files
    try:
        logger.debug(f"Entering read_simulation_status() from simulation_status() with batch_id={batch_id}")
        read_simulation_status(SIMULATION_STATUS_DIR, batch_id)
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
@server.route('/api/simulation/delete/<simulation_name>', methods=['DELETE'])
def delete_simulation(simulation_name):
    logger.debug("Within delete_simulation()")
    
    # Check param
    
    if simulation_name is None:
        logger.error("Error: Simulation name is required.")
        return jsonify({'error': 'Simulation name is required.'}), 400

    SIMULATION_DIR = os.path.join(USER_FILES_DIR, simulation_name)    
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
@server.route('/api/asset/config/<simulation_name>/<asset_id>', methods=['GET'])
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

# 3. Diagnostics and Logs

############################################################################################################
# Name: def recovery()
# Description: This function recovers a corrupted simulation by removing assets that are "Processing" or "Not Processed Yet"
#   from the feature_files directory and re-running the UO simulation.
# Calls the simulation_recovery function to recover the corrupted simulation.
############################################################################################################
@server.route('/api/diagnostics/recovery', methods=['POST'])
def recovery():
    logger.debug("Within recovery()")
    corrupted_simulation_name = request.form.get('corrupted_simulation_name')
    recover_simulation_name = request.form.get('recover_simulation_name')
    batch_id = request.form.get('recover_batch_id', default=None, type=int)
    num_cores = int(request.form.get('recover_num_cores', 1))

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
    RECOVERY_DIR_CONTAINER = os.path.join(USER_FILES_DIR, f'{recover_simulation_name}')
    if os.path.exists(RECOVERY_DIR_LOCAL) or os.path.exists(RECOVERY_DIR_CONTAINER):
        logger.debug("Recovery directory already exists")
        return jsonify({'error': 'Recovery directory already exists'}), 400

    os.makedirs(RECOVERY_DIR_CONTAINER, exist_ok=True)
    os.makedirs(RECOVERY_DIR_LOCAL, exist_ok=True)

    # Construct the metadata CSV file name
    metadata_csv_name = f'{corrupted_simulation_name}_metadata.csv'
    metadata_csv_path = os.path.join(CORRUPTED_SIMULATION_DIR, metadata_csv_name)
    # Construct the asset geojson file name
    geojson_name = f'{corrupted_simulation_name}_asset.geojson'
    geojson_path = os.path.join(CORRUPTED_SIMULATION_DIR, geojson_name)
    # Construct the config json file name
    config_name = f'{corrupted_simulation_name}_config.json'
    config_path = os.path.join(CORRUPTED_SIMULATION_DIR, config_name)

    if not os.path.exists(metadata_csv_path):
        logger.error("Metadata CSV file not found in the corrupted simulation directory")
        return jsonify({'error': 'Metadata CSV file not found in the corrupted simulation directory'}), 404

    # Copy and rename the metadata CSV file to the recovery directory
    new_metadata_csv_name = f'{recover_simulation_name}_metadata.csv'
    new_metadata_csv_path_container = os.path.join(RECOVERY_DIR_CONTAINER, new_metadata_csv_name)
    new_metadata_csv_path_local = os.path.join(RECOVERY_DIR_LOCAL, new_metadata_csv_name)
    shutil.copy(metadata_csv_path, new_metadata_csv_path_container)
    shutil.copy(metadata_csv_path, new_metadata_csv_path_local)

    # Copy and rename the metadata CSV file to the recovery directory
    new_geojson_name = f'{recover_simulation_name}_asset.geojson'
    new_geojson_name_path = os.path.join(RECOVERY_DIR_CONTAINER, new_geojson_name)
    shutil.copy(geojson_path, new_geojson_name_path)

    # Copy and rename the metadata CSV file to the recovery directory
    new_config_name = f'{recover_simulation_name}_config.json'
    new_config_name_path = os.path.join(RECOVERY_DIR_CONTAINER, new_config_name)
    shutil.copy(config_path, new_config_name_path)

    try:
        logger.debug("Calling simulation_recovery from recovery()")
        simulation_recovery(CORRUPTED_SIMULATION_DIR, RECOVERY_DIR_CONTAINER, RECOVERY_DIR_LOCAL, new_metadata_csv_path_container, batch_id, num_cores)
        
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
# Calls the get_logs function to zip the logs directory.
############################################################################################################
@server.route('/logs', methods=['GET'])
def get_logs():
    logger.debug("Within get_logs()")
    
    REQUESTED_FILES_DIR = os.path.join(LOCAL_DIR, 'requested_files')
    os.makedirs(REQUESTED_FILES_DIR, exist_ok=True)
    REQUESTED_LOG_FILE = os.path.join(REQUESTED_FILES_DIR, 'dev_logs.txt')

    LOGS_DIR = os.path.join('logs')
    LOG_FILE = os.path.join(LOGS_DIR, 'dev_logs.txt')
    
    if not os.path.exists(LOGS_DIR):
        logger.error("Logs dir does not exist")
        return jsonify({'error': 'Logs dir does not exist'}), 404
    
    if not os.path.exists(LOG_FILE):
        logger.error("Log file does not exist")
        return jsonify({'error': 'Log file does not exist'}), 404

    try:
        with open(LOG_FILE, 'r') as file:
            logs = file.read()
        # Save the log file to the requested_files directory
        with open(REQUESTED_LOG_FILE, 'w') as file:
            file.write(logs)
        logger.debug(f"Log file saved to {REQUESTED_LOG_FILE}")
    except Exception as e:
        logger.error(f"Exception while reading log file: {str(e)}")
        return jsonify({'error': str(e)}), 500

    # Render the logs in the template
    return render_template('logs.html', logs=logs)


############################################################################################################
# Name: def log_message()
# Description: This function logs a message to the dev_logs.txt file.
# Calls the log_message function to log a message to the dev_logs.txt file.
############################################################################################################
@server.route('/api/diagnostics/log', methods=['POST'])
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

if __name__ == '__main__':
    server.run(debug=True, host='0.0.0.0', port=8080)
    


