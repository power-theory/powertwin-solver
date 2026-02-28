#!/bin/bash
#==============================================================================
# PowerTwin HPC Start Script (with Nsight Systems Profiling)
# 
# Description: Orchestrates the start of PowerTwin simulations
#              using containerized execution with SQLite database and SLURM integration.
#
# Usage: sbatch sqlite-start.sh
#==============================================================================

#==============================================================================
# SLURM CONFIGURATION
#==============================================================================
#SBATCH --job-name=test-start
#SBATCH --nodes=20                   
#SBATCH --ntasks-per-node=1        
#SBATCH --cpus-per-task=40          
#SBATCH --time=7-00:00:00           
#SBATCH --account=cowy-nvhackathon 
#SBATCH --output=%x_%j.out


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
module load nvhpc-sdk/25.7
module load nvhpc/25.7

# =====================================================
# Configuration Variables - MODIFY THESE AS NEEDED
# =====================================================
# Simulation parameters
SIMULATION_NAME="teton1"
HPC_SHARED_STORAGE="/project/cowy-nvhackathon/nicolasreategui"
UPLOAD_DIR="${HPC_SHARED_STORAGE}/upload/${SIMULATION_NAME}"
ASSET_GEOJSON_PATH="${UPLOAD_DIR}/7_asset_geometries.geojson"
METADATA_CSV_PATH="${UPLOAD_DIR}/7_metadata.csv"
CONFIG_JSON_PATH="${UPLOAD_DIR}/default_config.json"
POWERTWIN_KEEP_DIRS=1
WITH_NSYS_PROFILING=0

# SIF files location
SIF_DIR="${HPC_SHARED_STORAGE}/sif_containers"
SOLVER_SIF="${SIF_DIR}/flask.sif"

# Shared directories
DATA_DIR="${HPC_SHARED_STORAGE}/powertwin_data"
USER_FILES_DIR="${HPC_SHARED_STORAGE}/user_files"
LOG_DIR="${HPC_SHARED_STORAGE}/logs"
TMP_BASE="${HPC_SHARED_STORAGE}/tmp"
NSYS_REPORTS_DIR="${HPC_SHARED_STORAGE}/nsys_reports"

# SQLite database configuration
SQLITE_DB_DIR="${DATA_DIR}/sqlite"
SQLITE_DB_PATH="${SQLITE_DB_DIR}/powertwin.db"

# Database environment variables
export SQLITE_DB_PATH="${SQLITE_DB_PATH}"
export HPC_SHARED_STORAGE="${HPC_SHARED_STORAGE}"
export PGDATABASE="powertwin"   # Table name used by SQLite operations

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
export HOME="${NODE_TMP_DIR}/home_${PROCESS_ID}"
export XML_CLEANUP_PID_FILE="${NODE_TMP_DIR}/xml_cleanup_${SLURM_JOB_ID}.pid"
export PROCESS_ID="${SLURM_JOB_ID}_${SLURM_PROCID}_$$"

mkdir -p "$GEM_HOME" "$HOME" "$LOG_DIR" "$USER_FILES_DIR"

# Define simulation directories
SIMULATION_DIR="${DATA_DIR}/${SIMULATION_NAME}"
LOCAL_SIMULATION_DIR="${USER_FILES_DIR}/${SIMULATION_NAME}"

# Calculate total tasks and cores from SLURM environment
TOTAL_CORES=$((SLURM_JOB_NUM_NODES * SLURM_CPUS_PER_TASK))

# Export variables for access in child processes
export POWERTWIN_LOG_DIR="${LOG_DIR}"
export SIMULATION_NAME
export HPC_SHARED_STORAGE
export UPLOAD_DIR
export ASSET_GEOJSON_PATH
export METADATA_CSV_PATH
export CONFIG_JSON_PATH
export SIMULATION_DIR
export LOCAL_SIMULATION_DIR

