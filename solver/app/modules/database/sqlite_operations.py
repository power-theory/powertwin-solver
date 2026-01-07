"""
SQLite database operations for PowerTwin Solver HPC environments.
Provides the same interface as PostgreSQL operations but uses SQLite for better HPC compatibility.
"""

import sqlite3
import os
import time
try:
    import fcntl  # Unix/Linux file locking
except ImportError:
    fcntl = None  # Windows doesn't have fcntl
import tempfile
from typing import List, Dict, Any, Optional, Tuple
import threading

from modules.utils import initialize_logger
from modules.utils.hpc_environment import get_hpc_info, is_hpc_environment, should_use_distributed_database
from .distributed_sqlite import get_distributed_manager

# Thread-local storage for database connections
thread_local = threading.local()

# Setup logger
external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('SQLiteDatabase', external_log_dir)

# SQLite database configuration
DEFAULT_SQLITE_PATH = "/tmp/powertwin.db"
SQLITE_DB_PATH = os.environ.get("SQLITE_DB_PATH", DEFAULT_SQLITE_PATH)

# Retry configuration for HPC environments
MAX_RETRIES = 5  # Increased for HPC coordination
BASE_RETRY_DELAY = 0.5  # seconds
MAX_RETRY_DELAY = 8.0   # seconds

# HPC coordination configuration
HPC_LOCK_TIMEOUT = 30.0  # seconds to wait for locks
DB_LOCK_FILE = f"{SQLITE_DB_PATH}.lock"
INIT_LOCK_FILE = f"{SQLITE_DB_PATH}.init.lock"


def retry_on_database_error(func):
    """
    Decorator to retry database operations on common SQLite errors in HPC environments.
    """
    def wrapper(*args, **kwargs):
        for attempt in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                error_msg = str(e).lower()
                
                # Check for common recoverable errors
                if any(phrase in error_msg for phrase in ['locked', 'database is locked', 'locking protocol']):
                    if attempt < MAX_RETRIES - 1:  # Don't log on final attempt
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


def coordinate_database_access(operation_name: str):
    """Coordinate database access across HPC nodes using file locks."""
    hpc_info = get_hpc_info()
    
    if not hpc_info['is_master']:
        # Non-master processes wait for master to complete critical operations
        max_wait = HPC_LOCK_TIMEOUT
        wait_time = 0
        while wait_time < max_wait:
            if os.path.exists(f"{DB_LOCK_FILE}.{operation_name}.done"):
                logger.debug(f"Rank {hpc_info['rank']}: Master completed {operation_name}, proceeding")
                return
            time.sleep(0.5)
            wait_time += 0.5
        
        logger.warning(f"Rank {hpc_info['rank']}: Timeout waiting for master to complete {operation_name}")
    else:
        # Master process creates completion marker
        try:
            with open(f"{DB_LOCK_FILE}.{operation_name}.done", 'w') as f:
                f.write(str(time.time()))
            logger.debug(f"Rank 0: Marked {operation_name} as completed")
        except Exception as e:
            logger.warning(f"Failed to create completion marker for {operation_name}: {e}")


def cleanup_coordination_files():
    """Clean up coordination files when simulation completes."""
    hpc_info = get_hpc_info()
    
    if hpc_info['is_master']:
        try:
            import glob
            coordination_files = glob.glob(f"{DB_LOCK_FILE}.*.done")
            for file_path in coordination_files:
                try:
                    os.remove(file_path)
                    logger.debug(f"Removed coordination file: {file_path}")
                except OSError:
                    pass  # File may have been removed by another process
        except Exception as e:
            logger.warning(f"Error cleaning coordination files: {e}")


