# ======================================================================================
# PowerTwin Solver Views Module
# This module contains all Flask view functions (controllers) that handle HTTP requests
# for simulation management, diagnostics, monitoring, and data retrieval.
# Each function corresponds to a REST API endpoint defined in routes.py
# ======================================================================================

import shutil
import os
import json
import datetime
from datetime import timezone
import csv
import threading
import zipfile

from flask import request, jsonify, render_template, send_file

from modules.simulation import initialize_uo, create_featurefiles, stop_UOsimulation
from modules.diagnostics import read_simulation_status, simulation_recovery, create_table
from modules.diagnostics.log_manager import get_log_streamer
from modules.diagnostics.status_tracker import get_tracker, get_simulation_summary, get_tracker_stats
from modules.diagnostics.db_optimizer import get_optimization_stats, invalidate_cache
from modules.diagnostics.performance_monitor import (
    get_monitor, check_log_health, check_system_health, 
    get_performance_report, get_recent_alerts, record_query_metric
)
from modules.diagnostics.simulation_performance import get_performance_tracker
from modules.utils import initialize_logger, send_error_to_mss
from modules.utils.hpc_environment import is_hpc_environment, get_hpc_info

# Initialize logger for this module
external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Views', external_log_dir)

# Directory paths for simulation data and local file storage
DATA_DIR = os.path.join('data')  # Container directory where simulations run
LOCAL_DIR = os.path.join('powertwin-solver-pg', 'user_files')  # Host directory for persistent storage
CURRENT_SIM_STATE_FILE = os.path.join('powertwin_data', 'current_simulation.json')  # File tracking currently running simulation

# ============ Simulation State Management =============

def save_simulation_state(simulation_name, status, progress=None):
    """
    Save the current simulation state to a JSON file for persistence across page refreshes.
    Uses atomic write (temp file + rename) to ensure data integrity.
    
    Args:
        simulation_name: Name of the simulation
        status: Current status ('running', 'completed', 'failed', etc.)
        progress: Optional dict with progress details (e.g., assets_processed, total_assets)
    """
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(CURRENT_SIM_STATE_FILE), exist_ok=True)
        
        state_data = {
            'simulation_name': simulation_name,
            'status': status,
            'last_updated': datetime.datetime.now(timezone.utc).isoformat(),
            'progress': progress or {}
        }
        
        # Atomic write: write to temp file, then rename to avoid corrupted state file
        temp_file = CURRENT_SIM_STATE_FILE + '.tmp'
        with open(temp_file, 'w') as f:
            json.dump(state_data, f, indent=2)
        
        # Atomic rename
        if os.path.exists(CURRENT_SIM_STATE_FILE):
            os.remove(CURRENT_SIM_STATE_FILE)
        os.rename(temp_file, CURRENT_SIM_STATE_FILE)
        
        logger.debug(f"Saved simulation state: {simulation_name} - {status}")
    except Exception as e:
        logger.error(f"Error saving simulation state: {str(e)}")

