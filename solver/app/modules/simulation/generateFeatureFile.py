############################################################################################################
# getfeaturefile.py
# This script reads the JSON data from the input file and the area data from the
#   metadata file. It processes each feature and creates a new feature structure with additional properties.
#   It writes the new feature structure to individual feature files in the output directory.
############################################################################################################

import csv
import json
import os
import shutil
import re

from modules.diagnostics import asset_analysis
from modules.utils import initialize_logger

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Generate Feature Files', external_log_dir)

OCCUPANTS_MAPPING = {
    "Educational": 355,
    "Business": 100,
    "SmallResidential": 4,
    "BigResidential": 355,
    "Vacant": 1,
    "Industrial": 100,
    "Storage": 10,
    "FoodMercantile": 30,
    "Institutional": 40,
    "Health Care": 60,
    "Assembly": 200,
    "Mercantile": 150,
    "Mixed": 355,
    "Parking": 1,
    "Unknown": 0
}

# Load asset subtypes from CSV: id -> {name, occupancy_type, effective_id}
ASSET_SUBTYPES = {}
ASSET_SUBTYPES_CSV = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'upload', 'asset_subtypes.csv')
with open(ASSET_SUBTYPES_CSV, 'r') as f:
    for row in csv.DictReader(f):
        ASSET_SUBTYPES[int(row['id'])] = {
            'name': row['name'],
            'occupancy_type': row['occupancy_type'],
            'effective_id': int(row['effective_id']),
        }

DEFAULT_SUBTYPE_ID = 4  # Single-Family

# Reverse lookup: building_type name -> occupancy_type (from effective/self-referencing rows only)
BUILDING_TYPE_TO_OCCUPANCY = {}
for sid, info in ASSET_SUBTYPES.items():
    if info['effective_id'] == sid:
        BUILDING_TYPE_TO_OCCUPANCY[info['name']] = info['occupancy_type']

def sanitize_filename(name):
    sanitized = name.replace("'", "")
    sanitized = re.sub(r'[^\w\-]', '_', sanitized)
    sanitized = re.sub(r'_+', '_', sanitized)
    sanitized = sanitized.strip('_')
    return sanitized

############################################################################################################
# Name: read_metadata()
# Description: This function reads the metadata CSV file and returns the building area and type data.
############################################################################################################
def read_metadata(metadata_csv):
    from modules.utils.weather import get_location
    
    building_area_list = {}
    building_type_list = {}
    building_name_list = {}
    building_weather_list = {}
    processed_building_ids = set()


    with open(metadata_csv, 'r') as metadata_file:
        reader = csv.DictReader(metadata_file)
        
        # Read each row in the CSV file to assign building data to its corresponding building ID
        for row in reader:
            asset_name = row['asset_name']
            asset_subtype_id = row.get('asset_subtype_id', '')
            asset_geometries_properties = json.loads(row['asset_geometries_properties'])
            asset_metadata = json.loads(row['asset_metadata'])

            floor_area = asset_metadata.get('area')
            building_id = str(asset_geometries_properties.get('id')) # Most important id, considered the PK

            if not floor_area or not building_id or building_id in processed_building_ids:
                continue

            # Resolve subtype: parse ID, fall back to default if missing/invalid
            try:
                subtype_id = int(asset_subtype_id)
            except (ValueError, TypeError):
                subtype_id = DEFAULT_SUBTYPE_ID

            if subtype_id not in ASSET_SUBTYPES:
                subtype_id = DEFAULT_SUBTYPE_ID

            # Resolve effective subtype via effective_id (handles temporary remappings)
            # https://docs.urbanopt.net/workflows/residential_workflows/building_types.html
            effective_id = ASSET_SUBTYPES[subtype_id]['effective_id']
            effective_subtype = ASSET_SUBTYPES[effective_id]
            building_type = effective_subtype['name']

            processed_building_ids.add(building_id)

            building_name_list[building_id] = asset_name
            building_area_list[building_id] = int(floor_area)
            building_type_list[building_id] = building_type
            building_weather_list[building_id] = get_location(asset_metadata)
    
    # Return the building area, type, name, and weather data
    return building_area_list, building_type_list, building_name_list, building_weather_list

