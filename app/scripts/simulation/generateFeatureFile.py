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
import pandas as pd

from scripts.diagnostics import asset_analysis
from scripts.helper import initialize_logger

gff_logger = initialize_logger('Generate Feature Files')

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

BUILDING_SUBTYPES = {
    "Education": "Educational",
    "Office": "Business",
    "Single-Family Detached": "SmallResidential",
    "Single-Family Attached": "SmallResidential",
    "Multifamily": "BigResidential",
    "Single-Family": "SmallResidential",
    "Multifamily (2 to 4 units)": "BigResidential",
    "Multifamily (5 or more units)": "BigResidential",
    "Vacant": "Vacant",
    "Laboratory": "Industrial",
    "Nonrefrigerated warehouse": "Storage",
    "Food sales": "FoodMercantile",
    "Public order and safety": "Institutional",
    "Outpatient health care": "Health Care",
    "Refrigerated warehouse": "Storage",
    "Religious worship": "Assembly",
    "Public assembly": "Assembly",
    "Food service": "FoodMercantile",
    "Inpatient health care": "Health Care",
    "Nursing": "Health Care",
    "Lodging": "BigResidential",
    "Strip shopping mall": "Mercantile",
    "Enclosed mall": "Mercantile",
    "Retail other than mall": "Mercantile",
    "Service": "Business",
    "Mixed use": "Mixed",
    "Uncovered Parking": "Parking",
    "Covered Parking": "Parking",
    "null": "Unknown"
}


WEATHER_MAP_CSV = 'app/urbanopt/weather_map.csv'

def get_weather_data(city):
    
    weather_df = pd.read_csv(WEATHER_MAP_CSV)
    
    # Find city
    city_data = weather_df[weather_df['City'].str.lower() == city.lower()]
    
    if city_data.empty:
        raise ValueError(f"No weather data found for city: {city}")
    
    # Extract data
    #https://docs.urbanopt.net/workflows/carbon_emissions.html
    city_data = city_data.iloc[0]
    return {
        "climate_zone": city_data['ClimateZone'],
        "weather_filename": city_data['WeatherFile'] + '.epw',
        "electricity_emissions_future_subregion": city_data['FutureSubregion'],
        "electricity_emissions_hourly_historical_subregion": city_data['AVERT_Region'],
        "electricity_emissions_annual_historical_subregion": city_data['Subregion']
    }


def load_json_file(file_path):
    with open(file_path, 'r') as file:
        return json.load(file)


############################################################################################################
# Name: read_metadata()
# Description: This function reads the metadata CSV file and returns the building area and type data.
############################################################################################################
def read_metadata(metadata_csv):
    building_area_list = {}
    building_type_list = {}
    building_name_list = {}
    processed_building_ids = set()

    gff_logger.debug("Reading metadata CSV file...")
    with open(metadata_csv, 'r') as metadata_file:
        reader = csv.DictReader(metadata_file)
        for row in reader:
            asset_name = row['asset_name']
            asset_subtype_name = row['asset_subtype_name']
            asset_geometries_properties = json.loads(row['asset_geometries_properties'])
            asset_metadata = json.loads(row['asset_metadata'])

            floor_area = asset_metadata.get('area')
            building_id = str(asset_geometries_properties.get('id'))

            if not floor_area or not building_id or not asset_subtype_name or asset_subtype_name == "NULL" or asset_subtype_name == "null" or building_id in processed_building_ids:
                continue

            # Exclude big residential buildings (Lodging and low highrise Multifamily are an exceptions) (Limited by UrbanOpt)
            # *Note - The Mixed use building type can accommodate up to 4 building types and their corresponding fractions of total floor area. 
            # If the number of building types is fewer than 4, additional building use types must be added but the fraction of total area can be
            # entered as 0.
            # # TODO: Mixed use requires a lot more detail to undestand what is mixed and what it contains, Laborartoy requires elevator support
            if asset_subtype_name in ["Multifamily", "Multifamily (2 to 4 units)", "Multifamily (5 or more units)", "Mixed use","Laboratory"]:
                continue

            processed_building_ids.add(building_id)
            
            building_name_list[building_id] = asset_name
            building_area_list[building_id] = int(floor_area)
            building_type_list[building_id] = asset_subtype_name

    gff_logger.debug("Metadata CSV file read successfully.")
    return building_area_list, building_type_list, building_name_list