def get_current_simulation():
    """
    Retrieve the current simulation state from file.
    
    Returns:
        dict: Current simulation state, or None if no simulation is running
    """
    try:
        if os.path.exists(CURRENT_SIM_STATE_FILE):
            with open(CURRENT_SIM_STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error reading simulation state: {str(e)}")
    return None

def clear_simulation_state():
    """Clear the current simulation state file."""
    try:
        if os.path.exists(CURRENT_SIM_STATE_FILE):
            os.remove(CURRENT_SIM_STATE_FILE)
            logger.debug("Cleared simulation state")
    except Exception as e:
        logger.error(f"Error clearing simulation state: {str(e)}")

# ============ UI ENDPOINTS ============

# Display the main application interface
def home():
    # Return the base HTML template for the web UI
    return render_template('base.html')

# 1. Simulation Managment

############################################################################################################
# Name: def start_simulation()
# Description: This function requires ASSET_GEOJSON, METADATA_CSV, config_data, and simulation_name, 
# and num_cores to start the simulation. Performs error checking and creates a directory 
# based on the given simulation name. Calls the create_featurefiles and initialize_uo functions to
# generate feature files and start the UrbanOpt simulation. This function parallelizes the 
# simulation after the feature files are created.
############################################################################################################
def start_simulation():
    logger.debug("Within start_simulation()")
    
    # Extract form data and uploaded files from the HTTP request
    ASSET_GEOJSON = request.files.get('asset_geojson_file')  # GeoJSON file containing building geometry
    METADATA_CSV = request.files.get('metadata_csv_file')  # CSV metadata for buildings
    config_data = request.form.get('config_data')  # JSON string with simulation configuration
    simulation_name = request.form.get('simulation_name')  # Unique identifier for this simulation run
    num_cores = int(request.form.get('num_cores', 1))  # Number of CPU cores for parallelization
    shared_storage = request.form.get('shared_storage')  # Shared storage path for HPC environments
    keep_dirs = request.form.get('keep_dirs', 'false').lower() == 'true'  # Flag to preserve directories after simulation
    
    # Check if running in HPC environment
    is_hpc = is_hpc_environment()

    # Set environment variable for keep directories flag if requested
    if keep_dirs:
        os.environ['POWERTWIN_KEEP_DIRS'] = '1'
    else:
        os.environ.pop('POWERTWIN_KEEP_DIRS', None)

    # Reference the volume directory where the local files will be stored
    # TODO: Set as global variable for consistency across all LOCAL_DIR references 
    LOCAL_DIR = os.path.join('powertwin-solver-pg', 'user_files')
    
    # Validate that all required parameters are provided and valid
    #TODO: Metadata csv should be optional since its ideally  only required for report cleaning
    if not ASSET_GEOJSON or not METADATA_CSV or not config_data or not simulation_name or num_cores <= 0:
        logger.error("Error: missing or invalid parameter.")
        return jsonify({'error': 'missing or invalid parameter'}), 400
    
    # Ensure shared storage is specified when running on HPC systems
    if is_hpc and not shared_storage:
        logger.error("Error: shared_storage is required when in HPC environment.")
        return jsonify({'error': 'shared_storage is required when in HPC environment'}), 400
    
    # Create simulation and local directories for file storage
    # SIMULATION_DIR: Temporary container directory where simulation runs
    # LOCAL_DIR: Persistent host directory for recovery and completed asset files
    SIMULATION_DIR = os.path.join(DATA_DIR, f'{simulation_name}')
    LOCAL_DIR = os.path.join(LOCAL_DIR, f'{simulation_name}')
    
    # Check if simulation with this name already exists
    if os.path.exists(SIMULATION_DIR) or os.path.exists(LOCAL_DIR):
        logger.error("Error: Simulation name already exists.")
        return jsonify({'error': 'Simulation name already exists.'}), 400
    
    # Create the directories
    os.makedirs(SIMULATION_DIR, exist_ok=True)
    os.makedirs(LOCAL_DIR, exist_ok=True)
    logger.info(f"Upload directory: {SIMULATION_DIR}")
    logger.info(f"Local directory: {LOCAL_DIR}")
    
    # Save uploaded files to local directory for persistence and recovery
    asset_geojson_path = os.path.join(LOCAL_DIR, f'{simulation_name}_asset.geojson')
    metadata_csv_path = os.path.join(LOCAL_DIR, f'{simulation_name}_metadata.csv')
    config_json_path = os.path.join(LOCAL_DIR, f'{simulation_name}_config.json')

    # Write uploaded files to disk
    ASSET_GEOJSON.save(asset_geojson_path)
    METADATA_CSV.save(metadata_csv_path)
    with open(config_json_path, 'w') as config_file:
        json.dump(json.loads(config_data), config_file)

    # Prepare data for background thread
    sim_data = {
        'simulation_name': simulation_name,
        'asset_geojson_path': asset_geojson_path,
        'metadata_csv_path': metadata_csv_path,
        'config_json_path': config_json_path,
        'num_cores': num_cores,
        'simulation_dir': SIMULATION_DIR,
        'local_dir': LOCAL_DIR
    }
    
    # Start the simulation in a background thread
    logger.info(f"Starting simulation '{simulation_name}' in background thread")
    thread = threading.Thread(
        target=_run_start_simulation_background,
        args=(sim_data,),
        daemon=False  # Don't make it a daemon thread so it continues even if main thread exits
    )
    thread.start()
    
    # Return immediately to the client
    return jsonify({
        'message': f'Simulation "{simulation_name}" started successfully',
        'simulation_name': simulation_name,
        'status': 'running'
    }), 200

############################################################################################################
# Name: def _run_start_simulation_background()
# Description: Background thread worker function that executes the simulation.
# This runs in a separate thread to avoid blocking the HTTP request.
############################################################################################################
def _run_start_simulation_background(sim_data):
    """Background worker function for start simulation"""
    simulation_name = sim_data['simulation_name']
    SIMULATION_DIR = sim_data['simulation_dir']
    LOCAL_DIR = sim_data['local_dir']
    
    logger.info(f"========== START SIMULATION BACKGROUND THREAD STARTED for {simulation_name} ==========")
    
    try:
        # Update state to running immediately
        save_simulation_state(simulation_name, 'running', {
            'assets_processed': 0,
            'total_assets': 0,
            'current_step': 'initializing'
        })
        logger.info(f"[START] State saved: running")
        
        # Initialize database status table for tracking
        create_table()
        logger.info(f"[START] Database table initialized")
        
        # Step 1: Generate UrbanOpt feature files from GeoJSON
        logger.info(f"[START] ===== STEP 1: Creating feature files =====")
        save_simulation_state(simulation_name, 'running', {
            'assets_processed': 0,
            'total_assets': 0,
            'current_step': 'creating_feature_files'
        })
        create_featurefiles(
            SIMULATION_DIR, 
            LOCAL_DIR, 
            sim_data['asset_geojson_path'], 
            sim_data['metadata_csv_path'], 
            sim_data['config_json_path'], 
            sim_data['num_cores'], 
            simulation_name
        )
        logger.info(f"[START] ===== STEP 1 COMPLETED: Feature files created =====")
        
        # Step 2: Initialize and run UrbanOpt simulation with parallelization
        logger.info(f"[START] ===== STEP 2: Running UrbanOpt simulation =====")
        save_simulation_state(simulation_name, 'running', {
            'assets_processed': 0,
            'total_assets': 0,
            'current_step': 'running_urbanopt'
        })
        initialize_uo(SIMULATION_DIR, LOCAL_DIR, simulation_name)
        logger.info(f"[START] ===== STEP 2 COMPLETED: UrbanOpt simulation finished =====")
        
        # Update state to completed
        save_simulation_state(simulation_name, 'completed', {
            'current_step': 'completed'
        })
        
        # Step 3: Clean up temporary simulation directory
        logger.info(f"[START] ===== STEP 3: Cleaning up =====")
        if os.path.exists(SIMULATION_DIR):
            shutil.rmtree(SIMULATION_DIR)
            logger.info(f"[START] Deleted simulation directory: {SIMULATION_DIR}")
        
        logger.info(f"========== START SIMULATION BACKGROUND THREAD COMPLETED SUCCESSFULLY for {simulation_name} ==========")
        
    except Exception as e:
        logger.error(f"========== START SIMULATION BACKGROUND THREAD FAILED for {simulation_name} ==========")
        logger.error(f"[START] Exception: {str(e)}")
        logger.error(f"[START] Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"[START] Traceback:\n{traceback.format_exc()}")
        send_error_to_mss('start_simulation', str(e))
        
        # Update state to failed
        save_simulation_state(simulation_name, 'failed', {
            'error': str(e),
            'current_step': 'error'
        })
        
        # Clean up simulation directory if it exists
        if SIMULATION_DIR and os.path.exists(SIMULATION_DIR):
            try:
                shutil.rmtree(SIMULATION_DIR)
                logger.info(f"[START] Cleaned up failed simulation directory: {SIMULATION_DIR}")
            except Exception as cleanup_err:
                logger.error(f"[START] Error cleaning up simulation directory: {str(cleanup_err)}")

############################################################################################################
# Name: def _run_autorun_simulation_background()
# Description: Background thread worker function that executes the autorun simulation.
# Reads simulation.json, performs validation, and runs the simulation with state tracking.
# This runs in a separate thread to avoid blocking the HTTP request.
############################################################################################################
def _run_autorun_simulation_background(data, simulation_name):
    """Background worker function for autorun simulation"""
    sim_dir = None
    local_dir_base = os.path.join('powertwin-solver-pg', 'user_files')
    
    logger.info(f"========== AUTORUN BACKGROUND THREAD STARTED for {simulation_name} ==========")
    
    try:
        # Clean up old batch log files from previous simulations
        import glob
        log_dir = os.path.join('logs')
        if os.path.exists(log_dir):
            batch_log_files = glob.glob(os.path.join(log_dir, 'batch_*_logs.log'))
            for batch_log_file in batch_log_files:
                try:
                    os.remove(batch_log_file)
                    logger.debug(f"Removed old batch log: {batch_log_file}")
                except Exception as e:
                    logger.warning(f"Could not remove batch log {batch_log_file}: {e}")
        
        # Update state to running
        save_simulation_state(simulation_name, 'running', {
            'assets_processed': 0,
            'total_assets': 0,
            'current_step': 'initializing'
        })
        logger.info(f"[AUTORUN] State saved: running")
        
        # Extract simulation parameters
        asset_geojson_path = data.get('asset_geojson_path')
        metadata_csv_path = data.get('metadata_csv_path')
        config_json_path = data.get('config_json_path')
        num_cores = data.get('num_cores', 1)
        
        logger.info(f"[AUTORUN] Parameters extracted: geojson={asset_geojson_path}, metadata={metadata_csv_path}, config={config_json_path}, cores={num_cores}")
        
        # Validate files exist
        if not os.path.exists(asset_geojson_path):
            raise FileNotFoundError(f"Asset GeoJSON file not found: {asset_geojson_path}")
        if not os.path.exists(metadata_csv_path):
            raise FileNotFoundError(f"Metadata CSV file not found: {metadata_csv_path}")
        if not os.path.exists(config_json_path):
            raise FileNotFoundError(f"Config JSON file not found: {config_json_path}")
        
        logger.info(f"[AUTORUN] All input files validated")
        
        # Define directories
        sim_dir = os.path.join(DATA_DIR, f'{simulation_name}')
        local_dir = os.path.join(local_dir_base, f'{simulation_name}')
        
        # Create the directories
        os.makedirs(sim_dir, exist_ok=True)
        os.makedirs(local_dir, exist_ok=True)
        logger.info(f"[AUTORUN] Upload directory created: {sim_dir}")
        logger.info(f"[AUTORUN] Local directory created: {local_dir}")
        
        # Copy the files to the Local directory
        asset_geojson_local = os.path.join(local_dir, f'{simulation_name}_asset.geojson')
        metadata_csv_local = os.path.join(local_dir, f'{simulation_name}_metadata.csv')
        config_json_local = os.path.join(local_dir, f'{simulation_name}_config.json')
        
        logger.info(f"[AUTORUN] Copying files...")
        shutil.copy(asset_geojson_path, asset_geojson_local)
        logger.info(f"[AUTORUN] Copied GeoJSON to {asset_geojson_local}")
        shutil.copy(metadata_csv_path, metadata_csv_local)
        logger.info(f"[AUTORUN] Copied CSV to {metadata_csv_local}")
        shutil.copy(config_json_path, config_json_local)
        logger.info(f"[AUTORUN] Copied config to {config_json_local}")
        
        # Initialize database status table
        logger.info(f"[AUTORUN] Initializing database table...")
        create_table()
        logger.info(f"[AUTORUN] Database table initialized")
        
        # Extract HPC-related parameters
        shared_storage = data.get('shared_storage', None)
        is_hpc = is_hpc_environment()
        logger.info(f"[AUTORUN] HPC Environment: {is_hpc}, Shared Storage: {shared_storage}")
        
        # Step 1: Generate UrbanOpt feature files from GeoJSON
        logger.info(f"[AUTORUN] ===== STEP 1: Creating feature files =====")
        save_simulation_state(simulation_name, 'running', {
            'assets_processed': 0,
            'total_assets': 0,
            'current_step': 'creating_feature_files'
        })
        create_featurefiles(sim_dir, local_dir, asset_geojson_local, metadata_csv_local, config_json_local, num_cores, simulation_name)
        logger.info(f"[AUTORUN] ===== STEP 1 COMPLETED: Feature files created =====")
        
        # Step 2: Initialize and run UrbanOpt simulation
        logger.info(f"[AUTORUN] ===== STEP 2: Running UrbanOpt simulation =====")
        save_simulation_state(simulation_name, 'running', {
            'assets_processed': 0,
            'total_assets': 0,
            'current_step': 'running_urbanopt'
        })
        initialize_uo(sim_dir, local_dir, simulation_name)
        logger.info(f"[AUTORUN] ===== STEP 2 COMPLETED: UrbanOpt simulation finished =====")
        
        # Step 3: Clean up temporary simulation directory
        logger.info(f"[AUTORUN] ===== STEP 3: Cleaning up =====")
        if os.path.exists(sim_dir):
            shutil.rmtree(sim_dir)
            logger.info(f"[AUTORUN] Deleted simulation directory: {sim_dir}")
        
        # Update state to completed
        save_simulation_state(simulation_name, 'completed', {
            'assets_processed': 0,
            'total_assets': 0,
            'current_step': 'completed'
        })
        logger.info(f"========== AUTORUN BACKGROUND THREAD COMPLETED SUCCESSFULLY for {simulation_name} ==========")
        
    except Exception as e:
        logger.error(f"========== AUTORUN BACKGROUND THREAD FAILED for {simulation_name} ==========")
        logger.error(f"[AUTORUN] Exception: {str(e)}")
        logger.error(f"[AUTORUN] Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"[AUTORUN] Traceback:\n{traceback.format_exc()}")
        send_error_to_mss('autorun_simulation', str(e))
        
        # Update state to failed
        save_simulation_state(simulation_name, 'failed', {
            'error': str(e),
            'current_step': 'error'
        })
        
        # Clean up simulation directory if it exists
        if sim_dir and os.path.exists(sim_dir):
            try:
                shutil.rmtree(sim_dir)
                logger.info(f"[AUTORUN] Cleaned up failed simulation directory: {sim_dir}")
            except Exception as cleanup_err:
                logger.error(f"[AUTORUN] Error cleaning up simulation directory: {str(cleanup_err)}")

############################################################################################################
# Name: def autorun_simulation()
# Description: This function reads the simulation.json file and starts the simulation in a background thread.
# The HTTP request returns immediately with a success message, while the simulation continues to run.
# The simulation state is persisted to a file, so it continues even if the UI is refreshed.
############################################################################################################
def autorun_simulation():
    logger.debug("Within autorun_simulation()")
    
    # Path to the JSON configuration file that defines the simulation
    SIMULATION_JSON = os.path.join('upload', 'simulation.json')
    
    # Check if the configuration file exists
    if not os.path.exists(SIMULATION_JSON):
        logger.error("Error: simulation.json file not found.")
        return jsonify({'error': 'simulation.json file not found.'}), 404
    
    try:
        # Get the parameters from the simulation.json file
        with open(SIMULATION_JSON, 'r') as file:
            data = json.load(file)
        
        # Extract required simulation parameters from config
        simulation_name = data.get('simulation_name')
        asset_geojson_path = data.get('asset_geojson_path')
        metadata_csv_path = data.get('metadata_csv_path')
        config_json_path = data.get('config_json_path')
        num_cores = data.get('num_cores', 1)
        
        # Validate that all required parameters are present
        if not simulation_name or not asset_geojson_path or not metadata_csv_path or not config_json_path or num_cores <= 0:
            logger.error("Error: Missing required fields in simulation.json")
            return jsonify({'error': 'Missing required fields in simulation.json'}), 400
        
        # Check if simulation with this name already exists
        local_dir_base = os.path.join('powertwin-solver-pg', 'user_files')
        SIMULATION_DIR = os.path.join(DATA_DIR, f'{simulation_name}')
        LOCAL_DIR = os.path.join(local_dir_base, f'{simulation_name}')
        
        if os.path.exists(SIMULATION_DIR) or os.path.exists(LOCAL_DIR):
            logger.error("Error: Simulation name already exists.")
            return jsonify({'error': 'Simulation name already exists.'}), 400
        
        # Start the simulation in a background thread
        logger.info(f"Starting autorun simulation '{simulation_name}' in background thread")
        thread = threading.Thread(
            target=_run_autorun_simulation_background,
            args=(data, simulation_name),
            daemon=False  # Don't make it a daemon thread so it continues even if main thread exits
        )
        thread.start()
        
        # Return immediately to the client
        return jsonify({
            'message': f'Autorun simulation "{simulation_name}" started successfully',
            'simulation_name': simulation_name,
            'status': 'running'
        }), 200
        
    except Exception as e:
        logger.error(f"Exception in autorun_simulation: {str(e)}")
        send_error_to_mss('autorun_simulation', str(e))
        return jsonify({'error': f"Failed to start autorun simulation: {str(e)}"}), 500

############################################################################################################
# Name: def stop_simulation()
# Description: This function stops the UrbanOpt simulation and clears the current simulation state.
# Calls the stop_UOsimulation function to gracefully and aggressively stop the simulation.
# WARNING: KILLS ALL PIDS WITHIN THE CONTAINER BESIDES ANY WITH app.py
############################################################################################################
def stop_simulation():
    logger.debug("Within stop_simulation()")
    logger.info("========== STOP SIMULATION REQUEST ==========")
    
    try:
        # Get current simulation info before stopping
        current_sim = get_current_simulation()
        if current_sim:
            logger.info(f"Stopping simulation: {current_sim.get('simulation_name')}")
        
        # Clear the simulation state immediately
        clear_simulation_state()
        logger.info("Cleared simulation state")
        
        # Start background thread to kill processes
        logger.info("Starting stop process in background thread")
        thread = threading.Thread(
            target=_run_stop_simulation_background,
            daemon=False
        )
        thread.start()
        
        # Return immediately to the client
        logger.info("========== SIMULATION STOP REQUEST ACCEPTED ==========")
        return jsonify({'message': 'Simulation stopped successfully'}), 200
        
    except Exception as e:
        logger.error(f"========== STOP SIMULATION FAILED ==========")
        logger.error(f"Exception while stopping the simulation: {str(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        send_error_to_mss('stop_simulation', str(e))
        return jsonify({'error': f"Failed to stop simulation: {str(e)}"}), 500


def _run_stop_simulation_background():
    """Background worker function for stopping simulation"""
    logger.info("========== STOP SIMULATION BACKGROUND THREAD STARTED ==========")
    
    try:
        logger.debug("Calling stop_UOsimulation()")
        stop_UOsimulation()
        logger.info("stop_UOsimulation() completed successfully")
        logger.info("========== STOP SIMULATION BACKGROUND THREAD COMPLETED ==========")
        
    except Exception as e:
        logger.error(f"========== STOP SIMULATION BACKGROUND THREAD FAILED ==========")
        logger.error(f"Exception: {str(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        send_error_to_mss('stop_simulation_background', str(e))


############################################################################################################
# Name: def get_current_simulation_status()
# Description: Returns the currently running simulation status without requiring a simulation_name parameter.
# Reads from the persistent state file that tracks which simulation is currently active.
############################################################################################################
def get_current_simulation_status():
    """Get the status of the currently running simulation (if any)"""
    # Disabled debug logging to reduce log spam from frequent polling
    # logger.debug("Within get_current_simulation_status()")
    
    try:
        current_sim = get_current_simulation()
        
        if not current_sim:
            return jsonify({
                'has_active_simulation': False,
                'message': 'No active simulation'
            }), 200
        
        simulation_name = current_sim.get('simulation_name')
        progress = current_sim.get('progress', {})
        
        # Ensure total_assets is set from database if not in state file
        if progress.get('total_assets') is None or progress.get('total_assets') == 0:
            try:
                from modules.diagnostics import get_asset_total
                total_in_db = get_asset_total(simulation_name)
                progress['total_assets'] = total_in_db
            except Exception as e:
                logger.debug(f"Could not get total_assets from database: {str(e)}")
        
        # Query database as fallback for assets_processed count
        try:
            from modules.diagnostics import get_asset_total
            total_in_db = get_asset_total(simulation_name)
            # Count completed + failed assets from database
            completed_count = 0
            try:
                import sqlite3
                db_path = os.path.join('powertwin_data', 'powertwin_default.db')
                if os.path.exists(db_path):
                    conn = sqlite3.connect(db_path)
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT COUNT(*) FROM powertwin WHERE simulation_name = ? AND status IN ('Processing', 'Finished', 'Failed')",
                        (simulation_name,)
                    )
                    completed_count = cursor.fetchone()[0]
                    conn.close()
            except Exception as db_error:
                logger.debug(f"Database fallback count failed: {str(db_error)}")
            # Use state file value if available, otherwise use database count
            if progress.get('assets_processed') is None or progress.get('assets_processed') == 0:
                if completed_count > 0:
                    progress['assets_processed'] = completed_count
        except Exception as fallback_error:
            logger.debug(f"Database fallback lookup failed: {str(fallback_error)}")
        
        return jsonify({
            'has_active_simulation': True,
            'simulation_name': simulation_name,
            'status': current_sim.get('status'),
            'progress': progress,
            'last_updated': datetime.datetime.now(timezone.utc).isoformat()  # Fresh timestamp on every call
        }), 200
        
    except Exception as e:
        logger.error(f"Exception in get_current_simulation_status: {str(e)}")
        return jsonify({'error': str(e)}), 500


############################################################################################################
# Name: def get_current_logs()
# Description: Returns recent logs without requiring any parameters.
# Useful for a "Get Logs" button that can be clicked to fetch latest logs.
############################################################################################################
def get_current_logs():
    """Get current logs from the user or dev log file (improved format)"""
    # Disabled debug logging to reduce log spam from frequent polling
    # logger.debug("Within get_current_logs()")
    
    try:
        # Extract optional query parameters
        num_lines = request.args.get('lines', default=None, type=int)  # None = all lines
        level_filter = request.args.get('level', default=None, type=str)
        log_type = request.args.get('log_type', default='user', type=str)  # New parameter: 'user' or 'dev'
        
        # Validate parameters
        if num_lines is not None and (num_lines < 1 or num_lines > 10000):
            num_lines = 100
        
        # Validate log_type parameter
        if log_type.startswith('batch_'):
            # Extract batch number from batch_N format
            try:
                batch_num = int(log_type.split('_')[1])
                log_filename = f'batch_{batch_num}_logs.log'
            except (IndexError, ValueError):
                log_type = 'user'
                log_filename = 'user_logs.log'
        elif log_type == 'user':
            log_filename = 'user_logs.log'
        elif log_type == 'dev':
            log_filename = 'dev_logs.log'
        elif log_type == 'error':
            log_filename = 'error_logs.log'
        else:
            log_type = 'user'
            log_filename = 'user_logs.log'
        
        # Define log file path
        LOGS_DIR = os.path.join('logs')
        LOG_FILE = os.path.join(LOGS_DIR, log_filename)
        
        # Create log file if it doesn't exist
        if not os.path.exists(LOG_FILE):
            return jsonify({
                'lines': [],
                'count': 0,
                'message': 'No logs available yet'
            }), 200
        
        # Read full log file or tail
        try:
            with open(LOG_FILE, 'r', encoding='utf-8', errors='ignore') as f:
                if num_lines is None:
                    # Read entire file
                    all_lines = f.readlines()
                    lines = [line.rstrip('\n') for line in all_lines]
                else:
                    # Read last N lines
                    f.seek(0, 2)  # Seek to end
                    file_size = f.tell()
                    f.seek(max(0, file_size - 100000))  # Go back ~100KB
                    chunk = f.read()
                    all_lines = chunk.split('\n')
                    lines = [line for line in all_lines if line.strip()][-num_lines:]
        except IOError as e:
            return jsonify({'error': f'Error reading log file: {str(e)}'}), 500
        
        return jsonify({
            'lines': lines,
            'count': len(lines),
            'level_filter': level_filter,
            'timestamp': datetime.datetime.now(timezone.utc).isoformat()
        }), 200
        
    except Exception as e:
        logger.error(f"Exception in get_current_logs: {str(e)}")
        return jsonify({'error': str(e)}), 500


############################################################################################################
# Name: def refresh_logs()
# Description: Refreshes and copies both dev and user log files to the requested_files folder.
# Returns JSON response with success/error message.
# Supports HPC and container modes like get_logs() does.
############################################################################################################
def refresh_logs():
    """Refresh and copy log files to requested_files directory"""
    # Check if running in HPC environment
    is_hpc = is_hpc_environment()
    
    # Set up log file paths based on deployment mode (HPC vs container)
    if is_hpc:
        shared_storage = request.form.get('shared_storage', None)
        if shared_storage:
            logger.debug(f"Running in HPC mode with shared storage: {shared_storage}")
            # In HPC mode, logs are stored in shared storage for multi-node access
            REQUESTED_FILES_DIR = os.path.join(shared_storage, 'logs', 'requested_files')
            LOGS_DIR = os.path.join(shared_storage, 'logs')
        else:
            # In HPC mode without explicit shared storage, use LOCAL_DIR
            REQUESTED_FILES_DIR = os.path.join(LOCAL_DIR, 'requested_files')
            LOGS_DIR = os.path.join('logs')
    else:
        # In container/local mode, logs are stored locally
        REQUESTED_FILES_DIR = os.path.join(LOCAL_DIR, 'requested_files')
        LOGS_DIR = os.path.join('logs')
    
    # Create directories if they don't exist
    os.makedirs(REQUESTED_FILES_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)
    
    # Define log files (dev, user, and error logs)
    DEV_REQUESTED_LOG_FILE = os.path.join(REQUESTED_FILES_DIR, 'dev_logs.log')
    USER_REQUESTED_LOG_FILE = os.path.join(REQUESTED_FILES_DIR, 'user_logs.log')
    ERROR_REQUESTED_LOG_FILE = os.path.join(REQUESTED_FILES_DIR, 'error_logs.log')
    DEV_LOG_FILE = os.path.join(LOGS_DIR, 'dev_logs.log')
    USER_LOG_FILE = os.path.join(LOGS_DIR, 'user_logs.log')
    ERROR_LOG_FILE = os.path.join(LOGS_DIR, 'error_logs.log')
    
    try:
        # Copy dev_logs.log to requested_files
        if os.path.exists(DEV_LOG_FILE):
            with open(DEV_LOG_FILE, 'r') as file:
                dev_logs = file.read()
            with open(DEV_REQUESTED_LOG_FILE, 'w') as file:
                file.write(dev_logs)
            logger.debug(f"Successfully refreshed dev logs to {DEV_REQUESTED_LOG_FILE}")
        else:
            logger.warning(f"Dev log file does not exist at {DEV_LOG_FILE}")
        
        # Copy user_logs.log to requested_files
        if os.path.exists(USER_LOG_FILE):
            with open(USER_LOG_FILE, 'r') as file:
                user_logs = file.read()
            with open(USER_REQUESTED_LOG_FILE, 'w') as file:
                file.write(user_logs)
            logger.debug(f"Successfully refreshed user logs to {USER_REQUESTED_LOG_FILE}")
        else:
            logger.warning(f"User log file does not exist at {USER_LOG_FILE}")
        
        # Copy error_logs.log to requested_files
        if os.path.exists(ERROR_LOG_FILE):
            with open(ERROR_LOG_FILE, 'r') as file:
                error_logs = file.read()
            with open(ERROR_REQUESTED_LOG_FILE, 'w') as file:
                file.write(error_logs)
            logger.debug(f"Successfully refreshed error logs to {ERROR_REQUESTED_LOG_FILE}")
        else:
            logger.warning(f"Error log file does not exist at {ERROR_LOG_FILE}")
        
        # Copy all batch_*_logs.log files to requested_files
        import glob
        batch_log_files = glob.glob(os.path.join(LOGS_DIR, 'batch_*_logs.log'))
        for batch_log_file in batch_log_files:
            batch_log_filename = os.path.basename(batch_log_file)
            batch_requested_file = os.path.join(REQUESTED_FILES_DIR, batch_log_filename)
            try:
                with open(batch_log_file, 'r') as file:
                    batch_logs = file.read()
                with open(batch_requested_file, 'w') as file:
                    file.write(batch_logs)
                logger.debug(f"Successfully refreshed {batch_log_filename} to {batch_requested_file}")
            except Exception as e:
                logger.warning(f"Could not copy {batch_log_filename}: {e}")
        
        return jsonify({
            'success': True,
            'message': 'Logs refreshed successfully',
            'timestamp': datetime.datetime.now(timezone.utc).isoformat()
        }), 200
            
    except Exception as e:
        logger.error(f"Exception while refreshing log files: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


############################################################################################################
# Name: def get_available_batch_logs()
# Description: Returns list of available batch log files from current simulation.
# Scans logs directory for batch_*_logs.log files and extracts batch numbers.
############################################################################################################
def get_available_batch_logs():
    """Get list of available batch log files"""
    try:
        import glob
        import re
        
        LOGS_DIR = os.path.join('logs')
        
        # Find all batch log files
        batch_log_files = glob.glob(os.path.join(LOGS_DIR, 'batch_*_logs.log'))
        
        # Extract batch numbers from filenames
        batch_numbers = []
        for batch_file in batch_log_files:
            filename = os.path.basename(batch_file)
            match = re.search(r'batch_(\d+)_logs\.log', filename)
            if match:
                batch_num = int(match.group(1))
                batch_numbers.append(batch_num)
        
        # Sort batch numbers
        batch_numbers.sort()
        
        return jsonify({
            'batches': batch_numbers,
            'count': len(batch_numbers),
            'timestamp': datetime.datetime.now(timezone.utc).isoformat()
        }), 200
        
    except Exception as e:
        logger.error(f"Exception in get_available_batch_logs: {str(e)}")
        return jsonify({'error': str(e)}), 500


# Retrieve the detailed status of a specific simulation by name
# Optionally filters by batch ID to get status for a specific batch
def get_simulation_status(simulation_name):
    logger.debug("Within simulation_status()")
    
    # Extract optional batch_id parameter from query string
    batch_id = request.args.get('batch_id', default=None, type=int)

    # Validate that simulation name was provided
    if simulation_name is None:
        logger.error("Error: Simulation name is required.")
        return jsonify({'error': 'Simulation name is required.'}), 400
    
    # Read and log the simulation status files
    try:
        logger.debug(f"Entering read_simulation_status() from simulation_status() with batch_id={batch_id}")
        read_simulation_status(simulation_name, batch_id)  # Retrieve and log status
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
    
    # Validate that simulation name was provided
    if simulation_name is None:
        logger.error("Error: Simulation name is required.")
        return jsonify({'error': 'Simulation name is required.'}), 400

    # Construct path to the simulation directory
    SIMULATION_DIR = os.path.join(DATA_DIR, simulation_name)    
    
    # Check if directory exists
    if not os.path.exists(SIMULATION_DIR):
        logger.error("Simulation status directory not found")
        return jsonify({'error': 'Simulation status directory not found'}), 404
    
    # Delete simulation directory
    try:
        shutil.rmtree(SIMULATION_DIR)  # Recursively delete directory and all contents
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
# If feature_files directory doesn't exist, extracts from feature_files.zip.
# Returns the feature file as a response. Available in the request_files directory.
############################################################################################################
def get_asset_config(simulation_name, asset_id):
    logger.debug("Within get_asset_config()")
    
    # Validate required parameters
    if not asset_id or not simulation_name:
        logger.error("Error: Asset ID and Simulation Name are required")
        return jsonify({'error': 'Asset ID and Simulation Name are required'}), 400
           
    try:
        # Search to see if user_files directory exists, so that we can search for the feature file
        SIMULATION_DIR = os.path.join(LOCAL_DIR, f'{simulation_name}')
        if not os.path.exists(SIMULATION_DIR):
            logger.error("Simulation directory does not exist")
            return jsonify({'error': 'Simulation directory does not exist'}), 404

        # Look for the feature_files subdirectory containing UrbanOpt feature JSON files
        FEATURE_FILE_DIR = os.path.join(SIMULATION_DIR, 'feature_files')
        FEATURE_FILE_ZIP = os.path.join(SIMULATION_DIR, 'feature_files.zip')
        
        # If feature_files directory doesn't exist, try to extract from zip
        if not os.path.exists(FEATURE_FILE_DIR):
            if os.path.exists(FEATURE_FILE_ZIP):
                logger.debug(f"feature_files directory not found, extracting from {FEATURE_FILE_ZIP}")
                try:
                    os.makedirs(FEATURE_FILE_DIR, exist_ok=True)
                    with zipfile.ZipFile(FEATURE_FILE_ZIP, 'r') as zip_ref:
                        zip_ref.extractall(FEATURE_FILE_DIR)
                    logger.debug(f"Successfully extracted feature files from zip")
                except Exception as e:
                    logger.error(f"Error extracting feature files from zip: {str(e)}")
                    return jsonify({'error': f'Error extracting feature files: {str(e)}'}), 500
            else:
                logger.error(f"Neither feature_files directory nor feature_files.zip found")
                return jsonify({'error': 'No feature files found for this simulation'}), 404

        # Search for the asset ID in the feature files
        logger.debug(f"Searching for feature file in {FEATURE_FILE_DIR}")
        if not os.path.exists(FEATURE_FILE_DIR):
            logger.error(f"Feature files directory does not exist")
            return jsonify({'error': 'No feature files directory found'}), 404
        
        # Search recursively for the file (in case zip extracted with nested structure)
        found_file = None
        for root, dirs, files in os.walk(FEATURE_FILE_DIR):
            for file_name in files:
                # Match files with pattern: <asset_id>_<descriptor>.json
                if file_name.startswith(f"{asset_id}_") and file_name.endswith('.json'):
                    found_file = os.path.join(root, file_name)
                    logger.debug(f"Found matching file: {found_file}")
                    break
            if found_file:
                break
        
        if found_file and os.path.exists(found_file):
            logger.info(f"Successfully found asset config for {asset_id} at {found_file}")
            # Read and return the JSON content directly
            with open(found_file, 'r') as f:
                json_content = f.read()
            return json_content, 200, {'Content-Type': 'application/json'}
    
        logger.error(f"No feature file found for asset ID: {asset_id}")
        return jsonify({'error': f'No feature file found for asset ID: {asset_id}'}), 404
    
    except Exception as e:
        logger.error(f"Exception: {str(e)}")
        send_error_to_mss('get_asset_config', str(e))
        return jsonify({'error': str(e)}), 500


############################################################################################################
# Name: def get_simulation_assets()
# Description: Returns a list of all assets for a given simulation with their details.
# Extracts feature files from zip if needed and returns asset metadata.
############################################################################################################
def get_simulation_assets(simulation_name):
    logger.debug(f"Within get_simulation_assets() for simulation: {simulation_name}")
    
    try:
        # Validate simulation directory exists
        SIMULATION_DIR = os.path.join(LOCAL_DIR, f'{simulation_name}')
        if not os.path.exists(SIMULATION_DIR):
            logger.error(f"Simulation directory does not exist: {SIMULATION_DIR}")
            return jsonify({'error': 'Simulation directory does not exist'}), 404

        FEATURE_FILE_DIR = os.path.join(SIMULATION_DIR, 'feature_files')
        FEATURE_FILE_ZIP = os.path.join(SIMULATION_DIR, 'feature_files.zip')
        
        # Extract from zip if needed
        if not os.path.exists(FEATURE_FILE_DIR):
            if os.path.exists(FEATURE_FILE_ZIP):
                logger.debug(f"Extracting feature files from {FEATURE_FILE_ZIP}")
                try:
                    os.makedirs(FEATURE_FILE_DIR, exist_ok=True)
                    with zipfile.ZipFile(FEATURE_FILE_ZIP, 'r') as zip_ref:
                        zip_ref.extractall(FEATURE_FILE_DIR)
                except Exception as e:
                    logger.error(f"Error extracting feature files: {str(e)}")
                    return jsonify({'error': f'Error extracting feature files: {str(e)}'}), 500
            else:
                logger.error("No feature files found for this simulation")
                return jsonify({'error': 'No feature files found for this simulation'}), 404

        # Collect all asset files
        assets = []
        for root, dirs, files in os.walk(FEATURE_FILE_DIR):
            for file_name in files:
                if file_name.endswith('.json'):
                    # Extract asset ID and name from filename (format: assetID_name.json)
                    try:
                        parts = file_name.rsplit('.', 1)[0].split('_', 1)
                        if len(parts) >= 2:
                            asset_id = parts[0]
                            asset_name = parts[1].replace('_', ' ')
                            
                            # Read file to get additional details
                            file_path = os.path.join(root, file_name)
                            try:
                                with open(file_path, 'r') as f:
                                    config = json.load(f)
                                    feature = config.get('features', [{}])[0]
                                    properties = feature.get('properties', {})
                                    
                                    assets.append({
                                        'asset_id': asset_id,
                                        'asset_name': asset_name,
                                        'filename': file_name,
                                        'floor_area': properties.get('floor_area', 'N/A'),
                                        'number_of_stories': properties.get('number_of_stories', 'N/A'),
                                        'building_type': properties.get('building_type', 'N/A')
                                    })
                            except json.JSONDecodeError:
                                logger.warning(f"Could not parse JSON from {file_name}")
                                assets.append({
                                    'asset_id': asset_id,
                                    'asset_name': asset_name,
                                    'filename': file_name,
                                    'floor_area': 'N/A',
                                    'number_of_stories': 'N/A',
                                    'building_type': 'N/A'
                                })
                    except (ValueError, IndexError):
                        logger.warning(f"Could not parse asset info from filename: {file_name}")
                        continue
        
        # Sort by asset ID
        assets.sort(key=lambda x: int(x['asset_id']) if x['asset_id'].isdigit() else 0)
        
        logger.info(f"Found {len(assets)} assets for simulation {simulation_name}")
        return jsonify({
            'simulation_name': simulation_name,
            'asset_count': len(assets),
            'assets': assets
        }), 200
    
    except Exception as e:
        logger.error(f"Exception in get_simulation_assets: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
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
        # Query database for asset statistics from the simulation
        assets_list, filename = get_asset_stats()
        
        # Check if any assets were found
        if not assets_list:
            return jsonify({'error': 'No assets found for the specified simulation'}), 404
            
        # Create directory for requested files if it doesn't exist
        requested_files_dir = os.path.join(LOCAL_DIR, 'requested_files')
        os.makedirs(requested_files_dir, exist_ok=True)
        
        # Define CSV file path
        csv_path = os.path.join(requested_files_dir, filename)
        
        # Write asset data to CSV file
        with open(csv_path, 'w', newline='') as csvfile:
            # Create CSV writer object
            csvwriter = csv.writer(csvfile)
            
            # Write CSV header row with all asset attribute names
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
        
        # Return success response with file path and asset count
        return jsonify({
            'message': 'Simulation stats exported successfully',
            'file_path': csv_path,
            'asset_count': len(assets_list)
        }), 200  # Fixed status code
        
    except Exception as e:
        # Clean up the CSV file if it was created but an error occurred
        logger.error(f"Exception: {str(e)}")
        if csv_path and os.path.exists(csv_path):
            os.remove(csv_path)  # Remove the incomplete file
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
    
    # Extract recovery parameters from request
    corrupted_simulation_name = request.form.get('corrupted_simulation_name')  # Name of the failed simulation
    recover_simulation_name = request.form.get('recover_simulation_name')  # Name for the recovery run
    batch_id = request.form.get('recover_batch_id', default=None, type=int)  # Optional: specific batch to recover
    num_cores = int(request.form.get('num_cores', 1))  # Number of CPU cores for recovery
    keep_dirs = request.form.get('keep_dirs', 'false').lower() == 'true'  # Flag to preserve directories

    # Set environment variable for keep directories flag if requested
    if keep_dirs:
        os.environ['POWERTWIN_KEEP_DIRS'] = '1'
    else:
        os.environ.pop('POWERTWIN_KEEP_DIRS', None)

    # Validate required parameters
    if not corrupted_simulation_name:
        logger.error("Error: Simulation name is required.")
        return jsonify({'error': 'Simulation name is required.'}), 400

    if not recover_simulation_name:
        logger.error("Error: Recover simulation name is required.")
        return jsonify({'error': 'Recover simulation name is required.'}), 400
    
    # Reference local file storage directory
    # TODO: Set as global variable for consistency across all LOCAL_DIR references 
    LOCAL_DIR = os.path.join('powertwin-solver-pg', 'user_files')
    
    # Validate that the corrupted simulation directory exists
    CORRUPTED_SIMULATION_DIR = os.path.join(LOCAL_DIR, corrupted_simulation_name)
    if not os.path.exists(CORRUPTED_SIMULATION_DIR):
        logger.error("Simulation directory not found")
        return jsonify({'error': 'Simulation directory not found'}), 404

    # Create new recovery simulation directories (both container and local storage)
    RECOVERY_DIR_LOCAL = os.path.join(LOCAL_DIR, f'{recover_simulation_name}')
    RECOVERY_DIR_CONTAINER = os.path.join(DATA_DIR, f'{recover_simulation_name}')
    
    # Check if recovery directories already exist to avoid overwriting
    if os.path.exists(RECOVERY_DIR_LOCAL) or os.path.exists(RECOVERY_DIR_CONTAINER):
        logger.debug("Recovery directory already exists")
        return jsonify({'error': 'Recovery directory already exists'}), 400

    # Create the new recovery directories
    os.makedirs(RECOVERY_DIR_CONTAINER, exist_ok=True)
    os.makedirs(RECOVERY_DIR_LOCAL, exist_ok=True)

    # Define paths to the original input files
    metadata_csv_path = os.path.join(CORRUPTED_SIMULATION_DIR, f'{corrupted_simulation_name}_metadata.csv')
    geojson_path = os.path.join(CORRUPTED_SIMULATION_DIR, f'{corrupted_simulation_name}_asset.geojson')
    config_path = os.path.join(CORRUPTED_SIMULATION_DIR, f'{corrupted_simulation_name}_config.json')

    # Validate that metadata CSV exists (required file)
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
    
    # Save initial state and start recovery in background thread
    save_simulation_state(recover_simulation_name, 'running', {
        'current_step': 'initializing'
    })
    logger.info(f"[RECOVERY] Initial state saved for {recover_simulation_name}")
    
    # Prepare recovery data for background thread
    recovery_data = {
        'recover_simulation_name': recover_simulation_name,
        'recovery_dir_container': RECOVERY_DIR_CONTAINER,
        'recovery_dir_local': RECOVERY_DIR_LOCAL,
        'corrupted_simulation_dir': CORRUPTED_SIMULATION_DIR,
        'corrupted_simulation_name': corrupted_simulation_name,
        'num_cores': num_cores,
        'batch_id': batch_id
    }
    
    # Start recovery in background thread and return immediately
    recovery_thread = threading.Thread(
        target=_run_recovery_simulation_background,
        args=(recovery_data,),
        daemon=False
    )
    recovery_thread.start()
    logger.info(f"[RECOVERY] Background thread started for {recover_simulation_name}")
    
    return jsonify({'message': 'Recovery simulation started in background'}), 200



def _run_recovery_simulation_background(recovery_data):
    """Background worker function for recovery simulation"""
    recover_simulation_name = recovery_data['recover_simulation_name']
    RECOVERY_DIR_CONTAINER = recovery_data['recovery_dir_container']
    RECOVERY_DIR_LOCAL = recovery_data['recovery_dir_local']
    CORRUPTED_SIMULATION_DIR = recovery_data['corrupted_simulation_dir']
    corrupted_simulation_name = recovery_data['corrupted_simulation_name']
    num_cores = recovery_data['num_cores']
    batch_id = recovery_data['batch_id']
    
    logger.info(f"========== RECOVERY SIMULATION BACKGROUND THREAD STARTED for {recover_simulation_name} ==========")
    
    try:
        # Update state to running immediately
        save_simulation_state(recover_simulation_name, 'running', {
            'current_step': 'initializing'
        })
        logger.info(f"[RECOVERY] State saved: running")
        
        # Initialize database status table for tracking
        create_table()
        logger.info(f"[RECOVERY] Database table initialized")
        
        # Step 1: Generate UrbanOpt feature files from GeoJSON (with recovery logic)
        logger.info(f"[RECOVERY] ===== STEP 1: Creating feature files =====")
        save_simulation_state(recover_simulation_name, 'running', {
            'current_step': 'creating_feature_files'
        })
        simulation_recovery(RECOVERY_DIR_CONTAINER, RECOVERY_DIR_LOCAL, CORRUPTED_SIMULATION_DIR, corrupted_simulation_name, recover_simulation_name, num_cores, batch_id)
        logger.info(f"[RECOVERY] ===== STEP 1 COMPLETED: Feature files created =====")
        
        # Step 2: Initialize and run UrbanOpt simulation with parallelization
        logger.info(f"[RECOVERY] ===== STEP 2: Running UrbanOpt simulation =====")
        save_simulation_state(recover_simulation_name, 'running', {
            'current_step': 'running_urbanopt'
        })
        initialize_uo(RECOVERY_DIR_CONTAINER, RECOVERY_DIR_LOCAL, recover_simulation_name)
        logger.info(f"[RECOVERY] ===== STEP 2 COMPLETED: UrbanOpt simulation finished =====")
        
        # Update state to completed
        save_simulation_state(recover_simulation_name, 'completed', {
            'current_step': 'completed'
        })
        
        # Step 3: Clean up temporary recovery directory
        logger.info(f"[RECOVERY] ===== STEP 3: Cleaning up =====")
        if os.path.exists(RECOVERY_DIR_CONTAINER):
            shutil.rmtree(RECOVERY_DIR_CONTAINER)
            logger.info(f"[RECOVERY] Deleted recovery directory: {RECOVERY_DIR_CONTAINER}")
        
        logger.info(f"========== RECOVERY SIMULATION BACKGROUND THREAD COMPLETED SUCCESSFULLY for {recover_simulation_name} ==========")
        
    except Exception as e:
        logger.error(f"========== RECOVERY SIMULATION BACKGROUND THREAD FAILED for {recover_simulation_name} ==========")
        logger.error(f"[RECOVERY] Exception: {str(e)}")
        logger.error(f"[RECOVERY] Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"[RECOVERY] Traceback:\n{traceback.format_exc()}")
        send_error_to_mss('recovery', str(e))
        
        # Update state to failed
        save_simulation_state(recover_simulation_name, 'failed', {
            'error': str(e),
            'current_step': 'error'
        })
        
        # Clean up recovery directory if it exists
        if RECOVERY_DIR_CONTAINER and os.path.exists(RECOVERY_DIR_CONTAINER):
            try:
                shutil.rmtree(RECOVERY_DIR_CONTAINER)
                logger.info(f"[RECOVERY] Cleaned up failed recovery directory: {RECOVERY_DIR_CONTAINER}")
            except Exception as cleanup_err:
                logger.error(f"[RECOVERY] Error cleaning up recovery directory: {str(cleanup_err)}")

############################################################################################################
# Name: def get_logs()
# Description: This function zips the logs directory and sends the zip file as a response for download.
# Supports HPC mode by using shared storage when specified.
############################################################################################################
def get_logs(shared_storage=None):
    # Check if running in HPC environment
    is_hpc = is_hpc_environment()
    
    # Set up log file paths based on deployment mode (HPC vs container)
    if is_hpc and shared_storage:
        logger.debug(f"Running in HPC mode with shared storage: {shared_storage}")
        # In HPC mode, logs are stored in shared storage for multi-node access
        REQUESTED_FILES_DIR = os.path.join(shared_storage, 'logs', 'requested_files')
        LOGS_DIR = os.path.join(shared_storage, 'logs')
    else:
        # In container/local mode, logs are stored locally
        REQUESTED_FILES_DIR = os.path.join(LOCAL_DIR, 'requested_files')
        LOGS_DIR = os.path.join('logs')
    
    # Create directories if they don't exist
    os.makedirs(REQUESTED_FILES_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)
    
    # Define log files (both dev and user logs)
    DEV_REQUESTED_LOG_FILE = os.path.join(REQUESTED_FILES_DIR, 'dev_logs.log')
    USER_REQUESTED_LOG_FILE = os.path.join(REQUESTED_FILES_DIR, 'user_logs.log')
    DEV_LOG_FILE = os.path.join(LOGS_DIR, 'dev_logs.log')
    USER_LOG_FILE = os.path.join(LOGS_DIR, 'user_logs.log')
    
    try:
        # Copy dev_logs.log to requested_files
        if os.path.exists(DEV_LOG_FILE):
            with open(DEV_LOG_FILE, 'r') as file:
                dev_logs = file.read()
            with open(DEV_REQUESTED_LOG_FILE, 'w') as file:
                file.write(dev_logs)
        else:
            logger.warning(f"Dev log file does not exist at {DEV_LOG_FILE}")
        
        # Copy user_logs.log to requested_files
        if os.path.exists(USER_LOG_FILE):
            with open(USER_LOG_FILE, 'r') as file:
                user_logs = file.read()
            with open(USER_REQUESTED_LOG_FILE, 'w') as file:
                file.write(user_logs)
        else:
            logger.warning(f"User log file does not exist at {USER_LOG_FILE}")
            
    except Exception as e:
        logger.error(f"Exception while reading/writing log files: {str(e)}")
        if not is_hpc:  # Only return response in non-HPC mode
            return jsonify({'error': str(e)}), 500
    
    # Render logs in HTML template (only in non-HPC mode) - show user logs by default
    if not is_hpc:
        # Try to read user logs first, fall back to dev logs
        if os.path.exists(USER_REQUESTED_LOG_FILE):
            with open(USER_REQUESTED_LOG_FILE, 'r') as file:
                logs = file.read()
        elif os.path.exists(DEV_REQUESTED_LOG_FILE):
            with open(DEV_REQUESTED_LOG_FILE, 'r') as file:
                logs = file.read()
        else:
            logs = "No logs available"
        # Display logs in HTML template
        return render_template('logs.html', logs=logs)


############################################################################################################
# Name: def update_asset()
# Description: This function forces an asset's status to 'Failed' so it will be reprocessed during recovery.
# Calls the update_status function to set the asset status to 'Failed'.
############################################################################################################
def update_asset():
    from modules.diagnostics.db import update_status

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
# Description: This function logs a message to the dev_logs.log file.
# Calls the log_message function to log a message to the dev_logs.log file.
############################################################################################################
def log_message():
    # Extract message data from JSON request
    data = request.get_json()
    message = data.get('message')
    log_type = data.get('type', 'log')  # Type of log message (e.g., 'log', 'warning', 'error')
    
    # Create a timestamp for the log entry
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Define the log file path
    log_txt = os.path.join('logs','dev_logs.log')
    os.makedirs(os.path.dirname(log_txt), exist_ok=True)

    # Append the log entry to the log file with timestamp and type
    with open(log_txt, 'a') as log_file:
        log_file.write(f"[{timestamp}] [{log_type.upper()}] {message}\n")

    return jsonify({'status': 'success', 'log_file': log_txt}), 200


############################################################################################################
# Modern Log Streaming and Status Endpoints
############################################################################################################

# Get logs with pagination, filtering, and efficient streaming
def get_logs_paginated():
    try:
        # Extract query parameters for pagination and filtering
        page = request.args.get('page', default=1, type=int)  # Page number (1-indexed)
        page_size = request.args.get('page_size', default=1000, type=int)  # Lines per page
        level_filter = request.args.get('level', default=None, type=str)  # Filter by log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        search_text = request.args.get('search', default=None, type=str)  # Search text to find in logs
        
        # Validate pagination parameters to prevent invalid requests
        if page < 1:
            page = 1
        if page_size < 1 or page_size > 5000:
            page_size = 1000
        
        # Define log file path
        LOGS_DIR = os.path.join('logs')
        LOG_FILE = os.path.join(LOGS_DIR, 'dev_logs.log')
        
        # Create log streamer and retrieve paginated results
        log_streamer = get_log_streamer(LOG_FILE, page_size=page_size)
        result = log_streamer.get_logs_paginated(page, page_size, level_filter, search_text)
        return jsonify(result), 200
        
    except Exception as e:
        logger.error(f"Exception in get_logs_paginated: {str(e)}")
        return jsonify({'error': str(e)}), 500


# Efficiently retrieve the last N lines of logs (tail operation)
def get_logs_tail():
    try:
        # Extract query parameters
        num_lines = request.args.get('lines', default=100, type=int)  # Number of lines to retrieve
        level_filter = request.args.get('level', default=None, type=str)  # Optional log level filter
        
        # Validate parameters to prevent excessive memory usage
        if num_lines < 1 or num_lines > 10000:
            num_lines = 100
        
        # Define log file path
        LOGS_DIR = os.path.join('logs')
        LOG_FILE = os.path.join(LOGS_DIR, 'dev_logs.log')
        
        # Get the last N log lines
        log_streamer = get_log_streamer(LOG_FILE)
        lines = log_streamer.get_logs_tail(num_lines, level_filter)
        
        return jsonify({
            'lines': lines,
            'count': len(lines),
            'level_filter': level_filter
        }), 200
        
    except Exception as e:
        logger.error(f"Exception in get_logs_tail: {str(e)}")
        return jsonify({'error': str(e)}), 500


# Retrieve logs within a specific time range
def get_logs_by_time():
    try:
        # Extract time range parameters in ISO format
        start_time = request.args.get('start', default=None, type=str)  # Start time (ISO format)
        end_time = request.args.get('end', default=None, type=str)  # End time (ISO format)
        page = request.args.get('page', default=1, type=int)  # Page number
        page_size = request.args.get('page_size', default=1000, type=int)  # Lines per page
        
        # Define log file path
        LOGS_DIR = os.path.join('logs')
        LOG_FILE = os.path.join(LOGS_DIR, 'dev_logs.log')
        
        # Retrieve logs within the specified time range
        log_streamer = get_log_streamer(LOG_FILE, page_size=page_size)
        result = log_streamer.get_logs_by_time_range(start_time, end_time, page, page_size)
        
        return jsonify(result), 200
        
    except Exception as e:
        logger.error(f"Exception in get_logs_by_time: {str(e)}")
        return jsonify({'error': str(e)}), 500


# Get comprehensive statistics about the log file
def get_log_stats():
    """
    Get comprehensive statistics about the log file.
    
    Returns information about:
        - Total lines
        - File size
        - Log level distribution
        - Time range (first and last log entries)
        - Creation and modification times
    """
    logger.debug("Within get_log_stats()")
    
    try:
        LOGS_DIR = os.path.join('logs')
        LOG_FILE = os.path.join(LOGS_DIR, 'dev_logs.log')
        
        log_streamer = get_log_streamer(LOG_FILE)
        stats = log_streamer.get_log_statistics()
        
        return jsonify(stats), 200
        
    except Exception as e:
        logger.error(f"Exception in get_log_stats: {str(e)}")
        return jsonify({'error': str(e)}), 500


def get_simulation_status_summary(simulation_name):
    """
    Get a comprehensive status summary for a simulation without multiple DB queries.
    Uses in-memory cache for efficiency.
    
    Returns:
        - Total assets
        - Completion count and percentage
        - In-progress and failed counts
        - Success rate
        - Timestamp
    """
    logger.debug(f"Within get_simulation_status_summary() for {simulation_name}")
    
    try:
        # Get in-memory cached summary to avoid repeated DB queries
        summary = get_simulation_summary(simulation_name)
        
        logger.debug(f"Returning status summary for {simulation_name}")
        return jsonify(summary), 200
        
    except Exception as e:
        logger.error(f"Exception in get_simulation_status_summary: {str(e)}")
        return jsonify({'error': str(e)}), 500


############################################################################################################
# Name: def get_batch_progress()
# Description: Returns detailed batch-level progress for the current simulation.
# Tracks assets by batch with completion counts and percentages for real-time UI display.
############################################################################################################
def get_batch_progress():
    """Get detailed batch progress with per-batch asset counts and completion status"""
    # Disabled debug logging to reduce log spam from frequent polling
    # logger.debug("Within get_batch_progress()")
    
    try:
        # Get current simulation
        current_sim = get_current_simulation()
        
        if not current_sim:
            return jsonify({
                'has_active_simulation': False,
                'batches': [],
                'message': 'No active simulation'
            }), 200
        
        simulation_name = current_sim.get('simulation_name')
        progress = current_sim.get('progress', {})
        
        # Read batch progress from state file (same approach as overall progress)
        batches_dict = progress.get('batches', {})
        
        # Convert to list format for API response
        batches_list = []
        for batch_key, batch_data in batches_dict.items():
            # Extract batch number from key like 'batch_1'
            batch_num = int(batch_key.split('_')[1])
            completed = batch_data.get('completed', 0)
            total = batch_data.get('total', 0)
            
            batch_info = {
                'batch': batch_num,
                'completed': completed,
                'total': total,
                'pending': total - completed,
                'in_progress': 0,  # Not tracking separately yet
                'failed': 0,  # Not tracking separately yet
                'completion_percentage': round((completed / total * 100), 1) if total > 0 else 0
            }
            batches_list.append(batch_info)
        
        # Sort by batch number
        batches_list = sorted(batches_list, key=lambda x: x['batch'])
        
        return jsonify({
            'has_active_simulation': True,
            'simulation_name': simulation_name,
            'batches': batches_list,
            'last_updated': current_sim.get('last_updated')
        }), 200
        
    except Exception as e:
        logger.error(f"Exception in get_batch_progress: {str(e)}")
        return jsonify({'error': str(e), 'batches': []}), 500


# Get performance statistics about the status tracker cache system
def get_status_tracker_stats():
    # Return metrics on cache effectiveness, query reductions, and batch processing
    logger.debug("Within get_status_tracker_stats()")
    
    try:
        # Retrieve tracker performance statistics
        stats = get_tracker_stats()
        return jsonify(stats), 200
        
    except Exception as e:
        logger.error(f"Exception in get_status_tracker_stats: {str(e)}")
        return jsonify({'error': str(e)}), 500


############################################################################################################
# Performance Monitoring and Health Endpoints
############################################################################################################

def get_performance_metrics():
    # Compile performance data from database, logs, and system health
    # Note: Disabled debug logging here to reduce log spam from frequent polling
    # logger.debug("Within get_performance_metrics()")
    
    try:
        # Gather system health information (CPU, memory, disk)
        system_health = check_system_health()
        
        # Analyze log file health and integrity
        logs_dir = os.path.join('logs')
        log_file = os.path.join(logs_dir, 'dev_logs.log')
        log_health = check_log_health(log_file)
        
        # Get database and query performance statistics
        report = get_performance_report()
        
        # Compile all metrics into a single response
        metrics = {
            'timestamp': datetime.datetime.now(timezone.utc).isoformat(),
            'system': system_health,  # CPU, memory, disk stats
            'logs': log_health,  # Log file integrity and size
            'database': report.get('database', {}),  # Query and connection stats
            'recent_alerts': report.get('recent_alerts', [])  # Active system alerts
        }
        
        return jsonify(metrics), 200
        
    except Exception as e:
        logger.error(f"Exception in get_performance_metrics: {str(e)}")
        return jsonify({'error': str(e)}), 500


# Get current system resource usage and health status
def get_system_health():
    # Monitor CPU, memory, disk usage and report health status
    logger.debug("Within get_system_health()")
    
    try:
        # Retrieve current system health metrics
        health = check_system_health()
        return jsonify(health), 200
        
    except Exception as e:
        logger.error(f"Exception in get_system_health: {str(e)}")
        return jsonify({'error': str(e)}), 500


# Get system alerts and warnings
def get_system_alerts():
    # Retrieve recent system alerts filtered by time and severity
    logger.debug("Within get_system_alerts()")
    
    try:
        # Extract filtering parameters from query string
        since_minutes = request.args.get('since_minutes', default=60, type=int)  # Only return recent alerts
        severity = request.args.get('severity', default=None, type=str)  # Filter by severity level
        
        # Retrieve alerts matching the filters
        alerts = get_recent_alerts(since_minutes=since_minutes, severity=severity)
        
        return jsonify({
            'alerts': alerts,
            'count': len(alerts),
            'filters': {
                'since_minutes': since_minutes,
                'severity': severity
            }
        }), 200
        
    except Exception as e:
        logger.error(f"Exception in get_system_alerts: {str(e)}")
        return jsonify({'error': str(e)}), 500


# Get database optimization and query performance statistics
def get_db_optimization_stats():
    # Return metrics on batch updates, query caching, and query performance
    logger.debug("Within get_db_optimization_stats()")
    
    try:
        # Retrieve database optimization metrics
        stats = get_optimization_stats()
        
        return jsonify({
            'optimization': stats,
            'timestamp': datetime.datetime.now().isoformat()
        }), 200
        
    except Exception as e:
        logger.error(f"Exception in get_db_optimization_stats: {str(e)}")
        return jsonify({'error': str(e)}), 500


# Get a comprehensive diagnostics report combining all monitoring data
def get_full_diagnostics():
    # Generate complete system diagnostics with performance, health, and optimization stats
    logger.debug("Within get_full_diagnostics()")
    
    try:
        # Compile diagnostics from all monitoring subsystems
        diagnostics = {
            'timestamp': datetime.datetime.now().isoformat(),
            'performance': get_performance_report(),  # Query and system performance
            'status_tracking': get_tracker_stats(),  # Cache and tracking effectiveness
            'database_optimization': get_optimization_stats(),  # Query optimization metrics
            'system_health': check_system_health(),  # CPU, memory, disk usage
            'alerts': get_recent_alerts(since_minutes=60)  # Recent system alerts
        }
        
        logger.info("Generated full diagnostics report")
        return jsonify(diagnostics), 200
        
    except Exception as e:
        logger.error(f"Exception in get_full_diagnostics: {str(e)}")
        return jsonify({'error': str(e)}), 500


# Get simulation performance metrics for visualization
def get_simulation_performance():
    """
    Get simulation performance metrics including timing, throughput, and failure rates.
    Returns historical data for chart rendering.
    """
    logger.debug("Within get_simulation_performance()")
    
    try:
        # Get query parameters
        time_range = request.args.get('time_range', default=None, type=int)  # Hours (1, 6, 24, or None)
        simulation_name = request.args.get('simulation_name', default=None, type=str)
        
        # If no simulation name provided, try to get current
        if not simulation_name:
            current_sim = get_current_simulation()
            if current_sim:
                simulation_name = current_sim.get('simulation_name')
        
        # Get performance tracker
        tracker = get_performance_tracker()
        
        # Record current snapshot (pass simulation_name even if None)
        tracker.record_performance_snapshot(simulation_name)
        
        # Get historical data
        historical_data = tracker.get_historical_data(time_range_hours=time_range)
        
        # Get latest snapshot for summary
        latest = tracker.get_latest_snapshot()
        
        # Calculate ETA in human-readable format
        eta_formatted = None
        if latest and latest.get('eta_seconds') and latest['eta_seconds']:
            eta_seconds = latest['eta_seconds']
            hours = int(eta_seconds // 3600)
            minutes = int((eta_seconds % 3600) // 60)
            eta_formatted = f"{hours}h {minutes}m"
        
        response = {
            'timestamp': datetime.datetime.now(timezone.utc).isoformat(),
            'latest': latest,
            'eta_formatted': eta_formatted,
            'historical_data': historical_data,
            'data_points': len(historical_data),
            'time_range_hours': time_range,
            'simulation_name': simulation_name
        }
        
        return jsonify(response), 200
        
    except Exception as e:
        logger.error(f"Exception in get_simulation_performance: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500
