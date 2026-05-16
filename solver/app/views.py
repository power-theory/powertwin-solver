import shutil
import os
import json
import datetime
import csv
import glob
import hmac
import threading

from flask import request, jsonify, render_template, send_file

from modules.simulation import initialize_uo, create_featurefiles, stop_UOsimulation
from modules.diagnostics import read_simulation_status, simulation_recovery, create_table
from modules.utils import (
    initialize_logger, send_error_to_mss,
    pack_simulation_results, atomic_write_json, write_status,
)
from modules.utils.hpc_environment import is_hpc_environment, get_hpc_info

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Views', external_log_dir)


# Define the output directory for the simulation files
DATA_DIR = os.path.join('data')

# Reporting-frequency translation: user-facing labels ↔ urbanopt natives.
# Loaded once at import; the file lives alongside the other upload/ defaults.
_REPORTING_FREQ_MAP_PATH = os.path.join('upload', 'reporting_frequency_map.json')
_REPORTING_FREQ_MAP = None


def _load_reporting_freq_map():
    global _REPORTING_FREQ_MAP
    if _REPORTING_FREQ_MAP is None:
        try:
            with open(_REPORTING_FREQ_MAP_PATH, 'r') as f:
                _REPORTING_FREQ_MAP = json.load(f)
        except Exception as e:
            logger.warning(f"Could not load {_REPORTING_FREQ_MAP_PATH}: {e}")
            _REPORTING_FREQ_MAP = {'user_to_urbanopt': {}, 'urbanopt_to_user': {}}
    return _REPORTING_FREQ_MAP


def _resolve_reporting_frequency(user_value):
    """Translate a user-facing reporting frequency (minutely/hourly/daily/monthly/yearly)
    to its urbanopt-native equivalent. Returns None when unset (= caller keeps the default).
    Raises ValueError on an unknown user label."""
    if user_value is None:
        return None
    label = str(user_value).strip().lower()
    if not label:
        return None
    fmap = _load_reporting_freq_map().get('user_to_urbanopt', {})
    native = fmap.get(label)
    if not native:
        valid = ', '.join(sorted(fmap.keys()))
        raise ValueError(f"unknown reporting_frequency '{label}' (valid: {valid})")
    return native
LOCAL_DIR = os.path.join('powertwin-solver-pg', 'user_files')

def home():
    return render_template('base.html')

# 1. Simulation Managment

############################################################################################################
# Name: def start_simulation()
# Description: This function requires ASSET_GEOJSON, METADATA_CSV, and simulation_name,
# and num_cores to start the simulation. Performs error checking and creates a directory 
# based on the given simulation name. Calls the create_featurefiles and initialize_uo functions to
# generate feature files and start the UrbanOpt simulation. This function parallelizes the 
# simulation after the feature files are created.
############################################################################################################
def start_simulation():
    logger.debug("Within start_simulation()")
    
    # Inputs
    ASSET_GEOJSON = request.files.get('asset_geojson_file')
    METADATA_CSV = request.files.get('metadata_csv_file')
    simulation_name = request.form.get('simulation_name')
    num_cores = int(request.form.get('num_cores', 1))
    shared_storage = request.form.get('shared_storage')
    keep_dirs = request.form.get('keep_dirs', 'false').lower() == 'true'
    
    # Use centralized HPC detection
    is_hpc = is_hpc_environment()

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
    if not ASSET_GEOJSON or not METADATA_CSV or not simulation_name or num_cores <= 0:
        logger.error("Error: missing or invalid parameter.")
        return jsonify({'error': 'missing or invalid parameter'}), 400
    
    # HPC mode validation
    if is_hpc and not shared_storage:
        logger.error("Error: shared_storage is required when in HPC environment.")
        return jsonify({'error': 'shared_storage is required when in HPC environment'}), 400
    
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

    ASSET_GEOJSON.save(asset_geojson_path)
    METADATA_CSV.save(metadata_csv_path)

    # Call the create_feature_files and initialize_uo to run the simulation and
    # delete the simulation directory (container)
    try:
        create_table()
        logger.debug("Calling create_feature_files from start_simulation()")
        create_featurefiles(SIMULATION_DIR, LOCAL_DIR, asset_geojson_path, metadata_csv_path, num_cores, simulation_name)
        logger.debug("Exited create_feature_files to start_simulation()")
        
        logger.debug("Calling initialize_uo from start_simulation()")
        initialize_uo(SIMULATION_DIR, LOCAL_DIR, simulation_name)
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
# Name: def _check_api_token()
# Description: Reject requests without the shared service token (API_SOLVER_TOKEN).
# Fails closed: if the env var is unset on this Flask process, every request is rejected with 500
# (operator misconfig — surfaces in monitoring rather than silently authorizing all traffic).
# Returns a Flask response tuple to short-circuit the handler, or None when authorized.
############################################################################################################
def _check_api_token():
    expected = os.environ.get('API_SOLVER_TOKEN')
    if not expected:
        logger.error("API_SOLVER_TOKEN is not set; refusing request")
        return jsonify({'error': 'Server misconfigured: API_SOLVER_TOKEN not set'}), 500
    presented = request.headers.get('api_token') or ''
    if not hmac.compare_digest(presented, expected):
        return jsonify({'error': 'Unauthorized'}), 403
    return None


