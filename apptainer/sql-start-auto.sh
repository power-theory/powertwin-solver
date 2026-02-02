#!/bin/bash
#==============================================================================
# PowerTwin HPC Start Script
# 
# Description: Orchestrates the start of PowerTwin simulations
#              using containerized execution with SQLite database and SLURM integration.
#
# Usage: sbatch sqlite-start.sh
#==============================================================================

#==============================================================================
# SLURM CONFIGURATION
#==============================================================================
#SBATCH --job-name=hack-sql-start-auto-v3
#SBATCH --nodes=1                   
#SBATCH --ntasks-per-node=1        
#SBATCH --cpus-per-task=1          
#SBATCH --time=7-00:00:00           
#SBATCH --mem-per-cpu=6G            
#SBATCH --account=cowy-nvhackathon
#SBATCH --output=%x_%j.out
#SBATCH --qos=long                  #debug or long


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

# =====================================================
# Configuration Variables - MODIFY THESE AS NEEDED
# =====================================================
# Simulation parameters
SIMULATION_NAME="asu3"
HPC_SHARED_STORAGE="/gscratch/lukemacy"

# Auto-recovery settings
AUTO_RECOVERY_ENABLED=true
FAILURE_THRESHOLD_PERCENT=2
MAX_RECOVERY_ATTEMPTS=3
MONITORING_INTERVAL_SECONDS=1500  # 5 minutes
UPLOAD_DIR="${HPC_SHARED_STORAGE}/upload/${SIMULATION_NAME}"
ASSET_GEOJSON_PATH="${UPLOAD_DIR}/1_asset_geometries.geojson"
METADATA_CSV_PATH="${UPLOAD_DIR}/1_metadata.csv"
CONFIG_JSON_PATH="${UPLOAD_DIR}/default_config.json"
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
export STATUS_MONITOR_PID_FILE="${NODE_TMP_DIR}/status_monitor_${SLURM_JOB_ID}.pid"

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
    
    # Check if directories were created successfully
    if [ ! -d "${DATA_DIR}" ] || [ ! -d "${LOG_DIR}" ] || [ ! -d "${USER_FILES_DIR}" ]; then
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
# FUNCTION: monitor_simulation_status
# Description: Background monitoring process that checks for failures during parallel processing
# Arguments: $1 - simulation name
# Returns: None (runs in background, exits when failure threshold exceeded or monitoring disabled)
#------------------------------------------------------------------------------
monitor_simulation_status() {
    local simulation_name="$1"
    local check_count=0
    local max_checks=288  # 24 hours at 5-minute intervals
    
    print_status "info" "Starting simulation status monitoring for ${simulation_name} (checking every $((MONITORING_INTERVAL_SECONDS/60)) minutes)..."
    
    while [ $check_count -lt $max_checks ]; do
        sleep "$MONITORING_INTERVAL_SECONDS"
        
        # Check if auto-recovery is still enabled
        if [ "$AUTO_RECOVERY_ENABLED" != "true" ]; then
            print_status "info" "Auto-recovery disabled, stopping status monitoring."
            break
        fi
        
        # Get aggregated status from all node databases
        local status_summary=$(aggregate_node_database_status "$simulation_name")
        local total_assets=$(echo "$status_summary" | cut -d'|' -f1)
        local total_finished=$(echo "$status_summary" | cut -d'|' -f2)
        local total_failed=$(echo "$status_summary" | cut -d'|' -f3)
        
        # Only check if we have a reasonable number of assets to avoid false positives
        if [ "$total_assets" -gt 10 ]; then
            local failure_percentage=$(calculate_failure_percentage "$status_summary")
            
            print_status "info" "Status check ${check_count}: ${total_finished} finished, ${total_failed} failed out of ${total_assets} total (${failure_percentage}% failure rate)"
            
            # Check if failure threshold exceeded
            if [ "$failure_percentage" -ge "$FAILURE_THRESHOLD_PERCENT" ]; then
                print_status "warning" "FAILURE THRESHOLD EXCEEDED: ${failure_percentage}% >= ${FAILURE_THRESHOLD_PERCENT}%"
                print_status "warning" "Initiating automatic recovery sequence..."
                
                # Trigger recovery by touching a flag file
                touch "${NODE_TMP_DIR}/trigger_recovery_${SLURM_JOB_ID}.flag"
                echo "$simulation_name" > "${NODE_TMP_DIR}/recovery_source_${SLURM_JOB_ID}.txt"
                
                print_status "warning" "Recovery trigger set. Main process will detect and initiate recovery."
                break
            fi
        fi
        
        check_count=$((check_count + 1))
    done
    
    print_status "info" "Status monitoring completed for ${simulation_name}."
}

