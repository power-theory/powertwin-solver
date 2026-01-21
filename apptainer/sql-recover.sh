#!/bin/bash
#==============================================================================
# PowerTwin HPC Recovery Script
# 
# Description: Orchestrates the recovery of corrupted PowerTwin simulations
#              using containerized execution with SQLite database and SLURM integration.
#
# Usage: sbatch sqlite-recover.sh
#==============================================================================

#==============================================================================
# SLURM CONFIGURATION
#==============================================================================
#SBATCH --job-name=test-recover
#SBATCH --nodes=3      
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=5
#SBATCH --time=7-00:00:00            
#SBATCH --mem-per-cpu=8G             
#SBATCH --account=cowy-ptheory
#SBATCH --partition=teton            # Teton partition
#SBATCH --output=%x_%j.out
#SBATCH --qos=long                   # debug or long

set -e  # Exit immediately if a command exits with a non-zero status

#==============================================================================
# ENVIRONMENT SETUP
#==============================================================================
module --force purge
module load arcc/1.0
module load slurm
module load miniconda3/24.3.0
module load gcc/14.2.0
module load apptainer/1.4.1

#==============================================================================
# Configuration Variables - MODIFY THESE AS NEEDED
#==============================================================================
# Simulation parameters
RECOVERY_SIMULATION_NAME="test2"
CORRUPTED_SIMULATION_NAME="test1"
BATCH_ID=""  # Optional - leave empty to recover all batches, or specify a batch number
HPC_SHARED_STORAGE="/project/cowy-ptheory/test" # Retain same as corrupted simulation for recovery to ensure access to all necessary files
POWERTWIN_KEEP_DIRS=1

# SIF files location
SIF_DIR="${HPC_SHARED_STORAGE}/sif_containers"
SOLVER_SIF="${SIF_DIR}/flask.sif"

# Shared directories
DATA_DIR="${HPC_SHARED_STORAGE}/powertwin_data"
USER_FILES_DIR="${HPC_SHARED_STORAGE}/user_files"
LOG_DIR="${HPC_SHARED_STORAGE}/logs"
TMP_BASE="${HPC_SHARED_STORAGE}/tmp"

# SQLite database configuration
SQLITE_DB_DIR="${DATA_DIR}/sqlite"
SQLITE_DB_PATH="${SQLITE_DB_DIR}/powertwin.db"

# SQLite environment variables
export SQLITE_DB_PATH="${SQLITE_DB_PATH}"
export HPC_SHARED_STORAGE="${HPC_SHARED_STORAGE}"
export PGDATABASE="powertwin"  # Keep for compatibility with existing Python code

# HPC networking and MPI environment variables
export RDMAV_FORK_SAFE=1
export IBV_FORK_SAFE=1
export OMPI_MCA_btl_vader_single_copy_mechanism=none
export OMPI_MCA_mpi_warn_on_fork=0
export OMPI_MCA_btl="^openib"
export OMPI_MCA_mpi_leave_pinned=0

# Create a node-specific temp directory to avoid /tmp disk space issues
export NODE_ID=$(hostname -s)
export NODE_TMP_DIR="${TMP_BASE}/node_${NODE_ID}_${SLURM_JOB_ID}"

# Clean up any leftover files from previous runs with the same job ID pattern
if [ -d "${NODE_TMP_DIR}" ]; then
    print_status "warning" "Found existing node temp directory, cleaning up leftover files..."
    find "${NODE_TMP_DIR}" -name "*.pid" -type f -delete 2>/dev/null
    sleep 2
fi

mkdir -p "${NODE_TMP_DIR}"

# Redirect temporary files to our custom location
export TMPDIR="${NODE_TMP_DIR}"
export TMP="${NODE_TMP_DIR}"
export TEMP="${NODE_TMP_DIR}"

# Temporary directories for this job - create unique per process to avoid race conditions
export GEM_HOME="${NODE_TMP_DIR}/gems_${SLURM_JOB_ID}"
export GEM_PATH="${GEM_HOME}:/usr/local/lib/ruby/gems/3.2.2"
export BUNDLE_PATH="${GEM_HOME}"
export RUBYLIB="${GEM_HOME}/lib"
export HOME="${NODE_TMP_DIR}/home_${SLURM_JOB_ID}_${SLURM_PROCID}"
export XML_CLEANUP_PID_FILE="${NODE_TMP_DIR}/xml_cleanup_${SLURM_JOB_ID}.pid"
export PROCESS_ID="${SLURM_JOB_ID}_${SLURM_PROCID}_$$"