############################################################################################################
# Name: def process_asset_update()
# Description: Async kickoff. Validates input, writes input files + an initial
# status.json('received'), then spawns a daemon thread that runs urbanopt and
# writes status.json after each phase plus an atomic results.json on completion.
# Returns 202 Accepted with {simulation_name} immediately so the caller (the
# powertwin-db API listener) can release its connection slot. The API polls
# /api/simulation/status/<name> and pulls /api/simulation/results/<name> when
# the worker thread reports completed.
############################################################################################################
def process_asset_update():
    logger.debug("Within process_asset_update()")

    auth_err = _check_api_token()
    if auth_err is not None:
        return auth_err

    started_at = datetime.datetime.now(datetime.timezone.utc)
    try:
        request_data = request.get_json()
        if not request_data:
            return jsonify({'error': 'No JSON payload received'}), 400
        metadata_json = request_data.get('data', [])
        geojson_array = request_data.get('geojson', [])
        if not metadata_json or not geojson_array:
            return jsonify({'error': 'Missing data or geojson in payload'}), 400

        # Optional per-request window for post-processing slicing. The simulation
        # itself still runs the full URBANOPT_SIMULATION_YEAR range; these only narrow what
        # gets returned/ingested. Either bound is independent.
        request_start_date_time = (request_data.get('start_date_time') or '').strip() or None
        request_end_date_time = (request_data.get('end_date_time') or '').strip() or None

        # Optional per-request reporting frequency. Accepts user-facing labels:
        # minutely | hourly | daily | monthly | yearly. Translated to urbanopt
        # natives via upload/reporting_frequency_map.json. Unset → keep the
        # server-wide URBANOPT_REPORTING_FREQUENCY default.
        try:
            request_reporting_frequency = _resolve_reporting_frequency(
                request_data.get('reporting_frequency')
            )
        except ValueError as ve:
            return jsonify({'error': str(ve)}), 400

        asset_id = str(metadata_json[0].get('asset_id', 'unknown'))
        timestamp = started_at.strftime('%Y%m%d_%H%M%S')
        simulation_name = f"asset_{asset_id}_{timestamp}"
        logger.info(f"Accepted async simulation {simulation_name} for asset {asset_id}")

        csv_content = convert_metadata_to_csv(metadata_json)
        geojson_content = convert_geometry_to_geojson(geojson_array)

        LOCAL_BASE = os.path.join('powertwin-solver-pg', 'user_files')
        SIMULATION_DIR = os.path.join(DATA_DIR, simulation_name)
        sim_local_dir = os.path.join(LOCAL_BASE, simulation_name)
        if os.path.exists(SIMULATION_DIR) or os.path.exists(sim_local_dir):
            return jsonify({'error': 'Simulation name already exists.'}), 400

        os.makedirs(SIMULATION_DIR, exist_ok=True)
        os.makedirs(sim_local_dir, exist_ok=True)
        write_status(sim_local_dir, 'received',
                     simulation_name=simulation_name, asset_id=asset_id)

        asset_geojson_path = os.path.join(sim_local_dir, f'{simulation_name}_asset.geojson')
        metadata_csv_path = os.path.join(sim_local_dir, f'{simulation_name}_metadata.csv')
        with open(asset_geojson_path, 'w') as f:
            json.dump(geojson_content, f)
        with open(metadata_csv_path, 'w', newline='', encoding='utf-8') as f:
            f.write(csv_content)

        thread = threading.Thread(
            target=_run_asset_update_simulation,
            args=(simulation_name, SIMULATION_DIR, sim_local_dir,
                  asset_geojson_path, metadata_csv_path,
                  started_at, request_start_date_time, request_end_date_time,
                  request_reporting_frequency),
            daemon=True,
        )
        thread.start()
        return jsonify({
            'success': True,
            'simulation_name': simulation_name,
            'status': 'accepted',
        }), 202

    except Exception as e:
        logger.error(f"Exception in process_asset_update: {str(e)}")
        send_error_to_mss('process_asset_update', str(e))
        return jsonify({'error': str(e)}), 500


