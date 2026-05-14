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
from modules.utils.hpc_environment import is_hpc_environment
logger = utils.initialize_logger("DirectRunner", os.environ.get('POWERTWIN_LOG_DIR'))

# Import simulation modules
from modules.simulation import initialize_uo, create_featurefiles
from modules.diagnostics import create_table
from modules.diagnostics.recover_UOsim import simulation_recovery

def _setup_simulation_directories(simulation_name, asset_geojson_path, metadata_csv_path, shared_storage):
    """
    Set up simulation directories and copy input files to their expected locations

    Args:
        simulation_name: Name of the simulation
        asset_geojson_path: Path to asset GeoJSON file
        metadata_csv_path: Path to metadata CSV file
        shared_storage: Path to shared storage

    Returns:
        tuple: (SIMULATION_DIR, LOCAL_SIMULATION_DIR, local_asset_path, local_metadata_path)
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

    # Only copy if source and destination are different
    if asset_geojson_path != local_asset_path:
        logger.info(f"Copying asset GeoJSON to {local_asset_path}")
        with open(asset_geojson_path, 'rb') as src, open(local_asset_path, 'wb') as dst:
            dst.write(src.read())

    if metadata_csv_path != local_metadata_path:
        logger.info(f"Copying metadata CSV to {local_metadata_path}")
        with open(metadata_csv_path, 'rb') as src, open(local_metadata_path, 'wb') as dst:
            dst.write(src.read())

    return (SIMULATION_DIR, LOCAL_SIMULATION_DIR, local_asset_path, local_metadata_path)

def direct_create_feature_files(simulation_name, asset_geojson_path, metadata_csv_path,
                          num_cores,
                          shared_storage=None):
    """
    Directly create feature files for a PowerTwin simulation

    Args:
        simulation_name: Name of the simulation
        asset_geojson_path: Path to asset GeoJSON file
        metadata_csv_path: Path to metadata CSV file
        num_cores: Number of cores to use
        shared_storage: Path to shared storage (required in HPC mode)

    Returns:
        tuple: (SIMULATION_DIR, LOCAL_SIMULATION_DIR) or None if error
    """

    # Use centralized HPC detection
    is_hpc = is_hpc_environment()

    # Validate inputs
    if is_hpc and not shared_storage:
        logger.error("Shared storage path is required in HCP environment")
        return None

    # Check if files exist
    if not os.path.exists(asset_geojson_path):
        logger.error(f"Asset GeoJSON file not found: {asset_geojson_path}")
        return None

    if not os.path.exists(metadata_csv_path):
        logger.error(f"Metadata CSV file not found: {metadata_csv_path}")
        return None

    try:
        # Create diagnostic table if not exists
        create_table()

        # Setup directories and copy files
        SIMULATION_DIR, LOCAL_SIMULATION_DIR, local_asset_path, local_metadata_path = _setup_simulation_directories(
            simulation_name, asset_geojson_path, metadata_csv_path, shared_storage
        )

        # Create feature files (normally done by views.py)
        create_featurefiles(
            SIMULATION_DIR,
            LOCAL_SIMULATION_DIR,
            local_asset_path,
            local_metadata_path,
            num_cores,
            simulation_name
        )
        
        return (SIMULATION_DIR, LOCAL_SIMULATION_DIR)
        
    except Exception as e:
        logger.error(f"Error creating feature files: {str(e)}")
        return None

def direct_initialize_uo(SIMULATION_DIR, LOCAL_SIMULATION_DIR, simulation_name):

    logger.info(f"Initializing UrbanOpt for: {SIMULATION_DIR}")
    
    # Use centralized HPC detection
    is_hpc = is_hpc_environment()
    
    try:
        # Initialize UrbanOpt simulation
        logger.info("Initializing UrbanOpt simulation...")
        result = initialize_uo(
            SIMULATION_DIR,
            LOCAL_SIMULATION_DIR,
            simulation_name
        )
        
        # In HPC mode, initialize_uo returns the batch range
        if is_hpc and isinstance(result, list):
            logger.info(f"UrbanOpt initialization for {simulation_name} completed successfully, returned {len(result)} batches")
            return result
        
        logger.info(f"UrbanOpt initialization for {simulation_name} completed successfully")
        return True
        
    except Exception as e:
        logger.error(f"Error initializing UrbanOpt: {str(e)}")
        return False

def direct_run_parallel_batches(SIMULATION_DIR, LOCAL_SIMULATION_DIR, simulation_name, batch_range=None):
    """
    
    Args:
        SIMULATION_DIR: Path to the simulation directory
        LOCAL_SIMULATION_DIR: Path to the local simulation directory
        simulation_name: Name of the simulation
        batch_range: Range of batches to process (if None, will use all batches)

        
    Returns:
        bool: True if successful, False otherwise
    """
    from modules.simulation.parallel import run_parallel_batches
    
    logger.info(f"Running parallel batches for: {simulation_name}")
    
    try:
        # If batch_range is not provided, determine it from the MASTER database
        # to avoid triggering node database creation prematurely
        if batch_range is None:
            from modules.database.sqlite_manager import get_sqlite_manager
            from modules.diagnostics import get_batch_total
            
            # Temporarily force connection to master database for batch query
            manager = get_sqlite_manager()
            original_db_path = manager.db_path
            
            # Query master database for batch information
            if manager.is_hpc_environment() and hasattr(manager, 'master_db_path'):
                # Temporarily use master database path
                manager.db_path = manager.master_db_path
                try:
                    batches = get_batch_total(simulation_name)
                    batch_range = list(range(batches))
                finally:
                    # Restore original path
                    manager.db_path = original_db_path
            else:
                batches = get_batch_total(simulation_name)
                batch_range = list(range(batches))
            
        # Check if we have any batches to process
        if len(batch_range) == 0:
            logger.error(f"No batches to process for simulation {simulation_name}.")
            # Try to diagnose the issue using master database
            from modules.diagnostics import get_asset_total
            
            # Use master database for diagnostics
            manager = get_sqlite_manager()
            original_db_path = manager.db_path
            if manager.is_hpc_environment() and hasattr(manager, 'master_db_path'):
                manager.db_path = manager.master_db_path
                try:
                    assets = get_asset_total(simulation_name=simulation_name)
                finally:
                    manager.db_path = original_db_path
            else:
                assets = get_asset_total(simulation_name=simulation_name)
                
            logger.info(f"Total assets in database for {simulation_name}: {assets}")
            
            if assets == 0:
                logger.error("No assets found - feature file generation likely failed")
            else:
                logger.error("Assets found but no batches - batch distribution failed")
            
            return False
            
        logger.info(f"Processing {len(batch_range)} batches in parallel")
        
        # Run the batches in parallel
        run_parallel_batches(
            batch_range,
            SIMULATION_DIR,
            LOCAL_SIMULATION_DIR,
            simulation_name
        )
        
        logger.info(f"Parallel batch processing for {simulation_name} completed")
        return True
        
    except Exception as e:
        logger.error(f"Error in parallel batch processing: {str(e)}")
        return False

def direct_simulation_recovery(recovery_dir, local_recovery_dir, corrupted_dir, corrupted_simulation_name, recovery_simulation_name, num_cores, batch_id=None):
    """
    Recover a corrupted simulation
    
    Args:
        recovery_dir: Path to the recovery directory
        local_recovery_dir: Path to the local recovery directory
        corrupted_dir: Path to the corrupted simulation directory
        corrupted_simulation_name: Name of the corrupted simulation
        recovery_simulation_name: Name for the recovered simulation
        num_cores: Number of cores to use
        batch_id: Batch ID to recover (None for all batches)
        
    Returns:
        bool: True if successful, False otherwise
    """    
    try:
        # Ensure recovery directories exist
        os.makedirs(recovery_dir, exist_ok=True)
        os.makedirs(local_recovery_dir, exist_ok=True)
        
        # Call the recovery function
        result = simulation_recovery(
            recovery_dir,
            local_recovery_dir,
            corrupted_dir,
            corrupted_simulation_name,
            recovery_simulation_name,
            num_cores,
            batch_id
        )
        
        logger.info(f"Simulation recovery for {corrupted_simulation_name} to {recovery_simulation_name} completed")
        return result
        
    except Exception as e:
        logger.error(f"Error in simulation recovery: {str(e)}")
        return False

def direct_simulation_status(simulation_name, batch_id=None):
    """
    Get the status of a PowerTwin simulation
    
    Args:
        simulation_name: Name of the simulation
        batch_id: Specific batch ID to check (optional, None for all batches)
        
    Returns:
        bool: True if successful, False otherwise
    """
    from modules.diagnostics.read_status import read_simulation_status
    
    logger.info(f"Getting status for simulation: {simulation_name}")
    
    try:
        read_simulation_status(simulation_name, batch_id)
        return True
    except Exception as e:
        logger.error(f"Error getting simulation status: {str(e)}")
        return False

def direct_consolidate_databases(simulation_name):
    """
    Consolidate node-specific databases back to master database
    
    Args:
        simulation_name: Name of the simulation to consolidate
        
    Returns:
        bool: True if successful, False otherwise
    """
    from modules.database.sqlite_manager import get_sqlite_manager
    
    logger.info(f"Consolidating databases for simulation: {simulation_name}")
    
    try:
        manager = get_sqlite_manager()
        success = manager.consolidate_node_databases(simulation_name)
        if success:
            logger.info(f"Successfully consolidated databases for {simulation_name}")
        else:
            logger.error(f"Failed to consolidate databases for {simulation_name}")
        return success
    except Exception as e:
        logger.error(f"Error consolidating databases: {str(e)}")
        return False

def direct_get_simulation_summary(simulation_name):
    """
    Get simulation status summary in the format needed by bash scripts
    
    Args:
        simulation_name: Name of the simulation to query
        
    Returns:
        bool: True if successful, False otherwise
    """
    from modules.diagnostics.read_status import get_simulation_summary
    
    logger.info(f"Getting simulation summary for: {simulation_name}")
    
    try:
        summary = get_simulation_summary(simulation_name)
        if summary:
            print(summary)  # Output to stdout for bash capture
            return True
        else:
            logger.error(f"Failed to get simulation summary for {simulation_name}")
            return False
    except Exception as e:
        logger.error(f"Error getting simulation summary: {str(e)}")
        return False

def direct_update_asset(asset_id, simulation_name):
    """
    Force set an asset's status to 'Failed' to ensure it gets reprocessed during recovery
    
    Args:
        asset_id: ID of the asset to mark as failed
        simulation_name: Name of the simulation the asset belongs to
        
    Returns:
        bool: True if successful, False otherwise
    """
    from modules.diagnostics.db import update_status
    
    logger.info(f"Forcing asset {asset_id} in simulation {simulation_name} to Failed status for reprocessing")
    
    try:
        result = update_status('Failed', asset_id, simulation_name)
        if result:
            logger.info(f"Successfully marked asset {asset_id} as Failed")
        else:
            logger.error(f"Failed to update status for asset {asset_id}")
        return result
    except Exception as e:
        logger.error(f"Error forcing asset {asset_id} to failed: {str(e)}")
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
    create_ff_parser.add_argument('num_cores', type=int, help='Number of cores to use')
    create_ff_parser.add_argument('--shared-storage', type=str, help='Path to shared storage for HPC mode (required in HPC mode)')
    
    # Initialize UrbanOpt command
    init_uo_parser = subparsers.add_parser('initialize-uo', help='Initialize UrbanOpt for simulation')
    init_uo_parser.add_argument('simulation_dir', type=str, help='Path to the simulation directory')
    init_uo_parser.add_argument('local_simulation_dir', type=str, help='Path to the local simulation directory')
    init_uo_parser.add_argument('simulation_name', type=str, help='Name of the simulation')

    # Run parallel batches command
    run_batch_parser = subparsers.add_parser('run-parallel-batches', help='Run parallel batches for simulation')
    run_batch_parser.add_argument('simulation_dir', type=str, help='Path to the simulation directory')
    run_batch_parser.add_argument('local_simulation_dir', type=str, help='Path to the local simulation directory')
    run_batch_parser.add_argument('simulation_name', type=str, help='Name of the simulation')
    run_batch_parser.add_argument('--batch-start', type=int, help='Start of batch range (optional)')
    run_batch_parser.add_argument('--batch-end', type=int, help='End of batch range (optional)')
    
    # Simulation recovery command
    recovery_parser = subparsers.add_parser('recover-simulation', help='Recover a corrupted simulation')
    recovery_parser.add_argument('recovery_dir', type=str, help='Path to the recovery directory')
    recovery_parser.add_argument('local_recovery_dir', type=str, help='Path to the local recovery directory')
    recovery_parser.add_argument('corrupted_dir', type=str, help='Path to the corrupted simulation directory')
    recovery_parser.add_argument('corrupted_simulation_name', type=str, help='Name of the corrupted simulation')
    recovery_parser.add_argument('recovery_simulation_name', type=str, help='Name for the recovered simulation')
    recovery_parser.add_argument('--batch-id', type=int, help='Specific batch ID to recover (optional, None for all batches)')
    recovery_parser.add_argument('num_cores', type=int, help='Number of cores to use')
    
    # Simulation status command
    status_parser = subparsers.add_parser('simulation-status', help='Get status of a simulation')
    status_parser.add_argument('simulation_name', type=str, help='Name of the simulation')
    status_parser.add_argument('--batch-id', type=int, help='Specific batch ID to check (optional, None for all batches)')
    
    # Consolidate databases command
    consolidate_parser = subparsers.add_parser('consolidate-databases', help='Consolidate distributed databases')
    consolidate_parser.add_argument('simulation_name', type=str, help='Simulation name to consolidate')
    
    # Force asset failed command
    force_failed_parser = subparsers.add_parser('update-asset', help='Force set an asset status to Failed for reprocessing')
    force_failed_parser.add_argument('asset_id', type=str, help='Asset ID to mark as failed in order to reprocess')
    force_failed_parser.add_argument('simulation_name', type=str, help='Simulation name the asset belongs to')
    
    # Get simulation summary command
    summary_parser = subparsers.add_parser('get-simulation-summary', help='Get simulation status summary for bash scripts')
    summary_parser.add_argument('simulation_name', type=str, help='Name of the simulation to query')

    args = parser.parse_args()
    
    if args.command == 'create-feature-files':
        # Run the create feature files function directly
        result = direct_create_feature_files(
            simulation_name=args.simulation_name,
            asset_geojson_path=args.asset_geojson_path,
            metadata_csv_path=args.metadata_csv_path,
            num_cores=args.num_cores,

            shared_storage=args.shared_storage
        )
        # Return success (0) if the function returned a tuple, otherwise error (1)
        result = 0 if result else 1
    elif args.command == 'initialize-uo':
        # Run the initialize UrbanOpt function directly
        result = direct_initialize_uo(
            SIMULATION_DIR=args.simulation_dir,
            LOCAL_SIMULATION_DIR=args.local_simulation_dir,
            simulation_name=args.simulation_name
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
            batch_range=batch_range
        )
        result = 0 if success else 1
    elif args.command == 'recover-simulation':
        # Run the simulation recovery function
        success = direct_simulation_recovery(
            recovery_dir=args.recovery_dir,
            local_recovery_dir=args.local_recovery_dir,
            corrupted_dir=args.corrupted_dir,
            corrupted_simulation_name=args.corrupted_simulation_name,
            recovery_simulation_name=args.recovery_simulation_name,
            num_cores=args.num_cores,
            batch_id=args.batch_id if hasattr(args, 'batch_id') else None
        )
        result = 0 if success else 1
    elif args.command == 'simulation-status':
        # Run the simulation status function
        success = direct_simulation_status(
            simulation_name=args.simulation_name,
            batch_id=args.batch_id if hasattr(args, 'batch_id') else None
        )
        result = 0 if success else 1
    elif args.command == 'consolidate-databases':
        # Run the database consolidation function
        success = direct_consolidate_databases(
            simulation_name=args.simulation_name
        )
        result = 0 if success else 1
    elif args.command == 'update-asset':
        logger.info(f"Forcing asset {args.asset_id} to failed status for reprocessing")
        result = direct_update_asset(args.asset_id, args.simulation_name)
        result = 0 if result else 1
    elif args.command == 'get-simulation-summary':
        # Get simulation summary for bash scripts
        success = direct_get_simulation_summary(args.simulation_name)
        result = 0 if success else 1
    else:
        parser.print_help()
        result = 1
    
    sys.exit(result)

if __name__ == "__main__":
    main()