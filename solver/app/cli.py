# ======================================================================================
# PowerTwin Solver CLI Module
# Command-line interface for managing PowerTwin simulations and operations.
# This module provides CLI commands that send HTTP requests to the Flask REST API.
# ======================================================================================

import argparse
import requests
import os
from modules.utils.hpc_environment import is_hpc_environment

# ============ CLI COMMAND FUNCTIONS ============

# Send request to start a new simulation via the REST API
def start_simulation(args):
    # Use centralized HPC detection
    is_hpc = is_hpc_environment()
    
    if is_hpc and not args.shared_storage:
        print("Error: --shared-storage is required when in HPC environment")
        return
    
    # Construct the API endpoint URL
    url = "http://localhost:8080/api/simulation/start"
    
    # Prepare file uploads
    files = {
        'asset_geojson_file': open(args.asset_geojson_path, 'rb'),  # Building geometry data
        'metadata_csv_file': open(args.metadata_csv_path, 'rb')  # Building metadata
    }
    
    # Prepare form data parameters
    data = {
        'simulation_name': args.simulation_name,  # Unique identifier for this run
        'config_data': args.config_json_path,  # Simulation configuration
        'num_cores': args.num_cores,  # Number of cores for parallelization
        'shared_storage': args.shared_storage if is_hpc else None,  # HPC shared storage path
        'keep_dirs': args.keep  # Flag to preserve directories
    }
    
    # Send POST request to start simulation
    response = requests.post(url, files=files, data=data)
    
    # Check response status and display result
    if response.status_code == 200:
        print("start_simulation function worked")
    else:
        print(f"Error: {response.status_code}")
        try:
            print(response.json())
        except requests.exceptions.JSONDecodeError:
            print(f"No JSON response body or invalid JSON returned. Response text: {response.text}")


# Send request to get the status of a simulation
def get_simulation_status(args):
    # Construct the API endpoint URL with simulation name
    url = f"http://localhost:8080/api/simulation/status/{args.simulation_name}"
    
    # Add optional batch ID parameter if provided
    params = {'batch_id': args.batch_id} if args.batch_id else {}
    
    # Send GET request to retrieve status
    response = requests.get(url, params=params)
    
    # Check response status and display result
    if response.status_code == 200:
        print("get_simulation_status function worked")
    else:
        print(f"Error: {response.status_code}")
        try:
            print(response.json())
        except requests.exceptions.JSONDecodeError:
            print(f"No JSON response body or invalid JSON returned. Response text: {response.text}")

# Send request to delete a simulation
def delete_simulation(args):
    # Construct the API endpoint URL with simulation name
    url = f"http://localhost:8080/api/simulation/delete/{args.simulation_name}"
    
    # Send GET request to delete the simulation
    response = requests.get(url)
    
    # Check response status and display result
    if response.status_code == 200:
        print("delete_simulation function worked")
    else:
        print(f"Error: {response.status_code}")
        try:
            print(response.json())
        except requests.exceptions.JSONDecodeError:
            print(f"No JSON response body or invalid JSON returned. Response text: {response.text}")

# Send request to stop a running simulation
def stop_simulation(args):
    # Construct the API endpoint URL
    url = "http://localhost:8080/api/simulation/stop"
    
    # Send POST request to stop the simulation
    response = requests.post(url)
    
    # Check response status and display result
    if response.status_code == 200:
        print("stop_simulation function worked")
    else:
        print(f"Error: {response.status_code}")
        try:
            print(response.json())
        except requests.exceptions.JSONDecodeError:
            print(f"No JSON response body or invalid JSON returned. Response text: {response.text}")

# Send request to autorun a simulation based on JSON configuration
def autorun_simulation(args):
    # Construct the API endpoint URL
    url = "http://localhost:8080/api/simulation/autorun_simulation"
    
    # Send POST request to start autorun
    response = requests.post(url)
    
    # Check response status and display result
    if response.status_code == 200:
        print("autorun_simulation function worked")
    else:
        print(f"Error: {response.status_code}")
        try:
            print(response.json())
        except requests.exceptions.JSONDecodeError:
            print(f"No JSON response body or invalid JSON returned. Response text: {response.text}")

# Send request to retrieve asset configuration file
def get_asset_config(args):
    # Construct the API endpoint URL with simulation name and asset ID
    url = f"http://localhost:8080/api/asset/config/{args.simulation_name}/{args.asset_id}"
    
    # Send GET request to retrieve asset configuration
    response = requests.get(url)
    
    # Check response status and display result
    if response.status_code == 200:
        print("get_asset_config function worked, check the user_files/requested_files directory for asset config")
    else:
        print(f"Error: {response.status_code}")
        try:
            print(response.json())
        except requests.exceptions.JSONDecodeError:
            print(f"No JSON response body or invalid JSON returned. Response text: {response.text}")

# Send request to retrieve and export simulation data as CSV
def get_data(args):
    # Construct the API endpoint URL
    url = "http://localhost:8080/api/simulation/data"

    # Send GET request to retrieve simulation data
    response = requests.get(url)
    
    # Check response status and display result
    if response.status_code == 200:
        print("get_simulation_data function worked, check the user_files/requested_files directory for simulation stats")
    else:
        print(f"Error: {response.status_code}")
        try:
            print(response.json())
        except requests.exceptions.JSONDecodeError:
            print(f"No JSON response body or invalid JSON returned. Response text: {response.text}")