def _run_asset_update_simulation(simulation_name, simulation_dir, sim_local_dir,
                                 asset_geojson_path, metadata_csv_path,
                                 started_at, request_start_date_time=None,
                                 request_end_date_time=None,
                                 request_reporting_frequency=None):
    """Background worker. Runs without a Flask request context — do not reference `request`."""
    # Per-request URBANOPT_REPORTING_FREQUENCY override. PowerTwin.rb (the urbanopt mapper)
    # reads ENV['URBANOPT_REPORTING_FREQUENCY'] when building the OSW. With
    # SIMULATION_CONCURRENCY=1 there's only ever one sim in flight, so mutating
    # os.environ around the urbanopt subprocess is safe. Always restore.
    prev_reporting_freq = os.environ.get('URBANOPT_REPORTING_FREQUENCY')
    if request_reporting_frequency:
        os.environ['URBANOPT_REPORTING_FREQUENCY'] = request_reporting_frequency
        logger.info(f"URBANOPT_REPORTING_FREQUENCY override for {simulation_name}: {request_reporting_frequency}")
    try:
        num_cores = 1
        create_table()
        write_status(sim_local_dir, 'feature_files_starting', simulation_name=simulation_name)
        create_featurefiles(simulation_dir, sim_local_dir, asset_geojson_path,
                            metadata_csv_path, num_cores, simulation_name)
        write_status(sim_local_dir, 'urbanopt_running', simulation_name=simulation_name)
        initialize_uo(simulation_dir, sim_local_dir, simulation_name)
        write_status(sim_local_dir, 'cleaning_reports', simulation_name=simulation_name)

        # prepare_record swallows per-asset failures (EnergyPlus fatal, uo process
        # crash, etc.), so an empty cleaned_reports/ is the only signal that the
        # sim didn't actually produce output. Raise so the except below writes
        # status='failed' instead of shipping a zero-row 'completed'.
        if not glob.glob(os.path.join(sim_local_dir, 'cleaned_reports', '*', 'cleaned_predicted_*.csv')):
            raise RuntimeError(
                f"Simulation {simulation_name} produced no cleaned_reports output — "
                "EnergyPlus/urbanopt failed (see earlier Flask logs)"
            )

        if os.path.exists(simulation_dir):
            shutil.rmtree(simulation_dir)

        runtime_seconds = (datetime.datetime.now(datetime.timezone.utc) - started_at).total_seconds()
        # URBANOPT_RESAMPLE: 'H' | 'D' | 'W' | 'M' | 'Y' | '' (native passthrough).
        # Same convention as the HPC consolidate-state.sh RESAMPLE arg.
        resample = os.environ.get('URBANOPT_RESAMPLE', '').strip() or None
        results_payload = pack_simulation_results(
            sim_local_dir,
            runtime_seconds=runtime_seconds,
            resample=resample,
            start_date_time=request_start_date_time,
            end_date_time=request_end_date_time,
        )
        response = {
            'success': True,
            'simulation_name': simulation_name,
            'results': results_payload['results'],
            'runtime_seconds': runtime_seconds,
            'datelevel': results_payload['datelevel'],
            'resample': results_payload.get('resample'),
        }
        atomic_write_json(os.path.join(sim_local_dir, 'results.json'), response)
        write_status(sim_local_dir, 'completed',
                     simulation_name=simulation_name,
                     completed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                     runtime_seconds=runtime_seconds,
                     datelevel=results_payload['datelevel'])
        logger.info(f"Async simulation completed: {simulation_name}")
    except Exception as e:
        logger.error(f"Async simulation failed for {simulation_name}: {str(e)}")
        try:
            write_status(sim_local_dir, 'failed',
                         simulation_name=simulation_name, error=str(e))
        except Exception:
            pass
        try:
            send_error_to_mss('process_asset_update_async', str(e))
        except Exception:
            pass
    finally:
        # Restore the prior URBANOPT_REPORTING_FREQUENCY so this override doesn't leak
        # to the next sim that doesn't supply one.
        if request_reporting_frequency:
            if prev_reporting_freq is None:
                os.environ.pop('URBANOPT_REPORTING_FREQUENCY', None)
            else:
                os.environ['URBANOPT_REPORTING_FREQUENCY'] = prev_reporting_freq