############################################################################################################
# Name: process_feature()
# Description: This function processes each feature and creates a new feature structure with additional properties.
# It takes the gathered lists from the metadata file and the features from the geojson file.
# The custom config serves as a default configuration unless modified by the user.
#   It returns the new feature structure.
############################################################################################################
def process_feature(feature, building_area_list, building_type_list, building_name_list, custom_config_data, location):
    properties = feature['properties']
    gff_logger.debug(f"Processing feature with properties: {properties}")
    asset_id = str(properties.get('asset_id'))
    building_id = str(properties.get('id'))

    floor_count = properties.get('floorCount')
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
    building_name = building_name_list[building_id].replace('/', ' ').replace('&', ' ')

    #TODO: Instead of a simple set mapping schema implement a more complex mapping schema that considers square footage and other factors
    occupancy_subtype = BUILDING_SUBTYPES.get(building_type, "Unknown")
    number_of_occupants = OCCUPANTS_MAPPING.get(occupancy_subtype, 0)

    # Create new properties
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
    # Add default properties
    new_properties.update({
        "name": building_name,
        "floor_area": int(floor_area),  
        "footprint_area": int(floor_area / floor_count),  
        "type": "Building",
        "building_type": #building_type, commenting to induce error
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
    
    if occupancy_subtype == "SmallResidential":
        new_properties.update({
            "number_of_stories_above_ground": floor_count,
            "foundation_type": "basement - conditioned", 
            "attic_type": "attic - unvented",  
            "number_of_residential_units": 1,
            "number_of_bedrooms": int(number_of_occupants / 2),
            "system_type": "Residential - electric resistance and central air conditioner"
        })
    
    
    # Load the custom configuration data, the user can override the following configurations for all feature files:
    #   weekday_start_time, weekday_duration, weekend_start_time, weekend_duration, system_type, heating_system_fuel_type, construction r values
    # The rest of the properties may be modified to individual feature files but must be done before processesing the feature file
    new_properties.update(custom_config_data)
    
    # Remove useless properties
    new_properties.pop('height', None)
    new_properties.pop('base', None)
    new_properties.pop('floorCount', None)

    new_feature = {
        "type": "Feature",
        "geometry": feature['geometry'],
        "properties": new_properties
    }
    
    weather_data = get_weather_data(location)

    final_json = {
        "type": "FeatureCollection",
        "mappers": [],
        "project": {
            "id": f"{building_id}",
            "name": f"{building_name}",
            "description": f"Feature file for building with asset id:{asset_id} and id: {building_id}",
            "begin_date": "2025-01-01T00:00:00.000Z",
            "end_date": "2025-12-31T23:00:00.000Z",
            "default_template": "90.1-2013",
            "cec_climate_zone": None,
            "import_surrounding_buildings_as_shading": None,
            "surface_elevation": None,
            "tariff_filename": None,
            "timesteps_per_hour": 1,
            "emissions": True,
            "climate_zone": weather_data["climate_zone"],
            "weather_filename": weather_data["weather_filename"],
            "electricity_emissions_future_subregion": weather_data["electricity_emissions_future_subregion"],
            "electricity_emissions_hourly_historical_subregion": weather_data["electricity_emissions_hourly_historical_subregion"],
            "electricity_emissions_annual_historical_subregion": weather_data["electricity_emissions_annual_historical_subregion"],
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
# Name: create_featurefiles()
# Description: This function reads the JSON data from the input file and the area data from the
#   metadata file. It processes each feature and creates a new feature structure with additional properties.
#   It writes the new feature structure to individual feature files in the output directory.
############################################################################################################
def create_featurefiles(SIMULATION_DIR, asset_geojson, metadata_csv, config_json, num_cores, location):
    gff_logger.info("Creating feature files...")

    feature_files_dir = os.path.join(SIMULATION_DIR, 'feature_files')
    os.makedirs(feature_files_dir, exist_ok=True)
    
    # Metadata requires the area, subtype and name of the building to be present from the metadata
    building_area_list, building_type_list, building_name_list, = read_metadata(metadata_csv)
    geojson_data = load_json_file(asset_geojson)
    custom_config_data = load_json_file(config_json)

    for feature in geojson_data['features']:
        result = process_feature(feature, building_area_list, building_type_list, building_name_list, custom_config_data, location)
        if result:
            final_json, building_id, building_name = result
            new_building_name = building_name.replace(' ', '_')
            feature_file_path = os.path.join(feature_files_dir, f'{building_id}_{new_building_name}.json')
            with open(feature_file_path, 'w') as feature_file:
                json.dump(final_json, feature_file, indent=4)

    gff_logger.info("Feature files created successfully.")
    asset_analysis(SIMULATION_DIR, num_cores, location)

    gff_logger.debug("Zipping the output directory...")
    zip_file_path = shutil.make_archive(feature_files_dir, 'zip', feature_files_dir)

    gff_logger.debug("Removing the unzipped directory...")
    shutil.rmtree(feature_files_dir)

    gff_logger.info(f"Zip file created at: {zip_file_path}")

############################################################################################################
# Name: main()
# Description: This function is the entry point for the script. Used for testing purposes.
############################################################################################################
if __name__ == "__main__":
    asset_geojson = 'app/powertwin-solver-pg/uploaded_files/asset.geojson'
    metadata_csv = 'app/powertwin-solver-pg/uploaded_files/metadata.csv'
    config_json = 'app/powertwin-solver-pg/uploaded_files/custom_config.json'
    SIMULATION_DIR = 'app/powertwin-solver-pg/uploaded_files'
    location = 'Phoenix'
    num_cores = 1
    
    create_featurefiles(SIMULATION_DIR, asset_geojson, metadata_csv, config_json, num_cores, location)