mkdir -p "$GEM_HOME" "$HOME" 

# Set up paths for the corrupted and recovery directories
CORRUPTED_SIMULATION_DIR="${USER_FILES_DIR}/${CORRUPTED_SIMULATION_NAME}"
RECOVERY_DIR_LOCAL="${USER_FILES_DIR}/${RECOVERY_SIMULATION_NAME}"
RECOVERY_DIR="${DATA_DIR}/${RECOVERY_SIMULATION_NAME}"

# Calculate total tasks and cores from SLURM environment
TOTAL_CORES=$((SLURM_JOB_NUM_NODES * SLURM_CPUS_PER_TASK))

# Export variables for access in child processes
export POWERTWIN_LOG_DIR="${LOG_DIR}"
export RECOVERY_SIMULATION_NAME
export HPC_SHARED_STORAGE
export CORRUPTED_SIMULATION_DIR
export RECOVERY_DIR_LOCAL
export RECOVERY_DIR



#==============================================================================
# SIGNAL HANDLING
#==============================================================================
# Set up signal traps for graceful termination
trap 'handle_termination SIGTERM' SIGTERM
trap 'handle_termination SIGINT' SIGINT
trap 'handle_termination SIGHUP' SIGHUP
trap 'handle_termination EXIT' EXIT

#==============================================================================
# FUNCTIONS
#==============================================================================

#------------------------------------------------------------------------------
# FUNCTION: print_status
# Description: Prints status messages with color coding
# Arguments: $1 - Status type (info, warning, error)
#            $2 - Message text
# Returns: None
#------------------------------------------------------------------------------
print_status() {
    GREEN='\033[0;32m'
    RED='\033[0;31m'
    YELLOW='\033[0;33m'
    NC='\033[0m' # No Color
    
    case $1 in
        "info")
            echo -e "${GREEN}[INFO]${NC} $2"
            ;;
        "warning")
            echo -e "${YELLOW}[WARNING]${NC} $2"
            ;;
        "error")
            echo -e "${RED}[ERROR]${NC} $2"
            ;;
        *)
            echo "$2"
            ;;
    esac
}

#------------------------------------------------------------------------------
# FUNCTION: check_sif_files
# Description: Validates that required SIF container files exist
# Arguments: None
# Returns: 0 on success, 1 on failure
#------------------------------------------------------------------------------
check_sif_files() {    
    if [ ! -f "$SOLVER_SIF" ]; then
        print_status "error" "Solver SIF file not found at: $SOLVER_SIF"
        return 1
    fi
    return 0
}

