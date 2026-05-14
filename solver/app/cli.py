import argparse
import requests
import os
from modules.utils.hpc_environment import is_hpc_environment

def start_simulation(args):
    # Use centralized HPC detection
    is_hpc = is_hpc_environment()
    
    if is_hpc and not args.shared_storage:
        print("Error: --shared-storage is required when in HPC environment")
        return
    
    url = "http://localhost:8080/api/simulation/start"
    files = {
        'asset_geojson_file': open(args.asset_geojson_path, 'rb'),
        'metadata_csv_file': open(args.metadata_csv_path, 'rb')
    }
    data = {
        'simulation_name': args.simulation_name,
        'num_cores': args.num_cores,
        'shared_storage': args.shared_storage if is_hpc else None,
        'keep_dirs': args.keep
    }
    response = requests.post(url, files=files, data=data)
    if response.status_code == 200:
        print("start_simulation function worked")
    else:
        print(f"Error: {response.status_code}")
        try:
            print(response.json())
        except requests.exceptions.JSONDecodeError:
            print(f"No JSON response body or invalid JSON returned. Response text: {response.text}")


def get_simulation_status(args):
    url = f"http://localhost:8080/api/simulation/status/{args.simulation_name}"
    params = {'batch_id': args.batch_id} if args.batch_id else {}
    response = requests.get(url, params=params)
    if response.status_code == 200:
        print("get_simulation_status function worked")
    else:
        print(f"Error: {response.status_code}")
        try:
            print(response.json())
        except requests.exceptions.JSONDecodeError:
            print(f"No JSON response body or invalid JSON returned. Response text: {response.text}")

def delete_simulation(args):
    url = f"http://localhost:8080/api/simulation/delete/{args.simulation_name}"
    response = requests.get(url)
    if response.status_code == 200:
        print("delete_simulation function worked")
    else:
        print(f"Error: {response.status_code}")
        try:
            print(response.json())
        except requests.exceptions.JSONDecodeError:
            print(f"No JSON response body or invalid JSON returned. Response text: {response.text}")

def stop_simulation(args):
    url = "http://localhost:8080/api/simulation/stop"
    response = requests.post(url)
    if response.status_code == 200:
        print("stop_simulation function worked")
    else:
        print(f"Error: {response.status_code}")
        try:
            print(response.json())
        except requests.exceptions.JSONDecodeError:
            print(f"No JSON response body or invalid JSON returned. Response text: {response.text}")

def autorun_simulation(args):
    url = "http://localhost:8080/api/simulation/autorun_simulation"
    response = requests.post(url)
    if response.status_code == 200:
        print("autorun_simulation function worked")
    else:
        print(f"Error: {response.status_code}")
        try:
            print(response.json())
        except requests.exceptions.JSONDecodeError:
            print(f"No JSON response body or invalid JSON returned. Response text: {response.text}")

def get_asset_config(args):
    url = f"http://localhost:8080/api/asset/config/{args.simulation_name}/{args.asset_id}"
    response = requests.get(url)
    if response.status_code == 200:
        print("get_asset_config function worked, check the user_files/requested_files directory for asset config")
    else:
        print(f"Error: {response.status_code}")
        try:
            print(response.json())
        except requests.exceptions.JSONDecodeError:
            print(f"No JSON response body or invalid JSON returned. Response text: {response.text}")

def get_data(args):
    url = "http://localhost:8080/api/simulation/data"

    response = requests.get(url)
    if response.status_code == 200:
        print("get_simulation_data function worked, check the user_files/requested_files directory for simulation stats")
    else:
        print(f"Error: {response.status_code}")
        try:
            print(response.json())
        except requests.exceptions.JSONDecodeError:
            print(f"No JSON response body or invalid JSON returned. Response text: {response.text}")


