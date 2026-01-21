# ======================================================================================
# Read Status Module
# Purpose: Provides real-time monitoring of simulation and batch execution progress,
#          including asset completion tracking, failure detection, and progress visualization
# ======================================================================================

from modules.utils import initialize_logger
import os
from .db import get_status_stats, get_batch_total, get_asset_total, get_bulk_batchids

# Setup logging with external log directory support (for HPC logging)
external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Read Batch Status', external_log_dir)


############################################################################################################
# Name: print_assets_progress(title, assets_completed, total_assets, progress)
# Description: This function prints the progress of the assets.
############################################################################################################
def print_assets_progress(title, assets_completed, total_assets, progress):
    # Display progress bar for asset completion with percentage and visual indicator
    # Format: "assets_completed/total_assets [percent%] |progress_bar| (title)"
    # Used for both per-batch and overall simulation progress
    
    # Calculate filled portion of progress bar (0-10 characters)
    filled_length = int(progress // 10)
    bar = '#' * filled_length + ' ' * (10 - filled_length)

    # Format asset count and percentage with proper spacing
    batch_format = f"{assets_completed}/{total_assets}"
    progress_format = f"[{progress:<4.1f}%]"

    # Construct and log progress output
    output = f"{batch_format: <14s}{progress_format: <10s}|{bar}| ({title})"
    logger.info(output)
    return output

############################################################################################################
# Name: read_batch_status(simulation_name, batch_id)
# Description: This function reads the status of the batch with the given batch_id. It prints the progress of the batch.
# It returns the number of finished batches, total assets, failed assets, and finished assets.
############################################################################################################
def read_batch_status(simulation_name, batch_id):
    
    # Retrieve asset completion statistics from database
    finished_assets, failed_assets = get_status_stats(simulation_name, batch_id)
    total_assets = get_asset_total(simulation_name, batch_id)
    # Mark batch as finished only if all assets are complete
    finished_batches = 1 if total_assets > 0 and total_assets == finished_assets else 0
    progress = (finished_assets / total_assets) * 100
    
    # Only display progress bar in non-HPC environments (avoid clutter in SLURM logs)
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
            # Query individual batch statistics
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

def get_simulation_summary(simulation_name):
    """
    Get simulation status summary in the format expected by bash scripts.
    In SLURM environments, reads from all active node databases.
    Otherwise, reads from consolidated master database.
    
    Args:
        simulation_name: Name of the simulation to query
        
    Returns:
        str: Status summary in format 'simulation_name | counts | context' or None if error
    """
    try:
        table_name = os.environ.get('PGDATABASE', 'powertwin')
        
        # Check if we're in SLURM environment (HPC parallel processing)
        if os.environ.get('SLURM_JOB_ID'):
            logger.debug(f"SLURM environment detected - reading from active node databases for {simulation_name}")
            return get_simulation_summary_from_nodes(simulation_name, table_name)
        else:
            logger.debug(f"Standard environment - reading from master database for {simulation_name}")
            return get_simulation_summary_from_master(simulation_name, table_name)
            
    except Exception as e:
        logger.error(f"Error in get_simulation_summary: {str(e)}")
        return f"{simulation_name} | Status query failed: {str(e)} | Status query completed"

def get_simulation_summary_from_master(simulation_name, table_name):
    """Get simulation status from consolidated master database"""
    try:
        from modules.database.sqlite_manager import get_sqlite_manager
        import sqlite3
        
        manager = get_sqlite_manager()
        db_path = manager.db_path
        
        if not os.path.exists(db_path):
            return f"{simulation_name} | Database not found | Status query completed"
        
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        
        # Query asset status counts from master database
        cursor = conn.execute('''
            SELECT 
                COUNT(CASE WHEN status = 'Finished' THEN 1 END) as finished,
                COUNT(CASE WHEN status = 'Failed' THEN 1 END) as failed,
                COUNT(CASE WHEN status = 'Not Processed Yet' THEN 1 END) as not_processed,
                COUNT(CASE WHEN status = 'Processing' THEN 1 END) as processing
            FROM {} 
            WHERE simulation_name = ?
        '''.format(table_name), (simulation_name,))
        
        row = cursor.fetchone()
        if row:
            finished = row['finished'] or 0
            failed = row['failed'] or 0
            not_processed = row['not_processed'] or 0
            processing = row['processing'] or 0
            summary = f"{simulation_name} | {finished}_assets_finished | {failed}_assets_failed | {not_processed}_assets_not_processed_yet | {processing}_assets_processing"
        else:
            summary = f"{simulation_name} | No assets found | Status query completed"
            
        conn.close()
        return summary
        
    except Exception as e:
        logger.error(f"Error in get_simulation_summary_from_master: {str(e)}")
        return f"{simulation_name} | Status query failed: {str(e)} | Status query completed"

def get_simulation_summary_from_nodes(simulation_name, table_name):
    """Get simulation status by aggregating from all active node databases in SLURM environment"""
    try:
        import sqlite3
        import glob
        
        # Get DATA_DIR from environment
        data_dir = os.environ.get('DATA_DIR')
        if not data_dir:
            logger.error("DATA_DIR environment variable not set - cannot locate node databases")
            return get_simulation_summary_from_master(simulation_name, table_name)
        
        # Node databases are in DATA_DIR/sqlite/node_*_t*/powertwin.db
        sqlite_dir = os.path.join(data_dir, 'sqlite')
        
        if not os.path.exists(sqlite_dir):
            logger.warning(f"SQLite directory not found: {sqlite_dir}")
            return get_simulation_summary_from_master(simulation_name, table_name)
        
        # Search pattern for node databases: node_*_t*/powertwin.db
        node_pattern = os.path.join(sqlite_dir, "node_*_t*", "powertwin.db")
        node_db_files = glob.glob(node_pattern)
        
        if not node_db_files:
            logger.warning(f"No node databases found matching pattern {node_pattern}. Falling back to master database.")
            return get_simulation_summary_from_master(simulation_name, table_name)
        
        logger.info(f"Found {len(node_db_files)} node databases: {[os.path.basename(os.path.dirname(f)) for f in node_db_files]}")
        
        # Initialize counters
        total_finished = 0
        total_failed = 0
        total_not_processed = 0
        total_processing = 0
        nodes_found = 0
        
        # Query each node database
        for node_db_path in node_db_files:
            try:
                if not os.path.exists(node_db_path):
                    logger.debug(f"Node database not found: {node_db_path}")
                    continue
                    
                conn = sqlite3.connect(node_db_path, timeout=5)
                conn.row_factory = sqlite3.Row
                
                # Check if table exists in this node database
                table_check = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,)
                ).fetchone()
                
                if not table_check:
                    logger.debug(f"Table {table_name} not found in {node_db_path}")
                    conn.close()
                    continue
                
                # Query status counts for this simulation from this node
                cursor = conn.execute('''
                    SELECT 
                        COUNT(CASE WHEN status = 'Finished' THEN 1 END) as finished,
                        COUNT(CASE WHEN status = 'Failed' THEN 1 END) as failed,
                        COUNT(CASE WHEN status = 'Not Processed Yet' THEN 1 END) as not_processed,
                        COUNT(CASE WHEN status = 'Processing' THEN 1 END) as processing
                    FROM {} 
                    WHERE simulation_name = ?
                '''.format(table_name), (simulation_name,))
                
                row = cursor.fetchone()
                if row:
                    node_finished = row['finished'] or 0
                    node_failed = row['failed'] or 0
                    node_not_processed = row['not_processed'] or 0
                    node_processing = row['processing'] or 0
                    
                    # Accumulate totals
                    total_finished += node_finished
                    total_failed += node_failed
                    total_not_processed += node_not_processed
                    total_processing += node_processing
                    
                    if (node_finished + node_failed + node_not_processed + node_processing) > 0:
                        nodes_found += 1
                        node_name = os.path.basename(os.path.dirname(node_db_path))
                        logger.debug(f"Node {node_name}: {node_finished} finished, {node_failed} failed, {node_not_processed} not processed, {node_processing} processing")
                
                conn.close()
                
            except Exception as e:
                logger.warning(f"Error reading node database {node_db_path}: {str(e)}")
                continue
        
        if nodes_found == 0:
            logger.warning(f"No data found in any node databases for simulation {simulation_name}")
            return f"{simulation_name} | No assets found in node databases | Status query completed"
        
        # Format aggregated results
        summary = f"{simulation_name} | {total_finished}_assets_finished | {total_failed}_assets_failed | {total_not_processed}_assets_not_processed_yet | {total_processing}_assets_processing"
        logger.info(f"Aggregated status from {nodes_found} nodes: {total_finished} finished, {total_failed} failed, {total_not_processed} not processed, {total_processing} processing")
        
        return summary
        
    except Exception as e:
        logger.error(f"Error in get_simulation_summary_from_nodes: {str(e)}")
        return f"{simulation_name} | Status query failed: {str(e)} | Status query completed"
        
if __name__ == "__main__":
    simulation_name = ''
    read_simulation_status(simulation_name, batch_id=None)
    
    
    
