import os
import multiprocessing
from joblib import Parallel, delayed, parallel_backend
from modules.utils import initialize_logger


external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Parallel', external_log_dir)

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
        'slurm_ntasks': None,
        'node_name': 'unknown'
    }
    
    # Check for SLURM environment variables
    slurm_job_id = os.environ.get('SLURM_JOB_ID')
    slurm_nodes = os.environ.get('SLURM_JOB_NUM_NODES')
    slurm_ntasks = os.environ.get('SLURM_NTASKS')
    slurm_procid = os.environ.get('SLURM_PROCID', os.environ.get('PMI_RANK'))  # Try both SLURM_PROCID and PMI_RANK
    slurm_nodeid = os.environ.get('SLURM_NODEID')  # Node index within job
    
    # Get the hostname for this node
    import socket
    hpc_env['node_name'] = socket.gethostname()
    
    # Log SLURM environment for debugging
    if slurm_job_id:
        logger.info(f"SLURM environment detected: JOB_ID={slurm_job_id}, " 
                    f"NODES={slurm_nodes}, NTASKS={slurm_ntasks}, PROCID={slurm_procid}, "
                    f"NODEID={slurm_nodeid}, NODE={hpc_env['node_name']}")
    
    # Initialize MPI if available
    try:
        from mpi4py import MPI
        comm = MPI.COMM_WORLD
        rank = comm.Get_rank()
        size = comm.Get_size()
        
        hpc_env['rank'] = rank
        hpc_env['size'] = size
        hpc_env['is_master'] = rank == 0
        hpc_env['is_hpc'] = size > 1
                    
    except Exception as e:
        logger.warning(f"MPI initialization failed: {e}")
        # If SLURM is detected but MPI failed, use SLURM variables directly
        if slurm_procid is not None:
            hpc_env['rank'] = int(slurm_procid)
            hpc_env['size'] = int(slurm_ntasks) if slurm_ntasks else 1
            hpc_env['is_master'] = hpc_env['rank'] == 0
    
    # Update with SLURM info if available
    if slurm_job_id:
        hpc_env['is_hpc'] = True
        hpc_env['slurm_job_id'] = slurm_job_id
        hpc_env['slurm_nodes'] = int(slurm_nodes) if slurm_nodes else 1
        hpc_env['slurm_ntasks'] = int(slurm_ntasks) if slurm_ntasks else 1
        
        # If MPI initialization failed but we have SLURM_NODEID, use it for node-based division
        if slurm_nodeid is not None:
            hpc_env['node_id'] = int(slurm_nodeid)
            logger.debug(f"Using SLURM_NODEID for node-based batch division: node_id={hpc_env['node_id']}")
        
        # Always consider a SLURM job to be HPC even if MPI initialization failed
        if slurm_ntasks and int(slurm_ntasks) > 1:
            hpc_env['is_hpc'] = True
            if hpc_env['size'] == 1:
                logger.debug(f"MPI world size is 1 but SLURM environment has {slurm_ntasks} tasks - treating as HPC")
        
    return hpc_env

def run_parallel_batches(batch_function, batch_range, simulation_dir, local_dir, simulation_name, hpc_mode):
    """
    Run batch processing either with MPI (HPC mode) or joblib (local mode)
    
    Args:
        batch_function: Function to execute for each batch
        batch_range: Range of batch numbers to process
        simulation_dir: Directory containing simulation files
        local_dir: Local directory for processed files
        simulation_name: Name of the simulation
        hpc_mode: Whether to use HPC mode (MPI) or local mode (joblib)
    
    Returns:
        True if successful, False otherwise
    """
    
    # Get total number of batches
    total_batches = len(batch_range)

    # HPC mode
    if hpc_mode:
        hpc_env = get_hpc_environment()
        node_id = hpc_env.get('node_id', None)
        num_nodes = hpc_env.get('slurm_nodes', 1)
        node_name = hpc_env.get('node_name', 'unknown')

        # Get SLURM_CPUS_PER_TASK, default to all available if not set
        cpus_per_task = int(os.environ.get('SLURM_CPUS_PER_TASK', multiprocessing.cpu_count()))

        # Node-based batch assignment
        if node_id is not None and num_nodes > 1:
            batches_per_node = total_batches // num_nodes
            remainder = total_batches % num_nodes

            node_start = node_id * batches_per_node + min(node_id, remainder)
            node_extra = 1 if node_id < remainder else 0
            node_end = node_start + batches_per_node + node_extra

            node_batches = batch_range[node_start:node_end]

            logger.info(
                f"Node {node_name} (ID {node_id}): Assigned batches {node_start}-{node_end-1} "
                f"({len(node_batches)} total of {total_batches})"
            )

            if node_batches:
                n_jobs = min(cpus_per_task, len(node_batches))
                logger.debug(f"Node {node_name}: Using {n_jobs} cores for joblib parallel processing (SLURM_CPUS_PER_TASK={cpus_per_task})")
                try:
                    with parallel_backend('loky', n_jobs=n_jobs):
                        Parallel(verbose=10)(
                            delayed(batch_function)(
                                batch_num, simulation_dir, local_dir, simulation_name
                            ) for batch_num in node_batches
                        )
                    logger.info(f"Node {node_name}: Completed processing all assigned batches")
                except Exception as e:
                    logger.error(f"Node {node_name}: Error in joblib parallel execution: {e}")
            else:
                logger.info(f"Node {node_name}: No batches to process")
            return True
    else:
        # Truly local environment (no HPC, no SLURM)
        logger.warning(f"Running in local mode with joblib: {total_batches} batches")
        return _run_joblib_parallel(batch_function, batch_range, simulation_dir, local_dir, simulation_name)

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
