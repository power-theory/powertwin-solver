import os
import time
from modules.utils import initialize_logger

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Parallel', external_log_dir)


############################################################################################################
# Name: wait_for_all_nodes_ready()
# Description: Wait for all SLURM nodes to complete initialization before starting batch processing
############################################################################################################
def wait_for_all_nodes_ready():
    """Wait for all SLURM nodes to complete initialization"""
    if not os.environ.get('SLURM_JOB_ID'):
        return
        
    node_id = os.environ.get('SLURM_NODEID', '0')
    total_nodes = int(os.environ.get('SLURM_JOB_NUM_NODES', '1'))
    
    logger.info(f"Node {node_id}: Waiting for all {total_nodes} nodes to be ready...")
    
    # Create ready marker
    ready_dir = os.path.join(os.environ.get('HPC_SHARED_STORAGE', '/tmp'), 'node_ready')
    os.makedirs(ready_dir, exist_ok=True)
    
    ready_file = os.path.join(ready_dir, f'node_{node_id}_ready')
    with open(ready_file, 'w') as f:
        f.write(str(time.time()))
    
    # Wait for all nodes
    max_wait_time = 600  # 10 minutes maximum wait
    start_time = time.time()
    
    while time.time() - start_time < max_wait_time:
        try:
            ready_files = [f for f in os.listdir(ready_dir) if f.endswith('_ready')]
            if len(ready_files) >= total_nodes:
                logger.info(f"Node {node_id}: All {total_nodes} nodes ready, proceeding with batch processing")
                return
        except OSError:
            # Directory might be temporarily unavailable
            pass
        time.sleep(2)
    
    logger.warning(f"Node {node_id}: Timeout waiting for all nodes to be ready. Proceeding anyway...")
