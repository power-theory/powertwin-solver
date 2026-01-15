#!/bin/bash
#SBATCH --job-name=test-start
#SBATCH --nodes=5                   
#SBATCH --ntasks-per-node=1        
#SBATCH --cpus-per-task=5          
#SBATCH --time=7-00:00:00           
#SBATCH --mem-per-cpu=6G            
#SBATCH --account=cowy-ptheory
#SBATCH --partition=teton            # Teton partition
#SBATCH --output=%x_%j.out
#SBATCH --qos=long                  #debug or long

# PowerTwin HPC Container Orchestration Script with Direct SLURM Parallelism
# This script uses a job array approach with proper SLURM step management
# where each step has a clear purpose and dependencies are properly handled

set -e  # Exit immediately if a command exits with a non-zero status

module --force purge
module load arcc/1.0
module load slurm
module load miniconda3/24.3.0
module load gcc/14.2.0
module load apptainer/1.4.1

# =====================================================
# Configuration Variables - MODIFY THESE AS NEEDED
# =====================================================
SIMULATION_NAME="test1"
HPC_SHARED_STORAGE="/project/cowy-ptheory/test"
UPLOAD_DIR="${HPC_SHARED_STORAGE}/upload"
ASSET_GEOJSON_PATH="${UPLOAD_DIR}/${SIMULATION_NAME}/asu-asset-geometries.geojson"
METADATA_CSV_PATH="${UPLOAD_DIR}/${SIMULATION_NAME}/asu-metadata.csv"
CONFIG_JSON_PATH="${UPLOAD_DIR}/${SIMULATION_NAME}/default_config.json"

# Container configuration
# SQLite configuration for HPC environment
SQLITE_DB_DIR="${HPC_SHARED_STORAGE}/powertwin_data/sqlite"
SQLITE_DB_PATH="${SQLITE_DB_DIR}/powertwin.db"

# Database environment variables
export DATABASE_TYPE="sqlite"
export SQLITE_DB_PATH="${SQLITE_DB_PATH}"
export SQLDATABASE="powertwin"  # Table name for compatibility

# HPC networking and MPI environment variables
export RDMAV_FORK_SAFE=1
export IBV_FORK_SAFE=1
export OMPI_MCA_btl_vader_single_copy_mechanism=none
export OMPI_MCA_mpi_warn_on_fork=0
export OMPI_MCA_btl="^openib"
export OMPI_MCA_mpi_leave_pinned=0

# SIF files location
SIF_DIR="${HPC_SHARED_STORAGE}/sif_containers"
SOLVER_SIF="${SIF_DIR}/flask.sif"

# Shared directories
DATA_DIR="${HPC_SHARED_STORAGE}/powertwin_data"
USER_FILES_DIR="${HPC_SHARED_STORAGE}/user_files"
LOG_DIR="${HPC_SHARED_STORAGE}/logs"

TMP_BASE="${HPC_SHARED_STORAGE}/tmp"
export NODE_ID=$(hostname -s)
export NODE_TMP_DIR="${TMP_BASE}/node_${NODE_ID}_${SLURM_JOB_ID}"

# Clean up any leftover files from previous runs with the same job ID pattern
if [ -d "${NODE_TMP_DIR}" ]; then
    print_status "warning" "Found existing node temp directory, cleaning up leftover files..."
    find "${NODE_TMP_DIR}" -name "*.pid" -type f -delete 2>/dev/null
    sleep 2  # Give time for processes to fully terminate
fi

mkdir -p "${NODE_TMP_DIR}"

# Redirect temporary files to our custom location
export TMPDIR="${NODE_TMP_DIR}"
export TMP="${NODE_TMP_DIR}"
export TEMP="${NODE_TMP_DIR}"

