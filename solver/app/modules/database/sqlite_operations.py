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
    """SQLite connection with environment-dependent lifetime and journal mode.

    LOCAL (flask container, tests): per-thread cached connection (Q/A R-017) -- the old
    open-per-call pattern let two module calls in one thread hold each other's write locks
    through the 30 s busy_timeout, and thread_local was declared for this cache but never
    assigned (which also made the retry decorator's reset path dead code). WAL is fine on
    a local disk.

    HPC (SLURM): a FRESH connection per call and journal_mode=DELETE for EVERY database.
    The master db lives on network storage where WAL is unsafe (its -shm mmap does not
    coordinate across nodes); nat80 ran clean only because transient per-call handles kept
    the WAL window microscopic. Caching those handles (first teton3tt attempt, job
    15949939) held WAL state open across a 20-node job and stormed SQLITE_PROTOCOL
    ("locking protocol") until inserts died. Transient handles + DELETE journal is the
    proven shape on this filesystem; CPython closes sqlite connections at dealloc, so
    per-call connections do not leak descriptors."""
    manager = get_sqlite_manager()
    db_path = manager.get_db_path()
    on_hpc = bool(os.environ.get('SLURM_JOB_ID'))

    if not on_hpc:
        cached = getattr(thread_local, "connection", None)
        if cached is not None and getattr(thread_local, "db_path", None) == db_path:
            return cached

    # Ensure directory exists for node databases
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(db_path)
    # Enable Row factory for dictionary-style access
    conn.row_factory = sqlite3.Row
    if on_hpc or 'node_' in db_path:
        # Network filesystem: WAL requires cross-process shared memory; DELETE is safe.
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=FULL")
    else:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    if not on_hpc:
        thread_local.connection = conn
        thread_local.db_path = db_path
    return conn

def _table_ddl(table_name, if_not_exists=True):
    """Single source of the tracking-table schema, shared by create_table and the rekey."""
    exists = "IF NOT EXISTS " if if_not_exists else ""
    return f"""
        CREATE TABLE {exists}{table_name} (
                asset_id INTEGER NOT NULL,
                batch INTEGER,
                order_rank INTEGER,
                simulation_name VARCHAR(255) NOT NULL,
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
                failure_reason TEXT,
                node_name VARCHAR(255),
                process_id VARCHAR(255),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                -- Keyed by run, not by asset: concurrent sims of one asset must not share a row.
                PRIMARY KEY (asset_id, simulation_name)
            )
    """


