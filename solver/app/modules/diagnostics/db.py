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
# Q/A X-3: this used to be a module-level constant frozen at import, so per-process env changes
# (request overrides, tests, late scheduler detection) never re-routed the backend. Evaluate at
# call time via the helper instead.
def _is_hpc():
    return is_hpc_environment()


def create_table():
    """Create table using appropriate database for the environment."""
    if _is_hpc():
        return sqlite_ops.create_table()
    else:
        return postgres_ops.create_table()
      

def insert_asset(asset_id, state, weather_file, floor_area, number_of_stories, complexity, asset_name, subtype, simulation_name):
    """Insert asset using appropriate database for the environment."""
    if _is_hpc():
        return sqlite_ops.insert_asset(asset_id, state, weather_file, floor_area, number_of_stories, complexity, asset_name, subtype, simulation_name)
    else:
        return postgres_ops.insert_asset(asset_id, state, weather_file, floor_area, number_of_stories, complexity, asset_name, subtype, simulation_name)

  
def insert_bulk_assets(asset_data_list):
    """Insert bulk assets using appropriate database for the environment."""
    logger.debug(f"insert_bulk_assets called: HPC={_is_hpc()}, asset_count={len(asset_data_list) if asset_data_list else 0}")
    
    if _is_hpc():
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
    if _is_hpc():
        return sqlite_ops.distribute_assets_to_batches(simulation_name, num_cores)
    else:
        return postgres_ops.distribute_assets_to_batches(num_cores, simulation_name)


def update_batch(asset_id, batch, simulation_name):
    """Update batch using appropriate database for the environment."""
    if _is_hpc():
        return sqlite_ops.update_batch(asset_id, batch, simulation_name)
    else:
        return postgres_ops.update_batch(asset_id, batch, simulation_name)


def update_time(asset_id, uorun_time, uoprocess_time, total_time, simulation_name):
    """Update timing using appropriate database for the environment.

    simulation_name is REQUIRED. This used to look it up by asset_id alone when omitted, which
    with a per-run key picks an ARBITRARY concurrent run (and fell back to the literal string
    'unknown', writing timings onto a row no query can find). Both callers pass it.
    """
    if simulation_name is None:
        raise ValueError(
            f"update_time for asset {asset_id} needs simulation_name: asset_id alone matches "
            f"every concurrent run of that asset"
        )
    if _is_hpc():
        return sqlite_ops.update_time(simulation_name, asset_id, uorun_time, uoprocess_time, total_time)
    else:
        return postgres_ops.update_time(asset_id, uorun_time, uoprocess_time, total_time, simulation_name)

    
def update_status(status, asset_id=None, simulation_name=None, failure_reason=None):
    """Update status using appropriate database for the environment.

    Naming an asset_id without a simulation_name is rejected: it would match every concurrent
    run of that asset. (simulation_name alone is fine -- that means "the whole run".)
    """
    if asset_id is not None and simulation_name is None:
        raise ValueError(
            f"update_status for asset {asset_id} needs simulation_name: asset_id alone matches "
            f"every concurrent run of that asset"
        )
    if _is_hpc():
        if simulation_name and asset_id:
            return sqlite_ops.update_status(simulation_name, asset_id, status, failure_reason=failure_reason)
        else:
            logger.error("SQLite version requires both simulation_name and asset_id")
            return False
    else:
        return postgres_ops.update_status(status, asset_id, simulation_name, failure_reason)


def bulk_update_status(asset_ids, status, simulation_name):
    """Bulk update status for multiple assets using appropriate database for the environment."""
    if _is_hpc():
        if simulation_name and asset_ids:
            return sqlite_ops.bulk_update_status(asset_ids, status, simulation_name)
        else:
            logger.error("SQLite version requires both simulation_name and asset_ids")
            return False
    else:
        # Use optimized PostgreSQL bulk update function
        if simulation_name and asset_ids:
            return postgres_ops.bulk_update_status(asset_ids, status, simulation_name)
        else:
            logger.error("PostgreSQL bulk update requires both simulation_name and asset_ids")
            return False


