import os

from modules.utils import initialize_logger

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Parallel', external_log_dir)

def get_hpc_environment():
    """
    Detect if running in HPC environment and get MPI configuration
    """
    from mpi4py import MPI

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
    slurm_procid = os.environ.get('SLURM_PROCID')
    
    # Log SLURM environment for debugging
    if slurm_job_id:
        logger.info(f"SLURM environment detected: JOB_ID={slurm_job_id}, " 
                    f"NODES={slurm_nodes}, NTASKS={slurm_ntasks}, PROCID={slurm_procid}")
    
    # Initialize MPI if available
    try:
        # Use the already imported MPI
        comm = MPI.COMM_WORLD
        rank = comm.Get_rank()
        size = comm.Get_size()
        
        hpc_env['rank'] = rank
        hpc_env['size'] = size
        hpc_env['is_master'] = rank == 0
        hpc_env['is_hpc'] = size > 1
        
        logger.info(f"MPI environment initialized: rank={rank}, size={size}")
            
    except Exception as e:
        logger.warning(f"MPI initialization failed: {e}")
        # If SLURM is detected but MPI failed, use SLURM variables directly
        if slurm_procid is not None:
            hpc_env['rank'] = int(slurm_procid)
            hpc_env['size'] = int(slurm_ntasks) if slurm_ntasks else 1
            hpc_env['is_master'] = hpc_env['rank'] == 0
            logger.info(f"Using SLURM variables for rank/size: rank={hpc_env['rank']}, size={hpc_env['size']}")
    
    # Update with SLURM info if available
    if slurm_job_id:
        hpc_env['is_hpc'] = True
        hpc_env['slurm_job_id'] = slurm_job_id
        hpc_env['slurm_nodes'] = int(slurm_nodes) if slurm_nodes else 1
        hpc_env['slurm_ntasks'] = int(slurm_ntasks) if slurm_ntasks else 1
        
    return hpc_env

def run_parallel_batches(batch_function, batch_range, simulation_dir, local_dir, simulation_name, hpc_mode=False):
    """
    Run batch processing either with MPI (HPC mode) or joblib (local mode)
    """
    
    if hpc_mode:
        hpc_env = get_hpc_environment()
        logger.info(f"Running in HPC mode: Rank {hpc_env['rank']}/{hpc_env['size']}")
        return _run_mpi_parallel(batch_function, batch_range, simulation_dir, local_dir, simulation_name)
    else:
        logger.info("Running in local mode with joblib")
        return _run_joblib_parallel(batch_function, batch_range, simulation_dir, local_dir, simulation_name)

def _run_mpi_parallel(batch_function, batch_range, simulation_dir, local_dir, simulation_name):
    """
    MPI-based parallel execution with explicit node mapping
    """
    from mpi4py import MPI

    
    comm = MPI.COMM_WORLD
    rank = comm.Get_rank()
    size = comm.Get_size()
    
    # Get node information
    node_name = MPI.Get_processor_name() if hasattr(MPI, 'Get_processor_name') else 'unknown'
    
    # Gather all node names to rank 0
    all_nodes = comm.gather(node_name, root=0)
    
    # Rank 0 determines node-to-task mapping
    node_task_map = {}
    if rank == 0:
        # Create a map of node names to ranks running on that node
        for r, node in enumerate(all_nodes):
            if node not in node_task_map:
                node_task_map[node] = []
            node_task_map[node].append(r)
            
        logger.info(f"Node to rank mapping: {node_task_map}")
        
        # Get unique nodes
        unique_nodes = list(node_task_map.keys())
        num_nodes = len(unique_nodes)
        
        logger.info(f"Running on {num_nodes} unique nodes with {size} total ranks")
    
    # Broadcast node mapping to all ranks
    node_task_map = comm.bcast(node_task_map, root=0)
    
    # Identify which ranks are on the same node as this rank
    my_node_ranks = node_task_map.get(node_name, [rank])
    my_node_rank_idx = my_node_ranks.index(rank) if rank in my_node_ranks else 0
    num_ranks_on_my_node = len(my_node_ranks)
    
    logger.info(f"Rank {rank}: Running on node {node_name}, position {my_node_rank_idx+1}/{num_ranks_on_my_node}")
    
    # Distribute batches across nodes first, then across ranks within nodes
    total_batches = len(batch_range)
    
    # Two-level distribution: first to nodes, then to ranks within each node
    unique_nodes = list(node_task_map.keys())
    num_nodes = len(unique_nodes)
    
    # Find my node's index in the list of unique nodes
    my_node_idx = unique_nodes.index(node_name)
    
    # Calculate how many batches each node gets
    batches_per_node = total_batches // num_nodes
    node_remainder = total_batches % num_nodes
    
    # Calculate batch range for this node
    node_start_idx = my_node_idx * batches_per_node + min(my_node_idx, node_remainder)
    node_end_idx = node_start_idx + batches_per_node + (1 if my_node_idx < node_remainder else 0)
    node_batches = batch_range[node_start_idx:node_end_idx]
    
    # Now distribute this node's batches among ranks on this node
    my_node_batches = len(node_batches)
    batches_per_rank = my_node_batches // num_ranks_on_my_node
    rank_remainder = my_node_batches % num_ranks_on_my_node
    
    # Calculate batch range for this rank
    local_start_idx = my_node_rank_idx * batches_per_rank + min(my_node_rank_idx, rank_remainder)
    local_end_idx = local_start_idx + batches_per_rank + (1 if my_node_rank_idx < rank_remainder else 0)
    
    # Get the actual batch numbers this rank will process
    if node_batches and local_start_idx < len(node_batches):
        my_batches = node_batches[local_start_idx:local_end_idx]
    else:
        my_batches = []
    
    logger.info(f"Rank {rank} on node {node_name}: Processing batches {my_batches}")
    
    # Process assigned batches
    for batch_num in my_batches:
        try:
            batch_function(batch_num, simulation_dir, local_dir, simulation_name)
        except Exception as e:
            logger.error(f"Rank {rank} on node {node_name}: Failed to process batch {batch_num}: {e}")
    
    # Synchronize all ranks before completing
    comm.Barrier()
    
    if rank == 0:
        logger.info("All MPI ranks completed batch processing")

def _run_joblib_parallel(batch_function, batch_range, simulation_dir, local_dir, simulation_name):
    """
    Joblib-based parallel execution for local machines
    """
    from joblib import Parallel, delayed, parallel_backend

    num_batches = len(batch_range)
    
    try:
        with parallel_backend('loky', n_jobs=num_batches, verbose=10):
            Parallel()(delayed(batch_function)(batch_num, simulation_dir, local_dir, simulation_name) 
                      for batch_num in batch_range)
    except Exception as e:
        logger.error(f"Error running joblib parallel execution: {e}")
        raise