def _rekey_if_needed(conn, table_name):
    """Rebuild a table still keyed on asset_id alone. One row per asset let a later run
    replace an earlier one: on a node that is two users simulating the same asset, and in
    the master db it is a later simulation silently overwriting an earlier one's record.
    SQLite cannot ALTER a primary key, so copy through a new table."""
    old = f"{table_name}_old"
    cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    # Skip only when the key is ALREADY the composite one. Testing `!= 1` instead conflated a
    # table with NO primary key (pk flags all 0) with "already migrated", and there
    # INSERT OR REPLACE degenerates to a plain INSERT and quietly accumulates duplicate rows.
    if sum(1 for c in cols if c['pk']) == 2:
        return False

    logger.info(f"Rekeying {table_name} to PRIMARY KEY (asset_id, simulation_name)")
    # Explicit BEGIN: python's sqlite3 opens a transaction for DML but not DDL, so without
    # it the rename commits on its own and a failed copy strands the rows behind an empty
    # table that the next run skips (the key already looks right).
    conn.execute("BEGIN")
    try:
        conn.execute(f"ALTER TABLE {table_name} RENAME TO {old}")
        conn.execute(_table_ddl(table_name, if_not_exists=False))
        kept = {c['name'] for c in conn.execute(f"PRAGMA table_info({table_name})")}
        dropped = [c['name'] for c in cols if c['name'] not in kept]
        if dropped:
            logger.warning(f"Rekey drops columns absent from the current schema: {dropped}")
        names = ', '.join(c['name'] for c in cols if c['name'] in kept)
        # NULL simulation_name cannot enter the key and no run can ever match it.
        orphans = conn.execute(f"SELECT COUNT(*) FROM {old} WHERE simulation_name IS NULL").fetchone()[0]
        if orphans:
            logger.warning(f"Rekey drops {orphans} rows with NULL simulation_name")
        conn.execute(f"INSERT INTO {table_name} ({names}) SELECT {names}"
                     f" FROM {old} WHERE simulation_name IS NOT NULL")
        conn.execute(f"DROP TABLE {old}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return True


def create_table() -> bool:
    """Create the main assets table if it doesn't exist."""
    logger.debug('Creating SQLite table if not exists')

    # NOTE (Q/A R-018): create_table used to rmtree every node_* directory here under an
    # "assumes fresh run" comment -- reachable from three views.py endpoints, so a new
    # submission could delete LIVE node databases mid-campaign. The HPC start script owns
    # stale-run cleanup (its Step 1 cleans the sqlite dir); in-code sweeping is removed.
    try:
        conn = get_sqlite_connection()
        
        # Get table name from environment
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        # Create main table with same schema as PostgreSQL version
        # Note: asset_id stores original building IDs, not auto-incremented values
        conn.execute(_table_ddl(table_name))
        conn.commit()

        # Add diagnostic columns to existing tables that don't have them
        for col, col_type in [('failure_reason', 'TEXT'), ('node_name', 'VARCHAR(255)'), ('process_id', 'VARCHAR(255)')]:
            try:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {col} {col_type}")
                conn.commit()
            except Exception:
                pass  # Column already exists

        logger.debug(f"Table '{table_name}' created or verified to exist")

    except Exception as e:
        logger.error(f"Error creating table: {e}")
        return False

    # OUTSIDE the try, and deliberately not swallowed: every caller of create_table discards the
    # return value, so a rekey that silently failed would leave the run on the legacy
    # asset_id-only key and collapse concurrent sims into one row -- the original bug, with no
    # signal at all. Postgres fails loud here too.
    _rekey_if_needed(conn, table_name)
    return True




def insert_asset(asset_id: int, state: str, weather_file: str, floor_area: float, 
                number_of_stories: int, complexity: int, asset_name: str, 
                subtype: str, simulation_name: str) -> bool:
    """Insert a single asset into the database."""
    logger.debug(f'Inserting asset {asset_id} for simulation {simulation_name}')
    
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        conn.execute(f"""
            INSERT OR REPLACE INTO {table_name} (
                asset_id, state, weather_file, floor_area, number_of_stories,
                complexity, asset_name, subtype, simulation_name, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Not Processed Yet')
        """, (asset_id, state, weather_file, floor_area, number_of_stories,
              complexity, asset_name, subtype, simulation_name))
        # get_sqlite_connection hands back a fresh connection per call, so without this the
        # insert is discarded when it goes out of scope (insert_bulk_assets already commits).
        conn.commit()

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
                # value, not just presence: asset_id and simulation_name are the key and are now
                # NOT NULL, and INSERT OR IGNORE suppresses a NOT NULL violation as readily as a
                # PK conflict -- the row would vanish while this still logged success.
                null_keys = [k for k in ('asset_id', 'simulation_name') if asset[k] is None]
                if null_keys:
                    logger.error(f"Asset {i} has null {null_keys}; these form the row key")
                    return False


                insert_data.append((
                    asset['asset_id'], asset['state'], asset['weather_file'],
                    asset['floor_area'], asset['number_of_stories'], asset['complexity'],
                    asset['asset_name'], asset['subtype'], asset['simulation_name'], 
                    asset.get('status', 'Not Processed Yet')  # Preserve existing status if available
                ))
            except Exception as e:
                logger.error(f"Error processing asset {i}: {e}, asset: {asset}")
                return False
        
        # Use INSERT OR IGNORE followed by UPDATE for existing records (excluding finished assets)
        conn.executemany(f"""
            INSERT OR IGNORE INTO {table_name} (
                asset_id, state, weather_file, floor_area, number_of_stories,
                complexity, asset_name, subtype, simulation_name, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, insert_data)
        
        # Update existing records but protect finished assets. Mirrors the postgres upsert
        # (Q/A R-019b): a re-submitted asset must have its run state RESET (status, batch,
        # order_rank, timings) or it keeps stale scheduling and looks already-run. One
        # executemany instead of a per-row loop.
        conn.executemany(f"""
            UPDATE {table_name} SET
                state = ?, weather_file = ?, floor_area = ?, number_of_stories = ?,
                complexity = ?, asset_name = ?, subtype = ?,
                status = NULL, batch = NULL, order_rank = NULL,
                uorun_time = NULL, uoprocess_time = NULL, total_time = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE asset_id = ? AND simulation_name = ?
              AND (status IS NULL OR status != 'Finished')
        """, [(a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[0], a[8]) for a in insert_data])
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
        
        query = f"SELECT * FROM {table_name} WHERE simulation_name = ? ORDER BY order_rank"
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
        
        # Q/A R-027b: resetting to 'Not Processed Yet' must not resurrect Finished work --
        # that is the re-submission path that silently re-ran whole campaigns.
        protect = " AND (status IS NULL OR status != 'Finished')" if status == 'Not Processed Yet' else ""
        cur = conn.executemany(f"""
            UPDATE {table_name} 
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE simulation_name = ? AND asset_id = ?{protect}
        """, update_data)
        conn.commit()
        updated = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        # Report REAL success like postgres (Q/A D-05): the constant True let recover_UOsim
        # believe a bulk update that matched zero rows had worked.
        if updated != len(asset_ids):
            if status != 'Not Processed Yet':
                logger.error(
                    f"bulk_update_status matched {updated}/{len(asset_ids)} assets for '{status}' "
                    f"in {simulation_name}")
                return False
            logger.info(
                f"bulk_update_status reset {updated}/{len(asset_ids)} assets in {simulation_name}; "
                f"{len(asset_ids) - updated} already Finished and kept")
        logger.info(f"Successfully updated status to '{status}' for {updated} assets")
        return True
        
    except Exception as e:
        logger.error(f"Error bulk updating asset status: {e}")
        return False


@retry_on_database_error
def update_status(simulation_name: str, asset_id: int, status: str, failure_reason: str = None) -> bool:
    """Update asset status. Consider using bulk_update_status for better performance with multiple assets."""
    logger.debug(f'Updating asset {asset_id} status to {status} for simulation {simulation_name}')

    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")

        import socket
        node_name = socket.gethostname().split('.')[0]
        pid = str(os.getpid())

        conn.execute(f"""
            UPDATE {table_name}
            SET status = ?, failure_reason = ?, node_name = ?, process_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE simulation_name = ? AND asset_id = ?
        """, (status, failure_reason, node_name, pid, simulation_name, asset_id))
        conn.commit()

        logger.debug(f"Asset {asset_id} status updated to {status}")
        return True

    except Exception as e:
        logger.error(f"Error updating asset status: {e}")
        return False


@retry_on_database_error
def get_distinct_weather_files(simulation_name: str) -> list:
    """Get distinct weather file names for a simulation."""
    logger.debug(f'Getting distinct weather files for simulation {simulation_name}')

    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")

        cursor = conn.execute(f"""
            SELECT DISTINCT weather_file FROM {table_name}
            WHERE simulation_name = ? AND weather_file IS NOT NULL AND (status IS NULL OR status != 'Finished')
        """, (simulation_name,))

        return [row['weather_file'] for row in cursor.fetchall()]

    except Exception as e:
        logger.error(f"Error getting distinct weather files: {e}")
        return []


@retry_on_database_error
def get_weather(asset_id: int, simulation_name: str) -> Optional[Tuple[str, str]]:
    """Get weather file and state for an asset in a specific run.

    simulation_name is REQUIRED: (asset_id, simulation_name) is the row key, and matching on
    asset_id alone returns whichever concurrent run's row comes first -- another user's weather.
    """
    logger.debug(f'Getting weather file for asset {asset_id}')
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")

        cursor = conn.execute(f"""
            SELECT state, weather_file FROM {table_name}
            WHERE asset_id = ? AND simulation_name = ?
        """, (asset_id, simulation_name))
        
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
            WHERE simulation_name = ? AND status = 'Failed' COLLATE NOCASE
            ORDER BY batch, order_rank
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
        
        # Count SIMULATABLE assets only -- a NULL weather_file (no-coords building, recorded Failed)
        # can't run in EnergyPlus, so it's never distributed to a batch.
        cursor = conn.execute(f"""
            SELECT COUNT(*) as count FROM {table_name} WHERE simulation_name = ? AND weather_file IS NOT NULL AND (status IS NULL OR status != 'Finished')
        """, (simulation_name,))
        
        total_assets = cursor.fetchone()['count']
        
        if total_assets == 0:
            logger.warning(f"No assets found for simulation {simulation_name}")
            return False
        if total_batches < 1:
            logger.error(f"total_batches={total_batches} for {simulation_name}; nothing distributed")
            return False

        # Mirror the postgres LPT balancing (Q/A R-012): complexity DESC with round-robin
        # assignment and a GLOBAL order_rank. The old contiguous asset_id chunks abandoned
        # longest-processing-time-first on exactly the platform (HPC) where uneven batch
        # durations cost allocation hours; complexity was computed and stored but never read.
        cursor = conn.execute(f"""
            SELECT asset_id FROM {table_name}
            WHERE simulation_name = ? AND weather_file IS NOT NULL AND (status IS NULL OR status != 'Finished')
            ORDER BY CAST(complexity AS INTEGER) DESC,
                     CAST(number_of_stories AS INTEGER) DESC,
                     CAST(floor_area AS REAL) DESC, asset_id
        """, (simulation_name,))
        asset_ids = [row['asset_id'] for row in cursor.fetchall()]
        conn.executemany(f"""
            UPDATE {table_name}
            SET batch = ?, order_rank = ?, updated_at = CURRENT_TIMESTAMP
            WHERE simulation_name = ? AND asset_id = ?
        """, [(rank % total_batches, rank, simulation_name, aid)
              for rank, aid in enumerate(asset_ids)])
        
        conn.commit()
        logger.info(f"Distributed {total_assets} assets across {total_batches} batches")
        return True
        
    except Exception as e:
        logger.error(f"Error distributing assets to batches: {e}")
        return False


@retry_on_database_error
def get_status_stats(simulation_name: str, batch_id: int = None) -> Dict[str, int]:
    """Get status statistics for a simulation (optionally one batch -- Q/A D-02: per-batch
    progress previously reported whole-simulation counts and could exceed 100%)."""
    logger.debug(f'Getting status stats for simulation {simulation_name} batch {batch_id}')
    
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        where = "WHERE simulation_name = ?"
        params = [simulation_name]
        if batch_id is not None:
            where += " AND batch = ?"
            params.append(batch_id)
        cursor = conn.execute(f"""
            SELECT status, COUNT(*) as count 
            FROM {table_name} 
            {where}
            GROUP BY status
        """, params)
        
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
        
        # NULL batches are included on purpose: recovery renames never-distributed
        # rows via update_simulation_name(..., batch=None), which is only reachable
        # when None appears in this list (D-51; postgres never filtered them either).
        cursor = conn.execute(f"""
            SELECT DISTINCT batch FROM {table_name} 
            WHERE simulation_name = ?
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
def update_batch(asset_id: int, batch: int, simulation_name: str) -> bool:
    """Update batch for a specific asset in a specific run.

    simulation_name is REQUIRED: without it the UPDATE rewrites the batch of every concurrent
    run of that asset.
    """
    logger.debug(f'Updating asset {asset_id} to batch {batch}')
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")

        conn.execute(f"""
            UPDATE {table_name}
            SET batch = ?, updated_at = CURRENT_TIMESTAMP
            WHERE asset_id = ? AND simulation_name = ?
        """, (batch, asset_id, simulation_name))
        conn.commit()
        
        logger.debug(f"Asset {asset_id} batch updated to {batch}")
        return True
        
    except Exception as e:
        logger.error(f"Error updating batch: {e}")
        return False


@retry_on_database_error
def update_simulation_name(recovery_simulation_name: str, corrupted_simulation_name: str, batch_id: int) -> bool:
    """Update simulation name for assets in a specific batch, excluding finished assets."""
    logger.debug(f'Updating simulation name from {corrupted_simulation_name} to {recovery_simulation_name} for batch {batch_id}')
    
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        # simulation_name is half the key now, so this rename can collide; returning False makes
        # recover_UOsim delete every feature file. Fail loud.
        batch_pred = "batch IS NULL" if batch_id is None else "batch = ?"
        params = ([recovery_simulation_name, corrupted_simulation_name] if batch_id is None
                  else [recovery_simulation_name, batch_id, corrupted_simulation_name])
        conn.execute(f"""
            UPDATE {table_name}
            SET simulation_name = ?, updated_at = CURRENT_TIMESTAMP
            WHERE {batch_pred} AND simulation_name = ? AND (status IS NULL OR status != 'Finished')
        """, params)
        conn.commit()

        logger.debug(f"Simulation name updated from {corrupted_simulation_name} to {recovery_simulation_name} for batch {batch_id}")
        return True

    except Exception as e:
        logger.error(f"Error updating simulation name: {e}")
        raise


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
def get_asset_stats(simulation_name: str = None):
    """Per-asset rows matching the postgres shape (Q/A R-020): views.py iterates dicts with
    asset_id/batch/order_rank/complexity/timings; the old single aggregate dict KeyError'd the
    HPC stats export. None/'' means all simulations, like postgres (Q/A D-15)."""
    try:
        conn = get_sqlite_connection()
        table_name = os.environ.get("PGDATABASE", "powertwin")
        q = (f"SELECT asset_id, batch, order_rank, simulation_name, state, weather_file, "
             f"floor_area, number_of_stories, complexity, "
             f"COALESCE(uorun_time, 0) as uorun_time, COALESCE(uoprocess_time, 0) as uoprocess_time, "
             f"asset_name, subtype, status, COALESCE(total_time, 0) as total_time FROM {table_name}")
        params = []
        if simulation_name:
            q += " WHERE simulation_name = ?"
            params.append(simulation_name)
        q += " ORDER BY simulation_name, batch, order_rank"
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error getting asset stats: {e}")
        return []


def get_asset_stats_aggregate(simulation_name: str) -> Dict[str, Any]:
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


# Q/A D-14: no retry decorator here on purpose -- the body has filesystem side effects
# (timestamped preservation files, backup/restore/rmtree). A lock-retry would re-enter from the
# top, orphaning the previous attempt's artifacts. Locks inside are already bounded by
# busy_timeout; a genuine failure should surface, not loop.
def create_preservation_database(master_db_path: str, simulation_name: str) -> Tuple[str, int]:
    """
    Create a preservation database to save all 'Finished' assets before consolidation.
    This ensures completed work is never lost during the consolidation process.
    
    Returns:
        Tuple[str, int]: (preservation_db_path, preserved_count)
    """
    import time
    import shutil
    
    # Create preservation database path
    timestamp = int(time.time())
    base_dir = os.path.dirname(master_db_path)
    preservation_db_path = os.path.join(base_dir, f"preservation_{simulation_name}_{timestamp}.db")
    
    logger.info(f"Creating preservation database: {preservation_db_path}")
    
    try:
        # Connect to master database
        master_conn = sqlite3.connect(master_db_path)
        master_conn.row_factory = sqlite3.Row
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        # Get all finished assets from master
        finished_cursor = master_conn.execute(
            f"SELECT * FROM {table_name} WHERE simulation_name = ? AND status = 'Finished'",
            (simulation_name,)
        )
        finished_assets = finished_cursor.fetchall()
        
        if not finished_assets:
            logger.info("No finished assets found to preserve")
            master_conn.close()
            return preservation_db_path, 0
        
        # Create preservation database
        preserve_conn = sqlite3.connect(preservation_db_path)
        preserve_conn.row_factory = sqlite3.Row
        
        # Create table with same schema
        preserve_conn.execute(_table_ddl(table_name, if_not_exists=False))
        
        # Copy finished assets to preservation database
        preserved_count = 0
        for asset_row in finished_assets:
            asset_dict = dict(asset_row)
            columns = ', '.join(asset_dict.keys())
            placeholders = ', '.join(['?' for _ in asset_dict])
            
            preserve_conn.execute(
                f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})",
                list(asset_dict.values())
            )
            preserved_count += 1
        
        preserve_conn.commit()
        preserve_conn.close()
        master_conn.close()
        
        logger.info(f"✓ Preserved {preserved_count} finished assets in preservation database")
        return preservation_db_path, preserved_count
        
    except Exception as e:
        logger.error(f"Failed to create preservation database: {e}")
        # Clean up partial preservation database
        if os.path.exists(preservation_db_path):
            try:
                os.remove(preservation_db_path)
            except:
                pass
        raise

