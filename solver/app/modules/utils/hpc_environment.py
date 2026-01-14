"""
Centralized HPC environment detection for PowerTwin Solver.
This module provides the single source of truth for HPC environment detection.
"""

import os
import socket
from modules.utils import initialize_logger

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('HPC Environment', external_log_dir)

def is_hpc_environment():
    return os.environ.get('SLURM_JOB_ID') is not None

def get_hpc_info():
    
    hpc_info = {
        'is_hpc': is_hpc_environment(),
        'job_id': os.environ.get('SLURM_JOB_ID'),
        'rank': int(os.environ.get('SLURM_PROCID', '0')),
        'total_tasks': int(os.environ.get('SLURM_NTASKS', '1')),
        'nodes': int(os.environ.get('SLURM_JOB_NUM_NODES', '1')),
        'node_id': int(os.environ.get('SLURM_NODEID', '0')),
        'node_name': socket.gethostname(),
        'tasks_per_node': int(os.environ.get('SLURM_NTASKS_PER_NODE', '1')),
        'cpus_per_task': int(os.environ.get('SLURM_CPUS_PER_TASK', '1'))
    }
    
    # Determine if this is the master process (rank 0)
    hpc_info['is_master'] = hpc_info['rank'] == 0
    
    # Log HPC environment details for debugging
    if hpc_info['is_hpc']:
        logger.debug(f"HPC Environment Detected - Job ID: {hpc_info['job_id']}, "
                    f"Rank: {hpc_info['rank']}/{hpc_info['total_tasks']}, "
                    f"Node: {hpc_info['node_name']} ({hpc_info['node_id']}/{hpc_info['nodes']})")
    else:
        logger.debug("Local/Docker environment detected")
    
    return hpc_info


def log_environment_summary():
    hpc_info = get_hpc_info()
    
    if hpc_info['is_hpc']:
        logger.info("="*60)
        logger.info("POWERTWIN HPC ENVIRONMENT SUMMARY")
        logger.info("="*60)
        logger.info(f"SLURM Job ID: {hpc_info['job_id']}")
        logger.info(f"Process Rank: {hpc_info['rank']} of {hpc_info['total_tasks']}")
        logger.info(f"Node: {hpc_info['node_name']} ({hpc_info['node_id']} of {hpc_info['nodes']})")
        logger.info(f"Distributed SQLite: {'Enabled' if is_hpc_environment() else 'Disabled'}")
        logger.info(f"Master Process: {'Yes' if hpc_info['is_master'] else 'No'}")
        logger.info("="*60)
    else:
        logger.info("Running in local/Docker environment")