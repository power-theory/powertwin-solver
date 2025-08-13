import os
import sys

try:
    from mpi4py import MPI
    MPI_AVAILABLE = True
except ImportError:
    MPI_AVAILABLE = False

from joblib import Parallel, delayed, parallel_backend
from modules.utils import initialize_logger

logger = initialize_logger('HPC Parallel')

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
        
        if MPI_AVAILABLE:
            comm = MPI.COMM_WORLD
            hpc_env['rank'] = comm.Get_rank()
            hpc_env['size'] = comm.Get_size()
            hpc_env['is_master'] = hpc_env['rank'] == 0
        else:
            logger.warning("MPI not available but SLURM environment detected")
    
    return hpc_env

def run_parallel_batches(batch_function, batch_range, simulation_dir, local_dir, simulation_name, hpc_mode=False, shared_storage=None):
    """
    Run batch processing either with MPI (HPC mode) or joblib (local mode)
    """
    hpc_env = get_hpc_environment()
    
    if hpc_mode and MPI_AVAILABLE and hpc_env['is_hpc']:
        logger.info(f"Running in HPC mode: Rank {hpc_env['rank']}/{hpc_env['size']}")
        return _run_mpi_parallel(batch_function, batch_range, simulation_dir, local_dir, simulation_name, shared_storage, hpc_env)
    else:
        if hpc_mode:
            logger.warning("HPC mode requested but MPI/SLURM not available, falling back to joblib")
        logger.info("Running in local mode with joblib")
        return _run_joblib_parallel(batch_function, batch_range, simulation_dir, local_dir, simulation_name)

def _run_mpi_parallel(batch_function, batch_range, simulation_dir, local_dir, simulation_name, shared_storage, hpc_env):
    """
    MPI-based parallel execution for HPC clusters
    """
    comm = MPI.COMM_WORLD
    rank = hpc_env['rank']
    size = hpc_env['size']
    
    # Adjust paths for shared storage
    if shared_storage:
        simulation_dir = os.path.join(shared_storage, os.path.basename(simulation_dir))
        local_dir = os.path.join(shared_storage, f'node_{rank}', os.path.basename(local_dir))
        os.makedirs(local_dir, exist_ok=True)
    
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

def get_effective_core_count(hpc_mode=False, requested_cores=None):
    """
    Get effective core count considering HPC vs local execution
    """
    hpc_env = get_hpc_environment()
    
    if hpc_mode and hpc_env['is_hpc']:
        # In HPC mode, use SLURM_NTASKS as the total parallel capacity
        effective_cores = hpc_env['slurm_ntasks']
        logger.info(f"HPC mode: Using {effective_cores} tasks across {hpc_env['slurm_nodes']} nodes")
        return effective_cores
    else:
        # Local mode: use requested cores or detect available cores
        import multiprocessing
        import psutil
        
        try:
            cpu_usage = psutil.cpu_percent(interval=1, percpu=True)
            available_cores = sum(1 for usage in cpu_usage if usage < 70.0)
            available_cores = max(1, available_cores)
        except Exception:
            available_cores = max(1, multiprocessing.cpu_count() // 2)
        
        if requested_cores and requested_cores > 0:
            if requested_cores > available_cores:
                logger.warning(f"Requested cores ({requested_cores}) exceeds available cores ({available_cores})")
                return available_cores
            return requested_cores
        
        return available_cores

def get_local_cores_per_task():
    """
    Get the number of local cores available per MPI task in HPC mode
    """
    hpc_env = get_hpc_environment()
    
    if hpc_env['is_hpc'] and MPI_AVAILABLE:
        # Get SLURM_CPUS_PER_TASK or fall back to detection
        cpus_per_task = os.environ.get('SLURM_CPUS_PER_TASK')
        if cpus_per_task:
            cores = int(cpus_per_task)
            logger.info(f"HPC mode: Using {cores} cores per MPI task (from SLURM_CPUS_PER_TASK)")
            return cores
        
        # Fall back to detecting available cores on this node
        import multiprocessing
        total_cores = multiprocessing.cpu_count()
        ntasks_per_node = int(os.environ.get('SLURM_NTASKS_PER_NODE', 1))
        cores_per_task = max(1, total_cores // ntasks_per_node)
        logger.info(f"HPC mode: Detected {cores_per_task} cores per task ({total_cores} total / {ntasks_per_node} tasks)")
        return cores_per_task
    
    return 1