#==============================================================================
# SIGNAL HANDLING
#==============================================================================
# Global flag to track consolidation status
CONSOLIDATION_COMPLETED=0

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
# FUNCTION: create_shared_dirs
# Description: Creates necessary shared directories for the simulation
# Arguments: None
# Returns: 0 on success, 1 on failure
#------------------------------------------------------------------------------
create_shared_dirs() {
    print_status "info" "Creating shared directories..."
    
    mkdir -p "${LOG_DIR}"
    mkdir -p "${USER_FILES_DIR}"
    mkdir -p "${NSYS_REPORTS_DIR}"
    
    # Check if directories were created successfully
    if [ ! -d "${DATA_DIR}" ] || [ ! -d "${LOG_DIR}" ] || [ ! -d "${NSYS_REPORTS_DIR}" ] || [ ! -d "${USER_FILES_DIR}" ]; then
        print_status "error" "Failed to create shared directories."
        return 1
    fi
    
    print_status "info" "Shared directories created successfully."
    return 0
}

#------------------------------------------------------------------------------
# FUNCTION: validate_input_files
# Description: Validates that required input files exist and are accessible
# Arguments: None
# Returns: 0 on success, 1 on failure
#------------------------------------------------------------------------------
validate_input_files() {
    print_status "info" "Validating input files..."
    
    if [ ! -f "$ASSET_GEOJSON_PATH" ]; then
        print_status "error" "Asset GeoJSON file not found at: $ASSET_GEOJSON_PATH"
        return 1
    fi
    
    if [ ! -f "$METADATA_CSV_PATH" ]; then
        print_status "error" "Metadata CSV file not found at: $METADATA_CSV_PATH"
        return 1
    fi
    
    if [ ! -f "$CONFIG_JSON_PATH" ]; then
        print_status "error" "Config JSON file not found at: $CONFIG_JSON_PATH"
        return 1
    fi

    print_status "info" "Input files validated and were located successfully within the ${UPLOAD_DIR} directory."
    return 0
}

#------------------------------------------------------------------------------
# FUNCTION: check_sqlite_database
# Description: Checks SQLite database directory and initializes if needed
# Arguments: None
# Returns: 0 on success, 1 on failure
#------------------------------------------------------------------------------
check_sqlite_database() {
    
    # Create SQLite database directory if it doesn't exist
    if [ ! -d "${SQLITE_DB_DIR}" ]; then
        print_status "info" "Creating SQLite database directory: ${SQLITE_DB_DIR}"
        mkdir -p "${SQLITE_DB_DIR}"
        if [ $? -ne 0 ]; then
            print_status "error" "Failed to create SQLite database directory: ${SQLITE_DB_DIR}"
            return 1
        fi
    fi
    
    # Check if SQLite database file exists
    if [ ! -f "${SQLITE_DB_PATH}" ]; then
        print_status "info" "SQLite database not found. It will be created automatically on first use."
        # This is a normal condition, not an error - return success
        return 0
    fi
    
    # Verify database integrity if sqlite3 command is available
    if command -v sqlite3 >/dev/null 2>&1; then
        if sqlite3 "${SQLITE_DB_PATH}" "PRAGMA integrity_check;" >/dev/null 2>&1; then
            print_status "info" "SQLite database verified successfully: ${SQLITE_DB_PATH}"
            return 0
        else
            print_status "warning" "SQLite database integrity check failed. Database will be recreated on next use."
            # Remove the corrupted database file so it can be recreated
            rm -f "${SQLITE_DB_PATH}"
            print_status "info" "Removed corrupted database file. New database will be created automatically."
            return 0
        fi
    else
        print_status "info" "SQLite command not available, skipping integrity check."
        return 0
    fi
}

