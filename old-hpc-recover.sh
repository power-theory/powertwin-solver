#!/bin/bash
#SBATCH --job-name=recover
#SBATCH --nodes=8       
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=30
#SBATCH --time=7-00:00:00            
#SBATCH --mem-per-cpu=8G             
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
RECOVERY_SIMULATION_NAME="wyoming29"
CORRUPTED_SIMULATION_NAME="wyoming28"
# Batch ID is optional - leave empty to recover all batches, or specify a batch number
BATCH_ID=""
HPC_SHARED_STORAGE="/project/cowy-ptheory/powertwin"

# Container configuration
PG_USER="postgres"
PG_PASSWORD="admin"
PG_DB="powertwin"


# PostgreSQL environment variables (used by client tools and applications)
export PGHOST="localhost"
export PGUSER="${PG_USER}"
export PGPASSWORD="${PG_PASSWORD}"
export PGDATABASE="${PG_DB}"
export POSTGRES_HOST_AUTH_METHOD="trust"

# SIF files location
SIF_DIR="${HPC_SHARED_STORAGE}/sif_containers"
MSS_SIF="${SIF_DIR}/mss.sif"
SOLVER_SIF="${SIF_DIR}/flask.sif"

# Shared directories
DATA_DIR="${HPC_SHARED_STORAGE}/powertwin_data"
USER_FILES_DIR="${HPC_SHARED_STORAGE}/user_files"
LOG_DIR="${HPC_SHARED_STORAGE}/logs"

# Use TMP_BASE for all temporary directories to ensure consistency and avoid /tmp disk space issues
TMP_BASE="${HPC_SHARED_STORAGE}/tmp"

# Create a node-specific temp directory to avoid /tmp disk space issues
export NODE_ID=$(hostname -s)
export NODE_TMP_DIR="${TMP_BASE}/node_${NODE_ID}_${SLURM_JOB_ID}"
mkdir -p "${NODE_TMP_DIR}"

# Redirect temporary files to our custom location
export TMPDIR="${NODE_TMP_DIR}"
export TMP="${NODE_TMP_DIR}"
export TEMP="${NODE_TMP_DIR}"

export GEM_HOME="${NODE_TMP_DIR}/gems_${SLURM_JOB_ID}"
export GEM_PATH="${GEM_HOME}:/usr/local/lib/ruby/gems/2.7.0"
export HOME="${NODE_TMP_DIR}/home_${SLURM_JOB_ID}_${SLURM_PROCID}"
NETWORK_DIR="${NODE_TMP_DIR}/apptainer_network_${SLURM_JOB_ID}"
PG_PID_FILE="${NODE_TMP_DIR}/postgres_${SLURM_JOB_ID}.pid"
STATUS_MONITOR_PID_FILE="${NODE_TMP_DIR}/status_monitor_${SLURM_JOB_ID}.pid"
XML_CLEANUP_PID_FILE="${NODE_TMP_DIR}/xml_cleanup_${SLURM_JOB_ID}.pid"

mkdir -p "$GEM_HOME" "$HOME" "$NETWORK_DIR"

DB_DATA_DIR="${DATA_DIR}/postgres_data"  # Use existing PostgreSQL data directory

CORRUPTED_SIMULATION_DIR="${USER_FILES_DIR}/${CORRUPTED_SIMULATION_NAME}"
RECOVERY_DIR_LOCAL="${USER_FILES_DIR}/${RECOVERY_SIMULATION_NAME}"
RECOVERY_DIR="${DATA_DIR}/${RECOVERY_SIMULATION_NAME}"

# Export variables for access in child processes
export POWERTWIN_LOG_DIR="${LOG_DIR}"
export RECOVERY_SIMULATION_NAME
export HPC_SHARED_STORAGE


