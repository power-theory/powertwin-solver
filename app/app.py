import shutil
import os
import json
import datetime
import psycopg

from flask import Flask, request, jsonify, render_template, send_file
from flask_cors import CORS

from scripts.simulation import initialize_uo, create_featurefiles, clean_single_report, stop_UOsimulation
from scripts.diagnostics import asset_analysis, read_simulation_status, simulation_recovery
from scripts.helper import initialize_logger, send_error_to_mss

username = "postgres"
password = "admin"

main_logger = initialize_logger('App Main')
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
USER_FILES_DIR = os.path.join('powertwin-solver-pg', 'user_files')


@server.route('/')
def home():
    return render_template('base.html')

@server.route('/dev')
def dev():
    return render_template('dev.html')

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
#         main_logger.error(f"Exception while testing database connection: {str(e)}")
#         return jsonify({'error': str(e)}), 500


# 1. Simulation Managment

############################################################################################################
# Name: def start_simulation()
# Description: This function requires ASSET_GEOJSON, METADATA_CSV, config_data, and simulation_name to start the simulation.
# Performs error checking and creates a directory based on the given simulation name.
# Calls the create_featurefiles and initialize_uo functions to generate feature files and start the UrbanOpt simulation.
# This function parallelizes the simulation after the feature files are created.
############################################################################################################
@server.route('/api/simulation/start', methods=['POST'])
def start_simulation():
    main_logger.debug("Within start_simulation()")
    ASSET_GEOJSON = request.files.get('asset_geojson_file')
    METADATA_CSV = request.files.get('metadata_csv_file')
    config_data = request.form.get('config_data')
    simulation_name = request.form.get('simulation_name')
    location = request.form.get('location')
    num_cores = int(request.form.get('num_cores', 1))

    
    if not ASSET_GEOJSON or not METADATA_CSV or not config_data:
        main_logger.error("Error: Both features asset Geojson,metadata CSV, and config data is required.")
        return jsonify({'error': 'Both features asset Geojson,metadata CSV, and config data is required.'}), 400
    
    if simulation_name is None:
        main_logger.error("Error: Simulation name is required.")
        return jsonify({'error': 'Simulation name is required.'}), 400
    
    if simulation_name:
        # Check if the simulation name already exists
        SIMULATION_DIR = os.path.join(USER_FILES_DIR,f'{simulation_name}')
        if os.path.exists(SIMULATION_DIR):
            main_logger.error("Error: Simulation name already exists.")
            return jsonify({'error': 'Simulation name already exists.'}), 400
    
    
    # Define the upload directory and paths for the uploaded files

    SIMULATION_DIR = os.path.join(USER_FILES_DIR,f'{simulation_name}')
    os.makedirs(SIMULATION_DIR, exist_ok=True)
    main_logger.info(f"Upload directory: {SIMULATION_DIR}")

    asset_geojson_path = os.path.join(SIMULATION_DIR, f'{simulation_name}_asset.geojson')
    metadata_csv_path = os.path.join(SIMULATION_DIR, f'{simulation_name}_metadata.csv')
    config_json_path = os.path.join(SIMULATION_DIR, f'{simulation_name}_config.json')
    
    # Save the uploaded files
    ASSET_GEOJSON.save(asset_geojson_path)
    METADATA_CSV.save(metadata_csv_path)
    with open(config_json_path, 'w') as config_file:
        json.dump(json.loads(config_data), config_file)

    # Call the create_feature_files and initialize_uo functions
    try:
        main_logger.debug("Calling create_feature_files from start_simulation()")
        create_featurefiles(SIMULATION_DIR,asset_geojson_path, metadata_csv_path, config_json_path, num_cores, location)
        main_logger.debug("Exited create_feature_files to start_simulation()")
        
        featurefile_zip_path = os.path.join(SIMULATION_DIR,'feature_files.zip')
        
        main_logger.debug("Calling initialize_uo from start_simulation()")
        initialize_uo(SIMULATION_DIR,metadata_csv_path,featurefile_zip_path, clean_report_flag=True)
        main_logger.debug("Exited initialize_uo to start_simulation()")
    except Exception as e:
        main_logger.error(f"Exception: {str(e)}")
        send_error_to_mss('start_simulation', str(e))
        return jsonify({'error': str(e)}), 500
    
    main_logger.debug("start_simulation() ran successfully")
    return jsonify({'confirmation': f'Simulation "{simulation_name}" ran successfully'})

