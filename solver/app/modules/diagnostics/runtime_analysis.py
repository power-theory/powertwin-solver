import os
import json
import multiprocessing
import psutil

from modules.utils import initialize_logger
from .db import insert_bulk_assets, distribute_assets_to_batches

logger = initialize_logger('Runtime Analysis')

#############################################################################################################
# Name: get_available_cores()
# Description: This function determines the number of available CPU cores for processing.
#   It checks the CPU usage per core and returns the number of cores that are below a certain threshold (70%).
#   If it fails to get the CPU usage, it falls back to using half of the total CPU cores available.
#############################################################################################################
def get_available_cores():
    try:
        # Get CPU usage per core
        cpu_usage = psutil.cpu_percent(interval=1, percpu=True)
        # Count cores with usage less than 70%
        available_cores = sum(1 for usage in cpu_usage if usage < 70.0)
        # Ensure at least one core is returned
        return max(1, available_cores)
    except Exception as e:
        logger.warning(f"Failed to get CPU usage: {e}. Falling back to default method.")
        return max(1, multiprocessing.cpu_count() // 2)


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
# Name: asset_analysis(SIMULATION_DIR, num_cores, location, simulation_name)
# Description:  This function reads the feature files in the feature_files directory and extracts the asset data.
#   It reads the floor area, number of stories, and complexity of each asset and stores the data in a list.
#   The asset data is then sorted by complexity, number of stories, and floor area.
#   The sorted asset data is then stored in the database.
#   The number of assets is then distributed to batches based on the number of cores.
#   The function returns the total number of assets processed.
############################################################################################################
def asset_analysis(SIMULATION_DIR, num_cores, location, simulation_name):
    logger.debug("Within asset_analysis()")
    

    FEATURE_FILES_DIR = os.path.join(SIMULATION_DIR, 'feature_files')
    
    # Replace the existing core check with:
    if num_cores <= 0:
        num_cores = multiprocessing.cpu_count()

    available_cores = get_available_cores()
    if num_cores > available_cores:
        logger.warning(f"Requested cores ({num_cores}) exceeds available cores ({available_cores})")
        logger.info(f"Adjusting to use {available_cores} available cores")
        num_cores = available_cores
    else:
        logger.info(f"Using {num_cores} of {available_cores} available cores")
    


    # Create a list to store the asset data
    # Iterate over all files in the directory
    asset_data = []
    asset_count = 0
    batch_size = 500  # Adjust this based on your dataset size
    
    logger.info(f"Processing assets...")
    for filename in os.listdir(FEATURE_FILES_DIR):
        if filename.endswith('.json'):
            # Extract asset_id from the filename
            asset_id = filename.split('_')[0]
            
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
                    number_of_stories = data['features'][0]['properties']['number_of_stories']
                    subtype = data['features'][0]['properties']['building_type']
                    name = data['project']['name']
                    # Count the number of lines in the coordinates section
                    coordinate_lines = count_coordinate_lines(json_string)

                    # Store the values in the list
                    asset_data.append((
                        asset_id,                # asset_id
                        location,                # location
                        floor_area,              # floor_area
                        number_of_stories,       # number_of_stories
                        coordinate_lines,        # complexity
                        name,                    # asset_name
                        subtype,                 # subtype
                        simulation_name          # simulation_name
                    ))
                    asset_count += 1
                    
                    # Process in batches to avoid memory issues
                    if len(asset_data) >= batch_size:
                        insert_bulk_assets(asset_data)
                        asset_data = []
                        
                except json.JSONDecodeError as e:
                    logger.error(f"Error decoding JSON in file {filename}: {e}")

    # Insert any remaining assets
    if asset_data:
        insert_bulk_assets(asset_data)
    
    logger.info(f"Processed total of {asset_count} assets")
    
    
    logger.info(f"Distributing assets to batches...")

    # Sort the asset data by complexity, number of stories, and floor area
    total_assets = distribute_assets_to_batches(num_cores, simulation_name)
    logger.info(f"Successfully processed {total_assets} assets")



############################################################################################################
# Name: main()
# Description: This function is the entry point for the script. Used for testing purposes.
############################################################################################################
if __name__ == "__main__":
    SIMULATION_DIR = ''
    num_cores = 4
    location = 'Phoenix-SkyHarbor'
    simulation_name = 'phoenix1'
    asset_analysis(SIMULATION_DIR, num_cores, location, simulation_name)