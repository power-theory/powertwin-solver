#!/usr/bin/env python3
"""
PowerTwin Direct Runner for HPC Environments

This module provides direct execution of PowerTwin simulation functions without 
requiring the Flask web server. It's designed specifically for HPC environments
where direct process execution is more reliable than client-server architecture.
"""

import os
import sys
import argparse

# Configure logging for direct runner
import modules.utils as utils
logger = utils.initialize_logger("DirectRunner", os.environ.get('POWERTWIN_LOG_DIR'))

# Import simulation modules
from modules.simulation import initialize_uo, create_featurefiles
from modules.diagnostics import create_table

def _setup_simulation_directories(simulation_name, asset_geojson_path, metadata_csv_path, config_json_path, shared_storage):
    """
    Set up simulation directories and copy input files to their expected locations
    
    Args:
        simulation_name: Name of the simulation
        asset_geojson_path: Path to asset GeoJSON file
        metadata_csv_path: Path to metadata CSV file
        config_json_path: Path to configuration JSON file
        shared_storage: Path to shared storage
        
    Returns:
        tuple: (SIMULATION_DIR, LOCAL_SIMULATION_DIR, local_asset_path, local_metadata_path, local_config_path)
    """
    # Define directories
    DATA_DIR = os.path.join(shared_storage, 'powertwin_data')
    LOCAL_DIR = os.path.join(shared_storage, 'user_files')
    SIMULATION_DIR = os.path.join(DATA_DIR, simulation_name)
    LOCAL_SIMULATION_DIR = os.path.join(LOCAL_DIR, simulation_name)
    
    # Create directories if they don't exist
    os.makedirs(SIMULATION_DIR, exist_ok=True)
    os.makedirs(LOCAL_SIMULATION_DIR, exist_ok=True)
    
    # Also create feature_files directory that will be referenced later
    feature_files_dir = os.path.join(SIMULATION_DIR, 'feature_files')
    os.makedirs(feature_files_dir, exist_ok=True)
    
    # Copy input files if not already in the expected location
    local_asset_path = os.path.join(LOCAL_SIMULATION_DIR, f'{simulation_name}_asset.geojson')
    local_metadata_path = os.path.join(LOCAL_SIMULATION_DIR, f'{simulation_name}_metadata.csv')
    local_config_path = os.path.join(LOCAL_SIMULATION_DIR, f'{simulation_name}_config.json')
    
    # Only copy if source and destination are different
    if asset_geojson_path != local_asset_path:
        logger.info(f"Copying asset GeoJSON to {local_asset_path}")
        with open(asset_geojson_path, 'rb') as src, open(local_asset_path, 'wb') as dst:
            dst.write(src.read())
    
    if metadata_csv_path != local_metadata_path:
        logger.info(f"Copying metadata CSV to {local_metadata_path}")
        with open(metadata_csv_path, 'rb') as src, open(local_metadata_path, 'wb') as dst:
            dst.write(src.read())
    
    if config_json_path != local_config_path:
        logger.info(f"Copying config JSON to {local_config_path}")
        with open(config_json_path, 'rb') as src, open(local_config_path, 'wb') as dst:
            dst.write(src.read())
            
    return (SIMULATION_DIR, LOCAL_SIMULATION_DIR, local_asset_path, local_metadata_path, local_config_path)

def direct_create_feature_files(simulation_name, asset_geojson_path, metadata_csv_path, 
                          config_json_path, location, num_cores, hpc_mode=False, 
                          shared_storage=None):
    """
    Directly create feature files for a PowerTwin simulation
    
    Args:
        simulation_name: Name of the simulation
        asset_geojson_path: Path to asset GeoJSON file
        metadata_csv_path: Path to metadata CSV file
        config_json_path: Path to configuration JSON file
        location: Location name
        num_cores: Number of cores to use
        hpc_mode: Whether running in HPC mode
        shared_storage: Path to shared storage (required in HPC mode)
        
    Returns:
        tuple: (SIMULATION_DIR, LOCAL_SIMULATION_DIR) or None if error
    """
    logger.info(f"Creating feature files for: {simulation_name}")
    
    # Validate inputs
    if hpc_mode and not shared_storage:
        logger.error("Shared storage path is required in HPC mode")
        return None
    
    # Check if files exist
    if not os.path.exists(asset_geojson_path):
        logger.error(f"Asset GeoJSON file not found: {asset_geojson_path}")
        return None
    
    if not os.path.exists(metadata_csv_path):
        logger.error(f"Metadata CSV file not found: {metadata_csv_path}")
        return None
    
    if not os.path.exists(config_json_path):
        logger.error(f"Config JSON file not found: {config_json_path}")
        return None
    
    try:
        # Create diagnostic table if not exists
        create_table()
        
        # Setup directories and copy files
        SIMULATION_DIR, LOCAL_SIMULATION_DIR, local_asset_path, local_metadata_path, local_config_path = _setup_simulation_directories(
            simulation_name, asset_geojson_path, metadata_csv_path, config_json_path, shared_storage
        )
        
        # Create feature files (normally done by views.py)
        logger.info("Creating feature files...")
        create_featurefiles(
            SIMULATION_DIR, 
            LOCAL_SIMULATION_DIR,
            local_asset_path, 
            local_metadata_path, 
            local_config_path, 
            num_cores, 
            location, 
            simulation_name,
            hpc_mode
        )
        
        return (SIMULATION_DIR, LOCAL_SIMULATION_DIR)
        
    except Exception as e:
        logger.error(f"Error creating feature files: {str(e)}")
        return None