############################################################################################################
# Name: def autorun_simulation()
# Description: This function reads the simulation.json file and starts the simulation based on the given parameters.
# Calls the create_featurefiles and initialize_uo functions to generate feature files and start the UrbanOpt simulation.
# This function parallelizes the simulation after the feature files are created.
############################################################################################################
@server.route('/api/simulation/autorun_simulation', methods=['POST'])
def autorun_simulation():
    SIMULATION_JSON = os.path.join('app', 'upload', 'simulation.json')

    if not os.path.exists(SIMULATION_JSON):
        main_logger.error("Error: simulation.json file not found.")
        return jsonify({'error': 'simulation.json file not found.'}), 404
    
    with open(SIMULATION_JSON, 'r') as file:
        data = json.load(file)
    
    simulation_name = data.get('simulation_name')
    asset_geojson_path = data.get('asset_geojson_path')
    metadata_csv_path = data.get('metadata_csv_path')
    config_json_path = data.get('config_json_path')
    location = data.get('location')
    num_cores = data.get('num_cores', 1)

    if not simulation_name or not asset_geojson_path or not metadata_csv_path or not config_json_path:
        main_logger.error("Error: Missing required fields in simulation.json")
        return jsonify({'error': 'Missing required fields in simulation.json'}), 400

    
    SIMULATION_DIR = os.path.join(USER_FILES_DIR, f'{simulation_name}')
    if os.path.exists(SIMULATION_DIR):
        main_logger.error("Error: Simulation name already exists.")
        return jsonify({'error': 'Simulation name already exists.'}), 400
    
    os.makedirs(SIMULATION_DIR, exist_ok=True)
    main_logger.info(f"Upload directory: {SIMULATION_DIR}")
    

    ASSET_GEOJSON = os.path.join(SIMULATION_DIR, f'{simulation_name}_asset.geojson')
    METADATA_CSV = os.path.join(SIMULATION_DIR, f'{simulation_name}_metadata.csv')
    CONFIG_JSON = os.path.join(SIMULATION_DIR, f'{simulation_name}_config.json')
    
    # Copy the files to the new directory
    shutil.copy(asset_geojson_path, ASSET_GEOJSON)
    shutil.copy(metadata_csv_path, METADATA_CSV)
    shutil.copy(config_json_path, CONFIG_JSON)

    # Call the create_feature_files and initialize_uo functions
    try:
        main_logger.debug("Calling create_feature_files from start_simulation_from_json()")
        create_featurefiles(SIMULATION_DIR, ASSET_GEOJSON, METADATA_CSV, CONFIG_JSON, num_cores, location)
        main_logger.debug("Exited create_feature_files to start_simulation_from_json()")
        
        featurefile_zip_path = os.path.join(SIMULATION_DIR, 'feature_files.zip')
        
        main_logger.debug("Calling initialize_uo from start_simulation_from_json()")
        initialize_uo(SIMULATION_DIR, METADATA_CSV, featurefile_zip_path, clean_report_flag=True)
        main_logger.debug("Exited initialize_uo to start_simulation_from_json()")
        return jsonify({'message': 'Simulation completed successfully.'}), 200
    except Exception as e:
        main_logger.error(f"Exception: {str(e)}")
        send_error_to_mss('autorun_simulation', str(e))
        return jsonify({'error': f"Simulation failed: {str(e)}"}), 500

############################################################################################################
# Name: def stop_simulation()
# Description: This function stops the UrbanOpt simulation.
# Calls the stop_UOsimulation function to stop the simulation.
############################################################################################################
@server.route('/api/simulation/stop', methods=['POST'])
def stop_simulation():
    main_logger.debug("Within stop_simulation()")
    # Stop the simulation
    try:
        main_logger.debug("Stopping the simulation")
        stop_UOsimulation()

        return jsonify({'message': 'Simulation stopped successfully'}), 200
    except Exception as e:
        main_logger.error(f"Exception while stopping the simulation: {str(e)}")
        send_error_to_mss('stop_simulation', str(e))
        return jsonify({'error': str(e)}), 500