#------------------------------------------------------------------------------
# FUNCTION: setup_dirs
# Description: Sets up necessary directories for the recovery process, 
# including shared directories and local recovery directories. 
# Validates the existence of required files and creates new ones as needed.
# Arguments: None
# Returns: 0 on success, 1 on failure
#------------------------------------------------------------------------------
setup_dirs() {
    print_status "info" "Creating shared directories..."
    
    # Create base directories if they don't exist
    mkdir -p "${LOG_DIR}"
    mkdir -p "${USER_FILES_DIR}"

    # Set up paths for corrupted and recovery directories
    if [ ! -d "${CORRUPTED_SIMULATION_DIR}" ]; then
        print_status "error" "Simulation directory not found: ${CORRUPTED_SIMULATION_DIR}"
        return 1
    fi

    # Check if recovery directories already exist
    if [ -d "${RECOVERY_DIR_LOCAL}" ] || [ -d "${RECOVERY_DIR}" ]; then
        print_status "error" "Recovery directory already exists. Exiting to prevent overwriting data."
        return 1
    else
        # Create recovery directories if they don't exist
        mkdir -p "${RECOVERY_DIR}"
        mkdir -p "${RECOVERY_DIR_LOCAL}"
        print_status "info" "Created recovery directories."
    fi

    # Define paths for the metadata, geojson, and config files
    METADATA_CSV_PATH="${CORRUPTED_SIMULATION_DIR}/${CORRUPTED_SIMULATION_NAME}_metadata.csv"
    GEOJSON_PATH="${CORRUPTED_SIMULATION_DIR}/${CORRUPTED_SIMULATION_NAME}_asset.geojson"
    CONFIG_PATH="${CORRUPTED_SIMULATION_DIR}/${CORRUPTED_SIMULATION_NAME}_config.json"

    # Check if required files exist
    if [ ! -f "${METADATA_CSV_PATH}" ]; then
        print_status "error" "Metadata CSV file not found: ${METADATA_CSV_PATH}"
        return 1
    fi

    if [ ! -f "${GEOJSON_PATH}" ]; then
        print_status "warning" "Asset GeoJSON file not found: ${GEOJSON_PATH}"
    fi

    if [ ! -f "${CONFIG_PATH}" ]; then
        print_status "warning" "Config JSON file not found: ${CONFIG_PATH}"
    fi

    # Define paths for the new files
    NEW_METADATA_CSV_PATH="${RECOVERY_DIR_LOCAL}/${RECOVERY_SIMULATION_NAME}_metadata.csv"
    NEW_GEOJSON_PATH="${RECOVERY_DIR_LOCAL}/${RECOVERY_SIMULATION_NAME}_asset.geojson"
    NEW_CONFIG_PATH="${RECOVERY_DIR_LOCAL}/${RECOVERY_SIMULATION_NAME}_config.json"
    
    # Copy and rename the files to the recovery directory
    cp "${METADATA_CSV_PATH}" "${NEW_METADATA_CSV_PATH}"
    
    if [ -f "${GEOJSON_PATH}" ]; then
        cp "${GEOJSON_PATH}" "${NEW_GEOJSON_PATH}"
    fi
    
    if [ -f "${CONFIG_PATH}" ]; then
        cp "${CONFIG_PATH}" "${NEW_CONFIG_PATH}"
    fi
    
    # Check if critical directories were created successfully
    if [ ! -d "${DATA_DIR}" ] || [ ! -d "${LOG_DIR}" ] || [ ! -d "${USER_FILES_DIR}" ]; then
        print_status "error" "Failed to create shared directories."
        return 1
    fi
    
    print_status "info" "Shared directories created successfully."
    return 0
}

# Setup SQLite database
setup_sqlite_database() {    
    # Create SQLite database directory if it doesn't exist
    mkdir -p "${SQLITE_DB_DIR}"
    
    # Check if SQLite database exists - required for recovery
    if [ ! -f "${SQLITE_DB_PATH}" ]; then
        print_status "error" "SQLite database not found at: ${SQLITE_DB_PATH}"
        print_status "error" "Database is required for recovery operations - cannot recover from non-existent simulation"
        return 1
    else
        print_status "info" "Found existing SQLite database at: ${SQLITE_DB_PATH}"
    fi
    
    # Set SQLite-specific environment variables
    export SQLITE_DB_PATH="${SQLITE_DB_PATH}"
    export PGDATABASE="powertwin"  # Keep for compatibility
    
    return 0
}

# Function to clean up temporary files created during simulation
cleanup_temp_files() {
    print_status "info" "Cleaning up temporary files..."
    
    # Clean up any temporary files containing the SLURM job ID in system /tmp
    # This is a fallback in case any files were created there despite our redirections
    if [ -n "${SLURM_JOB_ID}" ]; then
        find /tmp -name "*${SLURM_JOB_ID}*" -type f -delete 2>/dev/null
        find /tmp -name "*${SLURM_JOB_ID}*" -type d -exec rm -rf {} \; 2>/dev/null
        print_status "info" "Removed temporary files containing job ID from system /tmp: ${SLURM_JOB_ID}"
    fi
    
    # Expand cleanup for GEM_HOME and HOME directories
    if [ -d "$GEM_HOME" ]; then
        rm -rf "$GEM_HOME"
        print_status "info" "Removed GEM_HOME: ${GEM_HOME}"
    fi
    
    if [ -d "$HOME" ]; then
        rm -rf "$HOME"
        print_status "info" "Removed temporary HOME: ${HOME}"
    fi
    
    # Clean up the node-specific temp directory
    if [ -d "${NODE_TMP_DIR}" ]; then
        # First remove any PID files that might be left
        find "${NODE_TMP_DIR}" -name "*.pid" -type f -delete 2>/dev/null
        
        # Keep a list of problematic directories that might be in use
        find "${NODE_TMP_DIR}" -type d -name "OpenStudio*" -o -name "Temp-*" -o -name "urbanopt*" -o -name "ruby*" -o -name "xmlvalidation-*" -o -name "apptainer-*" | while read dir; do
            rm -rf "$dir" 2>/dev/null
            if [ $? -ne 0 ]; then
                print_status "warning" "Could not remove directory: $dir"
            fi
        done
        
        # Try to remove the entire NODE_TMP_DIR
        if rmdir "${NODE_TMP_DIR}" 2>/dev/null; then
            print_status "info" "Removed node-specific temp directory: ${NODE_TMP_DIR}"
        else
            print_status "warning" "Could not completely remove ${NODE_TMP_DIR}, it may still contain files in use"
            # List remaining content for debugging
            ls -la "${NODE_TMP_DIR}" 2>/dev/null
        fi
    fi
    
    # As a fallback, still try to clean specific patterns from system /tmp
    # Clean up OpenStudio temporary directories
    find /tmp -type d -name "OpenStudio*" -exec rm -rf {} \; 2>/dev/null
    
    # Clean up EnergyPlus temporary directories
    find /tmp -type d -name "Temp-*" -exec rm -rf {} \; 2>/dev/null
    
    # Clean up UrbanOpt temporary directories
    find /tmp -type d -name "urbanopt*" -exec rm -rf {} \; 2>/dev/null
    
    # Clean up any Ruby temporary directories that might be created
    find /tmp -type d -name "ruby*" -exec rm -rf {} \; 2>/dev/null
    
    # Clean up XML validation temporary directories
    find /tmp -type d -name "xmlvalidation-*" -exec rm -rf {} \; 2>/dev/null
    
    # Clean up any remaining apptainer temporary files
    find /tmp -type d -name "apptainer-*" -exec rm -rf {} \; 2>/dev/null
    
    print_status "info" "Temporary file cleanup completed"
}

