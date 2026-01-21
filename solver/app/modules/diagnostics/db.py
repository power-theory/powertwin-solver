"""
Database operations for Powertwin Solver.
Automatically routes to SQLite (HPC) or PostgreSQL (Docker) based on environment.
"""
import os
from datetime import datetime
import modules.database.sqlite_operations as sqlite_ops
import modules.database.postgres_operations as postgres_ops
from modules.utils import initialize_logger
from modules.utils.hpc_environment import is_hpc_environment, log_environment_summary

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Database', external_log_dir)

# Environment detection using centralized method
IS_HPC_ENVIRONMENT = is_hpc_environment()


def create_table():
    """Create table using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        return sqlite_ops.create_table()
    else:
        return postgres_ops.create_table()
      

def insert_asset(asset_id, state, weather_file, floor_area, number_of_stories, complexity, asset_name, subtype, simulation_name):
    """Insert asset using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        return sqlite_ops.insert_asset(asset_id, state, weather_file, floor_area, number_of_stories, complexity, asset_name, subtype, simulation_name)
    else:
        return postgres_ops.insert_asset(asset_id, state, weather_file, floor_area, number_of_stories, complexity, asset_name, subtype, simulation_name)

  
def insert_bulk_assets(asset_data_list):
    """Insert bulk assets using appropriate database for the environment."""
    logger.debug(f"insert_bulk_assets called: HPC={IS_HPC_ENVIRONMENT}, asset_count={len(asset_data_list) if asset_data_list else 0}")
    
    if IS_HPC_ENVIRONMENT:
        # Convert docker format to SQLite format if needed
        if not asset_data_list:
            logger.debug("No assets to insert")
            return True
            
        # Debug: log the first asset to understand the format
        logger.debug(f"First asset format: {type(asset_data_list[0])}, content: {asset_data_list[0] if asset_data_list else 'None'}")
        
        if isinstance(asset_data_list[0], (list, tuple)):
            # Convert from docker tuple format to dictionary format
            logger.debug("Converting from tuple format to dictionary format")
            converted_assets = []
            for asset in asset_data_list:
                try:
                    converted_assets.append({
                        'asset_id': asset[0],
                        'state': asset[1],
                        'weather_file': asset[2],
                        'floor_area': asset[3],
                        'number_of_stories': asset[4],
                        'complexity': asset[5],
                        'asset_name': asset[6],
                        'subtype': asset[7],
                        'simulation_name': asset[8]
                    })
                except IndexError as e:
                    logger.error(f"Asset tuple format error: {e}, asset: {asset}")
                    return False
            logger.debug(f"Converted {len(converted_assets)} assets to dictionary format")
            return sqlite_ops.insert_bulk_assets(converted_assets)
        elif isinstance(asset_data_list[0], dict):
            # Already in dictionary format
            logger.debug("Assets already in dictionary format")
            return sqlite_ops.insert_bulk_assets(asset_data_list)
        else:
            logger.error(f"Unknown asset data format: {type(asset_data_list[0])}")
            return False
    else:
        return postgres_ops.insert_bulk_assets(asset_data_list)


def distribute_assets_to_batches(num_cores, simulation_name):
    """Distribute assets to batches using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        return sqlite_ops.distribute_assets_to_batches(simulation_name, num_cores)
    else:
        return postgres_ops.distribute_assets_to_batches(num_cores, simulation_name)


def update_batch(asset_id, batch):
    """Update batch using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        return sqlite_ops.update_batch(asset_id, batch)
    else:
        return postgres_ops.update_batch(asset_id, batch)


def update_time(asset_id, uorun_time, uoprocess_time, total_time, simulation_name=None):
    """Update timing using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        # SQLite version expects simulation_name, try to get it if not provided
        if simulation_name is None:
            logger.warning(f"simulation_name not provided for update_time, asset_id: {asset_id}, "
                           f"attempting to retrieve from database")
            # Try to get simulation name from existing record
            conn = sqlite_ops.get_sqlite_connection()
            table_name = os.environ.get("PGDATABASE", "powertwin")
            cursor = conn.execute(f"SELECT simulation_name FROM {table_name} WHERE asset_id = ?", (asset_id,))
            row = cursor.fetchone()
            simulation_name = row['simulation_name'] if row else 'unknown'
        return sqlite_ops.update_time(simulation_name, asset_id, uorun_time, uoprocess_time, total_time)
    else:
        return postgres_ops.update_time(asset_id, uorun_time, uoprocess_time, total_time)

    
def update_status(status, asset_id=None, simulation_name=None):
    """Update status using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        if simulation_name and asset_id:
            return sqlite_ops.update_status(simulation_name, asset_id, status)
        else:
            logger.error("SQLite version requires both simulation_name and asset_id")
            return False
    else:
        return postgres_ops.update_status(status, asset_id, simulation_name)