# Temporary directories for this job - create unique per process to avoid race conditions
export PROCESS_ID="${SLURM_JOB_ID}_${SLURM_PROCID}_$$"
export GEM_HOME="${NODE_TMP_DIR}/gems_${PROCESS_ID}"
export GEM_PATH="${GEM_HOME}:/usr/local/lib/ruby/gems/3.2.2"
export HOME="${NODE_TMP_DIR}/home_${PROCESS_ID}"
export BUNDLE_PATH="${GEM_HOME}"
export RUBYLIB="${GEM_HOME}/lib"
export NETWORK_DIR="${NODE_TMP_DIR}/apptainer_network_${SLURM_JOB_ID}"
export STATUS_MONITOR_PID_FILE="${NODE_TMP_DIR}/status_monitor_${SLURM_JOB_ID}.pid"

mkdir -p "$GEM_HOME" "$HOME" "$NETWORK_DIR" "$LOG_DIR" "$USER_FILES_DIR" "$LOG_DIR/${SLURM_JOB_ID}"

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

# =====================================================
# Functions
# =====================================================

# Function to print colored output
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

# Check if SIF files exist
check_sif_files() {
    print_status "info" "Checking SIF container files..."
    
    if [ ! -f "$SOLVER_SIF" ]; then
        print_status "error" "Solver SIF file not found at: $SOLVER_SIF"
        return 1
    fi
    
    print_status "info" "Required SIF files found (Solver only)."
    return 0
}

# Create shared directories
create_shared_dirs() {
    print_status "info" "Creating shared directories..."
    
    mkdir -p "${LOG_DIR}"
    mkdir -p "${USER_FILES_DIR}"
    mkdir -p "${NETWORK_DIR}"
    
    # Check if directories were created successfully
    if [ ! -d "${DATA_DIR}" ] || [ ! -d "${LOG_DIR}" ] || [ ! -d "${USER_FILES_DIR}" ]; then
        print_status "error" "Failed to create shared directories."
        return 1
    fi
    
    print_status "info" "Shared directories created successfully."
    return 0
}

# Validate input files
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

    print_status "info" "Input files validated and were located successfully within the ${UPLOAD_DIR}/${SIMULATION_NAME} directory."
    return 0
}