############################################################################################################
# Name: flatten_geometry()
# Description: Flattens MultiPolygon geometries into single Polygons by merging all rings.
#   Returns True if geometry was modified, False otherwise.
############################################################################################################
def flatten_geometry(geom):
    if not geom or 'type' not in geom or 'coordinates' not in geom:
        return False
        
    gt = geom['type']
    coords = geom['coordinates']
    
    if gt == 'MultiPolygon':
        # merge every ring from every polygon into one Polygon
        rings = [ring for poly in coords for ring in poly]
        geom['type'] = 'Polygon'
        geom['coordinates'] = rings
        #logger.debug("Converted MultiPolygon to Polygon")
        return True
    return False

############################################################################################################
# Name: process_feature()
# Description: This function processes each feature and creates a new feature structure with additional properties.
#   It returns the new feature structure.
############################################################################################################
def process_feature(feature, building_area_list, building_type_list, building_name_list, building_weather_list, custom_config_data):
    # Flatten nested geometries if present
    if 'geometry' in feature:
        flatten_geometry(feature['geometry'])

    properties = feature['properties']
    #logger.debug(f"Processing feature with properties: {properties}")
    asset_id = str(properties.get('asset_id'))
    building_id = str(properties.get('id'))

    floor_count = properties.get('floor_count')
    if floor_count == str(floor_count):
        floor_count = int(floor_count)

    # No floor count leads to high unpredictable results, could potentially set to 1 however do not expect accurate results when comparing to real data
    if floor_count is None:
        return None

    # Essential data missing in metadata
    if building_id not in building_area_list or building_id not in building_type_list or building_id not in building_name_list:
        return None

    floor_area = building_area_list[building_id]
    building_type = building_type_list[building_id]
    building_name = sanitize_filename(building_name_list[building_id])
        
    #TODO: Instead of a simple set mapping schema implement a more complex mapping schema that considers square footage and other factors
    occupancy_subtype = BUILDING_TYPE_TO_OCCUPANCY.get(building_type, "Unknown")
    number_of_occupants = OCCUPANTS_MAPPING.get(occupancy_subtype, 0)

    # Create new properties (must be first)
    new_properties = {
        'id': str(properties.pop('id')),
        'asset_id': str(properties.pop('asset_id'))
    }
    new_properties.update(properties)
    
    # Calculate the perimeter of the building footprint (assuming a rectangular shape)
    # Assuming asset is rectangluar
    footprint_area = int(floor_area / floor_count)
    side_length = footprint_area ** 0.5
    perimeter = 4 * side_length

    # Calculate the exterior wall area
    floor_height = 9.0  # 9 feet per floor
    exterior_wall_area = perimeter * floor_count * floor_height

    # Apply a window-to-wall ratio (WWR)
    window_to_wall_ratio = 0.15  # 15% WWR
    window_area = int(window_to_wall_ratio * exterior_wall_area)

    # https://github.com/urbanopt/urbanopt-geojson-gem/blob/master/lib/urbanopt/geojson/schema/building_properties.json
    # Refer to Baseline.rb for default types found in all buildings (located in urbanopt github repo)
    # Add default properties
    new_properties.update({
        "name": building_name,
        "floor_area": int(floor_area),  
        "footprint_area": int(floor_area / floor_count),  
        "type": "Building",
        "building_type": building_type,
        "number_of_stories": floor_count,
        "windows": [
            {
                "window_area": window_area,
                "window_type": "Double Pane"
            }
        ],
        "weekday_start_time": "00:00",
        "weekday_duration": "24:00",
        "weekend_start_time": "00:00",
        "weekend_duration": "24:00",
        "number_of_occupants": number_of_occupants,
        "system_type": "VAV district chilled water with district hot water reheat",
        "heating_system_fuel_type": "electricity",
        "constructions": {
            "wall": {
                "material": "Super Insulated Wall",
                "r_value": 15.0
            },
            "roof": {
                "material": "Super Insulated Roof",
                "r_value": 15.0
            }
        }
    })
    # Apply custom properties if SmallResidential subtype
    if occupancy_subtype == "SmallResidential":
    
        #logger.debug(f"Building {building_id} in state {state}, climate zone {climate_zone}: selected system type '{residential_system_type}'")
        
        new_properties.update({
            "number_of_stories_above_ground": floor_count,
            "foundation_type": "basement - conditioned", 
            "attic_type": "attic - unvented",  
            "number_of_residential_units": 1,
            "number_of_bedrooms": int(number_of_occupants / 2)
        })

    # NOTE Load the custom configuration data, the user can override the following configurations for all feature files:
    #   weekday_start_time, weekday_duration, weekend_start_time, weekend_duration, heating_system_fuel_type, construction r values
    # The rest of the properties may be modified to individual feature files but must be done before processesing the feature file
    new_properties.update(custom_config_data)
    
    # Remove useless properties
    new_properties.pop('height', None)
    new_properties.pop('base', None)
    new_properties.pop('floor_count', None)

    # Combine geometry and properties
    new_feature = {
        "type": "Feature",
        "geometry": feature['geometry'],
        "properties": new_properties
    }
    
    # Get weather data from building_weather_list
    if building_id in building_weather_list:
        state, weather_file = building_weather_list[building_id]
        
        # Check if weather data is valid
        if state is None or weather_file is None:
            logger.warning(f"Invalid weather data for building_id {building_id} (missing coordinates)")
            return None
            
        weather_filename = weather_file + '.epw'
    else:
        # Fallback if weather data not found
        logger.warning(f"No weather data found for building_id {building_id}")
        return None

    # Map state to emissions regions
    future_emissions_mapping = {
        'FL': 'FRCCc', 'MS': 'SRMVc', 'NE': 'MROWc', 'OR': 'NWPPc', 'CA': 'CAMXc',
        'VA': 'SRVCc', 'AR': 'SRMVc', 'TX': 'ERCTc', 'OH': 'RFCWc', 'UT': 'NWPPc',
        'MT': 'NWPPc', 'TN': 'SRTVc', 'ID': 'NWPPc', 'WI': 'MROEc', 'WV': 'RFCWc',
        'NC': 'SRVCc', 'LA': 'SRMVc', 'IL': 'SRMWc', 'OK': 'SPSOc', 'IA': 'MROWc',
        'WA': 'NWPPc', 'SD': 'MROWc', 'MN': 'MROWc', 'KY': 'SRTVc', 'MI': 'RFCMc',
        'KS': 'SPNOc', 'NJ': 'RFCEc', 'NY': 'NYSTc', 'IN': 'RFCWc', 'VT': 'NEWEc',
        'NM': 'AZNMc', 'WY': 'RMPAc', 'GA': 'SRSOc', 'MO': 'SRMWc', 'DC': 'RFCEc',
        'SC': 'SRVCc', 'PA': 'RFCEc', 'CO': 'RMPAc', 'AZ': 'AZNMc', 'ME': 'NEWEc',
        'AL': 'SRSOc', 'MD': 'RFCEc', 'NH': 'NEWEc', 'MA': 'NEWEc', 'ND': 'MROWc',
        'NV': 'NWPPc', 'CT': 'NEWEc', 'DE': 'RFCEc', 'RI': 'NEWEc'
    }
    
    hourly_historical_mapping = {
        'FL': 'Florida', 'MS': 'Midwest', 'NE': 'Midwest', 'OR': 'Northwest', 'CA': 'California',
        'VA': 'Carolinas', 'AR': 'Midwest', 'TX': 'Texas', 'OH': 'Midwest', 'UT': 'Northwest',
        'MT': 'Northwest', 'TN': 'Tennessee', 'ID': 'Northwest', 'WI': 'Midwest', 'WV': 'Midwest',
        'NC': 'Carolinas', 'LA': 'Midwest', 'IL': 'Midwest', 'OK': 'Central', 'IA': 'Midwest',
        'WA': 'Northwest', 'SD': 'Midwest', 'MN': 'Midwest', 'KY': 'Tennessee', 'MI': 'Midwest',
        'KS': 'Central', 'NJ': 'Mid-Atlantic', 'NY': 'New York', 'IN': 'Midwest', 'VT': 'New England',
        'NM': 'Southwest', 'WY': 'Rocky Mountains', 'GA': 'Southeast', 'MO': 'Midwest', 'DC': 'Mid-Atlantic',
        'SC': 'Carolinas', 'PA': 'Mid-Atlantic', 'CO': 'Rocky Mountains', 'AZ': 'Southwest', 'ME': 'New England',
        'AL': 'Southeast', 'MD': 'Mid-Atlantic', 'NH': 'New England', 'MA': 'New England', 'ND': 'Midwest',
        'NV': 'Northwest', 'CT': 'New England', 'DE': 'Mid-Atlantic', 'RI': 'New England'
    }
    
    annual_historical_mapping = {
        'FL': 'FRCC', 'MS': 'SRMV', 'NE': 'MROW', 'OR': 'NWPP', 'CA': 'CAMX',
        'VA': 'SRVC', 'AR': 'SRMV', 'TX': 'ERCT', 'OH': 'RFCW', 'UT': 'NWPP',
        'MT': 'NWPP', 'TN': 'SRTV', 'ID': 'NWPP', 'WI': 'MROE', 'WV': 'RFCW',
        'NC': 'SRVC', 'LA': 'SRMV', 'IL': 'SRMW', 'OK': 'SPSO', 'IA': 'MROW',
        'WA': 'NWPP', 'SD': 'MROW', 'MN': 'MROW', 'KY': 'SRTV', 'MI': 'RFCM',
        'KS': 'SPNO', 'NJ': 'RFCE', 'NY': 'NYCW', 'IN': 'RFCW', 'VT': 'NEWE',
        'NM': 'AZNM', 'WY': 'RMPA', 'GA': 'SRSO', 'MO': 'SRMW', 'DC': 'RFCE',
        'SC': 'SRVC', 'PA': 'RFCE', 'CO': 'RMPA', 'AZ': 'AZNM', 'ME': 'NEWE',
        'AL': 'SRSO', 'MD': 'RFCE', 'NH': 'NEWE', 'MA': 'NEWE', 'ND': 'MROW',
        'NV': 'NWPP', 'CT': 'NEWE', 'DE': 'RFCE', 'RI': 'NEWE'
    }
    
    # TODO Climate zones may vary extensively depending on exact location, for now using state mapping 
    climate_zone_mapping = {
        'AL': '3A', 'AK': '7', 'AZ': '2B', 'AR': '3A', 'CA': '3B',
        'CO': '5B', 'CT': '5A', 'DE': '4A', 'FL': '2A', 'GA': '3A',
        'HI': '1A', 'ID': '5B', 'IL': '5A', 'IN': '5A', 'IA': '6A',
        'KS': '4A', 'KY': '4A', 'LA': '2A', 'ME': '6A', 'MD': '4A',
        'MA': '5A', 'MI': '6A', 'MN': '6A', 'MS': '3A', 'MO': '4A',
        'MT': '6B', 'NE': '5A', 'NV': '3B', 'NH': '6A', 'NJ': '4A',
        'NM': '4B', 'NY': '5A', 'NC': '4A', 'ND': '7', 'OH': '5A',
        'OK': '3A', 'OR': '4C', 'PA': '5A', 'RI': '5A', 'SC': '3A',
        'SD': '6A', 'TN': '4A', 'TX': '2A', 'UT': '5B', 'VT': '6A',
        'VA': '4A', 'WA': '4C', 'WV': '5A', 'WI': '6A', 'WY': '6B',
        'DC': '4A'
    }
    
    future_subregion = future_emissions_mapping.get(state)
    hourly_subregion = hourly_historical_mapping.get(state)
    annual_subregion = annual_historical_mapping.get(state)
    climate_zone = climate_zone_mapping.get(state)
    
    # Create the final JSON structure
    final_json = {
        "type": "FeatureCollection",
        "mappers": [],
        "project": {
            "id": f"{building_id}",
            "name": f"{building_name}",
            "description": f"Feature file for building with asset id:{asset_id} and id: {building_id}",
            "begin_date": f"{os.environ.get('SIMULATION_YEAR', '2025')}-01-01T00:00:00.000Z",
            "end_date": f"{os.environ.get('SIMULATION_YEAR', '2025')}-12-31T23:00:00.000Z",
            "default_template": "90.1-2013",
            "cec_climate_zone": None,
            "import_surrounding_buildings_as_shading": None,
            "surface_elevation": None,
            "tariff_filename": None,
            "timesteps_per_hour": 1,
            "emissions": True,
            "climate_zone": climate_zone,
            "weather_filename": weather_filename,
            "electricity_emissions_future_subregion": future_subregion,
            "electricity_emissions_hourly_historical_subregion": hourly_subregion,
            "electricity_emissions_annual_historical_subregion": annual_subregion,
            "electricity_emissions_future_year": "2026",
            "electricity_emissions_hourly_historical_year": "2019",
            "electricity_emissions_annual_historical_year": "2019"
        },
        "scenarios": [
            {
                "feature_mappings": [],
                "id": f"{building_id}",
                "name": f"{building_name} Scenario"
            }
        ],
        "features": [new_feature]
    }
    

    return final_json, building_id, building_name

