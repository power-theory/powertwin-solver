############################################################################################################
# clean_report.py
# This script processes the input CSV file and saves cleaned section reports to a new directory. 
#     The script reads a CSV file containing energy consumption data and processes it to create 
#     cleaned section reports. The script defines a column mapping for different sections of the report, 
#     filters the relevant columns, and saves the cleaned section reports to a new directory. 
#     The script also checks for sections with all zero values and skips them.
############################################################################################################


import pandas as pd
import json
import csv
import os
import shutil


from glob import glob
from scripts.helper import initialize_logger


cr_logger = initialize_logger('Clean Report')


# Define the column mapping
data_mapping = {
    1: {
        "name": "Electricity",
        "columns": ["Electricity:Facility(kWh)"]
    },
    2: {
        "name": "Renewables",
        "columns": ["ElectricityProduced:Facility()"]
    },
    3: {
        "name": "Hot Water",
        "columns": [
            "WaterSystems:NaturalGas(kBtu)",
            "WaterSystems:Propane(kBtu)",
            "WaterSystems:FuelOilNo2(kBtu)",
            "WaterSystems:OtherFuels(kBtu)"
        ]
    },
    4: {
        "name": "Water",
        "columns": []
    },
    5: {
        "name": "Chilled Water",
        "columns": []
    },
    6: {
        "name": "CO2 Emissions",
        "columns": [
            "Historical_Hourly_Electricity_Emissions(MT)",
            "Natural_Gas_Emissions(MT)",
            "Propane_Emissions(MT)",
            "FuelOilNo2_Emissions(MT)"
        ]
    },
    7: {
        "name": "Steam",
        "columns": []
    },
    8: {
        "name": "Natural Gas",
        "columns": ["NaturalGas:Facility(kBtu)"]
    },
    9: {
        "name": "Propane",
        "columns": ["Propane:Facility(kBtu)"]
    },
    10: {
        "name": "Fuel Oil",
        "columns": ["FuelOilNo2:Facility(kBtu)"]
    }
}

def clean_asset_dir(ASSET_DIR):
    # Define the files and directories to keep
    keep_files = {'in.osm', 'in.osw'}
    keep_dirs = {'feature_reports', 'generated_files'}

    # Iterate through the files and directories in ASSET_REPORT_DIR
    for item in os.listdir(ASSET_DIR):
        item_path = os.path.join(ASSET_DIR, item)
        
        # Check if the item is a file and not in the keep_files set
        if os.path.isfile(item_path) and item not in keep_files:
            os.remove(item_path)
            
        # Check if the item is a directory and not in the keep_dirs set
        elif os.path.isdir(item_path) and item not in keep_dirs:
            shutil.rmtree(item_path)

############################################################################################################
# Name: clean_report(CLEANED_REPORT_DEST,BATCH_SIMULATION_DIR, METADATA_CSV, asset_id)
# Description: This function processes the input CSV file and saves cleaned section reports to a new directory.
############################################################################################################
def clean_single_report(SIMULATION_DIR,BATCH_SIMULATION_DIR, METADATA_CSV, asset_id):
    cr_logger.debug(f"Within clean_report for asset_id: {asset_id}")
    
    CLEANED_REPORT_DEST = os.path.join(SIMULATION_DIR,'cleaned_reports', f'{asset_id}')
    os.makedirs(CLEANED_REPORT_DEST, exist_ok=True)
    
    # Find the metadata CSV file that ends with _metadata.csv
    METADATA_CSV = glob(os.path.join(SIMULATION_DIR, '*_metadata.csv'))
    if not METADATA_CSV:
        cr_logger.error("No metadata CSV file found")
        return
    METADATA_CSV = METADATA_CSV[0]  
    
    UNCLEAN_REPORT_CSV = os.path.join(BATCH_SIMULATION_DIR, "run", "powertwin_scenario", asset_id, "feature_reports", "default_feature_report.csv")
    ASSET_DIR = os.path.join(BATCH_SIMULATION_DIR, "run", "powertwin_scenario", asset_id)
        
    sensor_id_list = {}
    
    with open(METADATA_CSV, 'r') as metadata_file:
        reader = csv.DictReader(metadata_file)
        for row in reader:
            asset_geometries_properties = json.loads(row['asset_geometries_properties'])  

            if asset_id not in str(asset_geometries_properties.get('id')):
                continue
            
            sensor_id =  row['sensor_id']
            sensor_type_id = int(row['sensor_type_id'])
            
            sensor_id_list[sensor_type_id] = sensor_id
    

    # Read the CSV file into a DataFrame
    df = pd.read_csv(UNCLEAN_REPORT_CSV)
    
    # Convert the datetime format to UTC
    df['Datetime'] = pd.to_datetime(df['Datetime'], format='%Y/%m/%d %H:%M:%S')
    df['Datetime'] = df['Datetime'].dt.tz_localize('UTC').dt.strftime('%Y-%m-%dT%H:%M:%SZ')

    for data_id, data_info in data_mapping.items():
        data_header = data_info["name"]
        unclean_columns = data_info["columns"]

        # Filter the relevant columns and make a copy of the DataFrame
        clean_df = df[["Datetime"] + unclean_columns].copy()

        # Rename columns to include section name
        clean_df.columns = ["ts"] + [f"{col}" for col in unclean_columns]

        # Add id and metadata columns
        clean_df['id'] = sensor_id_list[data_id]
        clean_df['metadata'] = "{}"
        
        # Sum the columns together and name the result 'value'
        clean_df['value'] = clean_df[unclean_columns].sum(axis=1)

        # Reorder columns 
        columns_order = ["id", "ts", "value","metadata"]
        clean_df = clean_df[columns_order]

        # Check if the entire DataFrame contains 0 for all values in the measure or has no measures
        if clean_df['value'].eq(0).all() or clean_df['value'].isna().all():
            continue

        # Save the section DataFrame to a new CSV file
        output_file = os.path.join(CLEANED_REPORT_DEST, f'cleaned_predicted_{data_header.lower().replace(" ", "_")}.csv')
        cr_logger.debug(f"Saving cleaned section report to: {output_file}")
        clean_df.to_csv(output_file, index=False)

    clean_asset_dir(ASSET_DIR)

############################################################################################################
# Main script:
# Description: This function is the entry point for the script. Used for testing purposes.
############################################################################################################
if __name__ == "__main__":
    BATCH_SIMULATION_DIR = ''
    METADATA_CSV = 'metadata.csv'
    asset_id = '1'
    SIMULATION_DIR = 'output'
    
    # Process the CSV file
    clean_single_report(SIMULATION_DIR,BATCH_SIMULATION_DIR, METADATA_CSV, asset_id)