@retry_on_database_error
def get_sqlite_connection() -> sqlite3.Connection:
    """
    Get a SQLite database connection using appropriate database path.
    In distributed mode, uses local database; otherwise uses master database.
    """
    if not hasattr(thread_local, "connection"):
        # Check if we should use distributed database
        use_distributed = should_use_distributed_database()
        
        if use_distributed:
            # Use distributed database approach
            manager = get_distributed_manager()
            manager.ensure_local_db_exists()
            db_path = manager.get_local_db_path()
            
            logger.debug(f"Using distributed SQLite database: {db_path}")
        else:
            # Use traditional single database
            db_path = SQLITE_DB_PATH
            
            logger.debug(f"Using single SQLite database: {db_path}")
            
        # Ensure database directory exists
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
            
        # Create connection with optimized settings for HPC concurrency
        conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
            timeout=30.0,  # Timeout for distributed databases
            isolation_level=None  # Autocommit mode for better performance
        )
        
        # Enable WAL mode for better concurrent access
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")  # Balance safety and performance
        conn.execute("PRAGMA wal_autocheckpoint=100")  # Auto-checkpoint for WAL
        conn.execute("PRAGMA cache_size=10000")
        conn.execute("PRAGMA temp_store=memory")
        conn.execute("PRAGMA mmap_size=268435456")  # 256MB memory map
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")  # 30s busy timeout for locks
        
        # Set row factory to return rows as dictionaries
        conn.row_factory = sqlite3.Row
        
        thread_local.connection = conn
        logger.debug(f"Created new SQLite connection to {db_path}")
    
    return thread_local.connection


def create_table() -> bool:
    """Create the main assets table if it doesn't exist. Coordinated for HPC environments."""
    hpc_info = get_hpc_info()
    logger.debug(f'Rank {hpc_info["rank"]}: Creating SQLite table if not exists')
    
    # Only master process creates tables
    if not hpc_info['is_master']:
        coordinate_database_access("create_table")
        logger.debug(f'Rank {hpc_info["rank"]}: Table creation coordinated by master')
        return True
    
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
        
        # Create indices for performance
        indices = [
            f"CREATE INDEX IF NOT EXISTS idx_{table_name}_simulation_name ON {table_name}(simulation_name)",
            f"CREATE INDEX IF NOT EXISTS idx_{table_name}_batch ON {table_name}(batch)",
            f"CREATE INDEX IF NOT EXISTS idx_{table_name}_status ON {table_name}(status)",
            f"CREATE INDEX IF NOT EXISTS idx_{table_name}_state ON {table_name}(state)",
            f"CREATE INDEX IF NOT EXISTS idx_{table_name}_order_rank ON {table_name}(order_rank)"
        ]
        
        for index_sql in indices:
            conn.execute(index_sql)
        
        # Create trigger for updating timestamp
        conn.execute(f"""
            CREATE TRIGGER IF NOT EXISTS update_{table_name}_timestamp 
                AFTER UPDATE ON {table_name}
            BEGIN
                UPDATE {table_name} SET updated_at = CURRENT_TIMESTAMP WHERE asset_id = NEW.asset_id;
            END
        """)
        
        # Mark table creation as completed for other processes
        coordinate_database_access("create_table")
        
        logger.info("SQLite table and indices created successfully")
        return True
        
    except Exception as e:
        logger.error(f"Error creating SQLite table: {e}")
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


def insert_bulk_assets(assets: List[Dict[str, Any]]) -> bool:
    """Insert multiple assets in bulk for better performance. Coordinated for HPC environments."""
    logger.debug(f'Rank {get_hpc_rank()}: Bulk inserting {len(assets)} assets')
    
    if not assets:
        logger.debug("No assets to insert")
        return True
    
    # Only master process inserts assets
    if not is_hpc_master():
        coordinate_database_access("bulk_insert")
        logger.debug(f'Rank {get_hpc_rank()}: Bulk insert coordinated by master')
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
        
        # Mark bulk insert as completed for other processes
        coordinate_database_access("bulk_insert")
        
        logger.info(f"Bulk inserted {len(assets)} assets successfully")
        return True
        
    except Exception as e:
        logger.error(f"Error bulk inserting assets: {e}")
        return False


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
        
        with conn:
            conn.executemany(f"""
                UPDATE {table_name} 
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE simulation_name = ? AND asset_id = ?
            """, update_data)
        
        logger.info(f"Successfully updated status to '{status}' for {len(asset_ids)} assets")
        return True
        
    except Exception as e:
        logger.error(f"Error bulk updating asset status: {e}")
        return False


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
        
        logger.debug(f"Asset {asset_id} status updated to {status}")
        return True
        
    except Exception as e:
        logger.error(f"Error updating asset status: {e}")
        return False


