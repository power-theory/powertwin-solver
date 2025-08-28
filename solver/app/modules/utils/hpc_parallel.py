import os

from mpi4py import MPI
from joblib import Parallel, delayed, parallel_backend
from modules.utils import initialize_logger

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('HPC Parallel', external_log_dir)

def get_hpc_environment():
    """
    Detect if running in HPC environment and get MPI configuration
    """
    hpc_env = {
        'is_hpc': False,
        'rank': 0,
        'size': 1,
        'is_master': True,
        'slurm_job_id': None,
        'slurm_nodes': None,
        'slurm_ntasks': None
    }
    
    # Check for SLURM environment variables
    slurm_job_id = os.environ.get('SLURM_JOB_ID')
    slurm_nodes = os.environ.get('SLURM_JOB_NUM_NODES')
    slurm_ntasks = os.environ.get('SLURM_NTASKS')
    
    if slurm_job_id:
        hpc_env['is_hpc'] = True
        hpc_env['slurm_job_id'] = slurm_job_id
        hpc_env['slurm_nodes'] = int(slurm_nodes) if slurm_nodes else 1
        hpc_env['slurm_ntasks'] = int(slurm_ntasks) if slurm_ntasks else 1
        
        comm = MPI.COMM_WORLD
        hpc_env['rank'] = comm.Get_rank()
        hpc_env['size'] = comm.Get_size()
        hpc_env['is_master'] = hpc_env['rank'] == 0
        
    return hpc_env

def run_parallel_batches(batch_function, batch_range, simulation_dir, local_dir, simulation_name, hpc_mode=False):
    """
    Run batch processing either with MPI (HPC mode) or joblib (local mode)
    """
    hpc_env = get_hpc_environment()
    
    if hpc_mode and hpc_env['is_hpc']:
        logger.info(f"Running in HPC mode: Rank {hpc_env['rank']}/{hpc_env['size']}")
        return _run_mpi_parallel(batch_function, batch_range, simulation_dir, local_dir, simulation_name, hpc_env)
    else:
        if hpc_mode:
            logger.warning("HPC mode requested but MPI/SLURM not available, falling back to joblib")
        logger.info("Running in local mode with joblib")
        return _run_joblib_parallel(batch_function, batch_range, simulation_dir, local_dir, simulation_name)

def _run_mpi_parallel(batch_function, batch_range, simulation_dir, local_dir, simulation_name, hpc_env):
    """
    MPI-based parallel execution for HPC clusters
    """
    comm = MPI.COMM_WORLD
    rank = hpc_env['rank']
    size = hpc_env['size']
    
    simulation_dir = os.path.join(simulation_dir, f'node_{rank}')
    local_dir = os.path.join(local_dir, f'node_{rank}')
    os.makedirs(local_dir, exist_ok=True)
    os.makedirs(simulation_dir, exist_ok=True)

    # Distribute batches across MPI ranks
    total_batches = len(batch_range)
    batches_per_rank = total_batches // size
    remainder = total_batches % size
    
    # Calculate batch range for this rank
    start_idx = rank * batches_per_rank + min(rank, remainder)
    end_idx = start_idx + batches_per_rank + (1 if rank < remainder else 0)
    my_batches = batch_range[start_idx:end_idx]
    
    logger.info(f"Rank {rank}: Processing batches {my_batches}")
    
    # Process assigned batches
    for batch_num in my_batches:
        try:
            batch_function(batch_num, simulation_dir, local_dir, simulation_name)
        except Exception as e:
            logger.error(f"Rank {rank}: Failed to process batch {batch_num}: {e}")
    
    # Synchronize all ranks before completing
    comm.Barrier()
    
    if rank == 0:
        logger.info("All MPI ranks completed batch processing")

def _run_joblib_parallel(batch_function, batch_range, simulation_dir, local_dir, simulation_name):
    """
    Joblib-based parallel execution for local machines
    """
    num_batches = len(batch_range)
    
    try:
        with parallel_backend('loky', n_jobs=num_batches, verbose=10):
            Parallel()(delayed(batch_function)(batch_num, simulation_dir, local_dir, simulation_name) 
                      for batch_num in batch_range)
    except Exception as e:
        logger.error(f"Error running joblib parallel execution: {e}")
        raise

