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

from scripts.diagnostics import asset_analysis
from scripts.logger import initialize_logger


gff_logger = initialize_logger('Generate Feature Files')

#########################################################################################################
# Name: create_featurefiles(SIMULATION_DIR, asset_geojson, metadata_csv, config_json)
# Description: This function reads the JSON data from the input file and the area data from the
#   metadata file. It processes each feature and creates a new feature structure with additional properties.
#   It writes the new feature structure to individual feature files in the output directory.
#########################################################################################################
def create_featurefiles(SIMULATION_DIR,asset_geojson, metadata_csv, config_json, num_cores):
    
    gff_logger.info("Creating feature files...")

    # TODO: output dir should be the actual powertwin-db server
    feature_files_dir = os.path.join(SIMULATION_DIR, 'feature_files')
    os.makedirs(feature_files_dir, exist_ok=True)
    
        # Load the building occupancy mapping
    occupancy_mapping_path = os.path.join(SIMULATION_DIR, 'building_occupancy.json')
    with open(occupancy_mapping_path, 'r') as file:
        building_occupancy = json.load(file)


    # Read the metadata file
    building_area_list = {}
    building_type_list = {}
    building_name_list = {}
    
    processed_building_ids = set()
    
    # Read the metadata CSV file
    gff_logger.debug("Reading metadata CSV file...")
    with open(metadata_csv, 'r') as metadata_file:
        reader = csv.DictReader(metadata_file)
        for row in reader:
            asset_name = row['asset_name']
            asset_subtype_name = row['asset_subtype_name'] 
            asset_geometries_properties = json.loads(row['asset_geometries_properties'])
            asset_metadata = json.loads(row['asset_metadata'])  
            
            # Extract the area and id 
            floor_area = asset_metadata.get('area')
            building_id = str(asset_geometries_properties.get('id'))
            
            
            if (
                not floor_area or 
                not building_id or 
                not asset_subtype_name or 
                asset_subtype_name == "NULL" or  
                building_id in processed_building_ids
            ):
                continue
            
            # TODO: Temporary fix for Laboratory type until elevator problem is solved 
            if(asset_subtype_name == "Laboratory"):
                gff_logger.warning(f"This version does not support Laboratory subtype, changing building id {building_id} subtype to Education")
                asset_subtype_name = "Education"
            # if(asset_subtype_name == "Mixed use"):
            #     gff_logger.warning(f"Changing Mixed use type for building id: {building_id} to null")
            #     asset_subtype_name = "null"
        
            processed_building_ids.add(building_id)
            building_name_list[building_id] = asset_name
            building_area_list[building_id] = int(floor_area)
            building_type_list[building_id] = asset_subtype_name
        
    gff_logger.debug("Metadata CSV file read successfully.")
    
    # Read the JSON data
    gff_logger.debug("Reading asset geojson file...")
    with open(asset_geojson, 'r') as json_file:
        json_data = json.load(json_file)
    
    # Read the config JSON data
    gff_logger.debug("Reading config JSON file...")
    with open(config_json, 'r') as config_file:
        config_data = json.load(config_file)
        
    # Process each feature
    gff_logger.debug("Processing each feature...")
    for feature in json_data['features']:
        # Extract the properties
        properties = feature['properties']
        gff_logger.debug(f"Processing feature with properties: {properties}")
        asset_id = str(properties.get('asset_id')) 
        building_id = str(properties.get('id')) 
        
        floor_count = properties.get('floorCount') 
        if floor_count == str(floor_count):
            floor_count = int(floor_count)
        
        if floor_count is None:
            continue
            # floor_count = 1

        # Verify area, type, and name
        if (building_id not in building_area_list or 
            building_id not in building_type_list or 
            building_id not in building_name_list):
            continue

        floor_area = building_area_list[building_id]
        building_type = building_type_list[building_id]

        building_name = building_name_list[building_id].replace('/', ' ')

        # Get the occupancy subtype from the mapping
        occupancy_subtype = building_occupancy.get(building_type)
        
        # Define a mapping for number of occupants based on occupancy subtype
        occupants_mapping = {
            "Educational": 355,
            "Business": 100,
            "SmallResidential": 4,
            "BigResidential": 100,
            "Vacant": 1,
            "Industrial": 100,
            "Storage": 10,
            "FoodMercantile": 30,
            "Institutional": 40,
            "Health Care": 60,
            "Assembly": 200,
            "Mercantile": 150,
            "Mixed": 355,
            "Parking": 1
        }
        
        # Get the number of occupants based on the occupancy subtype
        number_of_occupants = occupants_mapping.get(occupancy_subtype)

        
        # Create new properties
        new_properties = {
            'id': str(properties.pop('id')),
            'asset_id': str(properties.pop('asset_id'))
        }

        new_properties.update(properties)
        
        # Add default properties
        new_properties.update({
            "floor_area": int(floor_area),  
            "footprint_area": int(floor_area / floor_count),  
            "type": "Building",
            "building_type": building_type,
            "number_of_stories": floor_count,
            "windows": [],
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
                    "r_value": 10.0
                },
                "roof": {
                    "material": "Super Insulated Roof",
                    "r_value": 15.0
                }
            }
        })
        
        # Implement properties from configuration JSON (overriding the default values)
        new_properties.update(config_data)

        # Remove unnecessary properties
        new_properties.pop('height', None)
        new_properties.pop('base', None)
        new_properties.pop('floorCount', None)

        # Create new feature structure
        new_feature = {
            "type": "Feature",
            "geometry": feature['geometry'], 
            "properties": new_properties
        }

        # Create the final JSON structure
        #TODO: modify weather data to be dynamic
        final_json = {
            "type": "FeatureCollection",
            "mappers": [],
            "project": {
                "id": f"{building_id}",
                "name": f"{building_name}",
                "description": f"Feature file for building with asset id:{asset_id} and id: {building_id}",
                "begin_date": "2023-01-01T00:00:00.000Z",
                "end_date": "2023-12-31T23:00:00.000Z",
                "cec_climate_zone": None,
                "climate_zone": "2A",
                "default_template": "90.1-2013",
                "import_surrounding_buildings_as_shading": None,
                "surface_elevation": None,
                "tariff_filename": None,
                "timesteps_per_hour": 1,
                "weather_filename": "USA_AZ_Phoenix-Sky.Harbor.Intl.AP.722780_TMY3.epw",
                "emissions": True,
                "electricity_emissions_future_subregion": "AZNMc",
                "electricity_emissions_hourly_historical_subregion": "Southwest",
                "electricity_emissions_annual_historical_subregion": "AZNM",
                "electricity_emissions_future_year": "2024",
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

        new_building_name = building_name.replace(' ', '_')
        
        # Write to individual feature file
        feature_file_path = os.path.join(feature_files_dir, f'{building_id}_{new_building_name}.json')
        with open(feature_file_path, 'w') as feature_file:
            json.dump(final_json, feature_file, indent=4)

    gff_logger.info("Feature files created successfully.")
    
    asset_analysis(SIMULATION_DIR, num_cores)
    
    # Zip the output directory
    gff_logger.debug("Zipping the output directory...")
    zip_file_path = shutil.make_archive(feature_files_dir, 'zip', feature_files_dir)
    
    # Remove the unzipped directory
    gff_logger.debug("Removing the unzipped directory...")
    shutil.rmtree(feature_files_dir)
    
    gff_logger.info(f"Zip file created at: {zip_file_path}")
    


############################################################################################################
# Name: main()
# Description: This function is the entry point for the script. Used for testing purposes.
############################################################################################################
if __name__ == "__main__":
    asset_geojson = 'app/powertwin-db/uploaded_files/asset.geojson'
    metadata_csv = 'app/powertwin-db/uploaded_files/metadata.csv'
    config_json = 'app/powertwin-db/uploaded_files/custom_config.json'
    SIMULATION_DIR = 'app/powertwin-db/uploaded_files'
    
    create_featurefiles(SIMULATION_DIR,asset_geojson, metadata_csv, config_json)
    