import argparse
import requests

def start_simulation(args):
    url = "http://localhost:8080/api/simulation/start"
    files = {
        'asset_geojson_file': open(args.asset_geojson_path, 'rb'),
        'metadata_csv_file': open(args.metadata_csv_path, 'rb')
    }
    data = {
        'simulation_name': args.simulation_name,
        'config_data': args.config_json_path,
        'location': args.location,
        'num_cores': args.num_cores
    }
    response = requests.post(url, files=files, data=data)
    print(response.json())

def get_simulation_status(args):
    url = f"http://localhost:8080/api/simulation/status/{args.simulation_name}"
    params = {'batch_id': args.batch_id} if args.batch_id else {}
    response = requests.get(url, params=params)
    print(response.json())

def delete_simulation(args):
    url = f"http://localhost:8080/api/simulation/delete/{args.simulation_name}"
    response = requests.get(url)
    print(response.json())

def stop_simulation(args):
    url = "http://localhost:8080/api/simulation/stop"
    response = requests.post(url)
    print(response.json())

def autorun_simulation(args):
    url = "http://localhost:8080/api/simulation/autorun_simulation"
    response = requests.post(url)
    print(response.json())

def get_asset_config(args):
    url = f"http://localhost:8080/api/asset/config/{args.simulation_name}/{args.asset_id}"
    response = requests.get(url)
    print(response.json())

def recovery(args):
    url = "http://localhost:8080/api/diagnostics/recovery"
    data = {
        'corrupted_simulation_name': args.corrupted_simulation_name,
        'recover_simulation_name': args.recover_simulation_name,
        'recover_batch_id': args.batch_id,
        'recover_num_cores': args.num_cores
    }
    response = requests.post(url, data=data)
    print(response.json())

def get_logs(args):
    url = "http://localhost:8080/api/diagnostics/getlogs"
    response = requests.get(url)
    print(response.json())

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PowerTwin Solver Commands")
    subparsers = parser.add_subparsers()

    # Start simulation command
    parser_start = subparsers.add_parser('start', help='Start a simulation')
    parser_start.add_argument('simulation_name', type=str, help='Name of the simulation')
    parser_start.add_argument('asset_geojson_path', type=str, help='Path to the asset geojson file')
    parser_start.add_argument('metadata_csv_path', type=str, help='Path to the metadata CSV file')
    parser_start.add_argument('config_json_path', type=str, help='Path to the config JSON file')
    parser_start.add_argument('location', type=str, help='Location of the simulation')
    parser_start.add_argument('num_cores', type=int, help='Number of cores to use')
    parser_start.set_defaults(func=start_simulation)

    # Get simulation status command
    parser_status = subparsers.add_parser('status', help='Get simulation status')
    parser_status.add_argument('simulation_name', type=str, help='Name of the simulation')
    parser_status.add_argument('--batch_id', type=int, help='ID of the batch')
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

    # Recovery command
    parser_recovery = subparsers.add_parser('recover', help='Recover a simulation')
    parser_recovery.add_argument('corrupted_simulation_name', type=str, help='Name of the corrupted simulation')
    parser_recovery.add_argument('recover_simulation_name', type=str, help='Name of the recovery simulation')
    parser_recovery.add_argument('num_cores', type=int, help='Number of cores to use')
    parser_recovery.add_argument('--batch_id', type=int, help='ID of the batch')
    parser_recovery.set_defaults(func=recovery)

    # Get logs command
    parser_get_logs = subparsers.add_parser('get_logs', help='Get logs')
    parser_get_logs.set_defaults(func=get_logs)

    args = parser.parse_args()
    args.func(args)