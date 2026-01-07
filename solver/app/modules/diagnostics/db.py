"""
Database operations for Powertwin Solver.
Automatically routes to SQLite (HPC) or PostgreSQL (Docker) based on environment.
"""
import os
from datetime import datetime
from modules.utils import initialize_logger
from modules.utils.hpc_environment import is_hpc_environment, should_use_distributed_database, log_environment_summary
from modules.database.database_environment import get_database_config, log_database_environment

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Database', external_log_dir)

# Legacy psycopg imports - kept for Docker environments
try:
    import psycopg
except ImportError:
    logger.warning("psycopg not available - PostgreSQL functions will not work")
    psycopg = None

# Import SQLite operations for HPC environments
import modules.database.sqlite_operations as sqlite_ops
from modules.database.distributed_sqlite import get_distributed_manager

# Environment detection using centralized method
IS_HPC_ENVIRONMENT = is_hpc_environment()
DB_CONFIG = get_database_config()

# Log environment summary once at module initialization
log_environment_summary()
log_database_environment()

# PostgreSQL configuration for Docker environments
DB_NAME = os.environ.get("PGDATABASE", "powertwin")
PASSWORD = os.environ.get("PGPASSWORD", "admin")
USER = os.environ.get("PGUSER", "postgres")
HOST = os.environ.get("PGHOST", "pgbouncer")
PORT = os.environ.get("PGPORT", "5432")


def get_db_connection():
    
    #logger.info(f"Attempting connection with HOST={HOST}, PORT={PORT}, USER={USER}, DB={DB_NAME}")
    
    if psycopg is None:
        raise ImportError("psycopg is not available - cannot connect to PostgreSQL")
    
    try:
        conn = psycopg.connect(
            host=HOST,
            port=int(PORT),
            user=USER,
            password=PASSWORD,
            dbname=DB_NAME,
            # PgBouncer-specific optimizations
            application_name=f"powertwin-{os.environ.get('SLURM_PROCID', 'unknown')}",
            # Disable prepared statements for PgBouncer transaction mode
            prepare_threshold=0
        )
        return conn
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        raise

