"""
SQLite manager for PowerTwin Solver.
Provides single SQLite database configuration with HPC node-specific support.
"""

import os
import shutil
import socket
from modules.utils import initialize_logger

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('SQLiteManager', external_log_dir)

class SQLiteManager:
    def __init__(self):
        """Initialize SQLite manager with node-specific database path in HPC mode."""
        base_db_path = os.environ.get("SQLITE_DB_PATH", "/tmp/powertwin.db")
        
        # Check if we're in HPC mode with SLURM
        slurm_job_id = os.environ.get('SLURM_JOB_ID')
        slurm_procid = os.environ.get('SLURM_PROCID', os.environ.get('PMI_RANK'))
        slurm_nodeid = os.environ.get('SLURM_NODEID')
        
        # Determine if this is a parallel processing step (Step 3) vs setup steps (Steps 1-2)
        # Use explicit environment variable set by SLURM script
        powertwin_step = os.environ.get('POWERTWIN_STEP', '')
        is_parallel_step = powertwin_step == 'parallel'
        
        if is_parallel_step:
            # Step 3 - Parallel processing: create node-specific database path
            base_dir = os.path.dirname(base_db_path)
            base_name = os.path.splitext(os.path.basename(base_db_path))[0]
            ext = os.path.splitext(os.path.basename(base_db_path))[1]
            
            node_name = socket.gethostname().split('.')[0]  # Get short hostname
            node_db_dir = os.path.join(base_dir, f"node_{slurm_nodeid}_{node_name}")
            self.db_path = os.path.join(node_db_dir, f"{base_name}_node_{slurm_nodeid}{ext}")
            self.master_db_path = base_db_path
            self.is_hpc_mode = True
            self.is_parallel_step = True
            self.node_id = int(slurm_nodeid)
            self.node_name = node_name
            
            logger.info(f"Initialized SQLite manager for parallel step: node={self.node_name}, node_id={self.node_id}")
            logger.info(f"Master DB: {self.master_db_path}")
            logger.info(f"Node DB: {self.db_path}")
        elif slurm_job_id:
            # Steps 1-2 - Setup steps in HPC: use master database directly
            self.db_path = base_db_path
            self.master_db_path = base_db_path
            self.is_hpc_mode = True
            self.is_parallel_step = False
            self.node_id = 0
            self.node_name = socket.gethostname().split('.')[0]
            
            logger.info(f"Initialized SQLite manager for HPC setup step: master_db={self.db_path}")
        else:
            # Local mode: use original path
            self.db_path = base_db_path
            self.master_db_path = base_db_path
            self.is_hpc_mode = False
            self.is_parallel_step = False
            self.node_id = 0
            self.node_name = socket.gethostname()
            
            logger.info(f"Initialized SQLite manager in local mode: db={self.db_path}")
    
    def get_db_path(self):
        """Get the database path (node-specific in HPC mode)."""
        return self.db_path
    
    def get_master_db_path(self):
        """Get the master database path."""
        return self.master_db_path
    
    def ensure_db_exists(self, simulation_name, assigned_batches=None):
        """
        Ensure database directory exists and copy only assigned batches in HPC mode.
        
        Args:
            simulation_name: Name of the simulation
            assigned_batches: List of batch numbers assigned to this node
        """
        # Ensure the directory exists
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
            logger.debug(f"Created database directory: {db_dir}")
        
        # In parallel step (Step 3)
        if hasattr(self, 'is_parallel_step') and self.is_parallel_step:
            if not os.path.exists(self.db_path):
                if os.path.exists(self.master_db_path):
                    if assigned_batches:
                        # Copy only assigned batches from master database
                        try:
                            logger.info(f"Copying {len(assigned_batches)} assigned batches from master database")
                            logger.debug(f"Assigned batches: {assigned_batches}")
                            
                            success = self._copy_assigned_batches(simulation_name, assigned_batches)
                            if success:
                                logger.info(f"Node {self.node_name} successfully copied assigned batches")
                            else:
                                logger.error(f"Failed to copy assigned batches to node {self.node_name}")
                                return False
                                
                        except Exception as e:
                            logger.error(f"Failed to copy assigned batches to node {self.node_name}: {e}")
                            return False
                    else:
                        # Fallback: copy entire master database if no specific batches assigned
                        try:
                            logger.warning(f"No specific batches assigned, copying entire master database")
                            shutil.copy2(self.master_db_path, self.db_path)
                            os.chmod(self.db_path, 0o664)
                            logger.info(f"Node {self.node_name} successfully copied master database")
                        except Exception as e:
                            logger.error(f"Failed to copy master database to node {self.node_name}: {e}")
                            return False
                else:
                    # Create new database if master doesn't exist
                    logger.warning(f"Master database not found at {self.master_db_path}, creating new database for node {self.node_name}")
                    # Database will be created automatically on first use by SQLite operations
        else:
            # Steps 1-2: ensure master database can be created
            logger.debug(f"Setup step - master database will be created at {self.db_path}")
                
        return True
    
    def _copy_assigned_batches(self, simulation_name, assigned_batches):
        """
        Copy only the assigned batches from master database to node database.
        Uses file-based coordination to prevent race conditions during master DB access.
        
        Args:
            simulation_name: Name of the simulation
            assigned_batches: List of batch numbers to copy
        """
        import fcntl
        import time
        
        # File-based coordination for master database access
        base_dir = os.path.dirname(self.master_db_path)
        coord_file = os.path.join(base_dir, f"{simulation_name}_batch_copy_coordination.lock")
        
        max_wait_time = 300  # 5 minutes maximum wait
        start_time = time.time()
        
        try:
            # Create coordination file if it doesn't exist
            with open(coord_file, 'w') as f:
                f.write(f"Coordination file for {simulation_name} batch copying\n")
        except Exception as e:
            logger.warning(f"Could not create coordination file {coord_file}: {e}")
            # Continue without coordination - fallback to original logic
            return self._copy_assigned_batches_fallback(simulation_name, assigned_batches)
        
        # Wait for our turn to access master database
        while time.time() - start_time < max_wait_time:
            try:
                with open(coord_file, 'r+') as lock_file:
                    try:
                        # Try to acquire exclusive lock with timeout
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        
                        logger.info(f"Node {self.node_name}: Acquired master database access lock")
                        
                        # Perform the actual batch copying while holding the lock
                        success = self._copy_assigned_batches_with_lock(simulation_name, assigned_batches)
                        
                        # Lock is automatically released when file is closed
                        logger.info(f"Node {self.node_name}: Released master database access lock")
                        
                        return success
                        
                    except IOError:
                        # Lock is held by another process, wait and retry
                        wait_time = 2 + (self.node_id * 0.5)  # Stagger retry times by node
                        logger.info(f"Node {self.node_name}: Master DB locked, waiting {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                        
            except Exception as e:
                logger.error(f"Error during coordination: {e}")
                break
        
        # If coordination failed, fall back to original method
        logger.warning(f"Node {self.node_name}: Coordination timeout, falling back to direct copy")
        return self._copy_assigned_batches_fallback(simulation_name, assigned_batches)
    
    def _copy_assigned_batches_with_lock(self, simulation_name, assigned_batches):
        """
        Perform batch copying while holding coordination lock.
        """
        return self._copy_assigned_batches_fallback(simulation_name, assigned_batches)
        
    def _copy_assigned_batches_fallback(self, simulation_name, assigned_batches):
        """
        Fallback method to copy assigned batches (original implementation).
        
        Args:
            simulation_name: Name of the simulation
            assigned_batches: List of batch numbers to copy
        """
        try:
            # Import here to avoid circular imports
            import sqlite3
            
            # Pre-verify master database exists and is accessible
            if not os.path.exists(self.master_db_path):
                logger.error(f"Master database not found at {self.master_db_path}")
                return False
            
            # Create the node database with same schema
            node_conn = sqlite3.connect(self.db_path)
            node_conn.row_factory = sqlite3.Row
            
            # Enable WAL mode for better concurrency
            node_conn.execute("PRAGMA journal_mode=WAL")
            node_conn.execute("PRAGMA synchronous=NORMAL")
            node_conn.execute("PRAGMA busy_timeout=30000")
            
            # Connect to master database with timeout
            master_conn = sqlite3.connect(self.master_db_path, timeout=30.0)
            master_conn.row_factory = sqlite3.Row
            
            # Enable optimizations for read operations
            master_conn.execute("PRAGMA busy_timeout=30000")
            master_conn.execute("PRAGMA temp_store=MEMORY")
            
            try:
                table_name = os.environ.get("PGDATABASE", "powertwin")
                
                # Verify master database has data for this simulation
                master_count_cursor = master_conn.execute(
                    f"SELECT COUNT(*) as count FROM {table_name} WHERE simulation_name = ?",
                    (simulation_name,)
                )
                master_total_count = master_count_cursor.fetchone()['count']
                
                if master_total_count == 0:
                    logger.error(f"No data found for simulation {simulation_name} in master database")
                    return False
                
                # Verify assigned batches exist in master
                batch_placeholders = ','.join(['?' for _ in assigned_batches])
                batch_check_query = f"""
                    SELECT COUNT(*) as count FROM {table_name} 
                    WHERE simulation_name = ? AND batch IN ({batch_placeholders})
                """
                batch_params = [simulation_name] + list(assigned_batches)
                
                batch_count_cursor = master_conn.execute(batch_check_query, batch_params)
                expected_count = batch_count_cursor.fetchone()['count']
                
                if expected_count == 0:
                    logger.error(f"No data found for assigned batches {assigned_batches} in simulation {simulation_name}")
                    return False
                
                logger.info(f"Node {self.node_name}: Expecting to copy {expected_count} records from {len(assigned_batches)} batches")
                
                # Get table schema from master
                schema_cursor = master_conn.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table_name}'")
                schema_row = schema_cursor.fetchone()
                
                if not schema_row:
                    logger.error(f"Table {table_name} not found in master database")
                    return False
                    
                # Create table in node database
                node_conn.execute(schema_row['sql'])
                
                # Begin transaction for atomic copying
                node_conn.execute("BEGIN TRANSACTION")
                
                # Copy only assigned batches with verification
                query = f"""
                    SELECT * FROM {table_name} 
                    WHERE simulation_name = ? AND batch IN ({batch_placeholders})
                """
                
                master_cursor = master_conn.execute(query, batch_params)
                
                # Get column names for insert
                columns = [description[0] for description in master_cursor.description]
                column_names = ','.join(columns)
                placeholders = ','.join(['?' for _ in columns])
                
                insert_query = f"INSERT INTO {table_name} ({column_names}) VALUES ({placeholders})"
                
                # Copy rows and track status preservation
                copied_count = 0
                status_counts = {}
                asset_ids_copied = set()
                
                for row in master_cursor:
                    try:
                        node_conn.execute(insert_query, tuple(row))
                        copied_count += 1
                        
                        # Track asset_id to ensure no duplicates
                        if 'asset_id' in columns:
                            asset_id_idx = columns.index('asset_id')
                            asset_id = row[asset_id_idx]
                            if asset_id in asset_ids_copied:
                                logger.warning(f"Duplicate asset_id {asset_id} found during copy")
                            asset_ids_copied.add(asset_id)
                        
                        # Track status distribution for verification
                        if 'status' in columns:
                            status_idx = columns.index('status')
                            status = row[status_idx]
                            status_counts[status] = status_counts.get(status, 0) + 1
                            
                    except Exception as e:
                        logger.error(f"Error inserting row: {e}")
                        node_conn.execute("ROLLBACK")
                        return False
                
                # Verify we copied exactly what we expected
                if copied_count != expected_count:
                    logger.error(f"Asset count mismatch: expected {expected_count}, copied {copied_count}")
                    node_conn.execute("ROLLBACK")
                    return False
                
                # Commit transaction
                node_conn.execute("COMMIT")
                
                logger.info(f"✓ Node {self.node_name}: Successfully copied {copied_count} records for batches {assigned_batches}")
                if status_counts:
                    status_summary = ', '.join([f"{status}: {count}" for status, count in status_counts.items()])
                    logger.info(f"Status distribution in copied data - {status_summary}")
                
                return True
                
            finally:
                master_conn.close()
                node_conn.close()
                
        except Exception as e:
            logger.error(f"Error copying assigned batches: {e}")
            return False
    
    def is_hpc_environment(self):
        """Check if running in HPC mode."""
        return self.is_hpc_mode
    
    def is_parallel_processing_step(self):
        """Check if this is the parallel processing step (Step 3)."""
        return hasattr(self, 'is_parallel_step') and self.is_parallel_step
    
    def get_node_info(self):
        """Get node information."""
        return {
            'node_id': self.node_id,
            'node_name': self.node_name,
            'is_hpc': self.is_hpc_mode
        }
    
    def consolidate_node_databases(self, simulation_name):
        """Consolidate all node databases back to master database after simulation."""
        if not self.is_hpc_mode:
            logger.debug("Not in HPC mode, no consolidation needed")
            return True
            
        base_dir = os.path.dirname(self.master_db_path)
        
        # Find all node database directories
        try:
            node_dirs = [d for d in os.listdir(base_dir) 
                        if os.path.isdir(os.path.join(base_dir, d)) and d.startswith('node_')]
        except OSError as e:
            logger.error(f"Error accessing base directory {base_dir}: {e}")
            return False
        
        if not node_dirs:
            logger.info("No node databases found to consolidate")
            return True
            
        logger.info(f"Consolidating {len(node_dirs)} node databases to master")
        
        try:
            # Import consolidation logic here to avoid circular imports
            from .sqlite_operations import consolidate_databases
            return consolidate_databases(simulation_name, node_dirs, self.master_db_path)
        except Exception as e:
            logger.error(f"Failed to consolidate databases: {e}")
            return False

# Global instance
_sqlite_manager = None

def get_sqlite_manager():
    """Get global SQLiteManager instance."""
    global _sqlite_manager
    if _sqlite_manager is None:
        _sqlite_manager = SQLiteManager()
    return _sqlite_manager