#------------------------------------------------------------------------------
# FUNCTION: handle_error
# Description: Handles errors with proper logging and cleanup
# Arguments: $1 - Error message
#            $2 - Exit code (optional, defaults to 1)
# Returns: Does not return, exits the script
#------------------------------------------------------------------------------
handle_error() {
    local error_message="$1"
    local exit_code="${2:-1}"
    
    print_status "error" "$error_message"
    
    # Perform cleanup
    cleanup_temp_files
    
    exit "$exit_code"
}

#------------------------------------------------------------------------------
# FUNCTION: handle_termination
# Description: Handles termination signals for graceful shutdown
# Arguments: $1 - Signal name
# Returns: Does not return, exits the script
#------------------------------------------------------------------------------
handle_termination() {
    local signal_name=$1

    # Prevent multiple cleanup runs by disabling all traps immediately
    trap - SIGTERM SIGINT SIGHUP EXIT
    
    print_status "warning" "Received ${signal_name} signal. Performing emergency cleanup..."
    
    # First, try to consolidate node databases to preserve work
    if [ "$signal_name" != "EXIT" ]; then
        print_status "info" "Attempting emergency database consolidation to preserve work..."
        print_status "info" "No timeout applied - large databases require extended time"
        
        apptainer exec \
            --bind "${DATA_DIR}:/powertwin_data" \
            --bind "${USER_FILES_DIR}:/powertwin-solver-pg/user_files" \
            --bind "${HPC_SHARED_STORAGE}:${HPC_SHARED_STORAGE}" \
            --bind "${SQLITE_DB_DIR}:/sqlite_data" \
            --bind "${LOG_DIR}:/solver/logs" \
            --env "POWERTWIN_LOG_DIR=/solver/logs" \
            --env "SQLITE_DB_PATH=${SQLITE_DB_PATH}" \
            --env "PGDATABASE=powertwin" \
            --env "POWERTWIN_STEP=consolidate" \
            --workdir /solver \
            "${SOLVER_SIF}" bash -c "cd /solver && python -m app.direct_runner consolidate-databases \"${RECOVERY_SIMULATION_NAME}\"" \
            2>&1 | tee "${LOG_DIR}/emergency_consolidation_${SLURM_JOB_ID}.log"
        
        EMERGENCY_CONSOLIDATE_EXIT_CODE=$?
        if [ $EMERGENCY_CONSOLIDATE_EXIT_CODE -eq 0 ]; then
            print_status "info" "Emergency database consolidation completed successfully"
            
            # Only clean up SQLite directory after SUCCESSFUL emergency consolidation
            print_status "info" "Cleaning up SQLite directory after successful emergency consolidation..."
            if [ -d "${SQLITE_DB_DIR}" ]; then
                # Find and remove any files that are not powertwin.db
                find "${SQLITE_DB_DIR}" -type f ! -name "powertwin.db" -delete 2>/dev/null
                # Remove any temporary SQLite files like wal, shm files from previous operations
                find "${SQLITE_DB_DIR}" -name "*.db-wal" -delete 2>/dev/null
                find "${SQLITE_DB_DIR}" -name "*.db-shm" -delete 2>/dev/null
                # Remove any node-specific database files ONLY after successful consolidation
                find "${SQLITE_DB_DIR}" -name "*node*" -delete 2>/dev/null
                find "${SQLITE_DB_DIR}" -name "*temp*" -delete 2>/dev/null
                print_status "info" "SQLite directory emergency cleanup completed - kept only powertwin.db"
            fi
        else
            print_status "warning" "Emergency database consolidation failed (exit code: $EMERGENCY_CONSOLIDATE_EXIT_CODE)"
            print_status "warning" "PRESERVING ALL node databases due to consolidation failure"
            
            # List preserved databases for manual recovery
            if [ -d "${SQLITE_DB_DIR}" ]; then
                print_status "info" "Preserved node databases for manual recovery:"
                find "${SQLITE_DB_DIR}" -name "*node*" -type f -exec basename {} \; 2>/dev/null | sort | head -10
                node_db_count=$(find "${SQLITE_DB_DIR}" -name "*node*" -type f | wc -l)
                print_status "info" "Total preserved node databases: ${node_db_count}"
            fi
        fi
    fi
    
    # Clean up temporary files
    cleanup_temp_files
    
    # Kill XML validation cleanup if it's running
    if [ -f "${XML_CLEANUP_PID_FILE}" ]; then
        XML_CLEANUP_PID=$(cat "${XML_CLEANUP_PID_FILE}")
        if kill -0 ${XML_CLEANUP_PID} 2>/dev/null; then
            kill ${XML_CLEANUP_PID}
            rm -f "${XML_CLEANUP_PID_FILE}"
        fi
    fi
    
    print_status "warning" "Emergency cleanup completed. Exiting due to ${signal_name} signal."
    
    # Return the appropriate exit code
    if [ "$signal_name" = "EXIT" ]; then
        exit 0
    else
        exit 1
    fi
}