@retry_on_database_error
def merge_preservation_database(master_conn, preservation_db_path: str, simulation_name: str) -> int:
    """
    Merge preserved finished assets back into the master database.
    
    Args:
        master_conn: Active connection to master database
        preservation_db_path: Path to preservation database
        simulation_name: Name of the simulation
        
    Returns:
        int: Number of preserved assets merged back
    """
    if not os.path.exists(preservation_db_path):
        logger.info("No preservation database found to merge")
        return 0
    
    try:
        # Connect to preservation database
        preserve_conn = sqlite3.connect(preservation_db_path)
        preserve_conn.row_factory = sqlite3.Row
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        # Get all preserved assets
        preserve_cursor = preserve_conn.execute(
            f"SELECT * FROM {table_name} WHERE simulation_name = ?",
            (simulation_name,)
        )
        preserved_assets = preserve_cursor.fetchall()
        
        if not preserved_assets:
            logger.info("No preserved assets to merge back")
            preserve_conn.close()
            return 0
        
        # Merge preserved assets back to master
        merged_count = 0
        for asset_row in preserved_assets:
            asset_dict = dict(asset_row)
            columns = ', '.join(asset_dict.keys())
            placeholders = ', '.join(['?' for _ in asset_dict])
            
            # Use INSERT OR REPLACE to ensure finished assets take priority
            master_conn.execute(
                f"INSERT OR REPLACE INTO {table_name} ({columns}) VALUES ({placeholders})",
                list(asset_dict.values())
            )
            merged_count += 1
        
        preserve_conn.close()
        logger.info(f"✓ Merged {merged_count} preserved finished assets back to master")
        return merged_count
        
    except Exception as e:
        logger.error(f"Failed to merge preservation database: {e}")
        raise

