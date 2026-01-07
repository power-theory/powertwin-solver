"""
Database environment detection and configuration for PowerTwin Solver.
Routes to SQLite (HPC) or PostgreSQL (Docker) based on environment.
"""

import os
from modules.utils import initialize_logger
from ..utils.hpc_environment import is_hpc_environment, get_hpc_info, should_use_distributed_database

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Database Environment', external_log_dir)

def get_database_config():
    """
    Get database configuration based on environment.
    
    Returns:
        dict: Database configuration
    """
    # Check for explicit database type override
    db_type_override = os.environ.get('DATABASE_TYPE', '').lower()
    
    if db_type_override == 'postgresql':
        # Force PostgreSQL mode
        return {
            'type': 'postgresql',
            'host': os.environ.get('PGHOST', 'localhost'),
            'port': int(os.environ.get('PGPORT', 5432)),
            'user': os.environ.get('PGUSER', 'postgres'),
            'password': os.environ.get('PGPASSWORD', ''),
            'database': os.environ.get('PGDATABASE', 'powertwin'),
            'distributed': False
        }
    elif db_type_override == 'sqlite':
        # Force SQLite mode
        sqlite_path = os.environ.get('SQLITE_DB_PATH', '/tmp/powertwin.db')
        return {
            'type': 'sqlite',
            'path': sqlite_path,
            'distributed': should_use_distributed_database()
        }
    else:
        # Auto-detect based on environment
        if is_hpc_environment():
            # HPC environment - use SQLite
            sqlite_path = os.environ.get('SQLITE_DB_PATH', '/tmp/powertwin.db')
            return {
                'type': 'sqlite',
                'path': sqlite_path,
                'distributed': should_use_distributed_database()
            }
        else:
            # Docker/local environment - use PostgreSQL
            return {
                'type': 'postgresql',
                'host': os.environ.get('PGHOST', 'localhost'),
                'port': int(os.environ.get('PGPORT', 5432)),
                'user': os.environ.get('PGUSER', 'postgres'),
                'password': os.environ.get('PGPASSWORD', ''),
                'database': os.environ.get('PGDATABASE', 'powertwin'),
                'distributed': False
            }

def log_database_environment():
    """
    Log database environment configuration for debugging.
    """
    config = get_database_config()
    hpc_info = get_hpc_info()
    
    logger.info("="*60)
    logger.info("POWERTWIN DATABASE CONFIGURATION")
    logger.info("="*60)
    logger.info(f"Environment: {'HPC (SLURM)' if hpc_info['is_hpc'] else 'Local/Docker'}")
    logger.info(f"Database Type: {config['type'].upper()}")
    
    if config['type'] == 'sqlite':
        logger.info(f"SQLite Path: {config['path']}")
        logger.info(f"Distributed Mode: {'Yes' if config['distributed'] else 'No'}")
        if config['distributed']:
            logger.info(f"Process Rank: {hpc_info['rank']}")
            logger.info(f"Node: {hpc_info['node_name']}")
    else:
        logger.info(f"PostgreSQL Host: {config['host']}:{config['port']}")
        logger.info(f"Database: {config['database']}")
    
    logger.info("="*60)