#------------------------------------------------------------------------------
# FUNCTION: cleanup_temp_files
# Description: Cleans up temporary files and directories created during simulation
# Arguments: None
# Returns: None
#------------------------------------------------------------------------------
cleanup_temp_files() {
    print_status "info" "Cleaning up temporary files in /tmp..."
    
    
    # Clean up any temporary files containing the SLURM job ID with timeout to prevent hanging
    if [ -n "${SLURM_JOB_ID}" ]; then
        # Use timeout to prevent find operations from hanging on slow filesystems
        timeout 30 find /tmp -maxdepth 2 -name "*${SLURM_JOB_ID}*" -type f -delete 2>/dev/null || true
        timeout 30 find /tmp -maxdepth 2 -name "*${SLURM_JOB_ID}*" -type d -exec rm -rf {} \; 2>/dev/null || true
        print_status "info" "Removed temporary files containing job ID: ${SLURM_JOB_ID}"
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
    
    # Only attempt emergency consolidation if normal consolidation hasn't completed
    # and this is not a normal EXIT from successful completion
    if [ "$CONSOLIDATION_COMPLETED" -eq 0 ] && [ "$signal_name" != "EXIT" ] && [ -n "${SIMULATION_NAME}" ] && [ -f "${SOLVER_SIF}" ]; then
        print_status "info" "Attempting emergency database consolidation to preserve work..."
        print_status "info" "No timeout applied - large databases require extended time"
        
        apptainer exec \
            --bind "${DATA_DIR}:/powertwin_data" \
            --bind "${USER_FILES_DIR}:/powertwin-solver-pg/user_files" \
            --bind "${HPC_SHARED_STORAGE}:${HPC_SHARED_STORAGE}" \
            --bind "${LOG_DIR}:/solver/logs" \
            --env "POWERTWIN_LOG_DIR=/solver/logs" \
            --env "SQLITE_DB_PATH=${SQLITE_DB_PATH}" \
            --env "POWERTWIN_STEP=consolidate" \
            "${SOLVER_SIF}" bash -c "cd /solver && python -m app.direct_runner consolidate-databases \"${SIMULATION_NAME}\"" \
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
        
        # Print status summary after emergency consolidation attempt
        print_status "info" "Generating emergency status summary..."
        
        # Run status summary query without timeout for large databases
        emergency_status=$(apptainer exec \
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
        
        EMERGENCY_STATUS_EXIT_CODE=$?
        if [ $EMERGENCY_STATUS_EXIT_CODE -eq 0 ] && [ -n "$emergency_status" ]; then
            print_status "info" "Emergency Status Summary: $emergency_status"
        else
            print_status "warning" "Emergency status query failed or returned no data (exit code: $EMERGENCY_STATUS_EXIT_CODE)"
        fi
        
    else
        if [ "$CONSOLIDATION_COMPLETED" -eq 1 ]; then
            print_status "info" "Skipping emergency database consolidation - normal consolidation already completed successfully"
        else
            print_status "warning" "Skipping emergency database consolidation - missing required variables or container"
        fi
    fi
    
    # Clean up temporary files
    cleanup_temp_files
    
    print_status "warning" "Emergency cleanup completed. Exiting due to ${signal_name} signal."
    
    # Return the appropriate exit code
    if [ "$signal_name" = "EXIT" ]; then
        exit 0
    else
        exit 1
    fi
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
    check_sqlite_database || handle_error "SQLite database setup failed" 1
    print_status "info" "SQLite database directory prepared: ${SQLITE_DB_DIR}"
    print_status "info" "Using SQLite database at: ${SQLITE_DB_PATH}"
    
    # All validation and setup
    check_sif_files || handle_error "SIF files validation failed" 1
    create_shared_dirs || handle_error "Shared directories creation failed" 1
    validate_input_files || handle_error "Input files validation failed" 1
    
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
# FUNCTION: create_feature_files
# Description: Creates feature files for the simulation
# Arguments: None
# Returns: 0 on success, exits on failure
#------------------------------------------------------------------------------
create_feature_files() {
    
    apptainer exec \
        --bind "${DATA_DIR}:/powertwin_data" \
        --bind "${USER_FILES_DIR}:/powertwin-solver-pg/user_files" \
        --bind "${HPC_SHARED_STORAGE}:${HPC_SHARED_STORAGE}" \
        --bind "${LOG_DIR}:/solver/logs" \
        --env "POWERTWIN_LOG_DIR=/solver/logs" \
        --env "SQLITE_DB_PATH=${SQLITE_DB_PATH}" \
        --env "POWERTWIN_STEP=setup" \
        --env "POWERTWIN_KEEP_DIRS=${POWERTWIN_KEEP_DIRS}" \
        "${SOLVER_SIF}" bash -c "cd /solver && python -m app.direct_runner create-feature-files \
        \"${SIMULATION_NAME}\" \
        \"${ASSET_GEOJSON_PATH}\" \
        \"${METADATA_CSV_PATH}\" \
        \"${CONFIG_JSON_PATH}\" \
        \"${TOTAL_CORES}\" \
        --shared-storage \"${HPC_SHARED_STORAGE}\"" \
        2>&1 | tee "${LOG_DIR}/powertwin_ff_${SLURM_JOB_ID}.log"
    
    FEATURE_FILES_EXIT_CODE=${PIPESTATUS[0]}
    if [ $FEATURE_FILES_EXIT_CODE -ne 0 ]; then
        handle_error "Feature files creation failed with exit code ${FEATURE_FILES_EXIT_CODE}" 1
    fi
    
    return 0
}

#------------------------------------------------------------------------------
# FUNCTION: initialize_urbanopt
# Description: Initializes UrbanOpt simulation
# Arguments: None
# Returns: 0 on success, exits on failure
#------------------------------------------------------------------------------
initialize_urbanopt() {

    INIT_UO_OUTPUT=$(apptainer exec \
      --bind "${DATA_DIR}:/powertwin_data:rw" \
      --bind "${USER_FILES_DIR}:/powertwin-solver-pg/user_files:rw" \
      --bind "${HPC_SHARED_STORAGE}:${HPC_SHARED_STORAGE}:rw" \
      --bind "${LOG_DIR}:/solver/logs:rw" \
      --env "SIMULATION_NAME=${SIMULATION_NAME}" \
      --env "SLURM_JOB_ID=${SLURM_JOB_ID}" \
      --env "PYTHONPATH=/solver" \
      --env "PYTHONDONTWRITEBYTECODE=1" \
      --env "POWERTWIN_LOG_DIR=/solver/logs" \
      --env "SQLITE_DB_PATH=${SQLITE_DB_PATH}" \
      --env "POWERTWIN_STEP=setup" \
      --env "POWERTWIN_KEEP_DIRS=${POWERTWIN_KEEP_DIRS}" \
      --workdir /powertwin_data \
      "${SOLVER_SIF}" python -m app.direct_runner initialize-uo \
        "${SIMULATION_DIR}" \
        "${LOCAL_SIMULATION_DIR}" \
        "${SIMULATION_NAME}" \
        2>&1 | tee "${LOG_DIR}/powertwin_init_${SLURM_JOB_ID}.log")

    TOTAL_BATCHES=$(echo "$INIT_UO_OUTPUT" | grep -oP 'returned \K[0-9]+(?= batches)' | tail -1)
    if [[ -z "$TOTAL_BATCHES" ]]; then
        handle_error "error" "Could not determine total batch count from UrbanOpt initialization." 1
    fi

    print_status "info" "UrbanOpt initialization returned ${TOTAL_BATCHES} batches."
    return 0
}

#------------------------------------------------------------------------------
# FUNCTION: process_batches
# Description: Processes batches in parallel using SLURM
# Arguments: None
# Returns: 0 on success
#------------------------------------------------------------------------------
process_batches() {

    local nsys_cmd=""

    # Check if profiling is enabled (1 = enabled)...
    if [ "${WITH_NSYS_PROFILING}" -eq 1 ]; then
        nsys_cmd="nsys profile \
            --output=${NSYS_REPORTS_DIR}/uo_${SLURM_JOB_ID}_node${NODE_ID}_rank%p \
            --trace=mpi,osrt,openmp,python-gil \
            --mpi-impl=mpich \
            --sample=process-tree \
            --duration=7200 \
            --stats=false"
    fi

    srun --mpi=pmix --exclusive \
    ${nsys_cmd} \
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
        "${SIMULATION_DIR}" \
        "${LOCAL_SIMULATION_DIR}" \
        "${SIMULATION_NAME}" \
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
        --bind "${LOG_DIR}:/solver/logs" \
        --env "POWERTWIN_LOG_DIR=/solver/logs" \
        --env "SQLITE_DB_PATH=${SQLITE_DB_PATH}" \
        --env "PGDATABASE=powertwin" \
        --env "POWERTWIN_STEP=consolidate" \
        --workdir /solver \
        "${SOLVER_SIF}" bash -c "cd /solver && python -m app.direct_runner consolidate-databases \"${SIMULATION_NAME}\""
    
    CONSOLIDATE_EXIT_CODE=$?
    if [ $CONSOLIDATE_EXIT_CODE -eq 0 ]; then
        print_status "info" "Database consolidation completed successfully"
        
        # Mark consolidation as completed to prevent emergency re-consolidation
        CONSOLIDATION_COMPLETED=1
        
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
        print_status "error" "Do NOT restart simulation until databases are manually consolidated"
        
        # List preserved databases for manual recovery
        if [ -d "${SQLITE_DB_DIR}" ]; then
            print_status "info" "Preserved node databases for manual recovery:"
            find "${SQLITE_DB_DIR}" -name "*node*" -type f -exec basename {} \; 2>/dev/null | sort
            node_db_count=$(find "${SQLITE_DB_DIR}" -name "*node*" -type f | wc -l)
            print_status "info" "Total preserved node databases: ${node_db_count}"
            print_status "info" "Manual consolidation command: python -m app.direct_runner consolidate-databases \"${SIMULATION_NAME}\""
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
# Description: Cleans up resources and temporary files
# Arguments: None
# Returns: 0 on success
#------------------------------------------------------------------------------
cleanup_resources() {    
    
    # Clean up any temporary files
    print_status "info" "Cleaning up temporary files..."
    cleanup_temp_files
    
    print_status "info" "Resource cleanup completed successfully."
    return 0
}

#------------------------------------------------------------------------------
# FUNCTION: main
# Description: Main execution flow of the script
# Arguments: None
# Returns: 0 on success, non-zero on failure
#------------------------------------------------------------------------------
main() {
    print_status "info" "Step 1: Starting PowerTwin simulation for: ${SIMULATION_NAME}"
    initialize_environment || return 1
    print_status "info" "Environment initialization completed successfully."
    
    print_status "info" "Step 2: Creating feature files..."
    create_feature_files || return 1
    print_status "info" "Feature files created successfully."
    
    print_status "info" "Step 3: Initializing UrbanOpt..."
    initialize_urbanopt || return 1
    print_status "info" "UrbanOpt initialization completed successfully."

    print_status "info" "Step 4: Processing batches..."
    process_batches || return 1
    print_status "info" "Batch processing completed successfully."
    
    print_status "info" "Step 5: Consolidating databases..."
    consolidate_databases || return 1
    print_status "info" "Database consolidation completed successfully."
    
    print_status "info" "Step 6: Generating final status..."
    generate_final_status || return 1
    print_status "info" "Final status generated successfully."
    
    print_status "info" "Step 7: Cleaning up resources..."
    cleanup_resources
    print_status "info" "Resource cleanup completed successfully."
    
    print_status "info" "PowerTwin simulation completed successfully."
    return 0
}

# Execute the main function
main