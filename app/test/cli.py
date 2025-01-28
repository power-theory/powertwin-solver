import argparse
import requests

def start_simulation(args):
    url = f"http://localhost:8080/api/simulation/start"
    data = {
        'simulation_name': args.simulation_name,
        'asset_geojson_path': args.asset_geojson_path,
        'metadata_csv_path': args.metadata_csv_path,
        'config_json_path': args.config_json_path,
        'num_cores': args.num_cores
    }
    response = requests.post(url, data=data)
    print(response.json())

def get_simulation_status(args):
    url = f"http://localhost:8080/api/simulation/status/{args.simulation_name}"
    params = {'batch_id': args.batch_id} if args.batch_id else {}
    response = requests.get(url, params=params)
    print(response.json())

def feature_files(args):
    url = f"http://localhost:8080/api/featurefiles"
    files = {
        'asset_geojson_file': open(args.asset_geojson_path, 'rb'),
        'metadata_csv_file_1': open(args.metadata_csv_path, 'rb')
    }
    data = {'config_data': args.config_data, 'num_cores': args.num_cores}
    response = requests.post(url, files=files, data=data)
    print(response.json())

def start_uo_simulation(args):
    url = f"http://localhost:8080/api/UOsimulation/start"
    files = {'featurefile_zip': open(args.featurefile_zip, 'rb')} if args.featurefile_zip else {}
    data = {'asset_id_2': args.asset_id, 'clean_report_1': args.clean_report}
    response = requests.post(url, files=files, data=data)
    print(response.json())

def clean_report(args):
    url = f"http://localhost:8080/api/clean_report"
    files = {
        'unclean_report_csv': open(args.unclean_report_csv, 'rb'),
        'metadata_csv_file_2': open(args.metadata_csv, 'rb')
    }
    data = {'asset_id_3': args.asset_id}
    response = requests.post(url, files=files, data=data)
    print(response.json())

def get_runtime_analysis(args):
    url = f"http://localhost:8080/api/diagnostics/analysis"
    files = {'featurefile_zip_2': open(args.featurefile_zip, 'rb')}
    response = requests.post(url, files=files)
    print(response.json())

def get_logs():
    url = f"http://localhost:8080/api/diagnostics/getlogs"
    response = requests.get(url)
    print(response.json())

#################################################################################################################
# Name: main
# Description: This is the main function that is called when the script is run. It parses the command line arguments
# and calls the appropriate function based on the command.
#################################################################################################################
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulation CLI")
    subparsers = parser.add_subparsers()

    # Start simulation command
    parser_start = subparsers.add_parser('start', help='Start a simulation')
    parser_start.add_argument('simulation_name', type=str, help='Name of the simulation')
    parser_start.add_argument('asset_geojson_path', type=str, help='Path to the asset geojson file')
    parser_start.add_argument('metadata_csv_path', type=str, help='Path to the metadata CSV file')
    parser_start.add_argument('config_json_path', type=str, help='Path to the config JSON file')
    parser_start.add_argument('--num_cores', type=int, default=1, help='Number of cores to use')
    parser_start.set_defaults(func=start_simulation)

    # Stop simulation command
    # parser_stop = subparsers.add_parser('stop', help='Stop a simulation')
    # parser_stop.add_argument('simulation_name', type=str, help='Name of the simulation')
    # parser_stop.set_defaults(func=stop_simulation)

    # Stop batch command
    # parser_stop_batch = subparsers.add_parser('stop_batch', help='Stop a batch in a simulation')
    # parser_stop_batch.add_argument('simulation_name', type=str, help='Name of the simulation')
    # parser_stop_batch.add_argument('batch_id', type=str, help='ID of the batch to stop')
    # parser_stop_batch.set_defaults(func=stop_single_batch)

    # Get simulation status command
    parser_status = subparsers.add_parser('status', help='Get simulation status')
    parser_status.add_argument('simulation_name', type=str, help='Name of the simulation')
    parser_status.add_argument('--batch_id', type=int, help='ID of the batch')
    parser_status.set_defaults(func=get_simulation_status)

    ###################################

    # Get feature files command
    parser_feature_files = subparsers.add_parser('feature_files', help='Get feature files')
    parser_feature_files.add_argument('asset_geojson_path', type=str, help='Path to the asset geojson file')
    parser_feature_files.add_argument('metadata_csv_path', type=str, help='Path to the metadata CSV file')
    parser_feature_files.add_argument('config_data', type=str, help='Config data as JSON string')
    parser_feature_files.add_argument('num_cores', type=int, default=1, help='Number of cores to use')
    parser_feature_files.set_defaults(func=feature_files)

    # Start UrbanOpt simulation command
    parser_uo_simulation = subparsers.add_parser('uo_simulation', help='Start UrbanOpt simulation')
    parser_uo_simulation.add_argument('--featurefile_zip', type=str, help='Path to the feature file zip')
    parser_uo_simulation.add_argument('--asset_id', type=str, help='Asset ID')
    parser_uo_simulation.add_argument('--clean_report', action='store_true', help='Clean report flag')
    parser_uo_simulation.set_defaults(func=start_uo_simulation)

    # Clean report command
    parser_clean_report = subparsers.add_parser('clean_report', help='Clean report')
    parser_clean_report.add_argument('unclean_report_csv', type=str, help='Path to the unclean report CSV file')
    parser_clean_report.add_argument('metadata_csv', type=str, help='Path to the metadata CSV file')
    parser_clean_report.add_argument('asset_id', type=str, help='Asset ID')
    parser_clean_report.set_defaults(func=clean_report)

    ####################################

    # Get runtime analysis command
    parser_runtime_analysis = subparsers.add_parser('runtime_analysis', help='Get runtime analysis')
    parser_runtime_analysis.add_argument('featurefile_zip', type=str, help='Path to the feature file zip')
    parser_runtime_analysis.set_defaults(func=get_runtime_analysis)

    # Get logs command
    parser_get_logs = subparsers.add_parser('get_logs', help='Get logs')
    parser_get_logs.set_defaults(func=get_logs)

    args = parser.parse_args()
    args.func(args)