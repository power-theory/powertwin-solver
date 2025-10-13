import psycopg
import os
from datetime import datetime
from modules.utils import initialize_logger

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Database',external_log_dir)


# Get the database name from environment or use default
DB_NAME = os.environ.get("PGDATABASE", "powertwin")
PASSWORD = os.environ.get("PGPASSWORD", "admin")
USER = os.environ.get("PGUSER", "postgres")
HOST = os.environ.get("PGHOST", "powertwin-solver-pg")
PORT = os.environ.get("PGPORT", "5432")
DB_NAME = DB_NAME


def get_db_connection():
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
                location VARCHAR(255),
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
        

def insert_asset(asset_id, location, floor_area, number_of_stories, complexity, asset_name, subtype, simulation_name):
    ensure_columns_exist()

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            INSERT INTO {DB_NAME} (asset_id, location, floor_area, number_of_stories, complexity, asset_name, subtype, simulation_name)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (asset_id) DO UPDATE SET
                location = EXCLUDED.location,
                floor_area = EXCLUDED.floor_area,
                number_of_stories = EXCLUDED.number_of_stories,
                complexity = EXCLUDED.complexity,
                asset_name = EXCLUDED.asset_name,
                subtype = EXCLUDED.subtype,
                simulation_name = EXCLUDED.simulation_name
        """, (asset_id, location, floor_area, number_of_stories, complexity, asset_name, subtype, simulation_name))
        conn.commit()
    except Exception as e:
        print(f"Error inserting asset ID {asset_id}: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()
        
def insert_bulk_assets(asset_data_list):

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
            values_parts.append("(%s, %s, %s, %s, %s, %s, %s, %s)")
            all_params.extend(asset)
        
        values_clause = ", ".join(values_parts)
        
        query = f"""
            INSERT INTO {DB_NAME} 
            (asset_id, location, floor_area, number_of_stories, complexity, asset_name, subtype, simulation_name)
            VALUES {values_clause}
            ON CONFLICT (asset_id) DO UPDATE SET
                location = EXCLUDED.location,
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

def update_time(asset_id, uorun_time, uoprocess_time, total_time):
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

def delete_table(DB_NAME):
    logger.debug('Within delete_table()')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(f'DROP TABLE IF EXISTS {DB_NAME}')
        conn.commit()
        print(f"Table '{DB_NAME}' deleted successfully.")
    except Exception as e:
        print(f"Error deleting table '{DB_NAME}': {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()
        
def get_status_stats(simulation_name, batch_id=None):
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
            return 0, 0, 0
            
    except Exception as e:
        logger.error(f"Database error getting batch stats: {str(e)}")
        return 0, 0, 0
    finally:
        cur.close()
        conn.close()  

def get_weather(asset_id=None, simulation_name=None):
    
    if asset_id is None and simulation_name is None:
        raise ValueError("Either asset_id or simulation_name must be provided to retrieve the location.")

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if asset_id is None:
            cur.execute(f'SELECT location FROM {DB_NAME} WHERE simulation_name = %s LIMIT 1', (simulation_name,))
        else:
            cur.execute(f'SELECT location FROM {DB_NAME} WHERE asset_id = %s', (asset_id,))
        location = cur.fetchone()
        return location[0]
    except Exception as e:
        print(f"Error getting location {asset_id}: {e}")
    finally:
        cur.close()
        conn.close()
        
def get_bulk_assets(simulation_name, batch=None):
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
    logger.debug('Within get_bulk_batchids()') 
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(f'SELECT DISTINCT batch FROM {DB_NAME} WHERE simulation_name = %s ORDER BY batch', (simulation_name,))
        rows = cur.fetchall()
        
        # Return a list of asset IDs
        return [row[0] for row in rows]
    except Exception as e:
        logger.error(f'Error getting assets from {simulation_name}: {e}')
        return []
    finally:
        cur.close()
        conn.close()

def get_batch_total(simulation_name):

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(f'SELECT COUNT(DISTINCT batch) FROM {DB_NAME} WHERE simulation_name = %s', 
                   (simulation_name,))
        batch_count = cur.fetchone()[0]

        return batch_count
    
    except Exception as e:
        print(f'Error getting assets from {simulation_name}: {e}')
        return 0
    finally:
        cur.close()
        conn.close()

def get_asset_total(simulation_name, batch_id=None):

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
        print(f'Error getting assets from {simulation_name}: {e}')
        return 0
    finally:
        cur.close()
        conn.close()

def get_failed_assets(simulation_name, batch_id=None):
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
                location,
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
            'location': 'VARCHAR(255)',
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