def direct_initialize_uo(SIMULATION_DIR, LOCAL_SIMULATION_DIR, simulation_name, hpc_mode=False):

    logger.info(f"Initializing UrbanOpt for: {SIMULATION_DIR}")
    
    try:
        # Initialize UrbanOpt simulation
        logger.info("Initializing UrbanOpt simulation...")
        result = initialize_uo(
            SIMULATION_DIR,
            LOCAL_SIMULATION_DIR,
            simulation_name,
            hpc_mode
        )
        
        # In HPC mode, initialize_uo returns the batch range
        if hpc_mode and isinstance(result, list):
            logger.info(f"UrbanOpt initialization for {simulation_name} completed successfully, returned {len(result)} batches")
            return result
        
        logger.info(f"UrbanOpt initialization for {simulation_name} completed successfully")
        return True
        
    except Exception as e:
        logger.error(f"Error initializing UrbanOpt: {str(e)}")
        return False

def direct_run_parallel_batches(SIMULATION_DIR, LOCAL_SIMULATION_DIR, simulation_name, batch_range=None, hpc_mode=True):
    """
    Run parallel batches for a PowerTwin simulation
    
    Args:
        SIMULATION_DIR: Path to the simulation directory
        LOCAL_SIMULATION_DIR: Path to the local simulation directory
        simulation_name: Name of the simulation
        batch_range: Range of batches to process (if None, will use all batches)
        hpc_mode: Whether running in HPC mode (default True)
        
    Returns:
        bool: True if successful, False otherwise
    """
    from modules.simulation.run_UOsim import run_batch
    from modules.utils.hpc_parallel import run_parallel_batches
    
    logger.info(f"Running parallel batches for: {simulation_name}")
    
    try:
        # If batch_range is not provided, determine it from the database
        if batch_range is None:
            from modules.diagnostics import get_batch_total
            batches = get_batch_total(simulation_name)
            batch_range = list(range(batches))
            
        logger.info(f"Processing {len(batch_range)} batches in parallel")
        
        # Run the batches in parallel
        run_parallel_batches(
            run_batch,
            batch_range,
            SIMULATION_DIR,
            LOCAL_SIMULATION_DIR,
            simulation_name,
            hpc_mode=hpc_mode
        )
        
        logger.info(f"Parallel batch processing for {simulation_name} completed successfully")
        return True
        
    except Exception as e:
        logger.error(f"Error in parallel batch processing: {str(e)}")
        return False

def direct_run_specific_batch(SIMULATION_DIR, LOCAL_SIMULATION_DIR, simulation_name, batch_num):
    """
    Run a specific batch for a PowerTwin simulation, designed for SLURM direct parallelism
    
    Args:
        SIMULATION_DIR: Path to the simulation directory
        LOCAL_SIMULATION_DIR: Path to the local simulation directory
        simulation_name: Name of the simulation
        batch_num: Specific batch number to process
        
    Returns:
        bool: True if successful, False otherwise
    """
    from modules.simulation.run_UOsim import run_batch
    
    logger.info(f"Running specific batch {batch_num} for simulation: {simulation_name}")
    
    try:
        # Run the specific batch
        run_batch(
            batch_num,
            SIMULATION_DIR,
            LOCAL_SIMULATION_DIR,
            simulation_name
        )
        
        logger.info(f"Batch {batch_num} processing for {simulation_name} completed successfully")
        return True
        
    except Exception as e:
        logger.error(f"Error processing batch {batch_num}: {str(e)}")
        return False