# Q/A D-14: no retry decorator here on purpose -- the body has filesystem side effects
# (timestamped preservation files, backup/restore/rmtree). A lock-retry would re-enter from the
# top, orphaning the previous attempt's artifacts. Locks inside are already bounded by
# busy_timeout; a genuine failure should surface, not loop.
def consolidate_databases(simulation_name: str, node_dirs: List[str], master_db_path: str) -> bool:
    """
    Atomic consolidation of node-specific databases back to master database.
    Implements backup/rollback mechanism and preservation database to prevent data loss.
    Preserves ALL finished assets and verifies asset counts throughout process.
    """
    import time
    import shutil
    
    logger.info(f"Starting ATOMIC consolidation with PRESERVATION for simulation {simulation_name}")
    logger.info(f"Found {len(node_dirs)} node directories to process")
    
    # Create backup of master database before consolidation
    backup_path = f"{master_db_path}.backup_before_consolidation_{int(time.time())}"
    backup_created = False
    preservation_db_path = None
    preserved_finished_count = 0
    
    try:
        if os.path.exists(master_db_path):
            shutil.copy2(master_db_path, backup_path)
            backup_created = True
            logger.info(f"Created backup: {backup_path}")
    except Exception as e:
        logger.error(f"Failed to create backup: {e}")
        return False
    
    # Create preservation database for finished assets
    try:
        preservation_db_path, preserved_finished_count = create_preservation_database(master_db_path, simulation_name)
        if preserved_finished_count > 0:
            logger.info(f"✓ Preservation database created with {preserved_finished_count} finished assets")
        else:
            logger.info("No finished assets to preserve")
    except Exception as e:
        logger.error(f"Failed to create preservation database: {e}")
        if backup_created:
            try:
                shutil.copy2(backup_path, master_db_path)
                logger.error("Restored from backup due to preservation failure")
            except:
                pass
        return False
    
    # Pre-consolidation verification
    original_asset_count = 0
    original_status_counts = {}
    
    try:
        if os.path.exists(master_db_path):
            master_conn = sqlite3.connect(master_db_path)
            table_name = os.environ.get("PGDATABASE", "powertwin")
            
            # Get original counts
            count_cursor = master_conn.execute(
                f"SELECT COUNT(*) as count FROM {table_name} WHERE simulation_name = ?",
                (simulation_name,)
            )
            original_asset_count = count_cursor.fetchone()[0]
            
            # Get original status distribution
            status_cursor = master_conn.execute(
                f"SELECT status, COUNT(*) as count FROM {table_name} WHERE simulation_name = ? GROUP BY status",
                (simulation_name,)
            )
            for row in status_cursor:
                original_status_counts[row[0]] = row[1]
            
            master_conn.close()
            logger.info(f"Pre-consolidation: {original_asset_count} total assets")
            logger.info(f"Pre-consolidation status: {original_status_counts}")
            
    except Exception as e:
        logger.warning(f"Could not verify pre-consolidation state: {e}")
    
    # Collect all node data first (verification phase)
    node_data_summary = []
    total_node_records = 0
    union_keys = set()  # DISTINCT (asset_id, simulation_name) across node DBs + preserved (R-016)
    node_status_counts = {}
    
    base_dir = os.path.dirname(master_db_path)
    
    for node_dir in node_dirs:
        try:
            full_node_dir = os.path.join(base_dir, node_dir)
            node_db_path = None
            
            # Find the database file
            for file in os.listdir(full_node_dir):
                if file.endswith('.db'):
                    node_db_path = os.path.join(full_node_dir, file)
                    break
            
            if not node_db_path or not os.path.exists(node_db_path):
                logger.warning(f"No database found in {node_dir}")
                continue
            
            # Verify node database
            node_conn = sqlite3.connect(node_db_path)
            table_name = os.environ.get("PGDATABASE", "powertwin")

            # Force WAL checkpoint in case node used WAL mode on network FS
            try:
                node_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass  # No WAL file, that's fine

            # Check if table exists
            table_check = node_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,)
            ).fetchone()
            
            if not table_check:
                logger.info(f"No data table in {node_dir}")
                node_conn.close()
                continue
            
            # Count records for this simulation
            count_cursor = node_conn.execute(
                f"SELECT COUNT(*) as count FROM {table_name} WHERE simulation_name = ?",
                (simulation_name,)
            )
            node_record_count = count_cursor.fetchone()[0]
            
            # Get status distribution
            node_status_cursor = node_conn.execute(
                f"SELECT status, COUNT(*) as count FROM {table_name} WHERE simulation_name = ? GROUP BY status",
                (simulation_name,)
            )
            # collect the node's row KEYS so the expected count can be a DISTINCT union
            # (Q/A R-016: node DBs are seeded from master, so Finished rows can live in both
            # a node DB and the preservation set; a plain sum double-counts them and the
            # count check then rolls back a consolidation that was actually correct)
            # Scoped to the simulation: a node DB seeded via the zero-assigned-batches
            # fallback can carry foreign simulation rows; unscoped keys inflate the
            # expected count and roll back a correct consolidation (code-review).
            for krow in node_conn.execute(
                    f"SELECT asset_id, simulation_name FROM {table_name} WHERE simulation_name = ?",
                    (simulation_name,)):
                union_keys.add((krow[0], krow[1]))
            node_statuses = {}
            for row in node_status_cursor:
                node_statuses[row[0]] = row[1]
                node_status_counts[row[0]] = node_status_counts.get(row[0], 0) + row[1]
            
            node_conn.close()
            
            if node_record_count > 0:
                node_data_summary.append({
                    'node_dir': node_dir,
                    'db_path': node_db_path,
                    'record_count': node_record_count,
                    'status_counts': node_statuses
                })
                total_node_records += node_record_count
                logger.info(f"✓ {node_dir}: {node_record_count} records verified")
            
        except Exception as e:
            logger.error(f"Failed to verify node {node_dir}: {e}")
            # Rollback on verification failure
            if backup_created:
                try:
                    shutil.copy2(backup_path, master_db_path)
                    logger.info("Restored from backup due to verification failure")
                except:
                    pass
            # Clean up preservation database
            if preservation_db_path and os.path.exists(preservation_db_path):
                try:
                    os.remove(preservation_db_path)
                except:
                    pass
            return False
    
    logger.info(f"Verification complete: {total_node_records} total records from {len(node_data_summary)} nodes")
    logger.info(f"Node status distribution: {node_status_counts}")
    
    # Expected final count = DISTINCT (asset_id, simulation_name) across nodes UNION preserved
    # (Q/A R-016). The old node+preserved SUM assumed disjoint sets.
    if preservation_db_path and os.path.exists(preservation_db_path):
        pconn = sqlite3.connect(preservation_db_path)
        try:
            for krow in pconn.execute(
                    f"SELECT asset_id, simulation_name FROM {table_name} WHERE simulation_name = ?",
                    (simulation_name,)):
                union_keys.add((krow[0], krow[1]))
        finally:
            pconn.close()
    expected_final_count = len(union_keys)
    logger.info(f"Expected final count: {expected_final_count} ({total_node_records} from nodes + {preserved_finished_count} preserved)")
    
    if total_node_records == 0 and preserved_finished_count == 0:
        logger.info("No records to consolidate")
        if backup_created:
            os.remove(backup_path)
        if preservation_db_path and os.path.exists(preservation_db_path):
            os.remove(preservation_db_path)
        return True
    
    # Atomic consolidation phase
    try:
        master_conn = sqlite3.connect(master_db_path)
        master_conn.row_factory = sqlite3.Row
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        # Create table if needed. The master accumulates every simulation, so a pre-existing
        # one keyed on asset_id alone loses an earlier run whenever a later one repeats an
        # asset. Rekey before the transaction: it commits, and DDL cannot share one.
        master_conn.execute(_table_ddl(table_name))
        _rekey_if_needed(master_conn, table_name)

        # Begin atomic transaction
        master_conn.execute("BEGIN TRANSACTION")
        
        # Clear existing data for this simulation INSIDE transaction
        logger.info(f"Removing old data for simulation {simulation_name} (within transaction)")
        master_conn.execute(f"DELETE FROM {table_name} WHERE simulation_name = ?", (simulation_name,))
        
        consolidated_count = 0
        
        # Step 1: Consolidate each node's data
        logger.info("Phase 1: Consolidating node databases...")
        for node_info in node_data_summary:
            try:
                node_conn = sqlite3.connect(node_info['db_path'])
                node_conn.row_factory = sqlite3.Row
                
                # Get all simulation data from this node  
                node_cursor = node_conn.execute(
                    f"SELECT * FROM {table_name} WHERE simulation_name = ?",
                    (simulation_name,)
                )
                
                # Insert preserving original asset_ids and latest status
                for row in node_cursor:
                    row_dict = dict(row)
                    columns = ', '.join(row_dict.keys())
                    placeholders = ', '.join(['?' for _ in row_dict])
                    
                    # Insert with conflict resolution to preserve latest updates
                    master_conn.execute(f"""
                        INSERT OR REPLACE INTO {table_name} ({columns}) 
                        VALUES ({placeholders})
                    """, list(row_dict.values()))
                    
                    consolidated_count += 1
                
                node_conn.close()
                logger.info(f"✓ Consolidated {node_info['record_count']} records from {node_info['node_dir']}")
                
            except Exception as e:
                logger.error(f"Failed to consolidate {node_info['node_dir']}: {e}")
                # Rollback the entire transaction
                master_conn.execute("ROLLBACK")
                master_conn.close()
                
                # Restore backup
                if backup_created:
                    shutil.copy2(backup_path, master_db_path)
                    logger.error("Restored from backup due to consolidation failure")
                
                # Clean up preservation database
                if preservation_db_path and os.path.exists(preservation_db_path):
                    try:
                        os.remove(preservation_db_path)
                    except:
                        pass
                return False
        
        # Step 2: Merge preserved finished assets back
        logger.info("Phase 2: Merging preserved finished assets...")
        merged_preserved_count = merge_preservation_database(master_conn, preservation_db_path, simulation_name)
        
        # Final verification before commit
        final_count_cursor = master_conn.execute(
            f"SELECT COUNT(*) as count FROM {table_name} WHERE simulation_name = ?",
            (simulation_name,)
        )
        final_count = final_count_cursor.fetchone()[0]
        
        # Verify total count matches expectation
        if final_count != expected_final_count:
            logger.error(f"CRITICAL: Asset count mismatch after consolidation: expected {expected_final_count}, got {final_count}")
            logger.error(f"Node records: {total_node_records}, Preserved: {preserved_finished_count}, Merged: {merged_preserved_count}")
            master_conn.execute("ROLLBACK")
            master_conn.close()
            
            if backup_created:
                shutil.copy2(backup_path, master_db_path)
                logger.error("Restored from backup due to count mismatch")
            
            # Clean up preservation database
            if preservation_db_path and os.path.exists(preservation_db_path):
                try:
                    os.remove(preservation_db_path)
                except:
                    pass
            return False
        
        # Verify finished assets are preserved
        if preserved_finished_count > 0:
            finished_count_cursor = master_conn.execute(
                f"SELECT COUNT(*) as count FROM {table_name} WHERE simulation_name = ? AND status = 'Finished'",
                (simulation_name,)
            )
            final_finished_count = finished_count_cursor.fetchone()[0]
            
            # Should have at least as many finished assets as we preserved (nodes might have added more)
            if final_finished_count < preserved_finished_count:
                logger.error(f"CRITICAL: Finished assets lost! Expected at least {preserved_finished_count}, got {final_finished_count}")
                master_conn.execute("ROLLBACK")
                master_conn.close()
                
                if backup_created:
                    shutil.copy2(backup_path, master_db_path)
                    logger.error("Restored from backup due to finished asset loss")
                return False
            
            logger.info(f"✓ Finished asset preservation verified: {final_finished_count} finished assets in final database")
        
        # Commit transaction
        master_conn.execute("COMMIT")
        master_conn.close()
        
        logger.info(f"✓ ATOMIC CONSOLIDATION WITH PRESERVATION SUCCESSFUL")
        logger.info(f"✓ Total records consolidated: {consolidated_count}")
        logger.info(f"✓ Preserved finished assets merged: {merged_preserved_count}")
        logger.info(f"✓ Final asset count verified: {final_count}")
        
        # Clean up backup and preservation database on success
        if backup_created:
            os.remove(backup_path)
            logger.info("Removed backup after successful consolidation")
        
        if preservation_db_path and os.path.exists(preservation_db_path):
            os.remove(preservation_db_path)
            logger.info("Removed preservation database after successful merge")
            
        return True
        
    except Exception as e:
        logger.error(f"Critical error in atomic consolidation: {e}")
        
        # Restore from backup
        if backup_created:
            try:
                shutil.copy2(backup_path, master_db_path)
                logger.error("Restored from backup due to critical error")
            except Exception as restore_error:
                logger.error(f"FAILED TO RESTORE BACKUP: {restore_error}")
        
        # Clean up preservation database
        if preservation_db_path and os.path.exists(preservation_db_path):
            try:
                os.remove(preservation_db_path)
            except:
                pass
        
        return False