def bulk_update_status(asset_ids, status, simulation_name):
    """Bulk update status for multiple assets using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        if simulation_name and asset_ids:
            return sqlite_ops.bulk_update_status(asset_ids, status, simulation_name)
        else:
            logger.error("SQLite version requires both simulation_name and asset_ids")
            return False
    else:
        # Legacy PostgreSQL environments - fall back to individual updates
        logger.warning("Bulk update not optimized for PostgreSQL environment, using individual updates")
        success_count = 0
        for asset_id in asset_ids:
            if postgres_ops.update_status(status, asset_id, simulation_name):
                success_count += 1
        return success_count == len(asset_ids)


def update_simulation_name(RECOVERY_SIMULATION_NAME, CORRUPTED_SIMULATION_NAME, batch_id):
    """Update simulation name using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        return sqlite_ops.update_simulation_name(RECOVERY_SIMULATION_NAME, CORRUPTED_SIMULATION_NAME, batch_id)
    else:
        return postgres_ops.update_simulation_name(RECOVERY_SIMULATION_NAME, CORRUPTED_SIMULATION_NAME, batch_id)


def delete_table(table_name):
    """Delete table using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        return sqlite_ops.delete_table(table_name)
    else:
        return postgres_ops.delete_table(table_name)

        
def get_status_stats(simulation_name, batch_id=None):
    """Get status stats using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        stats = sqlite_ops.get_status_stats(simulation_name)
        finished_assets = stats.get('Finished', 0)
        failed_assets = stats.get('Failed', 0)
        return finished_assets, failed_assets
    else:
        return postgres_ops.get_status_stats(simulation_name, batch_id)


def get_weather(asset_id, simulation_name=None):
    """Get weather using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        # In distributed mode, we might need to copy master data first
        if simulation_name:
            # Only copy data once per process
            copy_flag = f"/tmp/.weather_data_copied_{simulation_name}_{os.getpid()}"
            if not os.path.exists(copy_flag):
                sqlite_ops.copy_master_to_local(simulation_name)
                # Create flag file to avoid repeated copying
                with open(copy_flag, 'w') as f:
                    f.write(str(os.getpid()))
        
        return sqlite_ops.get_weather(asset_id)
    else:
        return postgres_ops.get_weather(asset_id)

        
def get_bulk_assets(simulation_name, batch=None):
    """Get bulk assets using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        assets = sqlite_ops.get_bulk_assets(simulation_name)
        if batch is None:
            # Return just asset IDs for compatibility
            return [asset['asset_id'] for asset in assets]
        else:
            # Filter by batch and return tuples of (asset_id, asset_name)
            batch_assets = [asset for asset in assets if asset.get('batch') == batch]
            return [(asset['asset_id'], asset['asset_name']) for asset in batch_assets]
    else:
        return postgres_ops.get_bulk_assets(simulation_name, batch)


def get_bulk_batchids(simulation_name):
    """Get bulk batch IDs using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        return sqlite_ops.get_bulk_batchids(simulation_name)
    else:
        return postgres_ops.get_bulk_batchids(simulation_name)


def get_batch_total(simulation_name):
    """Get batch total using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        return sqlite_ops.get_batch_total(simulation_name)
    else:
        return postgres_ops.get_batch_total(simulation_name)



def get_asset_total(simulation_name, batch_id=None):
    """Get asset total using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        total = sqlite_ops.get_asset_total(simulation_name)
        if batch_id is not None:
            # SQLite version doesn't support batch_id parameter, need to filter manually
            assets = sqlite_ops.get_bulk_assets(simulation_name)
            filtered_assets = [asset for asset in assets if asset.get('batch') == batch_id]
            return len(filtered_assets)
        return total
    else:
        return postgres_ops.get_asset_total(simulation_name, batch_id)


def get_failed_assets(simulation_name, batch_id=None):
    """Get failed assets using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        failed_assets = sqlite_ops.get_failed_assets(simulation_name)
        # Convert to docker format (just asset IDs)
        return [asset['asset_id'] if isinstance(asset, dict) else asset for asset in failed_assets]
    else:
        return postgres_ops.get_failed_assets(simulation_name, batch_id)



def get_asset_stats(simulation_name=None):
    """Get asset stats using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        stats = sqlite_ops.get_asset_stats(simulation_name if simulation_name else '')
        # Convert SQLite dict format to docker list format
        if stats:
            filename = f"{simulation_name}_assets_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv" if simulation_name else f"all_assets_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            return [stats], filename  # Wrap in list for compatibility
        return [], None
    else:
        return postgres_ops.get_asset_stats(simulation_name)