############################################################################################################
# Name: create_single_featurefile()
# Description: This function creates a single feature file for the specified asset ID.
############################################################################################################
def create_bulk_featurefiles(failed_asset_ids, SIMULATION_DIR, LOCAL_RECOVERY_DIR, simulation_name):
    """Efficiently create feature files for multiple failed assets by reading data files once."""
    if not failed_asset_ids:
        logger.info("No failed assets to process")
        return True
    
    logger.info(f"Creating feature files for {len(failed_asset_ids)} failed assets...")
    
    FEATURE_FILES_DIR = os.path.join(SIMULATION_DIR, 'feature_files')
    os.makedirs(FEATURE_FILES_DIR, exist_ok=True)
    
    METADATA_CSV = os.path.join(LOCAL_RECOVERY_DIR, f'{simulation_name}_metadata.csv')
    ASSET_GEOJSON = os.path.join(LOCAL_RECOVERY_DIR, f'{simulation_name}_asset.geojson')
    CONFIG_JSON = os.path.join(LOCAL_RECOVERY_DIR, f'{simulation_name}_config.json')
    
    # Read files once instead of for each asset
    try:
        logger.debug("Reading metadata, geojson, and config files...")
        building_area_list, building_type_list, building_name_list, building_weather_list = read_metadata(METADATA_CSV)
        
        with open(ASSET_GEOJSON, 'r') as geojson_file, open(CONFIG_JSON, 'r') as config_file:
            geojson_data = json.load(geojson_file)
            custom_config_data = json.load(config_file)
            
    except Exception as e:
        logger.error(f"Error reading data files: {e}")
        return False
    
    # Convert failed asset IDs to a set for O(1) lookups
    failed_assets_set = set(failed_asset_ids)
    processed_count = 0
    
    # Process only features for failed assets
    for feature in geojson_data['features']:
        properties = feature.get('properties', {})
        building_id = int(properties.get('id'))
        
        # Process feature only if it's in our failed assets list
        if building_id in failed_assets_set:
            result = process_feature(feature, building_area_list, building_type_list, 
                                  building_name_list, building_weather_list, custom_config_data)
            if result:
                final_json, _, building_name = result
                new_building_name = sanitize_filename(building_name)
                feature_file_path = os.path.join(FEATURE_FILES_DIR, f'{building_id}_{new_building_name}.json')
                
                try:
                    with open(feature_file_path, 'w') as feature_file:
                        json.dump(final_json, feature_file, indent=4)
                    processed_count += 1
                    logger.debug(f"Feature file updated for failed asset_id: {building_id}")
                except Exception as e:
                    logger.error(f"Error writing feature file for asset {building_id}: {e}")
            else:
                logger.warning(f"Could not process feature for failed asset {building_id}")
    
    logger.info(f"Successfully processed {processed_count}/{len(failed_asset_ids)} failed assets")
    return processed_count > 0