# Function to periodically clean XML validation directories
clean_xml_validation_dirs() {
    local interval_seconds=300  # 5 minutes
    
    print_status "info" "Starting XML validation directory cleanup every $((interval_seconds/60)) minutes..."
    
    while true; do
        # Get current timestamp
        local timestamp=$(date "+%Y-%m-%d %H:%M:%S")
        
        # Calculate disk usage before cleanup for reporting in NODE_TMP_DIR
        local before_size=$(find "${NODE_TMP_DIR}" -type d -name "xmlvalidation-*" -exec du -sk {} \; 2>/dev/null | awk '{sum += $1} END {print sum/1024}')
        
        # Count directories before cleanup in NODE_TMP_DIR
        local dir_count=$(find "${NODE_TMP_DIR}" -type d -name "xmlvalidation-*" -print 2>/dev/null | wc -l)
        
        if [ "$dir_count" -gt 0 ]; then
            print_status "info" "[$timestamp] Cleaning up $dir_count XML validation directories in ${NODE_TMP_DIR} (${before_size:-0} MB)..."
            
            # Use the precise pattern matching to find and remove XML validation directories
            find "${NODE_TMP_DIR}" -type d -name "xmlvalidation-*" -print0 2>/dev/null | xargs -0 rm -rf 2>/dev/null
            
            print_status "info" "[$timestamp] XML validation directories cleanup completed in ${NODE_TMP_DIR}."
        fi
        
        # As a fallback, also check system /tmp
        local tmp_dir_count=$(find /tmp -type d -name "xmlvalidation-*" -print 2>/dev/null | wc -l)
        
        if [ "$tmp_dir_count" -gt 0 ]; then
            print_status "info" "[$timestamp] Also cleaning up $tmp_dir_count XML validation directories in system /tmp..."
            find /tmp -type d -name "xmlvalidation-*" -print0 2>/dev/null | xargs -0 rm -rf 2>/dev/null
            print_status "info" "[$timestamp] XML validation directories cleanup completed in system /tmp."
        fi
        
        # Sleep for the specified interval
        sleep ${interval_seconds}
    done
}


