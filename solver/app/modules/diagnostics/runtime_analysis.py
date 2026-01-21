# ======================================================================================
# Runtime Analysis Module
# Purpose: Analyzes simulation runtime characteristics, determines optimal batch sizing,
#          and distributes assets across CPU cores for parallel execution
# ======================================================================================

import os
import json
import multiprocessing
import psutil

from modules.utils import initialize_logger
from modules.utils.hpc_environment import is_hpc_environment, get_hpc_info
from .db import insert_bulk_assets, distribute_assets_to_batches

# Setup logging with external log directory support (for HPC logging)
external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Runtime Analysis', external_log_dir)

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
    # Count lines in coordinates section of GeoJSON for complexity analysis
    # Used to estimate simulation complexity and runtime
    
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
# Name: asset_analysis(SIMULATION_DIR, num_cores, simulation_name)
# Description:  This function reads the feature files in the feature_files directory and extracts the asset data.
#   It reads the floor area, number of stories, and complexity of each asset and stores the data in a list.
#   The asset data is then sorted by complexity, number of stories, and floor area.
#   The sorted asset data is then stored in the database.
#   The number of assets is then distributed to batches based on the number of cores.
#   The function returns the total number of assets processed.
############################################################################################################
def asset_analysis(SIMULATION_DIR, num_cores, simulation_name):
    
    logger.debug("Within asset_analysis()")
    
    # Use centralized HPC detection
    is_hpc = is_hpc_environment()
    hpc_info = get_hpc_info()
    
    FEATURE_FILES_DIR = os.path.join(SIMULATION_DIR, 'feature_files')
    
    if is_hpc:
        logger.info(f"HPC environment detected - Job: {hpc_info['job_id']}, "
                   f"using {num_cores} cores as configured")
    else:
        # Local environment: validate requested cores against system availability
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
                # Skip empty files (can occur with corrupted downloads or processing errors)
                if not json_string.strip():
                    logger.warning(f"Skipping empty file: {filename}")
                    continue
                try:
                    data = json.loads(json_string)
                    # Extract building properties from GeoJSON structure
                    floor_area = data['features'][0]['properties']['floor_area']
                    number_of_stories = data['features'][0]['properties']['number_of_stories']
                    subtype = data['features'][0]['properties']['building_type']
                    # Extract simulation context (project-level metadata)
                    name = data['project']['name']
                    state = data['project']['climate_zone']
                    weather_file = data['project']['weather_filename']
                    # Calculate complexity metric based on coordinate count (proxy for geometry complexity)
                    coordinate_lines = count_coordinate_lines(json_string)

                    # Store the values in the list
                    asset_data.append((
                        asset_id,                # asset_id: unique building identifier
                        state,                   # state: climate zone for weather matching
                        weather_file,            # weather_file: EPW filename for simulation
                        floor_area,              # floor_area: building size in sq ft
                        number_of_stories,       # number_of_stories: building height
                        coordinate_lines,        # coordinate_lines: geometry complexity metric
                        name,                    # asset_name: human-readable building name
                        subtype,                 # subtype: building classification
                        simulation_name          # simulation_name: parent simulation identifier
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
    total_assets = asset_count  # Capture the actual filtered asset count
    
    # Import and update simulation state with true total_assets count
    from app.views import save_simulation_state
    save_simulation_state(simulation_name, 'running', {
        'assets_processed': 0,
        'total_assets': total_assets,
        'current_step': 'distributing_to_batches'
    })
    logger.info(f"Saved total_assets={total_assets} to simulation state")
    
    logger.info(f"Distributing assets to batches...")

    # Sort the asset data by complexity, number of stories, and floor area
    distributed_assets = distribute_assets_to_batches(num_cores, simulation_name)
    logger.info(f"Successfully distributed {distributed_assets} assets to {num_cores} batches")



############################################################################################################
# Name: main()
# Description: This function is the entry point for the script. Used for testing purposes.
############################################################################################################
if __name__ == "__main__":
    SIMULATION_DIR = ''
    num_cores = 4
    simulation_name = 'phoenix1'
    asset_analysis(SIMULATION_DIR, num_cores, simulation_name)
