import os
import csv

from rich.console import Console
from rich.table import Table
from scripts.helper import initialize_logger

rbs_logger = initialize_logger('Read Batch Status')
console = Console()

def print_assets_progress(title, assets_completed, total_assets, progress):
    filled_length = int(progress // 10)
    bar = '#' * filled_length + ' ' * (10 - filled_length)

    batch_format = f"{assets_completed}/{total_assets}"
    progress_format = f"[{progress:<4.1f}%]"

    output = f"{batch_format: <14s}{progress_format: <10s}|{bar}| ({title})"
    rbs_logger.info(output)
    return output

############################################################################################################
# Name: read_batch_status(BATCH_CSV)
# Description: This function reads the batch status file from the given file path.
############################################################################################################
def read_batch_status(BATCH_CSV):
    if not os.path.exists(BATCH_CSV):
        rbs_logger.error(f"Batch status file not found: {BATCH_CSV}")
        return
    filename = os.path.basename(BATCH_CSV)
    batch_id = filename.split('_')[0]
    
    log_entries = []
    total_assets = 0
    finished_assets = 0
    try:
        with open(BATCH_CSV, mode='r') as file:
            reader = csv.DictReader(file)
            for row in reader:
                total_assets += 1
                if row["Status"] != "Not Processed Yet":
                    log_entries.append(row)
                if row["Status"] == "Finished":
                    finished_assets += 1
    except Exception as e:
        rbs_logger.error(f"Error reading file {BATCH_CSV}: {str(e)}")
        return 0, 0
    
                
    rbs_logger.debug(f"Printing Status for Batch: {batch_id}")
    if log_entries:
        # Create a table with rich
        table = Table(title=f"Batch {batch_id} Status ({finished_assets}/{total_assets})", style="bold magenta")

        # Add columns to the table
        table.add_column("Asset ID", justify="center", style="red", no_wrap=True)
        table.add_column("Name", justify="center", style="white", no_wrap=True)
        table.add_column("Status", justify="center", no_wrap=True)


        # Add rows to the table
        for entry in log_entries:
            status = entry["Status"]
            if status == "Finished":
                status_style = "green"
            elif status == "Processing":
                status_style = "orange3"
            elif status == "Not Processed Yet":
                status_style = "grey50"
            elif status == "Failed":
                status_style = "red"
            else:
                status_style = "white"

            table.add_row(entry["Asset ID"], entry["Name"], f"[{status_style}]{status}[/{status_style}]")

        # Print the table to the console
        console.print(table)
            
        progress = (finished_assets / total_assets) * 100
        print_assets_progress(f"Batch {batch_id} Progress", finished_assets, total_assets, progress)
    else:
        rbs_logger.error("No entries found in the batch status file.")
        
    return total_assets, finished_assets

############################################################################################################
# Name: read_simulation_status(SIMULATION_STATUS_DIR, batch_id)
# Description: This function reads the batch status files in the simulation status directory.
############################################################################################################
def read_simulation_status(SIMULATION_STATUS_DIR, batch_id=None):
    if not os.path.exists(SIMULATION_STATUS_DIR):
        rbs_logger.error(f"Simulation status directory not found: {SIMULATION_STATUS_DIR}")
        return

    total_assets = 0
    finished_assets = 0
    total_batches = 0
    finished_batches = 0

    if batch_id is not None:
        # Read the specific batch status file
        BATCH_CSV = os.path.join(SIMULATION_STATUS_DIR, f'{batch_id}_status.csv')
        batch_total_assets, batch_finished_assets = read_batch_status(BATCH_CSV)
        
        total_assets += batch_total_assets
        finished_assets += batch_finished_assets
        total_batches = 1
        if batch_total_assets == batch_finished_assets:
            finished_batches = 1
    else:
        # Recursively go through the batch_status directory and read all batch status files
        for root, dirs, files in os.walk(SIMULATION_STATUS_DIR):
            for file_name in files:
                if file_name.endswith('_status.csv'):
                    BATCH_CSV = os.path.join(root, file_name)
                    rbs_logger.debug(f"Processing batch file: {BATCH_CSV}")
                    batch_total_assets, batch_finished_assets = read_batch_status(BATCH_CSV)
                    total_assets += batch_total_assets
                    finished_assets += batch_finished_assets
                    total_batches += 1
                    if batch_total_assets == batch_finished_assets:
                        finished_batches += 1
                    
    # Calculate overall progress
    if total_assets > 0:
        overall_progress = (finished_assets / total_assets) * 100
        rbs_logger.info(f"Batch Progress: ({finished_batches}/{total_batches})")
        print_assets_progress("Overall Progress", finished_assets, total_assets, overall_progress)
    else:
        rbs_logger.error("No assets found in the simulation status directory.")

if __name__ == "__main__":
    SIMULATION_STATUS_DIR = os.path.join(os.getcwd(), 'app', 'powertwin-solver-pg', 'user_files', 'example_simulation', 'batch_status')
    read_simulation_status(SIMULATION_STATUS_DIR)
    
    
    