def main():
    """Parse command line arguments and run the simulation directly"""
    parser = argparse.ArgumentParser(description="PowerTwin Direct Runner for HPC")
    subparsers = parser.add_subparsers(dest='command', help='Command to run')
    
    # Create feature files command
    create_ff_parser = subparsers.add_parser('create-feature-files', help='Create feature files for simulation')
    create_ff_parser.add_argument('simulation_name', type=str, help='Name of the simulation')
    create_ff_parser.add_argument('asset_geojson_path', type=str, help='Path to the asset geojson file')
    create_ff_parser.add_argument('metadata_csv_path', type=str, help='Path to the metadata CSV file')
    create_ff_parser.add_argument('config_json_path', type=str, help='Path to the config JSON file')
    create_ff_parser.add_argument('location', type=str, help='Location of the simulation')
    create_ff_parser.add_argument('num_cores', type=int, help='Number of cores to use')
    create_ff_parser.add_argument('--hpc', action='store_true', help='Enable HPC multi-node execution mode')
    create_ff_parser.add_argument('--shared-storage', type=str, help='Path to shared storage for HPC mode (required in HPC mode)')
    
    # Initialize UrbanOpt command
    init_uo_parser = subparsers.add_parser('initialize-uo', help='Initialize UrbanOpt for simulation')
    init_uo_parser.add_argument('simulation_dir', type=str, help='Path to the simulation directory')
    init_uo_parser.add_argument('local_simulation_dir', type=str, help='Path to the local simulation directory')
    init_uo_parser.add_argument('simulation_name', type=str, help='Name of the simulation')
    init_uo_parser.add_argument('--hpc', action='store_true', help='Enable HPC multi-node execution mode')

    # Run parallel batches command
    run_batch_parser = subparsers.add_parser('run-parallel-batches', help='Run parallel batches for simulation')
    run_batch_parser.add_argument('simulation_dir', type=str, help='Path to the simulation directory')
    run_batch_parser.add_argument('local_simulation_dir', type=str, help='Path to the local simulation directory')
    run_batch_parser.add_argument('simulation_name', type=str, help='Name of the simulation')
    run_batch_parser.add_argument('--batch-start', type=int, help='Start of batch range (optional)')
    run_batch_parser.add_argument('--batch-end', type=int, help='End of batch range (optional)')
    run_batch_parser.add_argument('--hpc', action='store_true', help='Enable HPC multi-node execution mode')
    
    # Run specific batch command (for SLURM direct parallelism)
    run_specific_batch_parser = subparsers.add_parser('run-specific-batch', help='Run a specific batch for simulation (for SLURM direct parallelism)')
    run_specific_batch_parser.add_argument('simulation_dir', type=str, help='Path to the simulation directory')
    run_specific_batch_parser.add_argument('local_simulation_dir', type=str, help='Path to the local simulation directory')
    run_specific_batch_parser.add_argument('simulation_name', type=str, help='Name of the simulation')
    run_specific_batch_parser.add_argument('batch_num', type=int, help='Specific batch number to process')

    args = parser.parse_args()
    
    if args.command == 'create-feature-files':
        # Run the create feature files function directly
        result = direct_create_feature_files(
            simulation_name=args.simulation_name,
            asset_geojson_path=args.asset_geojson_path,
            metadata_csv_path=args.metadata_csv_path,
            config_json_path=args.config_json_path,
            location=args.location,
            num_cores=args.num_cores,
            hpc_mode=args.hpc,
            shared_storage=args.shared_storage
        )
        # Return success (0) if the function returned a tuple, otherwise error (1)
        result = 0 if result else 1
    elif args.command == 'initialize-uo':
        # Run the initialize UrbanOpt function directly
        result = direct_initialize_uo(
            SIMULATION_DIR=args.simulation_dir,
            LOCAL_SIMULATION_DIR=args.local_simulation_dir,
            simulation_name=args.simulation_name,
            hpc_mode=args.hpc,
        )
        # Return success (0) if the function returned True or a list, otherwise error (1)
        result = 0 if result else 1
    elif args.command == 'run-parallel-batches':
        # Process batch range if provided
        batch_range = None
        if args.batch_start is not None and args.batch_end is not None:
            batch_range = list(range(args.batch_start, args.batch_end + 1))
            
        # Run the parallel batches function directly
        success = direct_run_parallel_batches(
            SIMULATION_DIR=args.simulation_dir,
            LOCAL_SIMULATION_DIR=args.local_simulation_dir,
            simulation_name=args.simulation_name,
            batch_range=batch_range,
            hpc_mode=args.hpc # Always use HPC mode for this command
        )
        result = 0 if success else 1
    elif args.command == 'run-specific-batch':
        # Run a specific batch (for SLURM direct parallelism)
        success = direct_run_specific_batch(
            SIMULATION_DIR=args.simulation_dir,
            LOCAL_SIMULATION_DIR=args.local_simulation_dir,
            simulation_name=args.simulation_name,
            batch_num=args.batch_num
        )
        result = 0 if success else 1
    else:
        parser.print_help()
        result = 1
    
    sys.exit(result)

if __name__ == "__main__":
    main()