#------------------------------------------------------------------------------
# FUNCTION: cleanup_temp_files
# Description: Cleans up temporary files and directories created during simulation
# Arguments: None
# Returns: None
#------------------------------------------------------------------------------
cleanup_temp_files() {
    print_status "info" "Cleaning up temporary files in /tmp..."
    
    
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

    # Clean up any remaining apptainer temporary files
    find /tmp -name "apptainer-*" -type d -delete 2>/dev/null

    # Clean up processing queue directories (per-node FIFO queues)
    if [ -d "${HPC_SHARED_STORAGE}/processing_queue" ]; then
        rm -rf "${HPC_SHARED_STORAGE}/processing_queue" 2>/dev/null
        print_status "info" "Removed processing queue directories"
    fi

    # Clean up node_ready synchronization directory
    if [ -d "${HPC_SHARED_STORAGE}/node_ready" ]; then
        rm -rf "${HPC_SHARED_STORAGE}/node_ready" 2>/dev/null
        print_status "info" "Removed node_ready synchronization directory"
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
    
    # First, try to consolidate node databases to preserve work
    if [ -n "${SIMULATION_NAME}" ] && [ -f "${SOLVER_SIF}" ]; then
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
        print_status "warning" "Skipping emergency database consolidation - missing required variables or container"
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
    # Clean up stale queue directories from previous runs to prevent deadlocks
    if [ -d "${HPC_SHARED_STORAGE}/processing_queue" ]; then
        rm -rf "${HPC_SHARED_STORAGE}/processing_queue" 2>/dev/null
    fi
    if [ -d "${HPC_SHARED_STORAGE}/node_ready" ]; then
        rm -rf "${HPC_SHARED_STORAGE}/node_ready" 2>/dev/null
    fi

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
# Description: Processes batches in parallel using SLURM with automatic failure monitoring
# Arguments: None
# Returns: 0 on success, 2 if auto-recovery triggered
#------------------------------------------------------------------------------
process_batches() {
    # Start status monitoring in background if auto-recovery is enabled
    if [ "$AUTO_RECOVERY_ENABLED" = "true" ]; then
        print_status "info" "Starting background status monitoring for auto-recovery..."
        monitor_simulation_status "${SIMULATION_NAME}" &
        local monitor_pid=$!
        echo "$monitor_pid" > "${STATUS_MONITOR_PID_FILE}"
        print_status "info" "Status monitoring started with PID ${monitor_pid}"
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
        "${SIMULATION_DIR}" \
        "${LOCAL_SIMULATION_DIR}" \
        "${SIMULATION_NAME}" \
    2>&1 | tee "${LOG_DIR}/powertwin_batches_${SLURM_JOB_ID}.log" &
    
    local batch_pid=$!
    
    # Monitor for recovery trigger while batch processing runs
    if [ "$AUTO_RECOVERY_ENABLED" = "true" ]; then
        while kill -0 "$batch_pid" 2>/dev/null; do
            if [ -f "${NODE_TMP_DIR}/trigger_recovery_${SLURM_JOB_ID}.flag" ]; then
                print_status "warning" "Recovery trigger detected! Terminating batch processing..."
                
                # Kill the batch processing
                kill "$batch_pid" 2>/dev/null
                
                # Wait a moment for graceful termination
                sleep 5
                
                # Force kill if still running
                if kill -0 "$batch_pid" 2>/dev/null; then
                    kill -9 "$batch_pid" 2>/dev/null
                fi
                
                print_status "warning" "Batch processing terminated for auto-recovery."
                return 2  # Special exit code for auto-recovery
            fi
            sleep 30  # Check every 30 seconds
        done
        
        # Wait for batch processing to complete normally
        wait "$batch_pid"
        
        # Stop status monitoring immediately after batch processing completes
        if [ -f "${STATUS_MONITOR_PID_FILE}" ]; then
            local monitor_pid=$(cat "${STATUS_MONITOR_PID_FILE}")
            if kill -0 "$monitor_pid" 2>/dev/null; then
                print_status "info" "Stopping status monitoring after batch completion (PID ${monitor_pid})..."
                kill "$monitor_pid" 2>/dev/null
                sleep 2  # Give it a moment to terminate gracefully
                # Force kill if still running
                if kill -0 "$monitor_pid" 2>/dev/null; then
                    kill -9 "$monitor_pid" 2>/dev/null
                fi
                rm -f "${STATUS_MONITOR_PID_FILE}"
                print_status "info" "Status monitoring stopped successfully."
            fi
        fi
    else
        # Wait for batch processing without monitoring
        wait "$batch_pid"
    fi
    
    print_status "info" "Parallel batch processing for ${SIMULATION_NAME} completed"
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
    # Stop status monitoring if running
    if [ -f "${STATUS_MONITOR_PID_FILE}" ]; then
        local monitor_pid=$(cat "${STATUS_MONITOR_PID_FILE}")
        if kill -0 "$monitor_pid" 2>/dev/null; then
            print_status "info" "Stopping status monitoring (PID ${monitor_pid})..."
            kill "$monitor_pid"
            rm -f "${STATUS_MONITOR_PID_FILE}"
        fi
    fi
    
    # Clean up recovery trigger files
    rm -f "${NODE_TMP_DIR}/trigger_recovery_${SLURM_JOB_ID}.flag"
    rm -f "${NODE_TMP_DIR}/recovery_source_${SLURM_JOB_ID}.txt"
    
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
    process_batches
    local batch_exit_code=$?
    
    if [ $batch_exit_code -eq 2 ]; then
        # Auto-recovery triggered
        print_status "warning" "Auto-recovery triggered due to high failure rate."
        
        # Get source simulation name for recovery
        local source_simulation="${SIMULATION_NAME}"
        if [ -f "${NODE_TMP_DIR}/recovery_source_${SLURM_JOB_ID}.txt" ]; then
            source_simulation=$(cat "${NODE_TMP_DIR}/recovery_source_${SLURM_JOB_ID}.txt")
        fi
        
        # Generate recovery simulation name
        local recovery_simulation=$(increment_simulation_name "$source_simulation")
        
        print_status "info" "Initiating recovery: ${source_simulation} -> ${recovery_simulation}"
        
        # Cleanup current resources before starting recovery
        cleanup_resources
        
        # Call sql-recover.sh with the appropriate parameters
        local script_dir="$(dirname "${BASH_SOURCE[0]}")"
        exec "${script_dir}/sql-recover.sh" "$source_simulation" "$recovery_simulation"
        
        # Should not reach here due to exec
        return 1
    elif [ $batch_exit_code -ne 0 ]; then
        return 1
    fi
    
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