#------------------------------------------------------------------------------
# FUNCTION: initialize_environment
# Description: Sets up the initial environment and validates prerequisites
# Arguments: None
# Returns: 0 on success, exits on failure
#------------------------------------------------------------------------------
initialize_environment() {
    # SQLite database setup
    print_status "info" "Setting up SQLite database..."
    setup_sqlite_database || handle_error "SQLite database setup failed" 1
    print_status "info" "SQLite database directory prepared: ${SQLITE_DB_DIR}"
    print_status "info" "Using SQLite database at: ${SQLITE_DB_PATH}"
    
    # All validation and setup
    check_sif_files || handle_error "SIF files validation failed" 1
    setup_dirs || handle_error "Directory setup failed" 1
    
    # Display SLURM job information
    print_status "info" "======= SLURM Job Information ======="
    print_status "info" "Job ID: ${SLURM_JOB_ID}"
    print_status "info" "Number of nodes: ${SLURM_JOB_NUM_NODES}"
    print_status "info" "Number of tasks: ${SLURM_NTASKS}"
    print_status "info" "Tasks per node: ${SLURM_NTASKS_PER_NODE}"
    print_status "info" "CPUs per task: ${SLURM_CPUS_PER_TASK}"
    print_status "info" "Total cores: ${TOTAL_CORES}"
    print_status "info" "SQLite database: ${SQLITE_DB_PATH}"
    print_status "info" "==================================="
    
    return 0
}

#------------------------------------------------------------------------------
# FUNCTION: recover_simulation
# Description: Runs the simulation recovery process
# Arguments: None
# Returns: 0 on success, exits on failure
#------------------------------------------------------------------------------
recover_simulation() {
    
    # Build the command based on whether BATCH_ID is provided
    RECOVERY_CMD="python -m app.direct_runner recover-simulation \
        \"${RECOVERY_DIR}\" \
        \"${RECOVERY_DIR_LOCAL}\" \
        \"${CORRUPTED_SIMULATION_DIR}\" \
        \"${CORRUPTED_SIMULATION_NAME}\" \
        \"${RECOVERY_SIMULATION_NAME}\" \
        ${TOTAL_CORES}"
    
    # Only add the --batch-id parameter if BATCH_ID is provided
    if [ -n "${BATCH_ID}" ]; then
        print_status "info" "Recovering specific batch: ${BATCH_ID}"
        RECOVERY_CMD="${RECOVERY_CMD} --batch-id ${BATCH_ID}"
    else
        print_status "info" "Recovering all batches"
    fi
    
    RECOVERY_OUTPUT=$(apptainer exec \
        --bind "${DATA_DIR}:/powertwin_data" \
        --bind "${USER_FILES_DIR}:/powertwin-solver-pg/user_files" \
        --bind "${HPC_SHARED_STORAGE}:${HPC_SHARED_STORAGE}" \
        --bind "${SQLITE_DB_DIR}:/sqlite_data" \
        --bind "${LOG_DIR}:/solver/logs" \
        --env "SIMULATION_NAME=${RECOVERY_SIMULATION_NAME}" \
        --env "SLURM_JOB_ID=${SLURM_JOB_ID}" \
        --env "PYTHONPATH=/solver" \
        --env "PYTHONDONTWRITEBYTECODE=1" \
        --env "POWERTWIN_LOG_DIR=/solver/logs" \
        --env "SQLITE_DB_PATH=${SQLITE_DB_PATH}" \
        --env "PGDATABASE=powertwin" \
        --env "HPC_SHARED_STORAGE=${HPC_SHARED_STORAGE}" \
        --env "POWERTWIN_KEEP_DIRS=${POWERTWIN_KEEP_DIRS}" \
        --workdir /solver \
        "${SOLVER_SIF}" bash -c "${RECOVERY_CMD}" \
        2>&1 | tee "${LOG_DIR}/powertwin_recovery_${SLURM_JOB_ID}.log")
    
    RECOVERY_EXIT_CODE=${PIPESTATUS[0]}
    if [ $RECOVERY_EXIT_CODE -ne 0 ]; then
        handle_error "Simulation recovery failed with exit code ${RECOVERY_EXIT_CODE}" 1
    fi
    
    return 0
}