############################################################################################################
# Name: def convert_metadata_to_csv(metadata_json)
# Description: Converts JSON metadata array to CSV format matching expected structure
############################################################################################################
def convert_metadata_to_csv(metadata_json):
    """Convert JSON metadata to CSV format."""
    if not metadata_json:
        return ""
    
    # CSV headers matching expected format
    headers = [
        'sensor_id', 'sensor_type_id', 'asset_id', 'asset_name',
        'asset_subtype_id', 'asset_metadata', 'asset_geometries_properties'
    ]

    # Build CSV content
    csv_lines = []
    csv_lines.append(','.join(headers))

    for row in metadata_json:
        # Extract values with defaults
        sensor_id = str(row.get('sensor_id', ''))
        sensor_type_id = str(row.get('sensor_type_id', ''))
        asset_id = str(row.get('asset_id', ''))
        asset_name = f'"{row.get("asset_name", "")}"'  # Quote for CSV
        asset_subtype_id = str(row.get('asset_subtype_id', ''))

        # Handle JSON fields - escape quotes and wrap in quotes
        asset_metadata = row.get('asset_metadata', {})
        if isinstance(asset_metadata, str):
            escaped_metadata = asset_metadata.replace('"', '""')
            asset_metadata_str = f'"{escaped_metadata}"'
        else:
            metadata_json_str = json.dumps(asset_metadata)
            escaped_metadata = metadata_json_str.replace('"', '""')
            asset_metadata_str = f'"{escaped_metadata}"'

        asset_geom_props = row.get('asset_geometries_properties', {})
        if isinstance(asset_geom_props, str):
            escaped_geom = asset_geom_props.replace('"', '""')
            asset_geom_props_str = f'"{escaped_geom}"'
        else:
            geom_json_str = json.dumps(asset_geom_props)
            escaped_geom = geom_json_str.replace('"', '""')
            asset_geom_props_str = f'"{escaped_geom}"'

        # Build CSV row
        csv_row = ','.join([
            sensor_id, sensor_type_id, asset_id, asset_name,
            asset_subtype_id, asset_metadata_str, asset_geom_props_str
        ])
        csv_lines.append(csv_row)
    
    return '\n'.join(csv_lines)

