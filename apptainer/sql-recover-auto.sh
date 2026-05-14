#!/bin/bash
#==============================================================================
# PowerTwin HPC Recovery Script
# 
# Description: Orchestrates the recovery of corrupted PowerTwin simulations
#              using containerized execution with SQLite database and SLURM integration.
#              Supports automatic recovery with failure threshold monitoring.
#
# Usage: sbatch sql-recover.sh [CORRUPTED_SIMULATION_NAME] [RECOVERY_SIMULATION_NAME] [BATCH_ID]
#        If no arguments provided, uses default values below
#==============================================================================

#==============================================================================
# SLURM CONFIGURATION
#==============================================================================
#SBATCH --job-name=test-recover
#SBATCH --nodes=40      
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=20
#SBATCH --time=7-00:00:00            
#SBATCH --mem-per-cpu=8G             
#SBATCH --account=cowy-ptheory
#SBATCH --partition=teton            # Teton partition
#SBATCH --output=%x_%j.out
#SBATCH --qos=long                   # debug or long

set -e  # Exit immediately if a command exits with a non-zero status
#==============================================================================
# COMMAND-LINE ARGUMENT PROCESSING
#==============================================================================
# Parse command-line arguments
CORRUPTED_SIMULATION_ARG="${1:-}"
RECOVERY_SIMULATION_ARG="${2:-}"
BATCH_ID_ARG="${3:-}"

# Auto-recovery configuration
AUTO_RECOVERY_ENABLED=true
FAILURE_THRESHOLD_PERCENT=5
MAX_RECOVERY_ATTEMPTS=3
MONITORING_INTERVAL_SECONDS=300  # 5 minutes
RECOVERY_ATTEMPT_FILE="/tmp/recovery_attempt_count.txt"


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
# Configuration Variables - MODIFY THESE AS NEEDED OR PASS AS ARGUMENTS
#==============================================================================
# Simulation parameters - use command-line args if provided, otherwise defaults
if [ -n "$CORRUPTED_SIMULATION_ARG" ] && [ -n "$RECOVERY_SIMULATION_ARG" ]; then
    CORRUPTED_SIMULATION_NAME="$CORRUPTED_SIMULATION_ARG"
    RECOVERY_SIMULATION_NAME="$RECOVERY_SIMULATION_ARG"
else
    RECOVERY_SIMULATION_NAME="test2"
    CORRUPTED_SIMULATION_NAME="test1"
fi

POWERTWIN_KEEP_DIRS=1

# Use command-line BATCH_ID if provided
if [ -n "$BATCH_ID_ARG" ]; then
    BATCH_ID="$BATCH_ID_ARG"
else
    BATCH_ID=""  # Optional - leave empty to recover all batches, or specify a batch number
fi

# HPC storage path - retain same as corrupted simulation for recovery to ensure access to all necessary files
HPC_SHARED_STORAGE="/project/cowy-ptheory/test"

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
# FUNCTION: increment_simulation_name
# Description: Increments simulation name for recovery (e.g., test1 -> test2)
# Arguments: $1 - original simulation name
# Returns: Outputs incremented simulation name
#------------------------------------------------------------------------------
increment_simulation_name() {
    local original_name="$1"
    
    # Check if name ends with a number
    if [[ $original_name =~ ^(.*)([0-9]+)$ ]]; then
        local base_name="${BASH_REMATCH[1]}"
        local number="${BASH_REMATCH[2]}"
        local next_number=$((number + 1))
        echo "${base_name}${next_number}"
    else
        # If no number at end, append "2"
        echo "${original_name}2"
    fi
}

#------------------------------------------------------------------------------
# FUNCTION: get_recovery_attempt_count
# Description: Gets current recovery attempt count for this simulation chain
# Arguments: $1 - base simulation name (e.g., "test" from "test1")
# Returns: Outputs attempt count as integer
#------------------------------------------------------------------------------
get_recovery_attempt_count() {
    local base_simulation="$1"
    
    # Extract base name from simulation (e.g., "test" from "test1")
    local base_name
    if [[ $base_simulation =~ ^([a-zA-Z_-]+) ]]; then
        base_name="${BASH_REMATCH[1]}"
    else
        base_name="$base_simulation"
    fi
    
    local attempt_file="${HPC_SHARED_STORAGE}/recovery_attempts_${base_name}.txt"
    
    if [ -f "$attempt_file" ]; then
        local count=$(cat "$attempt_file" 2>/dev/null || echo "0")
        echo "${count:-0}"
    else
        echo "0"
    fi
}