# Send request to recover a failed or interrupted simulation
def recovery(args):
    # Construct the API endpoint URL
    url = "http://localhost:8080/api/diagnostics/recovery"
    
    # Prepare recovery parameters
    data = {
        'corrupted_simulation_name': args.corrupted_simulation_name,  # Name of failed simulation
        'recover_simulation_name': args.recover_simulation_name,  # Name for recovery run
        'recover_batch_id': args.batch_id if hasattr(args, 'batch_id') and args.batch_id is not None else None,  # Optional batch to recover
        'recover_num_cores': args.num_cores,  # Number of cores for recovery
        'keep_dirs': args.keep if hasattr(args, 'keep') else False  # Flag to preserve directories
    }
    
    # Send POST request to start recovery
    response = requests.post(url, data=data)
    
    # Check response status and display result
    if response.status_code == 200:
        print("recovery function worked")
    else:
        print(f"Error: {response.status_code}")
        try:
            print(response.json())
        except requests.exceptions.JSONDecodeError:
            print(f"No JSON response body or invalid JSON returned. Response text: {response.text}")

# Send request to retrieve and display logs
def logs(args):
    # Construct the API endpoint URL
    url = "http://localhost:8080/logs"
    
    # Send GET request to retrieve logs
    response = requests.get(url)
    
    # Check response status and display result
    if response.status_code == 200:
        print("logs function worked")
    else:
        print(f"Error: {response.status_code}")
        try:
            print(response.json())
        except requests.exceptions.JSONDecodeError:
            print(f"No JSON response body or invalid JSON returned. Response text: {response.text}")


# ============ CLI PARSER AND ENTRY POINT ============

# Initialize argument parser and create CLI interface
def main():
    # Create main argument parser with description
    parser = argparse.ArgumentParser(description="PowerTwin Solver Commands")
    subparsers = parser.add_subparsers()  # Create subparsers for individual commands

    # ===== START SIMULATION COMMAND =====
    parser_start = subparsers.add_parser('start', help='Start a simulation')
    parser_start.add_argument('simulation_name', type=str, help='Name of the simulation')
    parser_start.add_argument('asset_geojson_path', type=str, help='Path to the asset geojson file')
    parser_start.add_argument('metadata_csv_path', type=str, help='Path to the metadata CSV file')
    parser_start.add_argument('config_json_path', type=str, help='Path to the config JSON file')
    parser_start.add_argument('num_cores', type=int, help='Number of cores to use')
    parser_start.add_argument('--shared-storage', type=str, help='Path to shared storage for HPC environments')
    parser_start.add_argument('-k', '--keep', action='store_true', help='Keep additional directories (feature_reports, generated_files) during asset cleanup')
    parser_start.set_defaults(func=start_simulation)

    # ===== GET SIMULATION STATUS COMMAND =====
    parser_status = subparsers.add_parser('status', help='Get simulation status')
    parser_status.add_argument('simulation_name', type=str, help='Name of the simulation')
    parser_status.add_argument('-b','--batch_id', type=int, help='ID of the batch')
    parser_status.set_defaults(func=get_simulation_status)

    # ===== DELETE SIMULATION COMMAND =====
    parser_status = subparsers.add_parser('delete', help='Delete simulation')
    parser_status.add_argument('simulation_name', type=str, help='Name of the simulation')
    parser_status.set_defaults(func=delete_simulation)

    # ===== STOP SIMULATION COMMAND =====
    parser_stop = subparsers.add_parser('stop', help='Stop the simulation')
    parser_stop.set_defaults(func=stop_simulation)

    # ===== AUTORUN SIMULATION COMMAND =====
    parser_autorun = subparsers.add_parser('autorun', help='Autorun a simulation')
    parser_autorun.set_defaults(func=autorun_simulation)

    # ===== GET ASSET CONFIG COMMAND =====
    parser_get_config = subparsers.add_parser('get_config', help='Get asset configuration')
    parser_get_config.add_argument('simulation_name', type=str, help='Name of the simulation')
    parser_get_config.add_argument('asset_id', type=str, help='ID of the asset')
    parser_get_config.set_defaults(func=get_asset_config)

    # ===== GET DATA COMMAND =====
    parser_get_data = subparsers.add_parser('get_data', help='Get simulation data')
    parser_get_data.set_defaults(func=get_data)

    # ===== RECOVERY COMMAND =====
    parser_recovery = subparsers.add_parser('recover', help='Recover a simulation')
    parser_recovery.add_argument('corrupted_simulation_name', type=str, help='Name of the corrupted simulation')
    parser_recovery.add_argument('recover_simulation_name', type=str, help='Name of the recovery simulation')
    parser_recovery.add_argument('num_cores', type=int, help='Number of cores to use')
    parser_recovery.add_argument('-b','--batch_id', type=int, help='ID of the batch')
    parser_recovery.add_argument('-k', '--keep', action='store_true', help='Keep additional directories (feature_reports, generated_files) during asset cleanup')
    parser_recovery.set_defaults(func=recovery)

    # ===== GET LOGS COMMAND =====
    parser_get_logs = subparsers.add_parser('logs', help='Get logs')
    parser_get_logs.set_defaults(func=logs)

    # Parse command-line arguments and execute the corresponding function
    args = parser.parse_args()
    args.func(args)
    
# Entry point for the module
if __name__ == "__main__":
    main()