#------------------------------------------------------------------------------
# FUNCTION: process_batches
# Description: Processes batches in parallel using SLURM
# Arguments: None
# Returns: 0 on success
#------------------------------------------------------------------------------
process_batches() {
    
    srun --mpi=pmix --exclusive \
    apptainer exec \
        --bind "${DATA_DIR}:/powertwin_data:rw" \
        --bind "${USER_FILES_DIR}:/powertwin-solver-pg/user_files:rw" \
        --bind "${HPC_SHARED_STORAGE}:${HPC_SHARED_STORAGE}:rw" \
        --bind "${LOG_DIR}:/solver/logs:rw" \
        --bind "${NODE_TMP_DIR}:${NODE_TMP_DIR}:rw" \
        --env "TMPDIR=${NODE_TMP_DIR}" \
        --env "TMP=${NODE_TMP_DIR}" \
        --env "TEMP=${NODE_TMP_DIR}" \
        --env "PROCESS_ID=${PROCESS_ID}" \
        --env "GEM_HOME=${GEM_HOME}" \
        --env "GEM_PATH=${GEM_PATH}" \
        --env "SIMULATION_NAME=${SIMULATION_NAME}" \
        --env "SLURM_JOB_ID=${SLURM_JOB_ID}" \
        --env "PYTHONPATH=/solver" \
        --env "PYTHONDONTWRITEBYTECODE=1" \
        --env "POWERTWIN_LOG_DIR=/solver/logs" \
        --env "SQLITE_DB_PATH=${SQLITE_DB_PATH}" \
        --env "POWERTWIN_STEP=parallel" \
        --env "POWERTWIN_KEEP_DIRS=${POWERTWIN_KEEP_DIRS}" \
        --workdir /solver \
        "${SOLVER_SIF}" python -m app.direct_runner run-parallel-batches \
        "${RECOVERY_DIR}" \
        "${RECOVERY_DIR_LOCAL}" \
        "${RECOVERY_SIMULATION_NAME}" \
    2>&1 | tee "${LOG_DIR}/powertwin_batches_${SLURM_JOB_ID}.log"
    
    return 0
}

#------------------------------------------------------------------------------
# FUNCTION: consolidate_databases
# Description: Consolidates node databases after parallel processing
# Arguments: None
# Returns: 0 on success
#------------------------------------------------------------------------------
consolidate_databases() {
    
    apptainer exec \
        --bind "${DATA_DIR}:/powertwin_data" \
        --bind "${USER_FILES_DIR}:/powertwin-solver-pg/user_files" \
        --bind "${HPC_SHARED_STORAGE}:${HPC_SHARED_STORAGE}" \
        --bind "${SQLITE_DB_DIR}:/sqlite_data" \
        --bind "${LOG_DIR}:/solver/logs" \
        --env "POWERTWIN_LOG_DIR=/solver/logs" \
        --env "SQLITE_DB_PATH=${SQLITE_DB_PATH}" \
        --env "PGDATABASE=powertwin" \
        --env "POWERTWIN_STEP=consolidate" \
        --workdir /solver \
        "${SOLVER_SIF}" bash -c "cd /solver && python -m app.direct_runner consolidate-databases \"${RECOVERY_SIMULATION_NAME}\""
    
    CONSOLIDATE_EXIT_CODE=$?
    if [ $CONSOLIDATE_EXIT_CODE -eq 0 ]; then
        print_status "info" "Database consolidation completed successfully"
        
        # Only clean up SQLite directory after SUCCESSFUL consolidation
        print_status "info" "Cleaning up SQLite directory after successful consolidation..."
        if [ -d "${SQLITE_DB_DIR}" ]; then
            # Find and remove any files that are not powertwin.db
            find "${SQLITE_DB_DIR}" -type f ! -name "powertwin.db" -delete 2>/dev/null
            # Remove any temporary SQLite files like wal, shm files from previous operations
            find "${SQLITE_DB_DIR}" -name "*.db-wal" -delete 2>/dev/null
            find "${SQLITE_DB_DIR}" -name "*.db-shm" -delete 2>/dev/null
            # Remove any node-specific database files ONLY after successful consolidation
            find "${SQLITE_DB_DIR}" -name "*node*" -delete 2>/dev/null
            find "${SQLITE_DB_DIR}" -name "*temp*" -delete 2>/dev/null
            print_status "info" "SQLite directory cleanup completed - kept only powertwin.db"
        fi
    else
        print_status "error" "Database consolidation FAILED with exit code: $CONSOLIDATE_EXIT_CODE"
        print_status "error" "PRESERVING ALL node databases for manual recovery"
        print_status "error" "Do NOT restart recovery until databases are manually consolidated"
        
        # List preserved databases for manual recovery
        if [ -d "${SQLITE_DB_DIR}" ]; then
            print_status "info" "Preserved node databases for manual recovery:"
            find "${SQLITE_DB_DIR}" -name "*node*" -type f -exec basename {} \; 2>/dev/null | sort
            node_db_count=$(find "${SQLITE_DB_DIR}" -name "*node*" -type f | wc -l)
            print_status "info" "Total preserved node databases: ${node_db_count}"
            print_status "info" "Manual consolidation command: python -m app.direct_runner consolidate-databases \"${RECOVERY_SIMULATION_NAME}\""
        fi
        
        # Return failure to stop the workflow
        return 1
    fi
    
    return 0
}