#------------------------------------------------------------------------------
# FUNCTION: increment_recovery_attempt_count
# Description: Increments and stores recovery attempt count
# Arguments: $1 - base simulation name
# Returns: None
#------------------------------------------------------------------------------
increment_recovery_attempt_count() {
    local base_simulation="$1"
    
    # Extract base name from simulation
    local base_name
    if [[ $base_simulation =~ ^([a-zA-Z_-]+) ]]; then
        base_name="${BASH_REMATCH[1]}"
    else
        base_name="$base_simulation"
    fi
    
    local attempt_file="${HPC_SHARED_STORAGE}/recovery_attempts_${base_name}.txt"
    local current_count=$(get_recovery_attempt_count "$base_simulation")
    local new_count=$((current_count + 1))
    
    echo "$new_count" > "$attempt_file"
    print_status "info" "Recovery attempt ${new_count} for ${base_name} series"
}

#------------------------------------------------------------------------------
# FUNCTION: aggregate_node_database_status
# Description: Aggregates status counts across all active node databases during parallel processing
# Arguments: $1 - simulation name
# Returns: Outputs status summary in format: "total_assets|finished|failed|processing|not_processed"
#------------------------------------------------------------------------------
aggregate_node_database_status() {
    local simulation_name="$1"
    local total_finished=0
    local total_failed=0
    local total_processing=0
    local total_not_processed=0
    local total_assets=0
    
    # Check if SQLITE_DB_DIR exists
    if [ ! -d "${SQLITE_DB_DIR}" ]; then
        echo "0|0|0|0|0"
        return 0
    fi
    
    # Find all node database directories
    for node_dir in "${SQLITE_DB_DIR}"/node_*; do
        if [ -d "$node_dir" ]; then
            local node_db="${node_dir}/powertwin_node_$(basename "$node_dir" | cut -d'_' -f2).db"
            
            if [ -f "$node_db" ]; then
                # Query status counts from this node database
                local node_status=$(apptainer exec \
                    --bind "${SQLITE_DB_DIR}:/sqlite_data" \
                    --env "SQLITE_DB_PATH=$node_db" \
                    --env "PGDATABASE=powertwin" \
                    "${SOLVER_SIF}" python3 -c "
import sqlite3
import os
db_path = os.environ.get('SQLITE_DB_PATH')
table_name = os.environ.get('PGDATABASE', 'powertwin')
simulation_name = '$simulation_name'
try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(f'SELECT status, COUNT(*) FROM {table_name} WHERE simulation_name = ? GROUP BY status', (simulation_name,))
    results = cursor.fetchall()
    status_counts = {'Finished': 0, 'Failed': 0, 'Processing': 0, 'Not Processed Yet': 0}
    for status, count in results:
        if status in status_counts:
            status_counts[status] = count
    print(f'{status_counts["Finished"]}|{status_counts["Failed"]}|{status_counts["Processing"]}|{status_counts["Not Processed Yet"]}')
    conn.close()
except Exception as e:
    print('0|0|0|0')
" 2>/dev/null)
                
                if [ -n "$node_status" ]; then
                    local finished=$(echo "$node_status" | cut -d'|' -f1)
                    local failed=$(echo "$node_status" | cut -d'|' -f2)
                    local processing=$(echo "$node_status" | cut -d'|' -f3)
                    local not_processed=$(echo "$node_status" | cut -d'|' -f4)
                    
                    total_finished=$((total_finished + finished))
                    total_failed=$((total_failed + failed))
                    total_processing=$((total_processing + processing))
                    total_not_processed=$((total_not_processed + not_processed))
                fi
            fi
        fi
    done
    
    total_assets=$((total_finished + total_failed + total_processing + total_not_processed))
    echo "${total_assets}|${total_finished}|${total_failed}|${total_processing}|${total_not_processed}"
}

#------------------------------------------------------------------------------
# FUNCTION: calculate_failure_percentage
# Description: Calculates failure percentage from aggregated status
# Arguments: $1 - status summary (total_assets|finished|failed|processing|not_processed)
# Returns: Outputs failure percentage as integer
#------------------------------------------------------------------------------
calculate_failure_percentage() {
    local status_summary="$1"
    local total_assets=$(echo "$status_summary" | cut -d'|' -f1)
    local total_failed=$(echo "$status_summary" | cut -d'|' -f3)
    
    if [ "$total_assets" -eq 0 ]; then
        echo "0"
        return 0
    fi
    
    # Calculate percentage using integer arithmetic
    local failure_percentage=$((total_failed * 100 / total_assets))
    echo "$failure_percentage"
}

#------------------------------------------------------------------------------
# FUNCTION: monitor_recovery_status
# Description: Background monitoring process that checks for failures during recovery
# Arguments: $1 - simulation name
# Returns: None (runs in background)
#------------------------------------------------------------------------------
monitor_recovery_status() {
    local simulation_name="$1"
    local check_count=0
    local max_checks=288  # 24 hours at 5-minute intervals
    
    print_status "info" "Starting recovery status monitoring for ${simulation_name}..."
    
    while [ $check_count -lt $max_checks ]; do
        sleep "$MONITORING_INTERVAL_SECONDS"
        
        # Check if auto-recovery is still enabled
        if [ "$AUTO_RECOVERY_ENABLED" != "true" ]; then
            break
        fi
        
        # Get aggregated status from all node databases
        local status_summary=$(aggregate_node_database_status "$simulation_name")
        local total_assets=$(echo "$status_summary" | cut -d'|' -f1)
        local total_finished=$(echo "$status_summary" | cut -d'|' -f2)
        local total_failed=$(echo "$status_summary" | cut -d'|' -f3)
        
        # Only check if we have assets to avoid false positives
        if [ "$total_assets" -gt 5 ]; then
            local failure_percentage=$(calculate_failure_percentage "$status_summary")
            
            print_status "info" "Recovery status check ${check_count}: ${total_finished} finished, ${total_failed} failed out of ${total_assets} total (${failure_percentage}% failure rate)"
            
            # Check if failure threshold exceeded again
            if [ "$failure_percentage" -ge "$FAILURE_THRESHOLD_PERCENT" ]; then
                local current_attempts=$(get_recovery_attempt_count "$simulation_name")
                
                if [ "$current_attempts" -lt "$MAX_RECOVERY_ATTEMPTS" ]; then
                    print_status "warning" "Recovery failure threshold exceeded: ${failure_percentage}% >= ${FAILURE_THRESHOLD_PERCENT}%"
                    print_status "warning" "Attempt ${current_attempts}/${MAX_RECOVERY_ATTEMPTS} - initiating another recovery..."
                    
                    # Trigger another recovery
                    touch "${NODE_TMP_DIR}/trigger_recovery_${SLURM_JOB_ID}.flag"
                    echo "$simulation_name" > "${NODE_TMP_DIR}/recovery_source_${SLURM_JOB_ID}.txt"
                    break
                else
                    print_status "error" "Maximum recovery attempts (${MAX_RECOVERY_ATTEMPTS}) reached. Stopping auto-recovery."
                    break
                fi
            fi
        fi
        
        check_count=$((check_count + 1))
    done
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

    # Define paths for the metadata and geojson files
    METADATA_CSV_PATH="${CORRUPTED_SIMULATION_DIR}/${CORRUPTED_SIMULATION_NAME}_metadata.csv"
    GEOJSON_PATH="${CORRUPTED_SIMULATION_DIR}/${CORRUPTED_SIMULATION_NAME}_asset.geojson"

    # Check if required files exist
    if [ ! -f "${METADATA_CSV_PATH}" ]; then
        print_status "error" "Metadata CSV file not found: ${METADATA_CSV_PATH}"
        return 1
    fi

    if [ ! -f "${GEOJSON_PATH}" ]; then
        print_status "warning" "Asset GeoJSON file not found: ${GEOJSON_PATH}"
    fi

    # Define paths for the new files
    NEW_METADATA_CSV_PATH="${RECOVERY_DIR_LOCAL}/${RECOVERY_SIMULATION_NAME}_metadata.csv"
    NEW_GEOJSON_PATH="${RECOVERY_DIR_LOCAL}/${RECOVERY_SIMULATION_NAME}_asset.geojson"

    # Copy and rename the files to the recovery directory
    cp "${METADATA_CSV_PATH}" "${NEW_METADATA_CSV_PATH}"

    if [ -f "${GEOJSON_PATH}" ]; then
        cp "${GEOJSON_PATH}" "${NEW_GEOJSON_PATH}"
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
# Description: Processes batches in parallel using SLURM with recovery monitoring
# Arguments: None
# Returns: 0 on success, 2 if auto-recovery triggered
#------------------------------------------------------------------------------
process_batches() {
    # Start recovery status monitoring in background if auto-recovery is enabled
    if [ "$AUTO_RECOVERY_ENABLED" = "true" ]; then
        print_status "info" "Starting background recovery monitoring..."
        monitor_recovery_status "${RECOVERY_SIMULATION_NAME}" &
        local monitor_pid=$!
        echo "$monitor_pid" > "${NODE_TMP_DIR}/recovery_monitor_${SLURM_JOB_ID}.pid"
        print_status "info" "Recovery monitoring started with PID ${monitor_pid}"
    fi
    
    # Start parallel batch processing
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
    2>&1 | tee "${LOG_DIR}/powertwin_batches_${SLURM_JOB_ID}.log" &
    
    local batch_pid=$!
    
    # Monitor for recovery trigger while batch processing runs
    if [ "$AUTO_RECOVERY_ENABLED" = "true" ]; then
        while kill -0 "$batch_pid" 2>/dev/null; do
            if [ -f "${NODE_TMP_DIR}/trigger_recovery_${SLURM_JOB_ID}.flag" ]; then
                print_status "warning" "Another recovery trigger detected! Terminating current recovery..."
                
                # Kill the batch processing
                kill "$batch_pid" 2>/dev/null
                
                # Wait a moment for graceful termination
                sleep 5
                
                # Force kill if still running
                if kill -0 "$batch_pid" 2>/dev/null; then
                    kill -9 "$batch_pid" 2>/dev/null
                fi
                
                print_status "warning" "Recovery batch processing terminated for another auto-recovery attempt."
                return 2  # Special exit code for auto-recovery
            fi
            sleep 30  # Check every 30 seconds
        done
        
        # Wait for batch processing to complete normally
        wait "$batch_pid"
        
        # Stop recovery monitoring immediately after batch processing completes
        if [ -f "${NODE_TMP_DIR}/recovery_monitor_${SLURM_JOB_ID}.pid" ]; then
            local monitor_pid=$(cat "${NODE_TMP_DIR}/recovery_monitor_${SLURM_JOB_ID}.pid")
            if kill -0 "$monitor_pid" 2>/dev/null; then
                print_status "info" "Stopping recovery monitoring after batch completion (PID ${monitor_pid})..."
                kill "$monitor_pid" 2>/dev/null
                sleep 2  # Give it a moment to terminate gracefully
                # Force kill if still running
                if kill -0 "$monitor_pid" 2>/dev/null; then
                    kill -9 "$monitor_pid" 2>/dev/null
                fi
                rm -f "${NODE_TMP_DIR}/recovery_monitor_${SLURM_JOB_ID}.pid"
                print_status "info" "Recovery monitoring stopped successfully."
            fi
        fi
    else
        # Wait for batch processing without monitoring
        wait "$batch_pid"
    fi
    
    print_status "info" "Parallel batch processing for ${RECOVERY_SIMULATION_NAME} completed"
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
    # Stop the recovery monitoring if running
    if [ -f "${NODE_TMP_DIR}/recovery_monitor_${SLURM_JOB_ID}.pid" ]; then
        local monitor_pid=$(cat "${NODE_TMP_DIR}/recovery_monitor_${SLURM_JOB_ID}.pid")
        if kill -0 "$monitor_pid" 2>/dev/null; then
            print_status "info" "Stopping recovery monitoring (PID ${monitor_pid})..."
            kill "$monitor_pid"
            rm -f "${NODE_TMP_DIR}/recovery_monitor_${SLURM_JOB_ID}.pid"
        fi
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
    
    # Clean up recovery trigger files
    rm -f "${NODE_TMP_DIR}/trigger_recovery_${SLURM_JOB_ID}.flag"
    rm -f "${NODE_TMP_DIR}/recovery_source_${SLURM_JOB_ID}.txt"
    
    # Clean up temporary files
    print_status "info" "Cleaning up resources..."
    cleanup_temp_files
    
    return 0
}

#------------------------------------------------------------------------------
# FUNCTION: main
# Description: Main execution flow of the recovery script with auto-recovery support
# Arguments: None
# Returns: 0 on success, non-zero on failure
#------------------------------------------------------------------------------
main() {
    # Check recovery attempt limits
    local current_attempts=$(get_recovery_attempt_count "$CORRUPTED_SIMULATION_NAME")
    
    if [ "$current_attempts" -ge "$MAX_RECOVERY_ATTEMPTS" ]; then
        print_status "error" "Maximum recovery attempts (${MAX_RECOVERY_ATTEMPTS}) reached for ${CORRUPTED_SIMULATION_NAME} series."
        print_status "error" "Manual intervention required. Exiting."
        return 1
    fi
    
    # Increment recovery attempt count
    increment_recovery_attempt_count "$CORRUPTED_SIMULATION_NAME"
    
    print_status "info" "Step 1: Starting PowerTwin recovery process for simulation: ${RECOVERY_SIMULATION_NAME}"
    print_status "info" "Recovering from: ${CORRUPTED_SIMULATION_NAME} (attempt $((current_attempts + 1))/${MAX_RECOVERY_ATTEMPTS})"
    initialize_environment || return 1
    print_status "info" "Environment initialization completed successfully."

    print_status "info" "Step 2: Starting simulation recovery..."
    recover_simulation || return 1
    print_status "info" "Simulation recovery completed successfully."
    
    print_status "info" "Step 3: Starting batch processing..."
    process_batches
    local batch_exit_code=$?
    
    if [ $batch_exit_code -eq 2 ]; then
        # Auto-recovery triggered
        print_status "warning" "Auto-recovery triggered due to continued high failure rate."
        
        # Get source simulation name for next recovery
        local source_simulation="${RECOVERY_SIMULATION_NAME}"
        if [ -f "${NODE_TMP_DIR}/recovery_source_${SLURM_JOB_ID}.txt" ]; then
            source_simulation=$(cat "${NODE_TMP_DIR}/recovery_source_${SLURM_JOB_ID}.txt")
        fi
        
        # Generate next recovery simulation name
        local next_recovery_simulation=$(increment_simulation_name "$source_simulation")
        
        print_status "info" "Initiating recursive recovery: ${source_simulation} -> ${next_recovery_simulation}"
        
        # Cleanup current resources before starting next recovery
        cleanup_resources
        
        # Recursively call this script with new parameters
        local script_path="${BASH_SOURCE[0]}"
        exec "$script_path" "$source_simulation" "$next_recovery_simulation"
        
        # Should not reach here due to exec
        return 1
    elif [ $batch_exit_code -ne 0 ]; then
        return 1
    fi
    
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
    
    # Reset recovery attempt count on successful completion
    local base_name
    if [[ $CORRUPTED_SIMULATION_NAME =~ ^([a-zA-Z_-]+) ]]; then
        base_name="${BASH_REMATCH[1]}"
    else
        base_name="$CORRUPTED_SIMULATION_NAME"
    fi
    rm -f "${HPC_SHARED_STORAGE}/recovery_attempts_${base_name}.txt"
    print_status "info" "Recovery attempt counter reset after successful completion."
    
    return 0
}

# Execute the main function
main