"""
Distributed SQLite management for HPC environments.
Each node/core maintains its own SQLite database to avoid contention.
Databases are consolidated at the end of simulation or during recovery.
"""

import os
import sqlite3
import glob
from modules.utils import initialize_logger
from modules.utils.hpc_environment import get_hpc_info, is_hpc_environment
from modules.database.database_environment import get_database_config

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('DistributedSQLite', external_log_dir)

class DistributedSQLiteManager:
    def __init__(self):
        if not is_hpc_environment():
            raise RuntimeError("DistributedSQLiteManager should only be used in HPC environments")
            
        self.db_config = get_database_config()
        self.hpc_info = get_hpc_info()
        
        self.base_db_path = self.db_config.get('path', '/tmp/powertwin.db')
        self.base_db_dir = os.path.dirname(self.base_db_path)
        
        # Create unique database path for this process
        self.local_db_path = os.path.join(
            self.base_db_dir, 
            f"powertwin_node{self.hpc_info['node_id']}_rank{self.hpc_info['rank']}.db"
        )
        
        # Master database path
        self.master_db_path = self.base_db_path
        
        logger.info(f"Initialized distributed SQLite: "
                   f"node={self.hpc_info['node_name']}, "
                   f"rank={self.hpc_info['rank']}, "
                   f"local_db={self.local_db_path}")
    
    def get_local_db_path(self):
        """Get the local database path for this process."""
        return self.local_db_path
    
    def get_master_db_path(self):
        """Get the master database path."""
        return self.master_db_path
    
    def ensure_local_db_exists(self):
        """Ensure local database exists and is initialized."""
        if not os.path.exists(self.local_db_path):
            # Create local database directory if needed
            os.makedirs(os.path.dirname(self.local_db_path), exist_ok=True)
            
            # Copy schema from master or create new
            self._initialize_local_db()
            logger.info(f"Created local database: {self.local_db_path}")
    
    def _initialize_local_db(self):
        """Initialize local database with schema."""
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        conn = sqlite3.connect(self.local_db_path)
        conn.execute('PRAGMA journal_mode=WAL')  # Enable WAL mode for better concurrency
        conn.execute('PRAGMA synchronous=NORMAL')  # Balance safety and performance
        
        # Create the assets table
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                asset_id TEXT PRIMARY KEY,
                batch INTEGER,
                order_rank INTEGER,
                simulation_name TEXT,
                state TEXT,
                weather_file TEXT,
                floor_area REAL,
                number_of_stories INTEGER,
                complexity INTEGER,
                uorun_time REAL,
                uoprocess_time REAL,
                asset_name TEXT,
                subtype TEXT,
                status TEXT,
                total_time REAL,
                node_name TEXT DEFAULT '{self.hpc_info['node_name']}',
                rank INTEGER DEFAULT {self.hpc_info['rank']},
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create indexes for performance
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_simulation ON {table_name}(simulation_name)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_batch ON {table_name}(batch)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_status ON {table_name}(status)")
        
        conn.commit()
        conn.close()
    
    def copy_master_data_to_local(self, simulation_name):
        """Copy relevant data from master database to local database."""
        if not os.path.exists(self.master_db_path):
            logger.warning(f"Master database not found at {self.master_db_path}")
            return
        
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        try:
            # Connect to both databases
            master_conn = sqlite3.connect(self.master_db_path)
            master_conn.row_factory = sqlite3.Row
            
            local_conn = sqlite3.connect(self.local_db_path)
            
            # Get assets for this simulation
            cursor = master_conn.execute(
                f"SELECT * FROM {table_name} WHERE simulation_name = ?", 
                (simulation_name,)
            )
            
            assets = cursor.fetchall()
            
            if assets:
                # Insert into local database
                placeholders = ','.join(['?' for _ in assets[0].keys()])
                columns = ','.join(assets[0].keys())
                
                for asset in assets:
                    local_conn.execute(
                        f"INSERT OR REPLACE INTO {table_name} ({columns}) VALUES ({placeholders})",
                        tuple(asset)
                    )
                
                local_conn.commit()
                logger.info(f"Copied {len(assets)} assets from master to local database")
            
            master_conn.close()
            local_conn.close()
            
        except Exception as e:
            logger.error(f"Error copying master data to local: {e}")
    
    def consolidate_databases(self, simulation_name=None):
        """Consolidate all distributed databases into the master database."""
        if not self._is_master_process():
            logger.info("Not master process, skipping consolidation")
            return False
        
        logger.info("Starting database consolidation...")
        
        # Find all distributed database files
        pattern = os.path.join(self.base_db_dir, "powertwin_node*_rank*.db")
        db_files = glob.glob(pattern)
        
        if not db_files:
            logger.warning("No distributed database files found for consolidation")
            return False
        
        logger.info(f"Found {len(db_files)} distributed databases to consolidate")
        
        table_name = os.environ.get("PGDATABASE", "powertwin")
        
        try:
            # Ensure master database exists
            self._ensure_master_db_exists()
            
            master_conn = sqlite3.connect(self.master_db_path)
            master_conn.execute('PRAGMA journal_mode=WAL')
            
            consolidated_count = 0
            
            for db_file in db_files:
                try:
                    logger.debug(f"Consolidating data from: {db_file}")
                    
                    # Attach the distributed database
                    master_conn.execute(f"ATTACH DATABASE '{db_file}' AS temp_db")
                    
                    # Copy data with conflict resolution
                    if simulation_name:
                        # Only consolidate specific simulation
                        cursor = master_conn.execute(
                            f"SELECT * FROM temp_db.{table_name} WHERE simulation_name = ?",
                            (simulation_name,)
                        )
                    else:
                        # Consolidate all data
                        cursor = master_conn.execute(f"SELECT * FROM temp_db.{table_name}")
                    
                    rows = cursor.fetchall()
                    
                    for row in rows:
                        # Insert or update with latest timestamp wins
                        master_conn.execute(f"""
                            INSERT OR REPLACE INTO {table_name} 
                            SELECT * FROM (SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            WHERE NOT EXISTS (
                                SELECT 1 FROM {table_name} 
                                WHERE asset_id = ? AND updated_at > ?
                            )
                        """, (*row, row[0], row[18] if len(row) > 18 else ''))
                        
                        consolidated_count += 1
                    
                    # Detach the database
                    master_conn.execute("DETACH DATABASE temp_db")
                    
                except Exception as e:
                    logger.error(f"Error consolidating {db_file}: {e}")
                    try:
                        master_conn.execute("DETACH DATABASE temp_db")
                    except:
                        pass
            
            master_conn.commit()
            master_conn.close()
            
            logger.info(f"Successfully consolidated {consolidated_count} records into master database")
            
            # Clean up distributed databases after successful consolidation
            self._cleanup_distributed_databases(db_files)
            
            return True
            
        except Exception as e:
            logger.error(f"Error during database consolidation: {e}")
            return False
    
    def _ensure_master_db_exists(self):
        """Ensure master database exists with proper schema."""
        if not os.path.exists(self.master_db_path):
            os.makedirs(os.path.dirname(self.master_db_path), exist_ok=True)
            
            table_name = os.environ.get("PGDATABASE", "powertwin")
            
            conn = sqlite3.connect(self.master_db_path)
            conn.execute('PRAGMA journal_mode=WAL')
            
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    asset_id TEXT PRIMARY KEY,
                    batch INTEGER,
                    order_rank INTEGER,
                    simulation_name TEXT,
                    state TEXT,
                    weather_file TEXT,
                    floor_area REAL,
                    number_of_stories INTEGER,
                    complexity INTEGER,
                    uorun_time REAL,
                    uoprocess_time REAL,
                    asset_name TEXT,
                    subtype TEXT,
                    status TEXT,
                    total_time REAL,
                    node_name TEXT,
                    rank INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.commit()
            conn.close()
            
            logger.info(f"Created master database: {self.master_db_path}")
    
    def _is_master_process(self):
        """Check if this is the master process."""
        return self.hpc_info['is_master']
    
    def _cleanup_distributed_databases(self, db_files):
        """Clean up distributed database files after consolidation."""
        for db_file in db_files:
            try:
                os.remove(db_file)
                # Also remove WAL and SHM files if they exist
                wal_file = db_file + '-wal'
                shm_file = db_file + '-shm'
                
                if os.path.exists(wal_file):
                    os.remove(wal_file)
                if os.path.exists(shm_file):
                    os.remove(shm_file)
                    
                logger.debug(f"Cleaned up distributed database: {db_file}")
                
            except Exception as e:
                logger.warning(f"Could not clean up {db_file}: {e}")
    
    def get_available_databases(self):
        """Get list of available distributed databases."""
        pattern = os.path.join(self.base_db_dir, "powertwin_node*_rank*.db")
        return glob.glob(pattern)

# Global instance
_distributed_manager = None

def get_distributed_manager():
    """Get the global distributed SQLite manager instance."""
    global _distributed_manager
    if _distributed_manager is None:
        _distributed_manager = DistributedSQLiteManager()
    return _distributed_manager