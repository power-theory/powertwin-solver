import psycopg

from modules.utils import initialize_logger
from flask import render_template

logger = initialize_logger('Database')

# Database connection configuration
username = "postgres"
password = "admin"


def get_db_connection():
    conn = psycopg.connect(
        dbname='powertwin',
        user=username,
        password=password,
        host='powertwin-solver-pg',  # This must match the hostname of the container
        port='5432'
    )
    return conn

def create_table():
    logger.debug('Within create_table()')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS powertwin_solver (
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
        

def insert_asset(asset_id, location, floor_area, number_of_stories, complexity, asset_name, simulation_name):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO powertwin_solver (asset_id, location, floor_area, number_of_stories, complexity, asset_name, simulation_name)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (asset_id) DO UPDATE SET
                location = EXCLUDED.location,
                floor_area = EXCLUDED.floor_area,
                number_of_stories = EXCLUDED.number_of_stories,
                complexity = EXCLUDED.complexity,
                asset_name = EXCLUDED.asset_name,
                simulation_name = EXCLUDED.simulation_name
        """, (asset_id, location, floor_area, number_of_stories, complexity, asset_name, simulation_name))
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
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        values_parts = []
        all_params = []
        
        for asset in asset_data_list:
            values_parts.append("(%s, %s, %s, %s, %s, %s, %s)")
            all_params.extend(asset)
        
        values_clause = ", ".join(values_parts)
        
        query = f"""
            INSERT INTO powertwin_solver 
            (asset_id, location, floor_area, number_of_stories, complexity, asset_name, simulation_name)
            VALUES {values_clause}
            ON CONFLICT (asset_id) DO UPDATE SET
                location = EXCLUDED.location,
                floor_area = EXCLUDED.floor_area,
                number_of_stories = EXCLUDED.number_of_stories,
                complexity = EXCLUDED.complexity,
                asset_name = EXCLUDED.asset_name,
                simulation_name = EXCLUDED.simulation_name
            WHERE powertwin_solver.status IS NULL OR powertwin_solver.status != 'Finished'
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
        cur.execute("""
        WITH ordered_assets AS (
            SELECT 
                asset_id,
                ROW_NUMBER() OVER (
                    PARTITION BY simulation_name
                    ORDER BY complexity::INTEGER DESC, number_of_stories::INTEGER DESC, floor_area::NUMERIC DESC
                ) - 1 as row_num
            FROM powertwin_solver
            WHERE simulation_name = %s
        )
        UPDATE powertwin_solver AS t
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
        cur.execute('UPDATE powertwin_solver SET batch = %s WHERE asset_id = %s', (batch, asset_id))
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
        cur.execute("""
            UPDATE powertwin_solver SET uorun_time = %s, uoprocess_time = %s, total_time = %s WHERE asset_id = %s
        """, (uorun_time, uoprocess_time, total_time, asset_id))
        conn.commit()
    except Exception as e:
        print(f"Error updating time for asset ID {asset_id}: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()
    
def update_status(status,asset_id=None, simulation_name=None):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if simulation_name is not None:
            cur.execute('UPDATE powertwin_solver SET status = %s WHERE simulation_name = %s', (status, simulation_name))
        else:
            cur.execute('UPDATE powertwin_solver SET status = %s WHERE asset_id = %s', (status, asset_id))
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
        cur.execute("""
            UPDATE powertwin_solver SET simulation_name = %s 
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
    logger.debug('Within delete_table()')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute('DROP TABLE IF EXISTS %s', (table_name,))
        conn.commit()
        print(f"Table '{table_name}' deleted successfully.")
    except Exception as e:
        print(f"Error deleting table '{table_name}': {e}")
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
            cur.execute("""
                SELECT 
                    SUM(CASE WHEN status IN ('Finished', 'Failed') THEN 1 ELSE 0 END) as finished_assets,
                    SUM(CASE WHEN status = 'Failed' THEN 1 ELSE 0 END) as failed_assets
                FROM powertwin_solver
                WHERE simulation_name = %s AND batch = %s
            """, (simulation_name, batch_id))
        else:
            # Get stats for entire simulation
            cur.execute("""
                SELECT 
                    SUM(CASE WHEN status IN ('Finished', 'Failed') THEN 1 ELSE 0 END) as finished_assets,
                    SUM(CASE WHEN status = 'Failed' THEN 1 ELSE 0 END) as failed_assets
                FROM powertwin_solver
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
            cur.execute('SELECT location FROM powertwin_solver WHERE simulation_name = %s LIMIT 1', (simulation_name,))
        else:
            cur.execute('SELECT location FROM powertwin_solver WHERE asset_id = %s', (asset_id,))
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
            cur.execute('SELECT asset_id FROM powertwin_solver WHERE simulation_name = %s', (simulation_name,))
            rows = cur.fetchall()
            
            # Return a list of asset IDs
            return [row[0] for row in rows]
        else:
            cur.execute("""
                SELECT asset_id, asset_name FROM powertwin_solver 
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
        cur.execute('SELECT DISTINCT batch FROM powertwin_solver WHERE simulation_name = %s ORDER BY batch', (simulation_name,))
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
        cur.execute('SELECT COUNT(DISTINCT batch) FROM powertwin_solver WHERE simulation_name = %s', 
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
            cur.execute('SELECT COUNT(*) FROM powertwin_solver WHERE simulation_name = %s', (simulation_name,))
        else:
            cur.execute('SELECT COUNT(*) FROM powertwin_solver WHERE simulation_name = %s AND batch = %s', 
                       (simulation_name, batch_id))
        
        asset_count = cur.fetchone()[0]
        return asset_count
    
    except Exception as e:
        print(f'Error getting assets from {simulation_name}: {e}')
        return 0
    finally:
        cur.close()
        conn.close()
        
def view_assets():
    logger.debug('Within view_assets()')
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM powertwin_solver')
    assets = cur.fetchall()
    cur.close()
    conn.close()

    # Convert the data to a list of dictionaries for JSON serialization
    assets_list = []
    for asset in assets:
        assets_list.append({
            'asset_id': asset[0],
            'batch': asset[1],
            'order_rank': asset[2],
            'simulation_name': asset[3],
            'location': asset[4],
            'floor_area': asset[5],
            'number_of_stories': asset[6],
            'complexity': asset[7],
            'uorun_time': asset[8],
            'uoprocess_time': asset[9],
            'asset_name': asset[10],
            'status': asset[11],
            'total_time': asset[12]
        })

    return render_template('uo_db.html', assets=assets_list)