############################################################################################################
# Name: def convert_geometry_to_geojson(geojson_array)
# Description: Converts geometry array to proper GeoJSON FeatureCollection
############################################################################################################
def convert_geometry_to_geojson(geojson_array):
    """Convert geometry array to GeoJSON FeatureCollection."""
    features = []
    
    for geom_item in geojson_array:
        try:
            # Parse geometry if it's a string
            if isinstance(geom_item.get('geometry'), str):
                geometry = json.loads(geom_item['geometry'])
            else:
                geometry = geom_item.get('geometry', {})
            
            # Get properties
            properties = geom_item.get('properties', {})
            
            # Ensure asset_id is in properties if available
            if 'asset_id' in geom_item and 'asset_id' not in properties:
                properties['asset_id'] = geom_item['asset_id']
            
            # Create feature
            feature = {
                'type': 'Feature',
                'geometry': geometry,
                'properties': properties
            }
            
            features.append(feature)
            
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Skipping invalid geometry item: {e}")
            continue
    
    return {
        'type': 'FeatureCollection',
        'features': features
    }

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
    num_cores = data.get('num_cores', 1)

    # Error checking
    #TODO: Metadata csv should be optional since ideally it should only required for report cleaning
    if not simulation_name or not asset_geojson_path or not metadata_csv_path or num_cores <= 0:
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

    shutil.copy(asset_geojson_path, ASSET_GEOJSON)
    shutil.copy(metadata_csv_path, METADATA_CSV)

    # Call the create_feature_files and initialize_uo to run the simulation and 
    # delete the simulation directory (container)
    try:
        create_table()
        
        # Extract HPC-related parameters from the simulation.json
        shared_storage = data.get('shared_storage', None)
        
        # Use centralized HPC detection
        is_hpc = is_hpc_environment()
        
        logger.debug(f"HPC Environment: {is_hpc}, Shared Storage: {shared_storage}")
        
        logger.debug("Calling create_feature_files from autorun_simulation()")
        create_featurefiles(SIMULATION_DIR, LOCAL_DIR, ASSET_GEOJSON, METADATA_CSV, num_cores, simulation_name)
        logger.debug("Exited create_feature_files")
                
        logger.debug("Calling initialize_uo from autorun_simulation()")
        initialize_uo(SIMULATION_DIR, LOCAL_DIR, simulation_name)
        logger.debug("Exited initialize_uo")
        
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
    
    # Prefer the status.json the async worker writes after each phase.
    sim_local_dir = os.path.join('powertwin-solver-pg', 'user_files', simulation_name)
    status_path = os.path.join(sim_local_dir, 'status.json')
    if os.path.isfile(status_path):
        try:
            with open(status_path, 'r') as f:
                return jsonify(json.load(f)), 200
        except Exception as e:
            logger.error(f"Failed to read status.json for {simulation_name}: {e}")
            send_error_to_mss('get_simulation_status', str(e))
            return jsonify({'error': str(e)}), 500

    # Fallback to legacy diagnostics for non-async callers.
    try:
        logger.debug(f"Entering read_simulation_status() with batch_id={batch_id}")
        read_simulation_status(simulation_name, batch_id)
        return jsonify({'phase': 'unknown', 'message': 'no status.json on disk'}), 200
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

    auth_err = _check_api_token()
    if auth_err is not None:
        return auth_err

    if simulation_name is None:
        logger.error("Error: Simulation name is required.")
        return jsonify({'error': 'Simulation name is required.'}), 400

    SIMULATION_DIR = os.path.join(DATA_DIR, simulation_name)
    LOCAL_DIR_SIM = os.path.join('powertwin-solver-pg', 'user_files', simulation_name)

    removed = []
    try:
        if os.path.isdir(SIMULATION_DIR):
            shutil.rmtree(SIMULATION_DIR)
            removed.append(SIMULATION_DIR)
        if os.path.isdir(LOCAL_DIR_SIM):
            shutil.rmtree(LOCAL_DIR_SIM)
            removed.append(LOCAL_DIR_SIM)
        if not removed:
            return jsonify({'message': 'Nothing to delete', 'simulation_name': simulation_name}), 200
        logger.info(f"Simulation '{simulation_name}' cleanup removed: {removed}")
        return jsonify({'message': 'Simulation cleaned up', 'removed': removed}), 200
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
                'Asset ID', 'Batch', 'Order Rank', 'Simulation Name', 'State', 'Weather File', 
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
                    asset['state'],
                    asset['weather_file'],
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

    # Only this file requires error handling
    if not os.path.exists(metadata_csv_path):
        logger.error("Metadata CSV file not found in the corrupted simulation directory")
        return jsonify({'error': 'Metadata CSV file not found in the corrupted simulation directory'}), 404

    # Copy and rename the metadata CSV and geojson to the recovery directory
    new_metadata_csv_path = os.path.join(RECOVERY_DIR_LOCAL, f'{recover_simulation_name}_metadata.csv')
    new_geojson_name_path = os.path.join(RECOVERY_DIR_LOCAL, f'{recover_simulation_name}_asset.geojson')

    shutil.copy(metadata_csv_path, new_metadata_csv_path)
    shutil.copy(geojson_path, new_geojson_name_path)
    
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
def get_logs(shared_storage=None):
    logger.debug("Within get_logs()")
    
    # Use centralized HPC detection
    is_hpc = is_hpc_environment()
    
    # Set up paths differently based on whether we're in HPC mode
    if is_hpc and shared_storage:
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
    
    REQUESTED_LOG_FILE = os.path.join(REQUESTED_FILES_DIR, 'dev.log')
    LOG_FILE = os.path.join(LOGS_DIR, 'dev.log')
    
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
        if not is_hpc:  # Only return response in non-HPC mode
            return jsonify({'error': str(e)}), 500
    
    # Only try to render template in non-HPC mode
    if not is_hpc:
        with open(REQUESTED_LOG_FILE, 'r') as file:
            logs = file.read()
        # Render the logs in the template
        return render_template('logs.html', logs=logs)