############################################################################################################
# Name: def get_simulation_status()
# Description: This function reads the simulation status files and logs the status of the simulation.
# Calls the read_simulation_status function to read the simulation status files.
############################################################################################################ 
@server.route('/api/simulation/status/<simulation_name>', methods=['GET'])
def get_simulation_status(simulation_name):
    main_logger.debug("Within simulation_status()")
    
    batch_id = request.args.get('batch_id', default=None, type=int)

    if simulation_name is None:
        main_logger.error("Error: Simulation name is required.")
        return jsonify({'error': 'Simulation name is required.'}), 400

    SIMULATION_STATUS_DIR = os.path.join(USER_FILES_DIR, simulation_name, 'batch_status')    

    if not os.path.exists(SIMULATION_STATUS_DIR):
        main_logger.error("Simulation status directory not found")
        return jsonify({'error': 'Simulation status directory not found'}), 404
    
    # Read and log the simulation status files
    try:
        main_logger.debug(f"Entering read_simulation_status() from simulation_status() with batch_id={batch_id}")
        read_simulation_status(SIMULATION_STATUS_DIR, batch_id)
        return jsonify({'message': 'Simulation status files read successfully'}), 200
    except Exception as e:
        main_logger.error(f"Exception while reading simulation status files: {str(e)}")
        send_error_to_mss('get_simulation_status', str(e))
        return jsonify({'error': str(e)}), 500


@server.route('/api/simulation/delete/<simulation_name>', methods=['DELETE'])
def delete_simulation(simulation_name):
    main_logger.debug("Within delete_simulation()")
    
    if simulation_name is None:
        main_logger.error("Error: Simulation name is required.")
        return jsonify({'error': 'Simulation name is required.'}), 400

    SIMULATION_DIR = os.path.join(USER_FILES_DIR, simulation_name)    

    if not os.path.exists(SIMULATION_DIR):
        main_logger.error("Simulation status directory not found")
        return jsonify({'error': 'Simulation status directory not found'}), 404
    
    # Delete simulation directory
    try:
        shutil.rmtree(SIMULATION_DIR)
        main_logger.info(f"Simulation directory {SIMULATION_DIR} deleted successfully.")
        return jsonify({'message': f'Simulation directory {SIMULATION_DIR} deleted successfully.'}), 200
    except Exception as e:
        main_logger.error(f"Exception while trying to delete simulation: {str(e)}")
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
    main_logger.debug("Within get_asset_config()")
    
    if not asset_id or not simulation_name:
        main_logger.error("Error: Asset ID and Simulation Name are required")
        return jsonify({'error': 'Asset ID and Simulation Name are required'}), 400
        
    # Fix to point to powertwin-solver-pg 
    current_dir = os.path.dirname(os.path.abspath(__file__))
    USER_FILES_DIR = os.path.join(current_dir, '..', 'powertwin-solver-pg', 'user_files')
    USER_FILES_DIR = os.path.normpath(USER_FILES_DIR)
   

    try:
        # Search to see if user_files directory exists, so that we can search for the feature file
        SIMULATION_DIR = os.path.join(USER_FILES_DIR, f'{simulation_name}')
        if not os.path.exists(SIMULATION_DIR):
            main_logger.error("Simulation directory does not exist")
            return jsonify({'error': 'Simulation directory does not exist'}), 404

        # Search for feature file dir
        FEATURE_FILE_DIR = os.path.join(SIMULATION_DIR, 'feature_files')
        if not os.path.exists(FEATURE_FILE_DIR):
            main_logger.error(F"{FEATURE_FILE_DIR} directory found")
            return jsonify({'error': 'No feature files directory found'}), 404

        # Search for the asset ID in the feature files
        main_logger.debug(f"Searching for feature file in {FEATURE_FILE_DIR}")
        for file_name in os.listdir(FEATURE_FILE_DIR):
            if file_name.startswith(f"{asset_id}_") and file_name.endswith('.json'):
                file_path = os.path.join(FEATURE_FILE_DIR, file_name)
                
                # Define the path to save the requested configuration file
                DOWNLOAD_DIR = os.path.join(USER_FILES_DIR, 'requested_files')
                os.makedirs(DOWNLOAD_DIR, exist_ok=True)
                requested_file_path = os.path.join(DOWNLOAD_DIR, file_name)
                
                # Copy the configuration file to the requested_files directory
                shutil.copy(file_path, requested_file_path)
                
                response = send_file(requested_file_path, as_attachment=True)
                return response
    
        main_logger.error(f"No feature file found for asset ID: {asset_id}")
        return jsonify({'error': f'No feature file found for asset ID: {asset_id}'}), 404
    
    except Exception as e:
        main_logger.error(f"Exception: {str(e)}")
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
    main_logger.debug("Within recovery()")
    corrupted_simulation_name = request.form.get('corrupted_simulation_name')
    recover_simulation_name = request.form.get('recover_simulation_name')
    batch_id = request.form.get('recover_batch_id', default=None, type=int)
    num_cores = int(request.form.get('recover_num_cores', 1))

    if not corrupted_simulation_name:
        main_logger.error("Error: Simulation name is required.")
        return jsonify({'error': 'Simulation name is required.'}), 400

    if not recover_simulation_name:
        main_logger.error("Error: Recover simulation name is required.")
        return jsonify({'error': 'Recover simulation name is required.'}), 400
    
    

    # # Fix to point to powertwin-solver-pg 
    # current_dir = os.path.dirname(os.path.abspath(__file__))
    # USER_FILES_DIR = os.path.join(current_dir, '..', 'powertwin-solver-pg', 'user_files')
    # USER_FILES_DIR = os.path.normpath(USER_FILES_DIR)
    
    
    CORRUPTED_SIMULATION_DIR = os.path.join(USER_FILES_DIR, corrupted_simulation_name)

    if not os.path.exists(CORRUPTED_SIMULATION_DIR):
        main_logger.error("Simulation directory not found")
        return jsonify({'error': 'Simulation directory not found'}), 404

    RECOVERY_DIR = os.path.join(USER_FILES_DIR, f'{recover_simulation_name}')

    if os.path.exists(RECOVERY_DIR):
        main_logger.debug("Recovery directory already exists")
        return jsonify({'error': 'Recovery directory already exists'}), 400

    os.makedirs(RECOVERY_DIR, exist_ok=True)

    # Construct the metadata CSV file name
    metadata_csv_name = f'{corrupted_simulation_name}_metadata.csv'
    metadata_csv_path = os.path.join(CORRUPTED_SIMULATION_DIR, metadata_csv_name)

    if not os.path.exists(metadata_csv_path):
        main_logger.error("Metadata CSV file not found in the corrupted simulation directory")
        return jsonify({'error': 'Metadata CSV file not found in the corrupted simulation directory'}), 404

    # Copy and rename the metadata CSV file to the recovery directory
    new_metadata_csv_name = f'{recover_simulation_name}_metadata.csv'
    new_metadata_csv_path = os.path.join(RECOVERY_DIR, new_metadata_csv_name)
    shutil.copy(metadata_csv_path, new_metadata_csv_path)

    try:
        main_logger.debug("Calling simulation_recovery from recovery()")
        simulation_recovery(CORRUPTED_SIMULATION_DIR, RECOVERY_DIR, metadata_csv_path, batch_id, num_cores)
        return jsonify({'message': 'Simulation recovery process completed successfully'}), 200
    except Exception as e:
        main_logger.error(f"Exception during simulation recovery: {str(e)}")
        send_error_to_mss('recovery', str(e))
        return jsonify({'error': str(e)}), 500