def update_simulation_name(RECOVERY_SIMULATION_NAME, CORRUPTED_SIMULATION_NAME, batch_id):
    """Update simulation name using appropriate database for the environment."""
    if _is_hpc():
        return sqlite_ops.update_simulation_name(RECOVERY_SIMULATION_NAME, CORRUPTED_SIMULATION_NAME, batch_id)
    else:
        return postgres_ops.update_simulation_name(RECOVERY_SIMULATION_NAME, CORRUPTED_SIMULATION_NAME, batch_id)


def delete_table(table_name):
    """Delete table using appropriate database for the environment."""
    if _is_hpc():
        return sqlite_ops.delete_table(table_name)
    else:
        return postgres_ops.delete_table(table_name)

        
def get_status_stats(simulation_name, batch_id=None):
    """Get status stats using appropriate database for the environment."""
    if _is_hpc():
        stats = sqlite_ops.get_status_stats(simulation_name, batch_id)
        failed_assets = stats.get('Failed', 0)
        # Postgres counts Finished+Failed as terminal ("done"); mirroring that here is what lets
        # HPC batch progress reach 100% when a batch contains failures (Q/A D-01). batch_id is
        # forwarded so per-batch progress stops reporting whole-simulation counts (Q/A D-02).
        finished_assets = stats.get('Finished', 0) + failed_assets
        return finished_assets, failed_assets
    else:
        return postgres_ops.get_status_stats(simulation_name, batch_id)


def get_distinct_weather_files(simulation_name):
    """Get distinct weather file names for a simulation."""
    if _is_hpc():
        return sqlite_ops.get_distinct_weather_files(simulation_name)
    else:
        return postgres_ops.get_distinct_weather_files(simulation_name)


def get_weather(asset_id, simulation_name):
    """Get weather using appropriate database for the environment. simulation_name is REQUIRED:
    (asset_id, simulation_name) is the row key.

    Note: this used to guard a `sqlite_ops.copy_master_to_local(simulation_name)` call for a
    "distributed mode" that was never implemented -- the function does not exist. It stayed
    invisible only because every caller passed asset_id alone, so `if simulation_name:` was
    always false; scoping weather lookups to the run made it reachable and it raised
    AttributeError on the first HPC asset. Each node already reads its own node database.
    """
    if _is_hpc():
        return sqlite_ops.get_weather(asset_id, simulation_name)
    else:
        return postgres_ops.get_weather(asset_id, simulation_name)

        
def get_bulk_assets(simulation_name, batch=None):
    """Get bulk assets using appropriate database for the environment."""
    if _is_hpc():
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
    if _is_hpc():
        return sqlite_ops.get_bulk_batchids(simulation_name)
    else:
        return postgres_ops.get_bulk_batchids(simulation_name)


def get_batch_total(simulation_name):
    """Get batch total using appropriate database for the environment."""
    if _is_hpc():
        return sqlite_ops.get_batch_total(simulation_name)
    else:
        return postgres_ops.get_batch_total(simulation_name)



def get_asset_total(simulation_name, batch_id=None):
    """Get asset total using appropriate database for the environment."""
    if _is_hpc():
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
    if _is_hpc():
        failed_assets = sqlite_ops.get_failed_assets(simulation_name)
        # Convert to docker format (just asset IDs)
        return [asset['asset_id'] if isinstance(asset, dict) else asset for asset in failed_assets]
    else:
        return postgres_ops.get_failed_assets(simulation_name, batch_id)



def get_asset_stats(simulation_name=None):
    """Get asset stats using appropriate database for the environment."""
    if _is_hpc():
        # sqlite now returns per-asset rows in the postgres shape (Q/A R-020); None means all
        # simulations on both backends (Q/A D-15: '' matched nothing yet stayed truthy, so the
        # 404 guard never fired and the export KeyError'd instead)
        rows = sqlite_ops.get_asset_stats(simulation_name or None)
        if rows:
            filename = f"{simulation_name}_assets_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv" if simulation_name else f"all_assets_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            return rows, filename
        return [], None
    else:
        return postgres_ops.get_asset_stats(simulation_name)