def recovery(args):
    url = "http://localhost:8080/api/diagnostics/recovery"
    data = {
        'corrupted_simulation_name': args.corrupted_simulation_name,
        'recover_simulation_name': args.recover_simulation_name,
        'recover_batch_id': args.batch_id if hasattr(args, 'batch_id') and args.batch_id is not None else None,
        'recover_num_cores': args.num_cores,
        'keep_dirs': args.keep if hasattr(args, 'keep') else False
    }
    response = requests.post(url, data=data)
    if response.status_code == 200:
        print("recovery function worked")
    else:
        print(f"Error: {response.status_code}")
        try:
            print(response.json())
        except requests.exceptions.JSONDecodeError:
            print(f"No JSON response body or invalid JSON returned. Response text: {response.text}")

def logs(args):
    url = "http://localhost:8080/logs"
    response = requests.get(url)
    if response.status_code == 200:
        print("logs function worked")
    else:
        print(f"Error: {response.status_code}")
        try:
            print(response.json())
        except requests.exceptions.JSONDecodeError:
            print(f"No JSON response body or invalid JSON returned. Response text: {response.text}")


def main():
    parser = argparse.ArgumentParser(description="PowerTwin Solver Commands")
    subparsers = parser.add_subparsers()

    # Start simulation command
    parser_start = subparsers.add_parser('start', help='Start a simulation')
    parser_start.add_argument('simulation_name', type=str, help='Name of the simulation')
    parser_start.add_argument('asset_geojson_path', type=str, help='Path to the asset geojson file')
    parser_start.add_argument('metadata_csv_path', type=str, help='Path to the metadata CSV file')
    parser_start.add_argument('num_cores', type=int, help='Number of cores to use')
    parser_start.add_argument('--shared-storage', type=str, help='Path to shared storage for HPC environments')
    parser_start.add_argument('-k', '--keep', action='store_true', help='Keep additional directories (feature_reports, generated_files) during asset cleanup')
    parser_start.set_defaults(func=start_simulation)

    # Get simulation status command
    parser_status = subparsers.add_parser('status', help='Get simulation status')
    parser_status.add_argument('simulation_name', type=str, help='Name of the simulation')
    parser_status.add_argument('-b','--batch_id', type=int, help='ID of the batch')
    parser_status.set_defaults(func=get_simulation_status)

    # Delete simulation command
    parser_status = subparsers.add_parser('delete', help='Delete simulation')
    parser_status.add_argument('simulation_name', type=str, help='Name of the simulation')
    parser_status.set_defaults(func=delete_simulation)

    # Stop simulation command
    parser_stop = subparsers.add_parser('stop', help='Stop the simulation')
    parser_stop.set_defaults(func=stop_simulation)

    # Autorun simulation command
    parser_autorun = subparsers.add_parser('autorun', help='Autorun a simulation')
    parser_autorun.set_defaults(func=autorun_simulation)

    # Get asset config command
    parser_get_config = subparsers.add_parser('get_config', help='Get asset configuration')
    parser_get_config.add_argument('simulation_name', type=str, help='Name of the simulation')
    parser_get_config.add_argument('asset_id', type=str, help='ID of the asset')
    parser_get_config.set_defaults(func=get_asset_config)

    # Get data command
    parser_get_data = subparsers.add_parser('get_data', help='Get simulation data')
    parser_get_data.set_defaults(func=get_data)

    # Recovery command
    parser_recovery = subparsers.add_parser('recover', help='Recover a simulation')
    parser_recovery.add_argument('corrupted_simulation_name', type=str, help='Name of the corrupted simulation')
    parser_recovery.add_argument('recover_simulation_name', type=str, help='Name of the recovery simulation')
    parser_recovery.add_argument('num_cores', type=int, help='Number of cores to use')
    parser_recovery.add_argument('-b','--batch_id', type=int, help='ID of the batch')
    parser_recovery.add_argument('-k', '--keep', action='store_true', help='Keep additional directories (feature_reports, generated_files) during asset cleanup')
    parser_recovery.set_defaults(func=recovery)

    # Get logs command
    parser_get_logs = subparsers.add_parser('logs', help='Get logs')
    parser_get_logs.set_defaults(func=logs)

    args = parser.parse_args()
    args.func(args)
    
if __name__ == "__main__":
    main()