############################################################################################################
# Name: def get_logs()
# Description: This function zips the logs directory and sends the zip file as a response for download.
# Calls the get_logs function to zip the logs directory.
############################################################################################################
@server.route('/api/diagnostics/getlogs', methods=['GET'])
def get_logs():
    main_logger.debug("Within get_logs()")

    LOGS_DIR = os.path.join('app','logs')
    
    # Fix to point to powertwin-solver-pg 
    current_dir = os.path.dirname(os.path.abspath(__file__))
    USER_FILES_DIR = os.path.join(current_dir, '..', 'powertwin-solver-pg', 'user_files')
    USER_FILES_DIR = os.path.normpath(USER_FILES_DIR)

    
    # Define the path to save the zipped batch status file
    DOWNLOAD_DIR = os.path.join(USER_FILES_DIR, 'requested_files')
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    ZIP_FILE = os.path.join(DOWNLOAD_DIR, 'logs.zip')
    
    if not os.path.exists(LOGS_DIR):
        main_logger.error("Logs dir does not exist")
        return jsonify({'error': 'Logs dir does not exist'}), 404
    

    # Zip the logs directory
    try:
        shutil.make_archive(os.path.splitext(ZIP_FILE)[0], 'zip', LOGS_DIR)
        main_logger.debug("Logs directory zipped successfully")
    except Exception as e:
        main_logger.error(f"Exception while zipping logs: {str(e)}")
        return jsonify({'error': str(e)}), 500


    # Send the zip file as a response for download
    try:
        main_logger.debug("Sending log file")
        return send_file(ZIP_FILE, as_attachment=True)
    except Exception as e:
        main_logger.error(f"Exception: {str(e)}")
        send_error_to_mss('get_logs', str(e))
        return jsonify({'error': str(e)}), 500

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
    log_txt = os.path.join(os.getcwd(), 'app','logs','dev_logs.txt')
    os.makedirs(os.path.dirname(log_txt), exist_ok=True)

    # Append the log entry to the log file
    with open(log_txt, 'a') as log_file:
        log_file.write(f"[{timestamp}] [{log_type.upper()}] {message}\n")

    return jsonify({'status': 'success', 'log_file': log_txt}), 200


