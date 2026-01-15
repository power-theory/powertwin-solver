"""
SQLite database operations for PowerTwin Solver.
Provides single SQLite database with file locking for concurrent access from multiple cores.
Same interface as PostgreSQL operations but uses SQLite with WAL mode for concurrency.
"""

import sqlite3
import os
import time
from typing import List, Dict, Any, Optional, Tuple
import threading

from modules.utils import initialize_logger
from .sqlite_manager import get_sqlite_manager

# Thread-local storage for database connections
thread_local = threading.local()

# Setup logger
external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('SQLiteOperations', external_log_dir)

# SQLite database configuration
DEFAULT_SQLITE_PATH = "/tmp/powertwin.db"
SQLITE_DB_PATH = os.environ.get("SQLITE_DB_PATH", DEFAULT_SQLITE_PATH)

# Retry configuration for SQLite locking
MAX_RETRIES = 5
BASE_RETRY_DELAY = 0.5  # seconds
MAX_RETRY_DELAY = 8.0   # seconds


def retry_on_database_error(func):
    """
    Decorator to retry database operations on SQLite lock errors.
    """
    def wrapper(*args, **kwargs):
        for attempt in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                error_msg = str(e).lower()
                
                # Check for common recoverable SQLite errors
                if any(phrase in error_msg for phrase in ['locked', 'database is locked', 'locking protocol']):
                    if attempt < MAX_RETRIES - 1:
                        delay = min(BASE_RETRY_DELAY * (2 ** attempt), MAX_RETRY_DELAY)
                        logger.warning(f"Database locked on attempt {attempt + 1}/{MAX_RETRIES}, retrying in {delay}s: {e}")
                        time.sleep(delay)
                        
                        # Force close existing connection to reset state
                        if hasattr(thread_local, "connection"):
                            try:
                                thread_local.connection.close()
                            except:
                                pass
                            delattr(thread_local, "connection")
                        continue
                    else:
                        logger.error(f"Database operation failed after {MAX_RETRIES} attempts: {e}")
                        raise
                else:
                    # Non-recoverable operational error
                    logger.error(f"Non-recoverable database error: {e}")
                    raise
            except Exception as e:
                # Other exceptions are not retried
                logger.error(f"Database operation failed: {e}")
                raise
        
        return None  # Should never reach here
    
    return wrapper
@retry_on_database_error
def get_sqlite_connection():
    """Get SQLite database connection using SQLiteManager."""
    manager = get_sqlite_manager()
    manager.ensure_db_exists()
    db_path = manager.get_db_path()
    
    conn = sqlite3.connect(db_path)
    # Enable Row factory for dictionary-style access
    conn.row_factory = sqlite3.Row
    # Enable WAL mode for concurrent access
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

def create_table() -> bool:
    """Create the main assets table if it doesn't exist."""
    logger.debug('Creating SQLite table if not exists')
    
    try:
        conn = get_sqlite_connection()
        
        # Get table name from environment
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        # Create main table with same schema as PostgreSQL version
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                asset_id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch INTEGER,
                order_rank INTEGER,
                simulation_name VARCHAR(255),
                state VARCHAR(255),
                weather_file VARCHAR(255),
                floor_area REAL,
                number_of_stories INTEGER,
                complexity INTEGER,
                uorun_time REAL,
                uoprocess_time REAL,
                asset_name VARCHAR(255),
                subtype VARCHAR(255),
                status VARCHAR(255),
                total_time REAL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        
        logger.debug(f"Table '{table_name}' created or verified to exist")
        return True
        
    except Exception as e:
        logger.error(f"Error creating table: {e}")
        return False




def insert_asset(asset_id: int, state: str, weather_file: str, floor_area: float, 
                number_of_stories: int, complexity: int, asset_name: str, 
                subtype: str, simulation_name: str) -> bool:
    """Insert a single asset into the database."""
    logger.debug(f'Inserting asset {asset_id} for simulation {simulation_name}')
    
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        conn.execute(f"""
            INSERT INTO {table_name} (
                asset_id, state, weather_file, floor_area, number_of_stories,
                complexity, asset_name, subtype, simulation_name, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """, (asset_id, state, weather_file, floor_area, number_of_stories,
              complexity, asset_name, subtype, simulation_name))
        
        logger.debug(f"Asset {asset_id} inserted successfully")
        return True
        
    except Exception as e:
        logger.error(f"Error inserting asset {asset_id}: {e}")
        return False


