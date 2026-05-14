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

from datetime import timezone, timedelta
from glob import glob
from modules.utils import initialize_logger
from modules.utils.weather import get_epw_utc_offset

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Clean Report', external_log_dir)


# Load sensor type mappings from CSV
SENSOR_TYPES_CSV = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'upload', 'sensor_types.csv')
data_mapping = {}
with open(SENSOR_TYPES_CSV, 'r') as f:
    for row in csv.DictReader(f):
        columns = [c for c in row['columns'].split('|') if c] if row['columns'] else []
        data_mapping[int(row['id'])] = {
            "name": row['name'],
            "columns": columns,
            "conversion_factor": float(row.get('conversion_factor', 1))
        }

############################################################################################################
# Name: clean_asset_dir(ASSET_DIR, LOCAL_BATCH_SIMULATION_DIR)
# Description: This function cleans the asset directory by removing all files and directories except for the
#   in.osm and in.osw files.
############################################################################################################
def clean_asset_dir(ASSET_DIR, LOCAL_BATCH_SIMULATION_DIR):

    # Define the files and directories to keep
    keep_files = {'in.osm', 'in.osw'}
    
    # Check environment variable to determine if we should keep additional directories
    keep_additional_dirs = os.environ.get('POWERTWIN_KEEP_DIRS') == '1'
    keep_dirs = {'feature_reports', 'generated_files'} if keep_additional_dirs else set()

    # Iterate through the files and directories in ASSET_REPORT_DIR
    for item in os.listdir(ASSET_DIR):
        item_path = os.path.join(ASSET_DIR, item)
        
        # Check if the item is a file and not in the keep_files set
        if os.path.isfile(item_path) and item not in keep_files:
            os.remove(item_path)
            
        # Check if the item is a directory and not in the keep_dirs set
        elif os.path.isdir(item_path) and item not in keep_dirs:
            shutil.rmtree(item_path)
    
    # Save file locally
    shutil.move(ASSET_DIR, os.path.join(LOCAL_BATCH_SIMULATION_DIR))
    
            
            