def create_table():
    """Create table using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        return sqlite_ops.create_table()
    else:
        return create_table_legacy()


def create_table_legacy():
    """Legacy table creation method."""
    logger.debug('Within create_table()')
    logger.debug(f"DB Connection parameters: PGHOST={HOST}, PGUSER={USER}, PGDATABASE={DB_NAME}")
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {DB_NAME} (
                asset_id SERIAL PRIMARY KEY,
                batch INTEGER,
                order_rank INTEGER,
                simulation_name VARCHAR(255),
                state VARCHAR(255),
                weather_file VARCHAR(255),
                floor_area NUMERIC,
                number_of_stories INTEGER,
                complexity INTEGER,
                uorun_time NUMERIC,
                uoprocess_time NUMERIC,
                asset_name VARCHAR(255),
                subtype VARCHAR(255),
                status VARCHAR(255),
                total_time NUMERIC
            )
        """)
        conn.commit()
    except Exception as e:
        print(f"Error creating table: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()
        

def insert_asset(asset_id, state, weather_file, floor_area, number_of_stories, complexity, asset_name, subtype, simulation_name):
    """Insert asset using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        return sqlite_ops.insert_asset(asset_id, state, weather_file, floor_area, number_of_stories, complexity, asset_name, subtype, simulation_name)
    else:
        return insert_asset_legacy(asset_id, state, weather_file, floor_area, number_of_stories, complexity, asset_name, subtype, simulation_name)


def insert_asset_legacy(asset_id, state, weather_file, floor_area, number_of_stories, complexity, asset_name, subtype, simulation_name):
    """Legacy insert asset method."""
    ensure_columns_exist()

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            INSERT INTO {DB_NAME} (asset_id, state, weather_file, floor_area, number_of_stories, complexity, asset_name, subtype, simulation_name)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (asset_id) DO UPDATE SET
                state = EXCLUDED.state,
                weather_file = EXCLUDED.weather_file,
                floor_area = EXCLUDED.floor_area,
                number_of_stories = EXCLUDED.number_of_stories,
                complexity = EXCLUDED.complexity,
                asset_name = EXCLUDED.asset_name,
                subtype = EXCLUDED.subtype,
                simulation_name = EXCLUDED.simulation_name
        """, (asset_id, state, weather_file, floor_area, number_of_stories, complexity, asset_name, subtype, simulation_name))
        conn.commit()
    except Exception as e:
        print(f"Error inserting asset ID {asset_id}: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()
        
def insert_bulk_assets(asset_data_list):
    """Insert bulk assets using appropriate database for the environment."""
    logger.debug(f"insert_bulk_assets called: HPC={IS_HPC_ENVIRONMENT}, asset_count={len(asset_data_list) if asset_data_list else 0}")
    
    if IS_HPC_ENVIRONMENT:
        # Convert legacy format to SQLite format if needed
        if not asset_data_list:
            logger.debug("No assets to insert")
            return True
            
        # Debug: log the first asset to understand the format
        logger.debug(f"First asset format: {type(asset_data_list[0])}, content: {asset_data_list[0] if asset_data_list else 'None'}")
        
        if isinstance(asset_data_list[0], (list, tuple)):
            # Convert from legacy tuple format to dictionary format
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
        return insert_bulk_assets_legacy(asset_data_list)


def insert_bulk_assets_legacy(asset_data_list):
    """Legacy bulk insert method."""
    if not asset_data_list:
        logger.debug("No assets to insert")
        return
        
    logger.debug(f'Bulk inserting {len(asset_data_list)} assets')
    
    ensure_columns_exist()

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        
        values_parts = []
        all_params = []
        
        for asset in asset_data_list:
            values_parts.append("(%s, %s, %s, %s, %s, %s, %s, %s, %s)")
            all_params.extend(asset)
        
        values_clause = ", ".join(values_parts)
        
        query = f"""
            INSERT INTO {DB_NAME} 
            (asset_id, state, weather_file, floor_area, number_of_stories, complexity, asset_name, subtype, simulation_name)
            VALUES {values_clause}
            ON CONFLICT (asset_id) DO UPDATE SET
                state = EXCLUDED.state,
                weather_file = EXCLUDED.weather_file,
                floor_area = EXCLUDED.floor_area,
                number_of_stories = EXCLUDED.number_of_stories,
                complexity = EXCLUDED.complexity,
                asset_name = EXCLUDED.asset_name,
                subtype = EXCLUDED.subtype,
                simulation_name = EXCLUDED.simulation_name
            WHERE {DB_NAME}.status IS NULL OR {DB_NAME}.status != 'Finished'
        """
        
        cur.execute(query, all_params)
        conn.commit()
        logger.debug(f"Successfully inserted/updated {len(asset_data_list)} assets")
        
    except Exception as e:
        logger.error(f"Error bulk inserting assets: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()
        
def distribute_assets_to_batches(num_cores, simulation_name):
    """Distribute assets to batches using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        return sqlite_ops.distribute_assets_to_batches(simulation_name, num_cores)
    else:
        return distribute_assets_to_batches_legacy(num_cores, simulation_name)


def distribute_assets_to_batches_legacy(num_cores, simulation_name):
    """Legacy asset distribution method."""
    logger.debug('Within distribute_assets_to_batches()')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # SQL query to assign batches AND store the order in order_rank column
        cur.execute(f"""
        WITH ordered_assets AS (
            SELECT 
                asset_id,
                ROW_NUMBER() OVER (
                    PARTITION BY simulation_name
                    ORDER BY complexity::INTEGER DESC, number_of_stories::INTEGER DESC, floor_area::NUMERIC DESC
                ) - 1 as row_num
            FROM {DB_NAME}
            WHERE simulation_name = %s
        )
        UPDATE {DB_NAME} AS t
        SET 
            batch = (oa.row_num %% %s),
            order_rank = oa.row_num
        FROM ordered_assets AS oa
        WHERE t.asset_id = oa.asset_id;
        """, (simulation_name, num_cores))
        affected_rows = cur.rowcount
        
        conn.commit()
        logger.info(f"Assigned {affected_rows} assets to {num_cores} batches and stored their order")
        return affected_rows
    except Exception as e:
        logger.error(f"Error distributing assets: {e}")
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()

def update_batch(asset_id, batch):
    """Update batch using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        return sqlite_ops.update_batch(asset_id, batch)
    else:
        return update_batch_legacy(asset_id, batch)


def update_batch_legacy(asset_id, batch):
    """Legacy update batch method."""
    logger.debug('Within update_batch()')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(f'UPDATE {DB_NAME} SET batch = %s WHERE asset_id = %s', (batch, asset_id))
        conn.commit()
    except Exception as e:
        print(f"Error updating batch for asset ID {asset_id}: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

def update_time(asset_id, uorun_time, uoprocess_time, total_time, simulation_name=None):
    """Update timing using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        # SQLite version expects simulation_name, try to get it if not provided
        if simulation_name is None:
            logger.warning(f"simulation_name not provided for update_time, asset_id: {asset_id}")
            # Try to get simulation name from existing record
            conn = sqlite_ops.get_sqlite_connection()
            table_name = os.environ.get("PGDATABASE", "powertwin")
            cursor = conn.execute(f"SELECT simulation_name FROM {table_name} WHERE asset_id = ?", (asset_id,))
            row = cursor.fetchone()
            simulation_name = row['simulation_name'] if row else 'unknown'
        return sqlite_ops.update_time(simulation_name, asset_id, uorun_time, uoprocess_time, total_time)
    else:
        return update_time_legacy(asset_id, uorun_time, uoprocess_time, total_time)


def update_time_legacy(asset_id, uorun_time, uoprocess_time, total_time):
    """Legacy update time method."""
    logger.debug('Within update_time()')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            UPDATE {DB_NAME} SET uorun_time = %s, uoprocess_time = %s, total_time = %s WHERE asset_id = %s
        """, (uorun_time, uoprocess_time, total_time, asset_id))
        conn.commit()
    except Exception as e:
        print(f"Error updating time for asset ID {asset_id}: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()
    
def update_status(status, asset_id=None, simulation_name=None):
    """Update status using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        if simulation_name and asset_id:
            return sqlite_ops.update_status(simulation_name, asset_id, status)
        else:
            logger.error("SQLite version requires both simulation_name and asset_id")
            return False
    else:
        return update_status_legacy(status, asset_id, simulation_name)

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
            if update_status_legacy(status, asset_id, simulation_name):
                success_count += 1
        return success_count == len(asset_ids)

def update_status_legacy(status, asset_id=None, simulation_name=None):
    """Legacy update status method."""
    # TODO: concerning that asset_id can be updated without simulation_name althought all Failed assets will be transferred to new simulation, this should be handled
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if simulation_name is not None:
            cur.execute(f'UPDATE {DB_NAME} SET status = %s WHERE simulation_name = %s', (status, simulation_name))
        else:
            cur.execute(f'UPDATE {DB_NAME} SET status = %s WHERE asset_id = %s', (status, asset_id))
        conn.commit()
    except Exception as e:
        print(f"Error updating status for asset ID {asset_id}: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

def update_simulation_name(RECOVERY_SIMULATION_NAME, CORRUPTED_SIMULATION_NAME, batch_id):
    """Update simulation name using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        return sqlite_ops.update_simulation_name(CORRUPTED_SIMULATION_NAME, RECOVERY_SIMULATION_NAME)
    else:
        return update_simulation_name_legacy(RECOVERY_SIMULATION_NAME, CORRUPTED_SIMULATION_NAME, batch_id)


def update_simulation_name_legacy(RECOVERY_SIMULATION_NAME, CORRUPTED_SIMULATION_NAME, batch_id):
    """Legacy update simulation name method."""
    logger.debug('Within update_simulation_name()')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            UPDATE {DB_NAME} SET simulation_name = %s 
            WHERE batch = %s AND simulation_name = %s AND status != 'Finished'
        """, (RECOVERY_SIMULATION_NAME, batch_id, CORRUPTED_SIMULATION_NAME))
        conn.commit()
    except Exception as e:
        print(f"Error updating simulation name for batch_id {batch_id}: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

def delete_table(table_name):
    """Delete table using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        return sqlite_ops.delete_table(table_name)
    else:
        return delete_table_legacy(table_name)


def delete_table_legacy(table_name):
    """Legacy delete table method."""
    logger.debug('Within delete_table()')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(f'DROP TABLE IF EXISTS {table_name}')
        conn.commit()
        print(f"Table '{table_name}' deleted successfully.")
    except Exception as e:
        print(f"Error deleting table '{table_name}': {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()
        
def get_status_stats(simulation_name, batch_id=None):
    """Get status stats using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        stats = sqlite_ops.get_status_stats(simulation_name)
        finished_assets = stats.get('Finished', 0) + stats.get('Failed', 0)
        failed_assets = stats.get('Failed', 0)
        return finished_assets, failed_assets
    else:
        return get_status_stats_legacy(simulation_name, batch_id)


def get_status_stats_legacy(simulation_name, batch_id=None):
    """Legacy get status stats method."""
    logger.debug('Within get_batch_stats()')
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if batch_id is not None:
            # Get stats for specific batch
            cur.execute(f"""
                SELECT 
                    SUM(CASE WHEN status IN ('Finished', 'Failed') THEN 1 ELSE 0 END) as finished_assets,
                    SUM(CASE WHEN status = 'Failed' THEN 1 ELSE 0 END) as failed_assets
                FROM {DB_NAME}
                WHERE simulation_name = %s AND batch = %s
            """, (simulation_name, batch_id))
        else:
            # Get stats for entire simulation
            cur.execute(f"""
                SELECT 
                    SUM(CASE WHEN status IN ('Finished', 'Failed') THEN 1 ELSE 0 END) as finished_assets,
                    SUM(CASE WHEN status = 'Failed' THEN 1 ELSE 0 END) as failed_assets
                FROM {DB_NAME}
                WHERE simulation_name = %s
            """, (simulation_name,))
            
        result = cur.fetchone()
        if result:
            finished_assets = result[0] or 0
            failed_assets = result[1] or 0  
            return finished_assets, failed_assets
        else:
            logger.error(f"No data found for simulation {simulation_name}")
            return 0, 0
            
    except Exception as e:
        logger.error(f"Database error getting batch stats: {str(e)}")
        return 0, 0, 0
    finally:
        cur.close()
        conn.close()  

def get_weather(asset_id, simulation_name=None):
    """Get weather using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        # In distributed mode, we might need to copy master data first
        if should_use_distributed_database() and simulation_name:
            # Only copy data once per process
            copy_flag = f"/tmp/.weather_data_copied_{simulation_name}_{os.getpid()}"
            if not os.path.exists(copy_flag):
                sqlite_ops.copy_master_to_local(simulation_name)
                # Create flag file to avoid repeated copying
                with open(copy_flag, 'w') as f:
                    f.write(str(os.getpid()))
        
        return sqlite_ops.get_weather(asset_id)
    else:
        return get_weather_legacy(asset_id)


def get_weather_legacy(asset_id):
    """
    Retrieve weather information for a given asset.
    
    Args:
        asset_id: The asset ID to retrieve weather data for
        
    Returns:
        tuple: (State, WeatherFile.epw) - State code and weather file name with .epw extension
    """
    if asset_id is None:
        raise ValueError("asset_id must be provided to retrieve weather information.")

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(f'SELECT state, weather_file FROM {DB_NAME} WHERE asset_id = %s', (asset_id,))
        result = cur.fetchone()
        
        if result is None:
            raise ValueError(f"No weather data found for asset_id {asset_id}")
        
        state, weather_file = result
        return state, weather_file
    except Exception as e:
        logger.error(f"Error getting weather data for asset ID {asset_id}: {e}")
        raise
    finally:
        cur.close()
        conn.close()
        
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
        return get_bulk_assets_legacy(simulation_name, batch)


def get_bulk_assets_legacy(simulation_name, batch=None):
    """Legacy get bulk assets method."""
    logger.debug('Within get_bulk_assets()') 
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if batch is None:
            cur.execute(f'SELECT asset_id FROM {DB_NAME} WHERE simulation_name = %s', (simulation_name,))
            rows = cur.fetchall()
            
            # Return a list of asset IDs
            return [row[0] for row in rows]
        else:
            cur.execute(f"""
                SELECT asset_id, asset_name FROM {DB_NAME} 
                WHERE simulation_name = %s AND batch = %s
                ORDER BY order_rank
            """, (simulation_name, batch))
            # Returns list of (asset_id, asset_name) tuples
            return cur.fetchall()  
    except Exception as e:
        logger.error(f'Error getting assets from {simulation_name}: {e}')
        # Return empty list on error to avoid None checks
        return [] if batch is None else []
    finally:
        cur.close()
        conn.close()

def get_bulk_batchids(simulation_name):
    """Get bulk batch IDs using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        return sqlite_ops.get_bulk_batchids(simulation_name)
    else:
        return get_bulk_batchids_legacy(simulation_name)


def get_bulk_batchids_legacy(simulation_name):
    """Legacy get bulk batch IDs method."""
    logger.debug('Within get_bulk_batchids()') 
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(f'SELECT DISTINCT batch FROM {DB_NAME} WHERE simulation_name = %s ORDER BY batch', (simulation_name,))
        rows = cur.fetchall()
        
        # Return a list of batch IDs
        return [row[0] for row in rows]
    except Exception as e:
        logger.error(f'Error getting batch IDs from {simulation_name}: {e}')
        return []
    finally:
        cur.close()
        conn.close()

def get_batch_total(simulation_name):
    """Get batch total using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        return sqlite_ops.get_batch_total(simulation_name)
    else:
        return get_batch_total_legacy(simulation_name)


def get_batch_total_legacy(simulation_name):
    """Legacy get batch total method."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(f'SELECT COUNT(DISTINCT batch) FROM {DB_NAME} WHERE simulation_name = %s', 
                   (simulation_name,))
        batch_count = cur.fetchone()[0]

        return batch_count
    
    except Exception as e:
        print(f'Error getting batch total from {simulation_name}: {e}')
        return 0
    finally:
        cur.close()
        conn.close()

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
        return get_asset_total_legacy(simulation_name, batch_id)


def get_asset_total_legacy(simulation_name, batch_id=None):
    """Legacy get asset total method."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if batch_id is None:
            cur.execute(f'SELECT COUNT(*) FROM {DB_NAME} WHERE simulation_name = %s', (simulation_name,))
        else:
            cur.execute(f'SELECT COUNT(*) FROM {DB_NAME} WHERE simulation_name = %s AND batch = %s', 
                       (simulation_name, batch_id))
        
        asset_count = cur.fetchone()[0]
        return asset_count
    
    except Exception as e:
        print(f'Error getting asset count from {simulation_name}: {e}')
        return 0
    finally:
        cur.close()
        conn.close()

def get_failed_assets(simulation_name, batch_id=None):
    """Get failed assets using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        failed_assets = sqlite_ops.get_failed_assets(simulation_name)
        # Convert to legacy format (just asset IDs)
        return [asset['asset_id'] if isinstance(asset, dict) else asset for asset in failed_assets]
    else:
        return get_failed_assets_legacy(simulation_name, batch_id)


def get_failed_assets_legacy(simulation_name, batch_id=None):
    """Legacy get failed assets method."""
    logger.debug(f'Getting failed assets for simulation: {simulation_name}, batch: {batch_id}')
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if batch_id is None:
            # Get all failed assets for the simulation
            cur.execute(f"""
                SELECT asset_id 
                FROM {DB_NAME} 
                WHERE simulation_name = %s 
                AND status ILIKE 'Failed'
                ORDER BY batch, order_rank
            """, (simulation_name,))
        else:
            # Get failed assets for specific batch
            cur.execute(f"""
                SELECT asset_id 
                FROM {DB_NAME} 
                WHERE simulation_name = %s 
                AND batch = %s 
                AND status ILIKE 'Failed'
                ORDER BY order_rank
            """, (simulation_name, batch_id))
        
        failed_assets = cur.fetchall()
        
        # Convert list of tuples to list of asset IDs
        asset_ids = [asset[0] for asset in failed_assets]
        
        logger.info(f"Found {len(asset_ids)} failed assets")
        return asset_ids
    
    except Exception as e:
        logger.error(f'Error getting failed assets from {simulation_name}: {e}')
        return []
    finally:
        cur.close()
        conn.close()


def get_failed_assets_legacy(simulation_name, batch_id=None):
    """Legacy get failed assets method."""
    logger.debug(f'Getting failed assets for simulation: {simulation_name}, batch: {batch_id}')
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if batch_id is None:
            # Get all failed assets for the simulation
            cur.execute(f"""
                SELECT asset_id 
                FROM {DB_NAME} 
                WHERE simulation_name = %s 
                AND status ILIKE 'Failed'
                ORDER BY batch, order_rank
            """, (simulation_name,))
        else:
            # Get failed assets for specific batch
            cur.execute(f"""
                SELECT asset_id 
                FROM {DB_NAME} 
                WHERE simulation_name = %s 
                AND batch = %s 
                AND status ILIKE 'Failed'
                ORDER BY order_rank
            """, (simulation_name, batch_id))
        
        failed_assets = cur.fetchall()
        
        # Convert list of tuples to list of asset IDs
        asset_ids = [asset[0] for asset in failed_assets]
        
        logger.info(f"Found {len(asset_ids)} failed assets")
        return asset_ids
    
    except Exception as e:
        logger.error(f'Error getting failed assets from {simulation_name}: {e}')
        return []
    finally:
        cur.close()
        conn.close()

def get_asset_stats(simulation_name=None):
    """Get asset stats using appropriate database for the environment."""
    if IS_HPC_ENVIRONMENT:
        stats = sqlite_ops.get_asset_stats(simulation_name if simulation_name else '')
        # Convert SQLite dict format to legacy list format
        if stats:
            filename = f"{simulation_name}_assets_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv" if simulation_name else f"all_assets_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            return [stats], filename  # Wrap in list for compatibility
        return [], None
    else:
        return get_asset_stats_legacy(simulation_name)


def get_asset_stats_legacy(simulation_name=None):
    """Legacy get asset stats method."""
    logger.debug(f'Getting asset stats for simulation: {simulation_name if simulation_name else "all"}')
    conn = None
    cur = None
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        query = f"""
            SELECT 
                asset_id::integer,
                batch::integer,
                order_rank::integer,
                simulation_name,
                state,
                weather_file,
                floor_area::numeric,
                number_of_stories::integer,
                complexity::integer,
                COALESCE(uorun_time::numeric, 0) as uorun_time,
                COALESCE(uoprocess_time::numeric, 0) as uoprocess_time,
                asset_name,
                subtype,
                status,
                COALESCE(total_time::numeric, 0) as total_time
            FROM {DB_NAME}
        """
        
        params = []
        if simulation_name:
            query += " WHERE simulation_name = %s"
            params.append(simulation_name)
            filename = f"{simulation_name}_assets_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        else:
            filename = f"all_assets_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

        query += " ORDER BY simulation_name, batch, order_rank"
        
        cur.execute(query, params)
        assets = cur.fetchall()
        
        if not assets:
            logger.warning(f"No assets found for simulation: {simulation_name if simulation_name else 'all'}")
            return [], None
            
        # Get column names from cursor description
        columns = [desc[0] for desc in cur.description]
        
        # Convert to list of dictionaries
        assets_list = [dict(zip(columns, asset)) for asset in assets]
        
        logger.info(f"Successfully retrieved stats for {len(assets_list)} assets")
        return assets_list, filename
        
    except psycopg.Error as e:
        logger.error(f"Database error retrieving assets: {str(e)}")
        return [], None
    except Exception as e:
        logger.error(f"Error retrieving asset stats: {str(e)}")
        return [], None
    finally:
        cur.close()
        conn.close()
            

def ensure_columns_exist():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # First check which columns exist
        cur.execute(f"""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = '{DB_NAME}'
        """)
        existing_columns = {row[0] for row in cur.fetchall()}
        
        # Define expected columns with their types
        expected_columns = {
            'asset_id': 'SERIAL PRIMARY KEY',
            'batch': 'INTEGER',
            'order_rank': 'INTEGER',
            'simulation_name': 'VARCHAR(255)',
            'state': 'VARCHAR(255)',
            'weather_file': 'VARCHAR(255)',
            'floor_area': 'NUMERIC',
            'number_of_stories': 'INTEGER',
            'complexity': 'INTEGER',
            'uorun_time': 'NUMERIC',
            'uoprocess_time': 'NUMERIC',
            'asset_name': 'VARCHAR(255)',
            'subtype': 'VARCHAR(255)',
            'status': 'VARCHAR(255)',
            'total_time': 'NUMERIC'
        }
        
        # Add missing columns
        for column, data_type in expected_columns.items():
            if column.lower() not in {col.lower() for col in existing_columns}:
                # Skip asset_id if it doesn't exist as it should be handled during table creation
                if column != 'asset_id':
                    logger.info(f'Adding missing column: {column}')
                    cur.execute(f"""
                        ALTER TABLE {DB_NAME} 
                        ADD COLUMN {column} {data_type}
                    """)
        
        conn.commit()
        
    except Exception as e:
        logger.error(f"Error checking/adding columns: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()


# Distributed Database Functions

def consolidate_distributed_databases(simulation_name=None):
    """Consolidate distributed databases (HPC mode only)."""
    if should_use_distributed_database():
        return sqlite_ops.consolidate_distributed_databases(simulation_name)
    else:
        logger.info("Distributed SQLite not enabled, skipping consolidation")
        return True

def setup_distributed_database(simulation_name):
    """Setup distributed database for HPC environment."""
    if should_use_distributed_database():
        return sqlite_ops.setup_distributed_database(simulation_name)
    return False

def copy_master_to_local(simulation_name):
    """Copy master database data to local database for this process."""
    if should_use_distributed_database():
        return sqlite_ops.copy_master_to_local(simulation_name)
    return False

def get_available_distributed_databases():
    """Get list of available distributed databases."""
    if should_use_distributed_database():
        return sqlite_ops.get_available_distributed_databases()
    return []