@retry_on_database_error
def insert_bulk_assets(assets: List[Dict[str, Any]]) -> bool:
    """Insert multiple assets in bulk for better performance."""
    logger.debug(f'Bulk inserting {len(assets)} assets')
    
    if not assets:
        logger.debug("No assets to insert")
        return True
    
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        # Prepare bulk insert data
        insert_data = []
        for i, asset in enumerate(assets):
            try:
                # Check if asset is a dictionary
                if not isinstance(asset, dict):
                    logger.error(f"Asset {i} is not a dictionary: {type(asset)}, value: {asset}")
                    return False
                
                # Check if all required keys are present
                required_keys = ['asset_id', 'state', 'weather_file', 'floor_area', 
                               'number_of_stories', 'complexity', 'asset_name', 'subtype', 'simulation_name']
                missing_keys = [key for key in required_keys if key not in asset]
                if missing_keys:
                    logger.error(f"Asset {i} missing keys: {missing_keys}, available keys: {list(asset.keys())}")
                    return False
                    
                insert_data.append((
                    asset['asset_id'], asset['state'], asset['weather_file'],
                    asset['floor_area'], asset['number_of_stories'], asset['complexity'],
                    asset['asset_name'], asset['subtype'], asset['simulation_name'], 'pending'
                ))
            except Exception as e:
                logger.error(f"Error processing asset {i}: {e}, asset: {asset}")
                return False
        
        conn.executemany(f"""
            INSERT INTO {table_name} (
                asset_id, state, weather_file, floor_area, number_of_stories,
                complexity, asset_name, subtype, simulation_name, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, insert_data)
        conn.commit()
        
        logger.info(f"Bulk inserted {len(assets)} assets successfully")
        return True
        
    except Exception as e:
        logger.error(f"Error bulk inserting assets: {e}")
        return False


@retry_on_database_error
def get_bulk_assets(simulation_name: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Get assets for a simulation."""
    logger.debug(f'Getting assets for simulation {simulation_name}')
    
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        query = f"SELECT * FROM {table_name} WHERE simulation_name = ?"
        params = [simulation_name]
        
        if limit:
            query += " LIMIT ?"
            params.append(limit)
            
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        
        # Convert to list of dictionaries
        assets = [dict(row) for row in rows]
        
        logger.debug(f"Retrieved {len(assets)} assets for simulation {simulation_name}")
        return assets
        
    except Exception as e:
        logger.error(f"Error getting bulk assets: {e}")
        return []


@retry_on_database_error
def bulk_update_status(asset_ids: List[int], status: str, simulation_name: str) -> bool:
    """Efficiently update status for multiple assets in a single transaction."""
    if not asset_ids:
        logger.info("No assets to update")
        return True
        
    logger.debug(f'Bulk updating {len(asset_ids)} assets status to {status} for simulation {simulation_name}')
    
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        # Use executemany for efficient bulk updates
        update_data = [(status, simulation_name, asset_id) for asset_id in asset_ids]
        
        conn.executemany(f"""
            UPDATE {table_name} 
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE simulation_name = ? AND asset_id = ?
        """, update_data)
        conn.commit()
        
        logger.info(f"Successfully updated status to '{status}' for {len(asset_ids)} assets")
        return True
        
    except Exception as e:
        logger.error(f"Error bulk updating asset status: {e}")
        return False


@retry_on_database_error
def update_status(simulation_name: str, asset_id: int, status: str) -> bool:
    """Update asset status. Consider using bulk_update_status for better performance with multiple assets."""
    logger.debug(f'Updating asset {asset_id} status to {status} for simulation {simulation_name}')
    
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        conn.execute(f"""
            UPDATE {table_name} 
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE simulation_name = ? AND asset_id = ?
        """, (status, simulation_name, asset_id))
        conn.commit()
        
        logger.debug(f"Asset {asset_id} status updated to {status}")
        return True
        
    except Exception as e:
        logger.error(f"Error updating asset status: {e}")
        return False


@retry_on_database_error
def get_weather(asset_id: int) -> Optional[Tuple[str, str]]:
    """Get weather file and state for an asset."""
    logger.debug(f'Getting weather file for asset {asset_id}')
    
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        cursor = conn.execute(f"""
            SELECT state, weather_file FROM {table_name} 
            WHERE asset_id = ?
        """, (asset_id,))
        
        row = cursor.fetchone()
        if row:
            return (row['state'], row['weather_file'])
        else:
            return None
        
    except Exception as e:
        logger.error(f"Error getting weather file: {e}")
        return None


@retry_on_database_error
def update_time(simulation_name: str, asset_id: int, uorun_time: float, 
                uoprocess_time: float, total_time: float) -> bool:
    """Update timing information for an asset."""
    logger.debug(f'Updating times for asset {asset_id} in simulation {simulation_name}')
    
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        conn.execute(f"""
            UPDATE {table_name} 
            SET uorun_time = ?, uoprocess_time = ?, total_time = ?, updated_at = CURRENT_TIMESTAMP
            WHERE simulation_name = ? AND asset_id = ?
        """, (uorun_time, uoprocess_time, total_time, simulation_name, asset_id))
        conn.commit()
        
        logger.debug(f"Times updated for asset {asset_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error updating times: {e}")
        return False


@retry_on_database_error
def get_failed_assets(simulation_name: str) -> List[Dict[str, Any]]:
    """Get assets that failed processing."""
    logger.debug(f'Getting failed assets for simulation {simulation_name}')
    
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        cursor = conn.execute(f"""
            SELECT * FROM {table_name} 
            WHERE simulation_name = ? AND status = 'failed'
        """, (simulation_name,))
        
        rows = cursor.fetchall()
        assets = [dict(row) for row in rows]
        
        logger.debug(f"Retrieved {len(assets)} failed assets")
        return assets
        
    except Exception as e:
        logger.error(f"Error getting failed assets: {e}")
        return []


@retry_on_database_error
def distribute_assets_to_batches(simulation_name: str, total_batches: int) -> bool:
    """Distribute assets across batches."""
    logger.debug(f'Distributing assets to {total_batches} batches for simulation {simulation_name}')
    
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        # Get total number of assets
        cursor = conn.execute(f"""
            SELECT COUNT(*) as count FROM {table_name} WHERE simulation_name = ?
        """, (simulation_name,))
        
        total_assets = cursor.fetchone()['count']
        
        if total_assets == 0:
            logger.warning(f"No assets found for simulation {simulation_name}")
            return False
            
        assets_per_batch = total_assets // total_batches
        remainder = total_assets % total_batches
        
        # Update assets with batch assignments
        cursor = conn.execute(f"""
            SELECT asset_id FROM {table_name} 
            WHERE simulation_name = ? 
            ORDER BY asset_id
        """, (simulation_name,))
        
        asset_ids = [row['asset_id'] for row in cursor.fetchall()]
        
        current_asset = 0
        for batch_num in range(total_batches):
            batch_size = assets_per_batch + (1 if batch_num < remainder else 0)
            batch_assets = asset_ids[current_asset:current_asset + batch_size]
            
            for i, asset_id in enumerate(batch_assets):
                conn.execute(f"""
                    UPDATE {table_name} 
                    SET batch = ?, order_rank = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE simulation_name = ? AND asset_id = ?
                """, (batch_num, i, simulation_name, asset_id))
            
            current_asset += batch_size
        
        conn.commit()
        logger.info(f"Distributed {total_assets} assets across {total_batches} batches")
        return True
        
    except Exception as e:
        logger.error(f"Error distributing assets to batches: {e}")
        return False


@retry_on_database_error
def get_status_stats(simulation_name: str) -> Dict[str, int]:
    """Get status statistics for a simulation."""
    logger.debug(f'Getting status stats for simulation {simulation_name}')
    
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        cursor = conn.execute(f"""
            SELECT status, COUNT(*) as count 
            FROM {table_name} 
            WHERE simulation_name = ? 
            GROUP BY status
        """, (simulation_name,))
        
        rows = cursor.fetchall()
        stats = {row['status']: row['count'] for row in rows}
        
        logger.debug(f"Status stats: {stats}")
        return stats
        
    except Exception as e:
        logger.error(f"Error getting status stats: {e}")
        return {}

@retry_on_database_error
def get_bulk_batchids(simulation_name: str) -> List[int]:
    """Get all batch IDs for a simulation."""
    logger.debug(f'Getting batch IDs for simulation {simulation_name}')
    
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        cursor = conn.execute(f"""
            SELECT DISTINCT batch FROM {table_name} 
            WHERE simulation_name = ? AND batch IS NOT NULL
            ORDER BY batch
        """, (simulation_name,))
        
        rows = cursor.fetchall()
        batch_ids = [row['batch'] for row in rows]
        
        logger.debug(f"Retrieved batch IDs: {batch_ids}")
        return batch_ids
        
    except Exception as e:
        logger.error(f"Error getting batch IDs: {e}")
        return []


@retry_on_database_error
def get_batch_total(simulation_name: str) -> int:
    """Get total number of batches for a simulation."""
    logger.debug(f'Getting batch total for simulation {simulation_name}')
    
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        cursor = conn.execute(f"""
            SELECT COUNT(DISTINCT batch) as count 
            FROM {table_name} 
            WHERE simulation_name = ? AND batch IS NOT NULL
        """, (simulation_name,))
        
        row = cursor.fetchone()
        total = row['count'] if row else 0
        
        logger.debug(f"Batch total: {total}")
        return total
        
    except Exception as e:
        logger.error(f"Error getting batch total: {e}")
        return 0


@retry_on_database_error
def get_asset_total(simulation_name: str) -> int:
    """Get total number of assets for a simulation."""
    logger.debug(f'Getting asset total for simulation {simulation_name}')
    
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        cursor = conn.execute(f"""
            SELECT COUNT(*) as count FROM {table_name} WHERE simulation_name = ?
        """, (simulation_name,))
        
        row = cursor.fetchone()
        total = row['count'] if row else 0
        
        logger.debug(f"Asset total: {total}")
        return total
        
    except Exception as e:
        logger.error(f"Error getting asset total: {e}")
        return 0


@retry_on_database_error
def update_batch(asset_id: int, batch: int) -> bool:
    """Update batch for a specific asset."""
    logger.debug(f'Updating asset {asset_id} to batch {batch}')
    
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        conn.execute(f"""
            UPDATE {table_name} 
            SET batch = ?, updated_at = CURRENT_TIMESTAMP
            WHERE asset_id = ?
        """, (batch, asset_id))
        conn.commit()
        
        logger.debug(f"Asset {asset_id} batch updated to {batch}")
        return True
        
    except Exception as e:
        logger.error(f"Error updating batch: {e}")
        return False


@retry_on_database_error
def update_simulation_name(old_name: str, new_name: str) -> bool:
    """Update simulation name for all assets."""
    logger.debug(f'Updating simulation name from {old_name} to {new_name}')
    
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        conn.execute(f"""
            UPDATE {table_name} 
            SET simulation_name = ?, updated_at = CURRENT_TIMESTAMP
            WHERE simulation_name = ?
        """, (new_name, old_name))
        conn.commit()
        
        logger.debug(f"Simulation name updated from {old_name} to {new_name}")
        return True
        
    except Exception as e:
        logger.error(f"Error updating simulation name: {e}")
        return False


@retry_on_database_error
def delete_table(simulation_name: str) -> bool:
    """Delete all assets for a simulation."""
    logger.debug(f'Deleting assets for simulation {simulation_name}')
    
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        conn.execute(f"""
            DELETE FROM {table_name} WHERE simulation_name = ?
        """, (simulation_name,))
        conn.commit()
        
        logger.debug(f"Assets deleted for simulation {simulation_name}")
        return True
        
    except Exception as e:
        logger.error(f"Error deleting assets: {e}")
        return False


@retry_on_database_error
def get_asset_stats(simulation_name: str) -> Dict[str, Any]:
    """Get comprehensive statistics for a simulation."""
    logger.debug(f'Getting asset stats for simulation {simulation_name}')
    
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        # Get basic counts
        cursor = conn.execute(f"""
            SELECT 
                COUNT(*) as total_assets,
                COUNT(DISTINCT batch) as total_batches,
                AVG(total_time) as avg_time,
                SUM(total_time) as total_time_sum
            FROM {table_name} 
            WHERE simulation_name = ?
        """, (simulation_name,))
        
        row = cursor.fetchone()
        
        stats = {
            'total_assets': row['total_assets'] if row else 0,
            'total_batches': row['total_batches'] if row else 0,
            'avg_time': row['avg_time'] if row else 0,
            'total_time_sum': row['total_time_sum'] if row else 0
        }
        
        logger.debug(f"Asset stats: {stats}")
        return stats
        
    except Exception as e:
        logger.error(f"Error getting asset stats: {e}")
        return {}


@retry_on_database_error
def consolidate_databases(simulation_name: str, node_dirs: List[str], master_db_path: str) -> bool:
    """Consolidate node-specific databases back to master database."""
    logger.info(f"Consolidating databases for simulation {simulation_name}")
    
    try:
        # Create/ensure master database exists
        master_conn = sqlite3.connect(master_db_path)
        master_conn.row_factory = sqlite3.Row
        
        # Create table if it doesn't exist
        table_name = os.environ.get("PGDATABASE", "powertwin")
        master_conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                asset_id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch INTEGER,
                order_rank INTEGER,
                simulation_name VARCHAR(255),
                state VARCHAR(255),
                weather_file VARCHAR(255),
                floor_area REAL,
                number_of_stories INTEGER,
                complexity INTEGER,
                uorun_time REAL,
                uoprocess_time REAL,
                asset_name VARCHAR(255),
                subtype VARCHAR(255),
                status VARCHAR(255),
                total_time REAL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Clear existing data for this simulation from master
        master_conn.execute(f"DELETE FROM {table_name} WHERE simulation_name = ?", (simulation_name,))
        
        consolidated_count = 0
        base_dir = os.path.dirname(master_db_path)
        
        # Consolidate data from each node database
        for node_dir in node_dirs:
            node_db_path = None
            full_node_dir = os.path.join(base_dir, node_dir)
            
            # Find the database file in the node directory
            try:
                for file in os.listdir(full_node_dir):
                    if file.endswith('.db'):
                        node_db_path = os.path.join(full_node_dir, file)
                        break
            except OSError:
                logger.warning(f"Could not access node directory {node_dir}")
                continue
                        
            if not node_db_path or not os.path.exists(node_db_path):
                logger.warning(f"No database found in {node_dir}")
                continue
                
            logger.info(f"Consolidating data from {node_db_path}")
            
            # Connect to node database
            node_conn = sqlite3.connect(node_db_path)
            node_conn.row_factory = sqlite3.Row
            
            # Get all data for this simulation
            node_cursor = node_conn.execute(f"""
                SELECT * FROM {table_name} WHERE simulation_name = ?
            """, (simulation_name,))
            
            nodes_data = node_cursor.fetchall()
            
            # Insert into master database
            for row in nodes_data:
                row_dict = dict(row)
                # Remove auto-increment primary key to avoid conflicts
                if 'asset_id' in row_dict:
                    del row_dict['asset_id']
                    
                columns = ', '.join(row_dict.keys())
                placeholders = ', '.join(['?' for _ in row_dict])
                
                master_conn.execute(f"""
                    INSERT INTO {table_name} ({columns}) 
                    VALUES ({placeholders})
                """, list(row_dict.values()))
                
                consolidated_count += 1
                
            node_conn.close()
            logger.info(f"Consolidated {len(nodes_data)} records from {node_dir}")
        
        master_conn.commit()
        master_conn.close()
        
        logger.info(f"Successfully consolidated {consolidated_count} total records to master database")
        return True
        
    except Exception as e:
        logger.error(f"Error consolidating databases: {e}")
        return False