# 0. Dev Tools WIP

@server.route('/api/featurefiles', methods=['POST'])
def feature_files():
    main_logger.debug("Within get_feature_files()")
    asset_geojson = request.files.get('asset_geojson_file')
    metadata_csv = request.files.get('metadata_csv_file_1')
    config_data = request.form.get('config_data')
    num_cores = request.form.get('num_cores', 1)
    
    if not asset_geojson or not metadata_csv or not config_data:
        main_logger.error("Error: Both features asset Geojson,metadata CSV, and config data is required.")
        return jsonify({'error': 'Both features asset Geojson,metadata CSV, and config data is required.'}), 400
    

    # Define the upload directory and paths for the uploaded files
    SIMULATION_DIR = os.path.join(USER_FILES_DIR,'default')
    os.makedirs(SIMULATION_DIR, exist_ok=True)
    main_logger.info(f"Upload directory: {SIMULATION_DIR}")


    asset_geojson_path = os.path.join(SIMULATION_DIR, asset_geojson.filename)
    metadata_csv_path = os.path.join(SIMULATION_DIR, metadata_csv.filename)
    config_json_path = os.path.join(SIMULATION_DIR, 'default_custom_config.json')
    
    # Save the uploaded files
    asset_geojson.save(asset_geojson_path)
    metadata_csv.save(metadata_csv_path)
    with open(config_json_path, 'w') as config_file:
        json.dump(json.loads(config_data), config_file)

    # Call the generate_feature_files function in the generateFeatureFile.py script
    try:
        main_logger.debug("Calling generateFeatureFile.py from get_feature_files()")
        create_featurefiles(SIMULATION_DIR,asset_geojson_path, metadata_csv_path, config_json_path, num_cores)
        main_logger.debug("Exited generateFeatureFile.py to get_feature_files()")
    except Exception as e:
        main_logger.error(f"Exception: {str(e)}")
        return jsonify({'error': str(e)}), 500


    # Define the zip file path for storing the feature files
    ZIP_PATH = os.path.join(SIMULATION_DIR,'feature_files.zip')

    # Send the zip file as a response for download
    try:
        main_logger.debug("Sending feature files zip")
        return send_file(ZIP_PATH, as_attachment=True)
    except Exception as e:
        main_logger.error(f"Exception: {str(e)}")
        return jsonify({'error': str(e)}), 500
    
@server.route('/api/UOsimulation/start', methods=['POST'])
def start_uo_simulation():
    main_logger.debug("Within start_uo_simulation()")
    featurefile_zip = request.files.get('featurefile_zip')
    asset_id = request.form.get('asset_id_2')
    clean_report_boolean = request.form.get('clean_report_1') is not None

    if (featurefile_zip and asset_id) or (not featurefile_zip and not asset_id):
        main_logger.error("Error: Please upload either a feature file zip or input an asset id, but not both.")
        return jsonify({'error': 'Please upload either a feature file zip or input an asset id, but not both.'}), 400
    
    if featurefile_zip:
        # Call the initialize_uo function with the feature file from runUOsimulation.py
        
        SIMULATION_DIR = os.path.join(USER_FILES_DIR, 'default')
        os.makedirs(SIMULATION_DIR, exist_ok=True)
        main_logger.info(f"Upload directory: {SIMULATION_DIR}")
    
        # Save the uploaded feature file zip
        featurefile_zip_path = os.path.join(SIMULATION_DIR, featurefile_zip.filename)
        featurefile_zip.save(featurefile_zip_path)
        
        try:
            main_logger.debug("Calling runUOsimulation.py from start_uo_simulation()")
            initialize_uo(SIMULATION_DIR,featurefile_zip_path, clean_report_flag=clean_report_boolean)
            main_logger.debug("Exited runUOsimulation.py to start_uo_simulation()")
        except Exception as e:
            main_logger.error(f"Exception: {str(e)}")
            return jsonify({'error': str(e)}), 500
    else:
        # Call the initialize_uo function with the asset ID from runUOsimulation.py
        # TODO: Fix directroy path to correctly find asset id
        
        UPLOAD_DIR = os.path.join(UPLOAD_DIR, f'{asset_id}')
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        main_logger.info(f"Upload directory: {UPLOAD_DIR}")
        
        try:
            main_logger.debug("Calling runUOsimulation.py from start_uo_simulation()")
            initialize_uo(UPLOAD_DIR,featurefile_zip_path, clean_report_flag=clean_report_boolean)
            main_logger.debug("Exited runUOsimulation.py to start_uo_simulation()")
        except Exception as e:
            main_logger.error(f"Exception: {str(e)}")
            return jsonify({'error': str(e)}), 500
        
    main_logger.debug("start_uo_simulation() ran successfully")
    return jsonify({'confirmation': 'Urban Opt simulation ran successfully'})

