from modules.utils import initialize_logger
import os
from .db import get_status_stats, get_batch_total, get_asset_total, get_bulk_batchids

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Read Batch Status', external_log_dir)


############################################################################################################
# Name: print_assets_progress(title, assets_completed, total_assets, progress)
# Description: This function prints the progress of the assets.
############################################################################################################
def print_assets_progress(title, assets_completed, total_assets, progress):
    filled_length = int(progress // 10)
    bar = '#' * filled_length + ' ' * (10 - filled_length)

    batch_format = f"{assets_completed}/{total_assets}"
    progress_format = f"[{progress:<4.1f}%]"

    output = f"{batch_format: <14s}{progress_format: <10s}|{bar}| ({title})"
    logger.info(output)
    return output

############################################################################################################
# Name: read_batch_status(simulation_name, batch_id)
# Description: This function reads the status of the batch with the given batch_id. It prints the progress of the batch.
# It returns the number of finished batches, total assets, failed assets, and finished assets.
############################################################################################################
def read_batch_status(simulation_name, batch_id):
    
    finished_assets, failed_assets = get_status_stats(simulation_name, batch_id)
    total_assets = get_asset_total(simulation_name,batch_id)
    finished_batches = 1 if total_assets > 0 and total_assets == finished_assets else 0
    progress = (finished_assets / total_assets) * 100
    
    if not os.environ.get('SLURM_JOB_ID'):
        print_assets_progress(f'Batch {batch_id}', finished_assets, total_assets, progress)
    
    return finished_batches,total_assets,failed_assets,finished_assets

############################################################################################################
# Name: read_simulation_status(simulation_name, batch_id)
# Description: This function reads the status of the simulation. If batch_id is None, it reads the status of all batches.
# If batch_id is not None, it reads the status of the batch with the given batch_id.
############################################################################################################
def read_simulation_status(simulation_name, batch_id=None):
    logger.debug(f"Reading status for {simulation_name}")
    
    if batch_id is None:
        total_batches = get_batch_total(simulation_name)
        
        # Initialize counters
        total_assets = 0
        finished_assets = 0
        failed_assets = 0 
        finished_batches = 0
        
        # Loop through each batch

        batch_list = get_bulk_batchids(simulation_name)
        for batch in batch_list:
            batch_finished, batch_total, batch_failed, batch_completed = read_batch_status(simulation_name, batch)
            
            # Accumulate totals
            total_assets += batch_total
            finished_assets += batch_completed
            failed_assets += batch_failed
            finished_batches += batch_finished
        
        # Calculate overall progress
        if total_assets > 0:
            overall_progress = (finished_assets / total_assets) * 100
            logger.info(f"\nBatch Progress: ({finished_batches}/{total_batches})")
            logger.info(f"Failed Assets: {failed_assets}")
            print_assets_progress("Overall Progress", finished_assets, total_assets, overall_progress)
        else:
            logger.error(f"No assets found for simulation {simulation_name}") 
    
    else:
        read_batch_status(simulation_name,batch_id)
        
    
   
        
if __name__ == "__main__":
    simulation_name = ''
    read_simulation_status(simulation_name, batch_id=None)
    
    
    