#------------------------------------------------------------------------------
# FUNCTION: generate_final_status
# Description: Generates final status summary after consolidation
# Arguments: None
# Returns: 0 on success
#------------------------------------------------------------------------------
generate_final_status() {
    
    final_status=$(apptainer exec \
        --bind "${DATA_DIR}:/powertwin_data" \
        --bind "${USER_FILES_DIR}:/powertwin-solver-pg/user_files" \
        --bind "${HPC_SHARED_STORAGE}:${HPC_SHARED_STORAGE}" \
        --bind "${LOG_DIR}:/solver/logs" \
        --env "POWERTWIN_LOG_DIR=/solver/logs" \
        --env "SQLITE_DB_PATH=${SQLITE_DB_PATH}" \
        --env "PGDATABASE=${PGDATABASE}" \
        --env "POWERTWIN_STEP=consolidate" \
        "${SOLVER_SIF}" bash -c "cd /solver && python -m app.direct_runner get-simulation-summary \"${SIMULATION_NAME}\"" \
        2>/dev/null)
    
    FINAL_STATUS_EXIT_CODE=$?
    if [ $FINAL_STATUS_EXIT_CODE -eq 0 ] && [ -n "$final_status" ]; then
        print_status "info" "Final Status Summary: $final_status"
    else
        print_status "warning" "Final status query failed or returned no data"
    fi
    
    return 0
}

#------------------------------------------------------------------------------
# FUNCTION: cleanup_resources
# Description: Cleans up resources and monitoring processes
# Arguments: None
# Returns: 0 on success
#------------------------------------------------------------------------------
cleanup_resources() {
    # Stop the XML validation cleanup
    if [ -f "${XML_CLEANUP_PID_FILE}" ]; then
        XML_CLEANUP_PID=$(cat "${XML_CLEANUP_PID_FILE}")
        if kill -0 ${XML_CLEANUP_PID} 2>/dev/null; then
            print_status "info" "Stopping XML validation cleanup (PID ${XML_CLEANUP_PID})..."
            kill ${XML_CLEANUP_PID}
            
            # One final cleanup of XML validation directories
            find /tmp -type d -name "xmlvalidation-????-????-????-????-??????????-?" -print0 2>/dev/null | xargs -0 rm -rf 2>/dev/null
            print_status "info" "Final XML validation directories cleanup completed."
        fi
        rm -f "${XML_CLEANUP_PID_FILE}"
    fi
    
    # Clean up temporary files
    print_status "info" "Cleaning up resources..."
    cleanup_temp_files
    
    return 0
}

#------------------------------------------------------------------------------
# FUNCTION: main
# Description: Main execution flow of the script
# Arguments: None
# Returns: 0 on success, non-zero on failure
#------------------------------------------------------------------------------
main() {
    print_status "info" "Step 1: Starting PowerTwin recovery process for simulation: ${RECOVERY_SIMULATION_NAME}"
    initialize_environment || return 1
    print_status "info" "Environment initialization completed successfully."

    print_status "info" "Step 2: Starting simulation recovery..."
    recover_simulation || return 1
    print_status "info" "Simulation recovery completed successfully."
    
    print_status "info" "Step 3: Starting batch processing..."
    process_batches || return 1
    print_status "info" "Batch processing completed successfully."
    
    print_status "info" "Step 4: Starting database consolidation..."
    consolidate_databases || return 1
    print_status "info" "Database consolidation completed successfully."
    
    print_status "info" "Step 5: Generating final status summary..."
    generate_final_status || return 1
    print_status "info" "Final status summary generated successfully."
    
    print_status "info" "Step 6: Cleaning up resources..."
    cleanup_resources
    print_status "info" "PowerTwin recovery completed successfully."
    return 0
}

# Execute the main function
main