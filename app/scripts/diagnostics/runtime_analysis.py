import os
import json
import csv
import multiprocessing
import shutil

from scripts.helper import initialize_logger

logger = initialize_logger('Runtime Analysis')

############################################################################################################
# Name: count_coordinate_lines(json_string)
# Description: This function counts the number of lines in the coordinates section of a JSON string.
#   It searches for the "coordinates" key and counts the number of lines between the opening and closing brackets.
#   It returns the number of lines in the coordinates section.
############################################################################################################
def count_coordinate_lines(json_string):
    start = json_string.find('"coordinates": [')
    if start == -1:
        return 0
    start = json_string.find('[', start)
    
    bracket_count = 0
    end = start
    while end < len(json_string):
        if json_string[end] == '[':
            bracket_count += 1
        elif json_string[end] == ']':
            bracket_count -= 1
            if bracket_count == 0:
                break
        end += 1
    
    coordinates_section = json_string[start:end+1]
    return coordinates_section.count('\n')

############################################################################################################
# Name: asset_analysis(SIMULATION_DIR, num_cores)
# Description: This function reads the JSON files in the specified directory and extracts the asset data.
#   It writes the asset data to a CSV file and schedules the assets for processing.
############################################################################################################
def asset_analysis(SIMULATION_DIR, LOCAL_DIR, num_cores, location):
    logger.debug("Within asset_analysis()")
    
    #TODO: Move uosim_time.csv into a Postgres database along with the batch assignment
    
    UOSIM_CSV = os.path.join(SIMULATION_DIR, 'uosim_time.csv')
    LOCAL_UOSIM_CSV = os.path.join(LOCAL_DIR, 'uosim_time.csv')
    FEATURE_FILES_DIR = os.path.join(SIMULATION_DIR, 'feature_files')
    
    if num_cores > multiprocessing.cpu_count():
        logger.warning(f"Warning: Number of cores ({num_cores}) is greater than the number of available cores ({multiprocessing.cpu_count()}).")
        logger.critical("Using all available cores.")
        num_cores = multiprocessing.cpu_count()
    else:
        logger.info(f"Number of cores: {num_cores}")
    
    # Define the fieldnames for the CSV file
    fieldnames = ['batch', 'name', 'assetid', 'floor_area', 'number_of_stories', 'complexity','location', 'total_time', 'uo_run', 'uo_process']

    # Create the CSV file if it doesn't exist
    if not os.path.exists(UOSIM_CSV):
        with open(UOSIM_CSV, mode='w', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()

    # Create a list to store the asset data
    # Iterate over all files in the directory
    asset_data = []
    logger.info(f"Processing assets...")
    for filename in os.listdir(FEATURE_FILES_DIR):
        if filename.endswith('.json'):
            # Extract asset_id from the filename
            asset_id = filename.split('_')[0]
            #logger.debug(f"Processing asset {asset_id}...")

            # Read the JSON file
            with open(os.path.join(FEATURE_FILES_DIR, filename), 'r') as json_file:
                json_string = json_file.read()
                if not json_string.strip():
                    logger.warning(f"Skipping empty file: {filename}")
                    continue
                try:
                    data = json.loads(json_string)
                    # Extract the values
                    floor_area = data['features'][0]['properties']['floor_area']
                    #name = data['features'][0]['properties']['name']
                    number_of_stories = data['features'][0]['properties']['number_of_stories']
                    name = data['project']['name']
                    
                    # Count the number of lines in the coordinates section
                    coordinate_lines = count_coordinate_lines(json_string)

                    # Store the values in the list
                    asset_data.append({
                        'batch': None,  
                        'assetid': asset_id,
                        'name': name,
                        'floor_area': floor_area,
                        'number_of_stories': number_of_stories,
                        'complexity': coordinate_lines,
                        'location': location,
                        'total_time': None,  
                        'uo_run': None,      
                        'uo_process': None   
                    })
                except json.JSONDecodeError as e:
                    logger.error(f"Error decoding JSON in file {filename}: {e}")

    # Sort the asset data by complexity, number of stories, and floor area
    asset_data.sort(key=lambda x: (x['complexity'], x['number_of_stories'], x['floor_area']), reverse=True)

    # Distribute the assets into batches
    for i, asset in enumerate(asset_data):
        asset['batch'] = i % num_cores

    # Write the data to the CSV file
    with open(UOSIM_CSV, mode='w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(asset_data)
    
    logger.info("Copying uosim_time.csv to local directory...")
    shutil.copy(UOSIM_CSV, LOCAL_UOSIM_CSV)

    # Print the scheduled assets for each core
    # for i in range(num_cores):
    #     logger.info(f"Batch {i + 1}:")
    #     for asset in asset_data:
    #         if asset['batch'] == i:
    #             logger.info(f"  {asset['name']}: Complexity: {asset['complexity']}, Stories: {asset['number_of_stories']}, Floor Area: {asset['floor_area']}")
    #     logger.info("")

############################################################################################################
# Name: main()
# Description: This function is the entry point for the script. Used for testing purposes.
############################################################################################################
if __name__ == "__main__":
    SIMULATION_DIR = ''
    num_cores = 4
    location = 'Phoenix-SkyHarbor'
    LOCAL_DIR = ''
    asset_analysis(SIMULATION_DIR, LOCAL_DIR, num_cores, location)