def create_single_featurefile(asset_id, SIMULATION_DIR, LOCAL_RECOVERY_DIR, simulation_name):
    """Create a single feature file for the specified asset ID. Consider using create_bulk_featurefiles for better performance."""
    FEATURE_FILES_DIR = os.path.join(SIMULATION_DIR, 'feature_files')
    os.makedirs(FEATURE_FILES_DIR, exist_ok=True)
    
    METADATA_CSV = os.path.join(LOCAL_RECOVERY_DIR, f'{simulation_name}_metadata.csv')
    ASSET_GEOJSON = os.path.join(LOCAL_RECOVERY_DIR, f'{simulation_name}_asset.geojson')
    CONFIG_JSON = os.path.join(LOCAL_RECOVERY_DIR, f'{simulation_name}_config.json')
    
    # Metadata requires the area, subtype and name of the building to be present from the metadata
    building_area_list, building_type_list, building_name_list, building_weather_list = read_metadata(METADATA_CSV)
    with open(ASSET_GEOJSON, 'r') as geojson_file, open(CONFIG_JSON, 'r') as config_file:
        geojson_data = json.load(geojson_file)
        custom_config_data = json.load(config_file)


    # Process each feature in the GeoJSON data 
    for feature in geojson_data['features']:
        # Extract building_id from feature properties
        properties = feature.get('properties', {})
        building_id = int(properties.get('id'))  
        
        # Process feature only if it matches the asset_id
        if building_id == int(asset_id): 
            result = process_feature(feature, building_area_list, building_type_list, 
                                  building_name_list, building_weather_list, custom_config_data)
            if result:
                final_json, _, building_name = result
                new_building_name = sanitize_filename(building_name)
                feature_file_path = os.path.join(FEATURE_FILES_DIR, f'{asset_id}_{new_building_name}.json')
                with open(feature_file_path, 'w') as feature_file:
                    json.dump(final_json, feature_file, indent=4)
                logger.debug(f"Feature file created for asset_id: {asset_id}")
                return True
    
    logger.debug(f"No matching feature found for asset_id: {asset_id}")
    return False

