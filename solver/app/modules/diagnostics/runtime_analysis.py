import os
import json
import multiprocessing

from modules.utils import initialize_logger
from .db import insert_bulk_assets, distribute_assets_to_batches

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
    
    if num_cores > multiprocessing.cpu_count():
        logger.warning(f"Warning: Number of cores ({num_cores}) is greater than the number of available cores ({multiprocessing.cpu_count()}).")
        logger.critical("Using all available cores.")
        num_cores = multiprocessing.cpu_count()
    else:
        logger.info(f"Number of cores: {num_cores}")
    


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