############################################################################################################
# Name: clean_report(LOCAL_DIR,LOCAL_BATCH_SIMULATION_DIR,SIMULATION_DIR, METADATA_CSV, asset_id)
# Description: This function processes the input CSV file and saves cleaned section reports to a new directory.
############################################################################################################
def clean_single_report(LOCAL_DIR,LOCAL_BATCH_SIMULATION_DIR,SIMULATION_DIR, METADATA_CSV, asset_id):
    logger.debug(f"Within clean_report for asset_id: {asset_id}")
    
    CLEANED_REPORT_DEST = os.path.join(LOCAL_DIR,'cleaned_reports', f'{asset_id}')
    os.makedirs(CLEANED_REPORT_DEST, exist_ok=True)
    
    # Find the metadata CSV file that ends with _metadata.csv
    METADATA_CSV = glob(os.path.join(LOCAL_DIR, '*_metadata.csv'))
    if not METADATA_CSV:
        logger.error("No metadata CSV file found")
        return
    METADATA_CSV = METADATA_CSV[0]  
    
    UNCLEAN_REPORT_CSV = os.path.join(SIMULATION_DIR, asset_id, "feature_reports", "default_feature_report.csv")
    ASSET_DIR = os.path.join(SIMULATION_DIR, asset_id)
    LOCAL_ASSET_DIR = os.path.join(LOCAL_BATCH_SIMULATION_DIR, asset_id)
    
    if ASSET_DIR is None:
        logger.error(f"No asset directory found for asset id: {asset_id}")
        return
    
    if UNCLEAN_REPORT_CSV is None:
        logger.error(f"No unclean report CSV file found for asset id: {asset_id}")
        return
        
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

    # Convert the datetime values to true UTC.
    #
    # EnergyPlus writes timestamps in the EPW file's local standard time (no
    # DST). Each building is matched to an EPW per-asset via haversine
    # proximity (see modules/utils/weather.py::get_location), so the offset
    # must be resolved from the EPW actually used for THIS building, not
    # inferred from the state. The EPW filename is recorded in in.osw under
    # the ChangeBuildingLocation measure.
    osw_path = os.path.join(ASSET_DIR, 'in.osw')
    with open(osw_path, 'r') as f:
        osw = json.load(f)
    weather_file_name = None
    for step in osw.get('steps', []):
        if step.get('measure_dir_name') == 'ChangeBuildingLocation':
            weather_file_name = step.get('arguments', {}).get('weather_file_name')
            break
    if not weather_file_name:
        raise ValueError(
            f"ChangeBuildingLocation weather_file_name missing in {osw_path}"
        )
    weather_title = weather_file_name[:-4] if weather_file_name.endswith('.epw') else weather_file_name
    utc_offset_hours = get_epw_utc_offset(weather_title)
    epw_tz = timezone(timedelta(hours=utc_offset_hours))
    # Keep timestamps in EPW local time (with offset) instead of converting to UTC.
    # The simulation's natural time domain is the EPW's local calendar, and
    # downstream bucketing (pack_results, consolidate_sensor_logs) needs to operate
    # in that domain to avoid year-boundary spillover into the next UTC year.
    df['Datetime'] = (
        pd.to_datetime(df['Datetime'], format='%Y/%m/%d %H:%M:%S')
          .dt.tz_localize(epw_tz)
          .map(lambda x: x.isoformat() if pd.notna(x) else None)
    )

    for data_id, data_info in data_mapping.items():
        data_header = data_info["name"]
        unclean_columns = data_info["columns"]
        
        # Check if all required columns exist in the DataFrame
        missing_columns = [col for col in unclean_columns if col not in df.columns]
        if missing_columns:
            logger.debug(f"Skipping {data_header} due to missing columns: {missing_columns}")
            continue
        
        # Skip if no columns to process
        if not unclean_columns:
            logger.debug(f"Skipping {data_header} as no columns are defined")
            continue
        
        # Skip if sensor_id is not available for this data_id
        if data_id not in sensor_id_list:
            logger.debug(f"Skipping {data_header} as no sensor ID found for data_id {data_id}")
            continue
            
        # Filter the relevant columns and make a copy of the DataFrame
        try:
            clean_df = df[["Datetime"] + unclean_columns].copy()
            
            # Rename columns to include section name
            clean_df.columns = ["ts"] + [f"{col}" for col in unclean_columns]
            
            # Add id and metadata columns
            clean_df['id'] = sensor_id_list[data_id]
            clean_df['metadata'] = "{}"
            
            # Sum the columns together and apply unit conversion (e.g. kBtu -> MMBtu)
            conversion_factor = data_info.get("conversion_factor", 1)
            clean_df['value'] = clean_df[unclean_columns].sum(axis=1) * conversion_factor
            
            # Reorder columns 
            columns_order = ["id", "ts", "value", "metadata"]
            clean_df = clean_df[columns_order]
            
            # Check if the entire DataFrame contains 0 for all values in the measure or has no measures
            if clean_df['value'].eq(0).all() or clean_df['value'].isna().all():
                logger.debug(f"Skipping {data_header} as all values are 0 or NA")
                continue
            
            # Save the section DataFrame to a new CSV file
            output_file = os.path.join(CLEANED_REPORT_DEST, f'cleaned_predicted_{data_header.lower().replace(" ", "_")}.csv')
            logger.debug(f"Saving cleaned section report to: {output_file}")
            clean_df.to_csv(output_file, index=False)
        
        except Exception as e:
            logger.error(f"Error processing {data_header}: {str(e)}")
            continue
        
    # Clean the asset directory    
    clean_asset_dir(ASSET_DIR, LOCAL_ASSET_DIR)

############################################################################################################
# Main script:
# Description: This function is the entry point for the script. Used for testing purposes.
############################################################################################################
if __name__ == "__main__":
    SIMULATION_DIR = ''
    METADATA_CSV = 'metadata.csv'
    asset_id = '1'
    SIMULATION_DIR = 'output'
    
    # Process the CSV file
    clean_single_report(SIMULATION_DIR,SIMULATION_DIR, METADATA_CSV, asset_id)