# Calculate total tasks and cores from SLURM environment
TOTAL_TASKS=$((SLURM_JOB_NUM_NODES * SLURM_NTASKS_PER_NODE))
TOTAL_CORES=$((TOTAL_TASKS * SLURM_CPUS_PER_TASK))

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
    
    if [ ! -f "$MSS_SIF" ]; then
        print_status "error" "MSS SIF file not found at: $MSS_SIF"
        return 1
    fi
    
    if [ ! -f "$SOLVER_SIF" ]; then
        print_status "error" "Solver SIF file not found at: $SOLVER_SIF"
        return 1
    fi
    
    print_status "info" "All SIF files found."
    return 0
}

# Create shared directories
setup_dirs() {
    print_status "info" "Creating shared directories..."
    
    # Create base directories if they don't exist
    mkdir -p "${LOG_DIR}"
    mkdir -p "${USER_FILES_DIR}"
    # NETWORK_DIR is already created at the top of the script

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

# Check PostgreSQL data directory
check_postgres_data() {
    print_status "info" "Checking PostgreSQL data directory..."
    
    # Check if the PostgreSQL data directory exists and has data files
    if [ ! -d "${DB_DATA_DIR}" ]; then
        print_status "error" "PostgreSQL data directory not found at: ${DB_DATA_DIR}"
        return 1
    fi
    
    # Check for critical PostgreSQL files
    if [ ! -f "${DB_DATA_DIR}/PG_VERSION" ]; then
        print_status "error" "Invalid PostgreSQL data directory: ${DB_DATA_DIR}/PG_VERSION not found"
        return 1
    fi
    
    # Check PostgreSQL version and set connection settings
    PG_VERSION=$(cat "${DB_DATA_DIR}/PG_VERSION" 2>/dev/null || echo "unknown")
    print_status "info" "Found PostgreSQL data directory with version: ${PG_VERSION}"
    
    # Set the connection parameters to use the existing database
    export PGHOST="localhost"
    export PGUSER="${PG_USER}"
    export PGPASSWORD="${PG_PASSWORD}"
    export PGDATABASE="${PG_DB}"
    
    print_status "info" "PostgreSQL data directory validated."
    return 0
}

# Start PostgreSQL server
start_postgres() {
    print_status "info" "Starting PostgreSQL server..."
    
    # Check available space in /tmp and shared storage
    print_status "info" "Checking available disk space:"
    df -h /tmp "${NODE_TMP_DIR}"
    
    # Clean up any existing PostgreSQL socket files in /tmp
    find /tmp -name ".s.PGSQL.*" -exec rm -f {} \; 2>/dev/null
    
    # Create a socket directory in node-specific temp directory
    PG_SOCKET_DIR="${NODE_TMP_DIR}/pg_socket_${SLURM_JOB_ID}"
    mkdir -p "${PG_SOCKET_DIR}"
    chmod 0700 "${PG_SOCKET_DIR}"
    
    PG_SIF="${SIF_DIR}/postgres17.sif"
    if [ ! -f "${PG_SIF}" ]; then
        print_status "warning" "PostgreSQL 17 container not found. Creating it..."
        apptainer build "${PG_SIF}" docker://postgres:17
    fi
    
    apptainer exec \
        --bind "${DB_DATA_DIR}:/data" \
        --bind "${PG_SOCKET_DIR}:/pg_socket" \
        "${PG_SIF}" bash -c "postgres -D /data -h 0.0.0.0 -k /pg_socket" &
    
    # Save PID for later cleanup
    echo $! > "${PG_PID_FILE}"
    
    # Update connection environment variables to use the custom socket
    export PGHOST="${PG_SOCKET_DIR}"
    
    # Wait for PostgreSQL to start up (max 30 seconds)
    print_status "info" "Waiting for PostgreSQL to start..."
    for i in {1..30}; do
        if apptainer exec \
            --bind "${PG_SOCKET_DIR}:/pg_socket" \
            "${PG_SIF}" bash -c "pg_isready -h /pg_socket" &>/dev/null; then
            print_status "info" "PostgreSQL server started successfully."
            break
        fi
        
        if [ $i -eq 30 ]; then
            print_status "error" "PostgreSQL failed to start within 30 seconds."
            return 1
        fi
        
        sleep 1
    done
    
    return 0
}

# Function to stop the PostgreSQL server gracefully
stop_postgres() {
    print_status "info" "Stopping PostgreSQL server..."
    
    # Path to the PID file is now defined at the top of the script
    
    if [ -f "${PG_PID_FILE}" ]; then
        PG_PID=$(cat "${PG_PID_FILE}")
        
        if kill -0 "${PG_PID}" &>/dev/null; then
            # Attempt to stop PostgreSQL gracefully first
            kill -TERM "${PG_PID}"
            
            # Wait for up to 10 seconds for PostgreSQL to shut down
            for i in {1..10}; do
                if ! kill -0 "${PG_PID}" &>/dev/null; then
                    print_status "info" "PostgreSQL server stopped successfully."
                    break
                fi
                
                if [ $i -eq 10 ]; then
                    print_status "warning" "PostgreSQL did not stop gracefully, forcing shutdown..."
                    kill -9 "${PG_PID}" &>/dev/null
                fi
                
                sleep 1
            done
        else
            print_status "warning" "PostgreSQL process (PID: ${PG_PID}) not found."
        fi
        
        # Remove the PID file
        rm -f "${PG_PID_FILE}"
    else
        print_status "warning" "PostgreSQL PID file not found."
    fi
}

# Function to clean up temporary files created during simulation
cleanup_temp_files() {
    print_status "info" "Cleaning up temporary files..."
    
    # Remove apptainer network directory
    if [ -d "${NETWORK_DIR}" ]; then
        rm -rf "${NETWORK_DIR}"
        print_status "info" "Removed apptainer network directory: ${NETWORK_DIR}"
    fi
    
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

# Function to handle termination signals
handle_termination() {
    local signal_name=$1
    print_status "warning" "Received ${signal_name} signal. Performing emergency cleanup..."
    
    # Stop PostgreSQL gracefully
    stop_postgres
    
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

# Add this function to the script, before the main() function
monitor_simulation_status() {
    local simulation_name=$1
    local interval_seconds=$2
    local log_file=$3
    
    print_status "info" "Starting simulation status monitoring every $((interval_seconds/60)) minutes..."
    
    while true; do
        # Get current timestamp
        local timestamp=$(date "+%Y-%m-%d %H:%M:%S")
        
        print_status "info" "[$timestamp] Running periodic status check..."
        
        # Run the status command using the same container setup
        apptainer exec \
            --bind "${DATA_DIR}:/powertwin_data" \
            --bind "${USER_FILES_DIR}:/powertwin-solver-pg/user_files" \
            --bind "${HPC_SHARED_STORAGE}:${HPC_SHARED_STORAGE}" \
            --bind "${DB_DATA_DIR}:/postgres_data" \
            --bind "${LOG_DIR}:/solver/logs" \
            --env "SIMULATION_NAME=${RECOVERY_SIMULATION_NAME}" \
            --env "PYTHONPATH=/solver" \
            --env "POWERTWIN_LOG_DIR=/solver/logs" \
            --env "POSTGRES_USER=${PG_USER}" \
            --env "POSTGRES_PASSWORD=${PG_PASSWORD}" \
            --env "POSTGRES_DB=${PG_DB}" \
            --env "PGHOST=${DB_HOST}" \
            --env "PGUSER=${PG_USER}" \
            --env "PGPASSWORD=${PG_PASSWORD}" \
            --env "PGDATABASE=${PG_DB}" \
            --workdir /solver \
            "${SOLVER_SIF}" python -c "
from modules.diagnostics.read_status import read_simulation_status
import os
simulation_name = '${RECOVERY_SIMULATION_NAME}'
read_simulation_status(simulation_name)
" >> "${log_file}" 2>&1
        
        # Sleep for the specified interval
        sleep ${interval_seconds}
    done
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

# Main script execution
main() {
    
    # All validation and setup happens in one place - the master script
    check_sif_files || exit 1
    setup_dirs || exit 1
    check_postgres_data || exit 1
    
    # Start PostgreSQL server
    start_postgres || exit 1
    
    # Display SLURM job information
    print_status "info" "======= SLURM Job Information ======="
    print_status "info" "Job ID: ${SLURM_JOB_ID}"
    print_status "info" "Number of nodes: ${SLURM_JOB_NUM_NODES}"
    print_status "info" "Number of tasks: ${SLURM_NTASKS}"
    print_status "info" "Tasks per node: ${SLURM_NTASKS_PER_NODE}"
    print_status "info" "CPUs per task: ${SLURM_CPUS_PER_TASK}"
    print_status "info" "Total cores: ${TOTAL_CORES}"
    print_status "info" "==================================="

    # Determine DB host (the node running this script) and export for all tasks
    DB_HOST="$(hostname -f)"
    export PGHOST="${DB_HOST}"
    print_status "info" "Database host for tasks: ${DB_HOST}"

    # # Count total rows in the main powertwin table
    # apptainer exec ${SIF_DIR}/postgres17.sif \
    # psql -h ${PGHOST} -U ${PGUSER} -d ${PG_DB} -c "
    # SELECT COUNT(*) as total_assets FROM powertwin;
    # "

    # # Count assets by simulation name
    # apptainer exec ${SIF_DIR}/postgres17.sif \
    # psql -h ${PGHOST} -U ${PGUSER} -d ${PG_DB} -c "
    # SELECT simulation_name, COUNT(*) as asset_count
    # FROM powertwin
    # GROUP BY simulation_name
    # ORDER BY simulation_name;
    # "

    # # Count assets by status for each simulation
    # apptainer exec ${SIF_DIR}  /postgres17.sif \
    # psql -h ${PGHOST} -U ${PGUSER} -d ${PG_DB} -c "
    # SELECT simulation_name, status, COUNT(*)
    # FROM powertwin
    # GROUP BY simulation_name, status
    # ORDER BY simulation_name, status;
    # "
    
    # Run simulation recovery using the new direct_runner command
    print_status "info" "Running simulation recovery..."
    
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
        --bind "${DB_DATA_DIR}:/postgres_data" \
        --bind "${LOG_DIR}:/solver/logs" \
        --env "SIMULATION_NAME=${RECOVERY_SIMULATION_NAME}" \
        --env "SLURM_JOB_ID=${SLURM_JOB_ID}" \
        --env "PYTHONPATH=/solver" \
        --env "PYTHONDONTWRITEBYTECODE=1" \
        --env "POWERTWIN_LOG_DIR=/solver/logs" \
        --env "POSTGRES_USER=${PG_USER}" \
        --env "POSTGRES_PASSWORD=${PG_PASSWORD}" \
        --env "POSTGRES_DB=${PG_DB}" \
        --env "PGHOST=${DB_HOST}" \
        --env "PGUSER=${PG_USER}" \
        --env "PGPASSWORD=${PG_PASSWORD}" \
        --env "PGDATABASE=${PG_DB}" \
        --workdir /solver \
        "${SOLVER_SIF}" bash -c "${RECOVERY_CMD}" \
        2>&1 | tee "${LOG_DIR}/powertwin_recovery_${SLURM_JOB_ID}.log")
    
    RECOVERY_EXIT_CODE=${PIPESTATUS[0]}
    if [ $RECOVERY_EXIT_CODE -ne 0 ]; then
        print_status "error" "Simulation recovery failed with exit code ${RECOVERY_EXIT_CODE}"
        stop_postgres
        exit 1
    fi
    
    print_status "info" "Simulation recovery completed successfully."
    
    # Set up signal traps for graceful termination
    trap 'handle_termination SIGTERM' SIGTERM
    trap 'handle_termination SIGINT' SIGINT
    trap 'handle_termination SIGHUP' SIGHUP
    trap 'handle_termination EXIT' EXIT
    
    # Get batch count directly from the database after recovery
    BATCH_COUNT_CMD="python -c \"
from modules.diagnostics.db import get_batch_total
import os
simulation_name = os.environ.get('RECOVERY_SIMULATION_NAME')
print(get_batch_total(simulation_name))
\""

    print_status "info" "Getting batch count from database..."
    TOTAL_BATCHES=$(apptainer exec \
        --bind "${DATA_DIR}:/powertwin_data" \
        --bind "${USER_FILES_DIR}:/powertwin-solver-pg/user_files" \
        --bind "${HPC_SHARED_STORAGE}:${HPC_SHARED_STORAGE}" \
        --bind "${DB_DATA_DIR}:/postgres_data" \
        --bind "${LOG_DIR}:/solver/logs" \
        --env "SIMULATION_NAME=${RECOVERY_SIMULATION_NAME}" \
        --env "RECOVERY_SIMULATION_NAME=${RECOVERY_SIMULATION_NAME}" \
        --env "PYTHONPATH=/solver" \
        --env "POWERTWIN_LOG_DIR=/solver/logs" \
        --env "POSTGRES_USER=${PG_USER}" \
        --env "POSTGRES_PASSWORD=${PG_PASSWORD}" \
        --env "POSTGRES_DB=${PG_DB}" \
        --env "PGHOST=${DB_HOST}" \
        --env "PGUSER=${PG_USER}" \
        --env "PGPASSWORD=${PG_PASSWORD}" \
        --env "PGDATABASE=${PG_DB}" \
        --workdir /solver \
        "${SOLVER_SIF}" bash -c "${BATCH_COUNT_CMD}")
    
    if [[ -z "$TOTAL_BATCHES" || "$TOTAL_BATCHES" -eq 0 ]]; then
        print_status "error" "Could not determine total batch count after recovery."
        stop_postgres
        exit 1
    fi
    
    print_status "info" "Recovery completed with ${TOTAL_BATCHES} batches to process."

    # Create a specific log file for status updates
    STATUS_LOG_FILE="${LOG_DIR}/powertwin_status_${SLURM_JOB_ID}.log"
    touch "${STATUS_LOG_FILE}"

    # Start status monitoring in the background (every 30 minutes)
    monitor_simulation_status "${RECOVERY_SIMULATION_NAME}" 1800 "${STATUS_LOG_FILE}" &
    MONITOR_PID=$!

    # Store the PID for later cleanup
    echo ${MONITOR_PID} > "${STATUS_MONITOR_PID_FILE}"

    print_status "info" "Status monitoring started with PID ${MONITOR_PID}, logs at ${STATUS_LOG_FILE}"
    
    # Start XML validation directory cleanup in the background (every 5 minutes)
    XML_CLEANUP_PID_FILE="${NODE_TMP_DIR}/xml_cleanup_${SLURM_JOB_ID}.pid"
    clean_xml_validation_dirs &
    XML_CLEANUP_PID=$!
    
    # Store the PID for later cleanup
    echo ${XML_CLEANUP_PID} > "${XML_CLEANUP_PID_FILE}"
    
    print_status "info" "XML validation directory cleanup started with PID ${XML_CLEANUP_PID}"

    # STEP 2: Run parallel batch processing with proper SLURM task distribution
    print_status "info" "STEP 2: Running parallel batch processing for recovered simulation..."
    print_status "info" "Using node-specific temp directory: ${NODE_TMP_DIR}"
    
    # Check if all nodes can access the shared storage
    print_status "info" "Checking shared storage access across all nodes..."
    srun --mpi=pmix --exclusive bash -c "hostname -s && df -h /tmp ${HPC_SHARED_STORAGE}/tmp"
    
    # Create node-specific temp directories across all nodes
    print_status "info" "Creating node-specific temp directories across all nodes..."
    srun --mpi=pmix --exclusive bash -c "
        NODE_ID=\$(hostname -s)
        NODE_TMP=\"${HPC_SHARED_STORAGE}/tmp/node_\${NODE_ID}_${SLURM_JOB_ID}\"
        mkdir -p \"\${NODE_TMP}\"
        echo \"\${NODE_ID}: Node temp directory created at \${NODE_TMP}\"
    "

    srun --mpi=pmix --exclusive --kill-on-bad-exit=0 \
    apptainer exec \
        --bind "${DATA_DIR}:/powertwin_data:rw" \
        --bind "${USER_FILES_DIR}:/powertwin-solver-pg/user_files:rw" \
        --bind "${HPC_SHARED_STORAGE}:${HPC_SHARED_STORAGE}:rw" \
        --bind "${DB_DATA_DIR}:/postgres_data:rw" \
        --bind "${LOG_DIR}:/solver/logs:rw" \
        --bind "${NODE_TMP_DIR}:${NODE_TMP_DIR}:rw" \
        --env "TMPDIR=${NODE_TMP_DIR}" \
        --env "TMP=${NODE_TMP_DIR}" \
        --env "TEMP=${NODE_TMP_DIR}" \
        --env "GEM_HOME=${GEM_HOME}" \
        --env "GEM_PATH=${GEM_PATH}" \
        --env "SIMULATION_NAME=${RECOVERY_SIMULATION_NAME}" \
        --env "PYTHONPATH=/solver" \
        --env "PYTHONDONTWRITEBYTECODE=1" \
        --env "POWERTWIN_LOG_DIR=/solver/logs" \
        --env "POSTGRES_USER=${PG_USER}" \
        --env "POSTGRES_PASSWORD=${PG_PASSWORD}" \
        --env "POSTGRES_DB=${PG_DB}" \
        --env "PGHOST=${DB_HOST}" \
        --env "PGUSER=${PG_USER}" \
        --env "PGPASSWORD=${PG_PASSWORD}" \
        --env "PGDATABASE=${PG_DB}" \
        --workdir /solver \
        "${SOLVER_SIF}" python -m app.direct_runner run-parallel-batches \
        "${RECOVERY_DIR}" \
        "${RECOVERY_DIR_LOCAL}" \
        "${RECOVERY_SIMULATION_NAME}" \
    2>&1 | tee "${LOG_DIR}/powertwin_batches_${SLURM_JOB_ID}.log"

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
                --bind "${DB_DATA_DIR}:/postgres_data" \
                --bind "${LOG_DIR}:/solver/logs" \
                --bind "${NODE_TMP_DIR}:${NODE_TMP_DIR}:rw" \
                --env "TMPDIR=${NODE_TMP_DIR}" \
                --env "TMP=${NODE_TMP_DIR}" \
                --env "TEMP=${NODE_TMP_DIR}" \
                --env "SIMULATION_NAME=${RECOVERY_SIMULATION_NAME}" \
                --env "PYTHONPATH=/solver" \
                --env "POWERTWIN_LOG_DIR=/solver/logs" \
                --env "POSTGRES_USER=${PG_USER}" \
                --env "POSTGRES_PASSWORD=${PG_PASSWORD}" \
                --env "POSTGRES_DB=${PG_DB}" \
                --env "PGHOST=${DB_HOST}" \
                --env "PGUSER=${PG_USER}" \
                --env "PGPASSWORD=${PG_PASSWORD}" \
                --env "PGDATABASE=${PG_DB}" \
                --workdir /solver \
                "${SOLVER_SIF}" python -c "
    from modules.diagnostics.read_status import read_simulation_status
    import os
    simulation_name = '${RECOVERY_SIMULATION_NAME}'
    read_simulation_status(simulation_name)
    " >> "${STATUS_LOG_FILE}" 2>&1
        fi
        rm -f "${STATUS_MONITOR_PID_FILE}"
    fi
    
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

    
    # Clean up
    print_status "info" "Cleaning up resources..."
    stop_postgres
    
    # Clean up any temporary files
    print_status "info" "Cleaning up temporary files..."
    cleanup_temp_files
    
    print_status "info" "PowerTwin simulation completed."
    return 0
}

# Execute the main function
main