# Check SQLite database directory and initialize if needed
check_sqlite_database() {
    print_status "info" "Checking SQLite database..."
    
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

# Function to clean up temporary files created during simulation
cleanup_temp_files() {
    print_status "info" "Cleaning up temporary files in /tmp..."
    
    # Remove apptainer network directory
    if [ -d "${NETWORK_DIR}" ]; then
        rm -rf "${NETWORK_DIR}"
        print_status "info" "Removed apptainer network directory: ${NETWORK_DIR}"
    fi
    
    # Clean up any temporary files containing the SLURM job ID
    if [ -n "${SLURM_JOB_ID}" ]; then
        # Remove any temporary files with the job ID pattern in /tmp
        find /tmp -name "*${SLURM_JOB_ID}*" -type f -delete 2>/dev/null
        find /tmp -name "*${SLURM_JOB_ID}*" -type d -exec rm -rf {} \; 2>/dev/null
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
    
    # As a fallback, still try to clean specific patterns from system /tmp
    if [ -d "/tmp/OpenStudio" ]; then
        rm -rf /tmp/OpenStudio* 2>/dev/null
        print_status "info" "Removed OpenStudio temporary directories"
    fi
    
    # Clean up EnergyPlus temporary directories
    if [ -d "/tmp/Temp-" ]; then
        rm -rf /tmp/Temp-* 2>/dev/null
        print_status "info" "Removed EnergyPlus temporary directories"
    fi
    
    # Clean up UrbanOpt temporary directories
    if [ -d "/tmp/urbanopt" ]; then
        rm -rf /tmp/urbanopt* 2>/dev/null
        print_status "info" "Removed UrbanOpt temporary directories"
    fi
    
    # Clean up any Ruby temporary directories that might be created
    if [ -d "/tmp/ruby" ]; then
        rm -rf /tmp/ruby* 2>/dev/null
        print_status "info" "Removed Ruby temporary directories"
    fi

    if [ -f "${STATUS_MONITOR_PID_FILE}" ]; then
        rm -f "${STATUS_MONITOR_PID_FILE}"
        print_status "info" "Removed status monitor PID file"
    fi
    
    # Clean up any remaining apptainer temporary files
    find /tmp -name "apptainer-*" -type d -delete 2>/dev/null
    
    print_status "info" "Temporary file cleanup completed"
}

# Function to handle termination signals
handle_termination() {
    local signal_name=$1
    print_status "warning" "Received ${signal_name} signal. Performing emergency cleanup..."
    
    # Clean up temporary files
    cleanup_temp_files
    
    # Kill status monitoring if it's running
    if [ -f "${STATUS_MONITOR_PID_FILE}" ]; then
        MONITOR_PID=$(cat "${STATUS_MONITOR_PID_FILE}")
        if kill -0 ${MONITOR_PID} 2>/dev/null; then
            kill ${MONITOR_PID}
            rm -f "${STATUS_MONITOR_PID_FILE}"
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

# Add this function to the script, before the main() function
monitor_simulation_status() {
    local simulation_name=$1
    local interval_seconds=$2
    local log_file=$3
    
    print_status "info" "Starting simulation status monitoring every $((interval_seconds/60)) minutes..."
    
    local zero_progress_count=0
    local max_zero_progress=4  # Exit after 4 consecutive zero progress checks (1 hour)
    
    while true; do
        # Get current timestamp
        local timestamp=$(date "+%Y-%m-%d %H:%M:%S")
        
        print_status "info" "[$timestamp] Running periodic status check..."
        
        # Run the status command using the same container setup
        status_output=$(apptainer exec \
            --bind "${DATA_DIR}:/powertwin_data" \
            --bind "${USER_FILES_DIR}:/powertwin-solver-pg/user_files" \
            --bind "${HPC_SHARED_STORAGE}:${HPC_SHARED_STORAGE}" \
            --bind "${LOG_DIR}:/solver/logs" \
            --env "SIMULATION_NAME=${simulation_name}" \
            --env "PYTHONPATH=/solver" \
            --env "POWERTWIN_LOG_DIR=/solver/logs" \
            --env "DATABASE_TYPE=${DATABASE_TYPE}" \
            --env "SQLITE_DB_PATH=${SQLITE_DB_PATH}" \
            --env "SQLDATABASE=${SQLDATABASE}" \
            --workdir /solver \
            "${SOLVER_SIF}" python -c "
from modules.diagnostics.read_status import read_simulation_status
import os
simulation_name = '${simulation_name}'
read_simulation_status(simulation_name)
" 2>&1)
        
        echo "${status_output}" >> "${log_file}"
        
        # Check for zero progress and increment counter if found
        if echo "${status_output}" | grep -q "0/[0-9].*\[0.0 %\]"; then
            zero_progress_count=$((zero_progress_count + 1))
            print_status "warning" "[$timestamp] Zero progress detected (${zero_progress_count}/${max_zero_progress})"
            
            if [ ${zero_progress_count} -ge ${max_zero_progress} ]; then
                print_status "error" "Simulation appears stuck with zero progress for $((max_zero_progress * interval_seconds / 60)) minutes. Exiting monitoring."
                echo "[$timestamp] ERROR: Simulation stuck - zero progress for $((max_zero_progress * interval_seconds / 60)) minutes" >> "${log_file}"
                return 1
            fi
        else
            # Reset counter if progress is detected
            zero_progress_count=0
        fi
        
        # Sleep for the specified interval
        sleep ${interval_seconds}
    done
}

# Main script execution
main() {

    # Define simulation directories directly
    SIMULATION_DIR="${DATA_DIR}/${SIMULATION_NAME}"
    LOCAL_SIMULATION_DIR="${USER_FILES_DIR}/${SIMULATION_NAME}"
    
    # SQLite database setup
    print_status "info" "Setting up SQLite database..."
    check_sqlite_database
    print_status "info" "SQLite database directory prepared: ${SQLITE_DB_DIR}"
    print_status "info" "Using SQLite database at: ${SQLITE_DB_PATH}"
    
    # All validation and setup
    check_sif_files || exit 1
    create_shared_dirs || exit 1
    validate_input_files || exit 1
    
    # Display SLURM job information
    print_status "info" "======= SLURM Job Information ======="
    print_status "info" "Job ID: ${SLURM_JOB_ID}"
    print_status "info" "Number of nodes: ${SLURM_JOB_NUM_NODES}"
    print_status "info" "Tasks per node: ${SLURM_NTASKS_PER_NODE}"
    print_status "info" "Total cores: ${TOTAL_CORES}"
    print_status "info" "==================================="
    
    # STEP 1: Run initialization as a separate SLURM step (feature files creation)
    print_status "info" "STEP 1: Creating feature files..."
    
    apptainer exec \
        --bind "${DATA_DIR}:/powertwin_data" \
        --bind "${USER_FILES_DIR}:/powertwin-solver-pg/user_files" \
        --bind "${HPC_SHARED_STORAGE}:${HPC_SHARED_STORAGE}" \
        --bind "${LOG_DIR}:/solver/logs" \
        --env "POWERTWIN_LOG_DIR=/solver/logs" \
        --env "DATABASE_TYPE=${DATABASE_TYPE}" \
        --env "SQLITE_DB_PATH=${SQLITE_DB_PATH}" \
        --env "SQLDATABASE=${SQLDATABASE}" \
        --env "POWERTWIN_STEP=setup" \
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
        print_status "error" "Feature files creation failed with exit code ${FEATURE_FILES_EXIT_CODE}"
        exit 1
    fi
    
    # STEP 2: Initialize UrbanOpt (single process, uses master database)
    print_status "info" "STEP 2: Initializing UrbanOpt..."

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
      --env "DATABASE_TYPE=${DATABASE_TYPE}" \
      --env "SQLITE_DB_PATH=${SQLITE_DB_PATH}" \
      --env "SQLDATABASE=${SQLDATABASE}" \
      --env "POWERTWIN_STEP=setup" \
      --workdir /powertwin_data \
      "${SOLVER_SIF}" python -m app.direct_runner initialize-uo \
        "${SIMULATION_DIR}" \
        "${LOCAL_SIMULATION_DIR}" \
        "${SIMULATION_NAME}" \
        2>&1 | tee "${LOG_DIR}/powertwin_init_${SLURM_JOB_ID}.log")

    TOTAL_BATCHES=$(echo "$INIT_UO_OUTPUT" | grep -oP 'returned \K[0-9]+(?= batches)' | tail -1)
    if [[ -z "$TOTAL_BATCHES" ]]; then
        print_status "error" "Could not determine total batch count from UrbanOpt initialization."
        exit 1
    fi

    print_status "info" "UrbanOpt initialization returned ${TOTAL_BATCHES} batches."

    
    # Set up signal traps for graceful termination
    trap 'handle_termination SIGTERM' SIGTERM
    trap 'handle_termination SIGINT' SIGINT
    trap 'handle_termination SIGHUP' SIGHUP
    trap 'handle_termination EXIT' EXIT

    # Create a specific log file for status updates
    STATUS_LOG_FILE="${LOG_DIR}/powertwin_status_${SLURM_JOB_ID}.log"
    touch "${STATUS_LOG_FILE}"

    # Start status monitoring in the background (every 15 minutes)
    monitor_simulation_status "${SIMULATION_NAME}" 900 "${STATUS_LOG_FILE}" &
    MONITOR_PID=$!

    # Store the PID for later cleanup
    echo ${MONITOR_PID} > "${STATUS_MONITOR_PID_FILE}"

    print_status "info" "Status monitoring started with PID ${MONITOR_PID}, logs at ${STATUS_LOG_FILE}"

    
    # STEP 3: Run parallel batch processing with SQLite
    print_status "info" "STEP 3: Running parallel batch processing with SQLite..."

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
        --env "DATABASE_TYPE=${DATABASE_TYPE}" \
        --env "SQLITE_DB_PATH=${SQLITE_DB_PATH}" \
        --env "SQLDATABASE=${SQLDATABASE}" \
        --env "POWERTWIN_STEP=parallel" \
        --workdir /solver \
        "${SOLVER_SIF}" python -m app.direct_runner run-parallel-batches \
        "${SIMULATION_DIR}" \
        "${LOCAL_SIMULATION_DIR}" \
        "${SIMULATION_NAME}" \
    2>&1 | tee "${LOG_DIR}/powertwin_batches_${SLURM_JOB_ID}.log"

    # Wait for all parallel processes to complete
    wait
    
    print_status "info" "Parallel batch processing completed"


    # Stop the status monitoring
    if [ -f "${STATUS_MONITOR_PID_FILE}" ]; then
        MONITOR_PID=$(cat "${STATUS_MONITOR_PID_FILE}")
        if kill -0 ${MONITOR_PID} 2>/dev/null; then
            print_status "info" "Stopping status monitoring (PID ${MONITOR_PID})..."
            kill ${MONITOR_PID}
            
            # One final status update after completion
            print_status "info" "Running final status check..."
            apptainer exec \
                --bind "${DATA_DIR}:/powertwin_data" \
                --bind "${USER_FILES_DIR}:/powertwin-solver-pg/user_files" \
                --bind "${HPC_SHARED_STORAGE}:${HPC_SHARED_STORAGE}" \
                --bind "${LOG_DIR}:/solver/logs" \
                --bind "${NODE_TMP_DIR}:${NODE_TMP_DIR}:rw" \
                --env "TMPDIR=${NODE_TMP_DIR}" \
                --env "TMP=${NODE_TMP_DIR}" \
                --env "TEMP=${NODE_TMP_DIR}" \
                --env "SIMULATION_NAME=${SIMULATION_NAME}" \
                --env "PYTHONPATH=/solver" \
                --env "POWERTWIN_LOG_DIR=/solver/logs" \
                --env "DATABASE_TYPE=${DATABASE_TYPE}" \
                --env "SQLITE_DB_PATH=${SQLITE_DB_PATH}" \
                --env "SQLDATABASE=${SQLDATABASE}" \
                --workdir /solver \
                "${SOLVER_SIF}" python -c "
    from modules.diagnostics.read_status import read_simulation_status
    import os
    simulation_name = '${SIMULATION_NAME}'
    read_simulation_status(simulation_name)
    " >> "${STATUS_LOG_FILE}" 2>&1
        fi
        rm -f "${STATUS_MONITOR_PID_FILE}"
    fi

    
    # STEP 4: Consolidate node databases back to master
    print_status "info" "STEP 4: Consolidating node databases..."

    apptainer exec \
        --bind "${DATA_DIR}:/powertwin_data" \
        --bind "${USER_FILES_DIR}:/powertwin-solver-pg/user_files" \
        --bind "${HPC_SHARED_STORAGE}:${HPC_SHARED_STORAGE}" \
        --bind "${LOG_DIR}:/solver/logs" \
        --env "POWERTWIN_LOG_DIR=/solver/logs" \
        --env "DATABASE_TYPE=${DATABASE_TYPE}" \
        --env "SQLITE_DB_PATH=${SQLITE_DB_PATH}" \
        --env "SQLDATABASE=${SQLDATABASE}" \
        --env "POWERTWIN_STEP=consolidate" \
        "${SOLVER_SIF}" bash -c "cd /solver && python -m app.direct_runner consolidate-databases \"${SIMULATION_NAME}\""
    
    CONSOLIDATE_EXIT_CODE=$?
    if [ $CONSOLIDATE_EXIT_CODE -eq 0 ]; then
        print_status "info" "Database consolidation completed successfully"
    else
        print_status "warning" "Database consolidation completed with warnings (exit code: $CONSOLIDATE_EXIT_CODE)"
    fi
    
    # Clean up
    print_status "info" "Cleaning up resources..."
    
    print_status "info" "SQLite database - no database services to stop."
    
    # Clean up any temporary files
    print_status "info" "Cleaning up temporary files..."
    cleanup_temp_files
    
    print_status "info" "PowerTwin simulation completed."
    return 0
}

# Execute the main function
main