############################################################################################################
# Name: def update_asset()
# Description: This function forces an asset's status to 'Failed' so it will be reprocessed during recovery.
# Calls the update_status function to set the asset status to 'Failed'.
############################################################################################################
def update_asset():
    from modules.diagnostics.db import update_status
    
    logger.debug("Within update_asset()")
    
    # Get parameters from request
    asset_id = request.form.get('asset_id')
    simulation_name = request.form.get('simulation_name')
    
    # Validate parameters
    if not asset_id:
        logger.error("Missing asset_id parameter")
        return jsonify({'error': 'Missing asset_id parameter'}), 400
        
    if not simulation_name:
        logger.error("Missing simulation_name parameter")
        return jsonify({'error': 'Missing simulation_name parameter'}), 400
    
    try:
        # Update the asset status to Failed
        result = update_status('Failed', asset_id, simulation_name)
        
        if result:
            logger.info(f"Successfully marked asset {asset_id} in simulation {simulation_name} as Failed")
            return jsonify({
                'success': True, 
                'message': f'Asset {asset_id} has been marked as Failed and will be reprocessed during recovery'
            })
        else:
            logger.error(f"Failed to update status for asset {asset_id}")
            return jsonify({'error': f'Failed to update status for asset {asset_id}'}), 500
            
    except Exception as e:
        logger.error(f"Error updating asset {asset_id}: {str(e)}")
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

############################################################################################################
# Name: def log_message()
# Description: This function logs a message to the dev.log file.
# Calls the log_message function to log a message to the dev.log file.
############################################################################################################
def log_message():
    data = request.get_json()
    message = data.get('message')
    log_type = data.get('type', 'log')
    
    # Create a timestamp for the log entry
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Define the log file path
    log_txt = os.path.join('logs','dev.log')
    os.makedirs(os.path.dirname(log_txt), exist_ok=True)

    # Append the log entry to the log file
    with open(log_txt, 'a') as log_file:
        log_file.write(f"[{timestamp}] [{log_type.upper()}] {message}\n")

    return jsonify({'status': 'success', 'log_file': log_txt}), 200


############################################################################################################
# Name: def get_simulation_results()
# Description: Serve the persisted results.json for the API to ingest. 404 when the simulation is still
# in flight (no results.json yet) or never existed. The API polls until completed and then reads this.
############################################################################################################
def get_simulation_results(simulation_name):
    auth_err = _check_api_token()
    if auth_err is not None:
        return auth_err
    if simulation_name is None:
        return jsonify({'error': 'Simulation name is required.'}), 400
    sim_local_dir = os.path.join('powertwin-solver-pg', 'user_files', simulation_name)
    results_path = os.path.join(sim_local_dir, 'results.json')
    if not os.path.isfile(results_path):
        return jsonify({'error': 'results.json not found', 'simulation_name': simulation_name}), 404
    try:
        with open(results_path, 'r') as f:
            return jsonify(json.load(f))
    except Exception as e:
        logger.error(f"Failed to read results.json for {simulation_name}: {e}")
        send_error_to_mss('get_simulation_results', str(e))
        return jsonify({'error': str(e)}), 500