############################################################################################################
# Name: create_featurefiles()
# Description: This function reads the JSON data from the input file and the area data from the
#   metadata file. It processes each feature and creates a new feature structure with additional properties.
#   It writes the new feature structure to individual feature files in the output directory.
############################################################################################################
def create_featurefiles(SIMULATION_DIR, LOCAL_DIR, asset_geojson, metadata_csv, config_json, num_cores, simulation_name):
    logger.info("Creating feature files...")


    FEATURE_FILES_DIR = os.path.join(SIMULATION_DIR, 'feature_files')
    LOCAL_FEATURE_FILES_DIR = os.path.join(LOCAL_DIR, 'feature_files')
    os.makedirs(FEATURE_FILES_DIR, exist_ok=True)
    
    # Metadata requires the area, subtype and name of the building to be present from the metadata
    building_area_list, building_type_list, building_name_list, building_weather_list = read_metadata(metadata_csv)
    
    with open(asset_geojson, 'r') as file:
        geojson_data = json.load(file)
    
    with open(config_json, 'r') as file:
        custom_config_data = json.load(file)


    # Process each feature in the GeoJSON data 
    logger.info("Processing features...")   
    for feature in geojson_data['features']:
        result = process_feature(feature, building_area_list, building_type_list, building_name_list, building_weather_list, custom_config_data)
        # If the result is not None, write the feature file
        if result:
            final_json, building_id, building_name = result
            new_building_name = sanitize_filename(building_name)
            feature_file_path = os.path.join(FEATURE_FILES_DIR, f'{building_id}_{new_building_name}.json')
            with open(feature_file_path, 'w') as feature_file:
                json.dump(final_json, feature_file, indent=4)

    logger.info("Feature files created successfully.")
    # Run the asset analysis to organize the assets to their batch
    asset_analysis(SIMULATION_DIR, num_cores, simulation_name)

    logger.debug("Zipping the output directory...")
    shutil.make_archive(LOCAL_FEATURE_FILES_DIR, 'zip', FEATURE_FILES_DIR)
    zip_file_path = shutil.make_archive(FEATURE_FILES_DIR, 'zip', FEATURE_FILES_DIR)

    logger.debug("Removing the unzipped directory...")
    shutil.rmtree(FEATURE_FILES_DIR)

    logger.info(f"Zip file created at: {zip_file_path}")

############################################################################################################
# Name: main()
# Description: This function is the entry point for the script. Used for testing purposes.
############################################################################################################
if __name__ == "__main__":
    asset_geojson = 'powertwin-solver-pg/uploaded_files/asset.geojson'
    metadata_csv = 'powertwin-solver-pg/uploaded_files/metadata.csv'
    config_json = 'powertwin-solver-pg/uploaded_files/custom_config.json'
    SIMULATION_DIR = 'powertwin-solver-pg/uploaded_files'
    LOCAL_DIR = 'powertwin-solver-pg/user_files'
    num_cores = 1
    simulation_name = 'simulation'
    
    create_featurefiles(SIMULATION_DIR, LOCAL_DIR, asset_geojson, metadata_csv, config_json, num_cores, simulation_name)