@retry_on_database_error
def get_weather(asset_id: int) -> Optional[Tuple[str, str]]:
    """Get weather file and state for an asset with distributed database support."""
    logger.debug(f'Getting weather file for asset {asset_id}')
    
    # Check if we should use distributed database
    use_distributed = should_use_distributed_database()
    
    if use_distributed:
        # In distributed mode, check local database first, then master
        manager = get_distributed_manager()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        # First try local database
        try:
            conn = get_sqlite_connection()
            cursor = conn.execute(f"""
                SELECT state, weather_file FROM {table_name} 
                WHERE asset_id = ?
            """, (asset_id,))
            
            row = cursor.fetchone()
            if row:
                return (row['state'], row['weather_file'])
        except Exception as e:
            logger.debug(f"Asset {asset_id} not found in local database, checking master: {e}")
        
        # If not found locally, check master database
        try:
            master_conn = sqlite3.connect(manager.get_master_db_path(), timeout=5.0)
            master_conn.row_factory = sqlite3.Row
            
            cursor = master_conn.execute(f"""
                SELECT state, weather_file FROM {table_name} 
                WHERE asset_id = ?
            """, (asset_id,))
            
            row = cursor.fetchone()
            master_conn.close()
            
            if row:
                return (row['state'], row['weather_file'])
                
        except Exception as e:
            logger.error(f"Error accessing master database for asset {asset_id}: {e}")
            
        # If still not found, this is an error
        logger.error(f"No weather data found for asset_id {asset_id}")
        return None
    else:
        # Use original single database logic
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
        
        logger.debug(f"Times updated for asset {asset_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error updating times: {e}")
        return False


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


def distribute_assets_to_batches(simulation_name: str, total_batches: int) -> bool:
    """Distribute assets across batches. Coordinated for HPC environments."""
    logger.debug(f'Rank {get_hpc_rank()}: Distributing assets to {total_batches} batches for simulation {simulation_name}')
    
    # Only master process distributes assets
    if not is_hpc_master():
        coordinate_database_access("distribute_batches")
        logger.debug(f'Rank {get_hpc_rank()}: Asset distribution coordinated by master')
        return True
    
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
        
        logger.info(f"Distributed {total_assets} assets across {total_batches} batches")
        
        # Mark distribution as completed for other processes
        coordinate_database_access("distribute_batches")
        
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
    """Get total number of batches for a simulation. Safe for HPC concurrent access."""
    logger.debug(f'Rank {get_hpc_rank()}: Getting batch total for simulation {simulation_name}')
    
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
        
        logger.debug(f"Rank {get_hpc_rank()}: Batch total: {total}")
        return total
        
    except Exception as e:
        logger.error(f"Rank {get_hpc_rank()}: Error getting batch total: {e}")
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
        
        logger.debug(f"Asset {asset_id} batch updated to {batch}")
        return True
        
    except Exception as e:
        logger.error(f"Error updating batch: {e}")
        return False


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
        
        logger.debug(f"Simulation name updated from {old_name} to {new_name}")
        return True
        
    except Exception as e:
        logger.error(f"Error updating simulation name: {e}")
        return False


def delete_table(simulation_name: str) -> bool:
    """Delete all assets for a simulation."""
    logger.debug(f'Deleting assets for simulation {simulation_name}')
    
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        conn.execute(f"""
            DELETE FROM {table_name} WHERE simulation_name = ?
        """, (simulation_name,))
        
        logger.debug(f"Assets deleted for simulation {simulation_name}")
        return True
        
    except Exception as e:
        logger.error(f"Error deleting assets: {e}")
        return False


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


# Distributed Database Functions

def consolidate_distributed_databases(simulation_name=None):
    """Consolidate all distributed databases into master database."""
    manager = get_distributed_manager()
    return manager.consolidate_databases(simulation_name)

def copy_master_to_local(simulation_name):
    """Copy master database data to local database for this process."""
    manager = get_distributed_manager()
    return manager.copy_master_data_to_local(simulation_name)

def setup_distributed_database(simulation_name):
    """Setup distributed database for this process."""
    if should_use_distributed_database():
        manager = get_distributed_manager()
        manager.ensure_local_db_exists()
        # Copy initial data from master if it exists
        copy_master_to_local(simulation_name)
        return True
    return False

def get_available_distributed_databases():
    """Get list of available distributed databases."""
    manager = get_distributed_manager()
    return manager.get_available_databases()