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
    
    def ensure_db_exists(self):
        """Ensure database directory exists and copy/create master DB in HPC mode."""
        # Ensure the directory exists
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
            logger.debug(f"Created database directory: {db_dir}")
        
        # In parallel step (Step 3)
        if hasattr(self, 'is_parallel_step') and self.is_parallel_step:
            if not os.path.exists(self.db_path):
                if os.path.exists(self.master_db_path):
                    # Copy existing master database
                    try:
                        logger.info(f"Copying master database from {self.master_db_path} to {self.db_path}")
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