@server.route('/api/clean_report', methods=['POST'])
def clean_report():
    main_logger.debug("Within get_clean_report()")
    unclean_report_csv = request.json.get('unclean_report_csv')
    metadata_csv = request.json.get('metadata_csv_file_2')
    asset_id = request.json.get('asset_id_3')
    
    if not unclean_report_csv or not metadata_csv or not asset_id:
        main_logger.error("Error: Unclean Report, Metadata CSV, and Asset ID are required")
        return jsonify({'error': 'Unclean Report, Metadata CSV, and Asset ID are required'}), 400
        
    # Define the upload directory and paths for the uploaded files
    SIMULATION_DIR = os.path.join(USER_FILES_DIR, 'default')
    os.makedirs(SIMULATION_DIR, exist_ok=True)
    main_logger.info(f"Upload directory: {SIMULATION_DIR}")

    unclean_report_csv_path = os.path.join(SIMULATION_DIR, unclean_report_csv_path.filename)
    metadata_csv_path = os.path.join(SIMULATION_DIR, metadata_csv.filename)
    
    # Save the uploaded files
    unclean_report_csv.save(unclean_report_csv_path)
    metadata_csv.save(metadata_csv_path)
    
    # Call the clean_report function in the clean_report.py script
    try:
        main_logger.debug("Calling clean_report.py from get_clean_report()")
        clean_single_report(unclean_report_csv, metadata_csv, asset_id)
        main_logger.debug("Exited clean_report.py to get_clean_report()")
    except Exception as e:
        main_logger.error(f"Exception: {str(e)}")
        return jsonify({'error': str(e)}), 500

    main_logger.debug("get_clean_report() ran successfully")
    return jsonify({'confirmation': 'Clean report ran successfully'})

@server.route('/api/diagnostics/analysis', methods=['GET'])
def get_runtime_analysis():
    main_logger.debug("Within get_runtime_analysis()")
    featurefile_zip = request.files.get('featurefile_zip_2')
    num_cores = request.form.get('num_cores', 1)
    
    if not featurefile_zip:
        main_logger.error("Error: Feature file zip is required.")
        return jsonify({'error': 'Feature file zip is required.'}), 400
    
    # Define the upload directory and paths for the uploaded files
    SIMULATION_DIR = os.path.join(USER_FILES_DIR, 'default')
    
    os.makedirs(SIMULATION_DIR, exist_ok=True)
    main_logger.info(f"Upload directory: {SIMULATION_DIR}")

    # Save the uploaded feature file zip
    featurefile_zip_path = os.path.join(SIMULATION_DIR, featurefile_zip.filename)
    featurefile_zip.save(featurefile_zip_path)
    
    try:
        main_logger.debug("Calling asset_analysis from get_runtime_analysis()")
        asset_analysis(featurefile_zip, num_cores)
        main_logger.debug("Exited asset_analysis to get_runtime_analysis()")
    except Exception as e:
        main_logger.error(f"Exception: {str(e)}")
        return jsonify({'error': str(e)}), 500
    
    main_logger.debug("get_runtime_analysis() ran successfully")
    return jsonify({'analysis': 'data'})


if __name__ == '__main__':
    server.run(debug=True, host='0.0.0.0', port=8080)
    


