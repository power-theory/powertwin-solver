#!/bin/bash
#==============================================================================
# PowerTwin HPC Recovery Script
# 
# Description: Orchestrates the recovery of corrupted PowerTwin simulations
#              using containerized execution with proper SLURM integration.
#
# Usage: sbatch hpc-recover.sh
#==============================================================================

#==============================================================================
# SLURM CONFIGURATION
#==============================================================================
#SBATCH --job-name=test-recover
#SBATCH --nodes=4       
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
# CONFIGURATION
#==============================================================================
# Simulation parameters
RECOVERY_SIMULATION_NAME="test1"
CORRUPTED_SIMULATION_NAME="test2"
BATCH_ID=""  # Optional - leave empty to recover all batches, or specify a batch number

# Storage locations
HPC_SHARED_STORAGE="/project/cowy-ptheory/test"
DATA_DIR="${HPC_SHARED_STORAGE}/powertwin_data"
USER_FILES_DIR="${HPC_SHARED_STORAGE}/user_files"
LOG_DIR="${HPC_SHARED_STORAGE}/logs"
TMP_BASE="${HPC_SHARED_STORAGE}/tmp"

# Database configuration
PG_USER="postgres"
PG_PASSWORD="admin"
PG_DB="powertwin"
DB_DATA_DIR="${DATA_DIR}/postgres_data"  # Use existing PostgreSQL data directory

# PostgreSQL environment variables
export PGHOST="localhost"
export PGUSER="${PG_USER}"
export PGPASSWORD="${PG_PASSWORD}"
export PGDATABASE="${PG_DB}"
export POSTGRES_HOST_AUTH_METHOD="trust"

# Container images
SIF_DIR="${HPC_SHARED_STORAGE}/sif_containers"
SOLVER_SIF="${SIF_DIR}/flask.sif"
PG_SIF="${SIF_DIR}/postgres17.sif"
PGB_SIF="${SIF_DIR}/pgbouncer.sif"

# PgBouncer configuration
PGB_PORT=6432  # PgBouncer listening port
PGB_MAX_CLIENT_CONN=1000  # Maximum number of client connections per node
PGB_DEFAULT_POOL_SIZE=40  # Default pool size per user per database
PGB_MIN_POOL_SIZE=10     # Minimum pool size per user per database
PGB_RESERVE_POOL_SIZE=20 # Additional connections for when pool is full
PGB_MAX_DB_CONNECTIONS=60 # Maximum server connections per database

# Create a node-specific temp directory to avoid /tmp disk space issues
export NODE_ID=$(hostname -s)
export NODE_TMP_DIR="${TMP_BASE}/node_${NODE_ID}_${SLURM_JOB_ID}"

# Clean up any leftover files from previous runs with the same job ID pattern
if [ -d "${NODE_TMP_DIR}" ]; then
    print_status "warning" "Found existing node temp directory, cleaning up leftover files..."
    find "${NODE_TMP_DIR}" -name "*.pid" -type f -delete 2>/dev/null
    # Remove any leftover PgBouncer processes that might be running
    pkill -f "pgbouncer.*${SLURM_JOB_ID}" 2>/dev/null || true
fi

mkdir -p "${NODE_TMP_DIR}"

# Redirect temporary files to our custom location
export TMPDIR="${NODE_TMP_DIR}"
export TMP="${NODE_TMP_DIR}"
export TEMP="${NODE_TMP_DIR}"

export GEM_HOME="${NODE_TMP_DIR}/gems_${SLURM_JOB_ID}"
export GEM_PATH="${GEM_HOME}:/usr/local/lib/ruby/gems/3.2.2"
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
# Description: Validates that all required SIF container files exist
# Arguments: None
# Returns: 0 on success, 1 on failure
#------------------------------------------------------------------------------
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
    
    if [ ! -f "$PG_SIF" ]; then
        print_status "error" "PostgreSQL SIF file not found at: $PG_SIF"
        return 1
    fi
    
    if [ ! -f "$PGB_SIF" ]; then
        print_status "error" "PgBouncer SIF file not found at: $PGB_SIF"
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
    
    # Make sure we're using a compatible container
    PG_SIF="${SIF_DIR}/postgres17.sif"
    if [ ! -f "${PG_SIF}" ]; then
        print_status "warning" "PostgreSQL 17 container not found. Creating it..."
        apptainer build "${PG_SIF}" docker://postgres:17
    fi
    
    # Start PostgreSQL server in background with custom socket directory
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

#------------------------------------------------------------------------------
# FUNCTION: generate_pgbouncer_config
# Description: Generates PgBouncer configuration consistent with Docker Compose
# Arguments: None
# Returns: 0 on success, 1 on failure
#------------------------------------------------------------------------------
generate_pgbouncer_config() {
    local config_dir="$1"
    
    # Use the same environment variables as Docker Compose for consistency
    local databases_host="${DATABASES_HOST:-localhost}"
    local databases_port="${DATABASES_PORT:-5432}"
    local databases_user="${DATABASES_USER:-postgres}"
    local databases_password="${DATABASES_PASSWORD:-admin}"
    local databases_dbname="${DATABASES_DBNAME:-powertwin}"
    local listen_addr="${LISTEN_ADDR:-0.0.0.0}"
    local listen_port="${LISTEN_PORT:-6432}"
    local auth_type="${AUTH_TYPE:-scram-sha-256}"
    local pool_mode="${POOL_MODE:-transaction}"
    local max_client_conn="${MAX_CLIENT_CONN:-1000}"
    local default_pool_size="${DEFAULT_POOL_SIZE:-25}"
    local max_db_connections="${MAX_DB_CONNECTIONS:-100}"
    
    # Generate configuration that mirrors Docker Compose behavior
    cat > "${config_dir}/pgbouncer.ini" << EOF
; PowerTwin PgBouncer Configuration
; Generated to match Docker Compose environment variables

[databases]
${databases_dbname} = host=${databases_host} port=${databases_port} dbname=${databases_dbname} user=${databases_user} password=${databases_password}

[pgbouncer]
; Network settings (matches Docker Compose LISTEN_* vars)
listen_addr = ${listen_addr}
listen_port = ${listen_port}
unix_socket_dir = ${PGB_SOCKET_DIR}

; Authentication (matches Docker Compose AUTH_TYPE)
auth_type = ${auth_type}
auth_file = ${config_dir}/userlist.txt

; Connection pooling (matches Docker Compose POOL_* vars)
pool_mode = ${pool_mode}
max_client_conn = ${max_client_conn}
default_pool_size = ${default_pool_size}
min_pool_size = ${MIN_POOL_SIZE:-10}
reserve_pool_size = ${RESERVE_POOL_SIZE:-20}
max_db_connections = ${max_db_connections}

; UrbanOpt/OpenStudio optimization settings
server_reset_query = DISCARD ALL
server_check_delay = 30
server_lifetime = 3600
server_idle_timeout = 600
ignore_startup_parameters = ${IGNORE_STARTUP_PARAMETERS:-extra_float_digits,application_name}

; Administrative settings (matches Docker Compose ADMIN_USERS)
admin_users = ${ADMIN_USERS:-postgres}
stats_users = ${STATS_USERS:-postgres}

; Logging (matches Docker Compose LOG_* vars)
logfile = ${PGB_LOG_DIR}/pgbouncer.log
pidfile = ${PGB_PID_FILE}
log_connections = ${LOG_CONNECTIONS:-1}
log_disconnections = ${LOG_DISCONNECTIONS:-1}
log_pooler_errors = ${LOG_POOLER_ERRORS:-1}
EOF

    # Generate userlist for authentication
    cat > "${config_dir}/userlist.txt" << EOF
"${databases_user}" "${databases_password}"
EOF

    return 0
}

#------------------------------------------------------------------------------
# FUNCTION: start_pgbouncer
# Description: Starts PgBouncer with Docker Compose-compatible configuration
# Arguments: None
# Returns: 0 on success, 1 on failure
#------------------------------------------------------------------------------
start_pgbouncer() {
    print_status "info" "Setting up PgBouncer with Docker Compose-compatible configuration..."
    
    # Set environment variables to match Docker Compose exactly
    export DATABASES_HOST="${PGHOST}"
    export DATABASES_PORT="5432"
    export DATABASES_USER="${PG_USER}"
    export DATABASES_PASSWORD="${PG_PASSWORD}"
    export DATABASES_DBNAME="${PG_DB}"
    export LISTEN_ADDR="0.0.0.0"
    export LISTEN_PORT="${PGB_PORT}"
    export AUTH_TYPE="scram-sha-256"
    export POOL_MODE="transaction"
    export MAX_CLIENT_CONN="${PGB_MAX_CLIENT_CONN}"
    export DEFAULT_POOL_SIZE="${PGB_DEFAULT_POOL_SIZE}"
    export MAX_DB_CONNECTIONS="${PGB_MAX_DB_CONNECTIONS}"
    export ADMIN_USERS="${PG_USER}"
    export STATS_USERS="${PG_USER}"
    export LOG_CONNECTIONS="1"
    export LOG_DISCONNECTIONS="1" 
    export LOG_POOLER_ERRORS="1"
    export IGNORE_STARTUP_PARAMETERS="extra_float_digits,application_name"
    
    # Define directories
    PGB_CONFIG_DIR="${NODE_TMP_DIR}/pgbouncer_config"
    PGB_LOG_DIR="${NODE_TMP_DIR}/pgbouncer_logs"
    PGB_SOCKET_DIR="${NODE_TMP_DIR}/pgbouncer_run"
    PGB_PID_FILE="${NODE_TMP_DIR}/pgbouncer_${SLURM_JOB_ID}.pid"
    
    # Create directories
    mkdir -p "${PGB_CONFIG_DIR}" "${PGB_LOG_DIR}" "${PGB_SOCKET_DIR}"
    chmod 700 "${PGB_CONFIG_DIR}" "${PGB_LOG_DIR}" "${PGB_SOCKET_DIR}"
    
    # Generate configuration using the same logic as Docker Compose
    generate_pgbouncer_config "${PGB_CONFIG_DIR}" || {
        print_status "error" "Failed to generate PgBouncer configuration"
        return 1
    }
    
    print_status "info" "Generated PgBouncer configuration consistent with Docker Compose"
    
    # Start PgBouncer with the generated configuration
    apptainer exec \
        --bind "${PGB_CONFIG_DIR}:/etc/pgbouncer" \
        --bind "${PGB_LOG_DIR}:/var/log/pgbouncer" \
        --bind "${PGB_SOCKET_DIR}:/var/run/pgbouncer" \
        "${PGB_SIF}" pgbouncer /etc/pgbouncer/pgbouncer.ini &
    
    PGB_PID=$!
    echo $PGB_PID > "${PGB_PID_FILE}"
    
    # Wait for startup and validate
    for i in {1..10}; do
        if nc -z localhost ${LISTEN_PORT} 2>/dev/null; then
            print_status "info" "PgBouncer started successfully with Docker Compose-compatible settings"
            export PGHOST="localhost"
            export PGPORT="${LISTEN_PORT}"
            return 0
        fi
        sleep 1
    done
    
    print_status "error" "PgBouncer failed to start"
    return 1
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

#------------------------------------------------------------------------------
# FUNCTION: stop_pgbouncer
# Description: Stops the PgBouncer connection pooler
# Arguments: None
# Returns: 0 on success
#------------------------------------------------------------------------------
stop_pgbouncer() {
    print_status "info" "Stopping PgBouncer..."
    
    # Define PGB_PID_FILE using NODE_TMP_DIR (same as in start_pgbouncer)
    PGB_PID_FILE="${NODE_TMP_DIR}/pgbouncer_${SLURM_JOB_ID}.pid"
    
    if [ -f "${PGB_PID_FILE}" ]; then
        PGB_PID=$(cat "${PGB_PID_FILE}" 2>/dev/null)
        
        if [ -n "${PGB_PID}" ] && kill -0 "${PGB_PID}" &>/dev/null; then
            kill -TERM "${PGB_PID}"
            
            for i in {1..5}; do
                if ! kill -0 "${PGB_PID}" &>/dev/null; then
                    print_status "info" "PgBouncer stopped successfully"
                    break
                fi
                
                if [ $i -eq 5 ]; then
                    print_status "warning" "PgBouncer did not stop gracefully, forcing shutdown..."
                    kill -9 "${PGB_PID}" &>/dev/null
                fi
                
                sleep 1
            done
        else
            print_status "warning" "PgBouncer process (PID: ${PGB_PID}) not found or invalid PID"
        fi
        
        # Always remove the PID file regardless
        rm -f "${PGB_PID_FILE}"
    else
        # Try to kill any PgBouncer processes that might be running for this job ID
        pkill -f "pgbouncer.*${SLURM_JOB_ID}" 2>/dev/null && print_status "info" "Killed orphaned PgBouncer processes"
        print_status "warning" "PgBouncer PID file not found"
    fi
    
    return 0
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
    stop_postgres
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
    print_status "warning" "Received ${signal_name} signal. Performing emergency cleanup..."
    
    # Stop PgBouncer first
    stop_pgbouncer
    
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
" >> "${log_file}" 2>&1        # Sleep for the specified interval
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


#------------------------------------------------------------------------------
# FUNCTION: initialize_environment
# Description: Sets up the initial environment and validates prerequisites
# Arguments: None
# Returns: 0 on success, exits on failure
#------------------------------------------------------------------------------
initialize_environment() {
    # All validation and setup happens in one place - the master script
    check_sif_files || handle_error "SIF files validation failed" 1
    setup_dirs || handle_error "Directory setup failed" 1
    check_postgres_data || handle_error "PostgreSQL data validation failed" 1
    
    # Start PostgreSQL server
    start_postgres || handle_error "PostgreSQL server failed to start" 1
    
    # Start PgBouncer connection pooler on the head node
    #start_pgbouncer || handle_error "PgBouncer connection pooler failed to start" 1
    
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

        # STEP 1: Run initialization as a separate SLURM step (feature files creation)
    print_status "info" "STEP 1: Creating feature files..."
    
    apptainer exec \
        --bind "${DATA_DIR}:/powertwin_data" \
        --bind "${USER_FILES_DIR}:/powertwin-solver-pg/user_files" \
        --bind "${HPC_SHARED_STORAGE}:${HPC_SHARED_STORAGE}" \
        --bind "${DB_DATA_DIR}:/postgres_data" \
        --bind "${LOG_DIR}:/solver/logs" \
        --env "POWERTWIN_LOG_DIR=/solver/logs" \
        --env "POSTGRES_USER=${PG_USER}" \
        --env "POSTGRES_PASSWORD=${PG_PASSWORD}" \
        --env "POSTGRES_DB=${PG_DB}" \
        --env "PGHOST=${DB_HOST}" \
        --env "PGUSER=${PG_USER}" \
        --env "PGPASSWORD=${PG_PASSWORD}" \
        --env "PGDATABASE=${PG_DB}" \
        "${SOLVER_SIF}" bash -c "cd /solver && python -m app.direct_runner create-feature-files \
        \"${SIMULATION_NAME}\" \
        \"${ASSET_GEOJSON_PATH}\" \
        \"${METADATA_CSV_PATH}\" \
        \"${CONFIG_JSON_PATH}\" \
        \"${LOCATION}\" \
        \"${TOTAL_CORES}\" \
        --hpc \
        --shared-storage \"${HPC_SHARED_STORAGE}\"" \
        2>&1 | tee "${LOG_DIR}/powertwin_ff_${SLURM_JOB_ID}.log"
    
    FEATURE_FILES_EXIT_CODE=${PIPESTATUS[0]}
    if [ $FEATURE_FILES_EXIT_CODE -ne 0 ]; then
        print_status "error" "Feature files creation failed with exit code ${FEATURE_FILES_EXIT_CODE}"
        stop_postgres
        exit 1
    fi
    
    return 0
}

#------------------------------------------------------------------------------
# FUNCTION: recover_simulation
# Description: Runs the simulation recovery process
# Arguments: None
# Returns: 0 on success, exits on failure
#------------------------------------------------------------------------------
recover_simulation() {
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
        handle_error "Simulation recovery failed with exit code ${RECOVERY_EXIT_CODE}" 1
    fi
    
    print_status "info" "Simulation recovery completed successfully."
    return 0
}

#------------------------------------------------------------------------------
# FUNCTION: setup_monitoring
# Description: Sets up monitoring processes for the simulation
# Arguments: None
# Returns: 0 on success
#------------------------------------------------------------------------------
setup_monitoring() {
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
        handle_error "Could not determine total batch count after recovery." 1
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
    
    return 0
}

#------------------------------------------------------------------------------
# FUNCTION: process_batches
# Description: Processes batches in parallel using SLURM
# Arguments: None
# Returns: 0 on success
#------------------------------------------------------------------------------
process_batches() {
    print_status "info" "Running parallel batch processing for recovered simulation..."
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

    # Set up PgBouncer on each compute node
    print_status "info" "Setting up PgBouncer on each compute node..."
    srun --mpi=pmix --exclusive bash -c "
        NODE_ID=\$(hostname -s)
        NODE_TMP=\"${HPC_SHARED_STORAGE}/tmp/node_\${NODE_ID}_${SLURM_JOB_ID}\"
        PGB_SOCKET_DIR=\"\${NODE_TMP}/pgbouncer_run\"
        PGB_CONFIG_DIR=\"\${NODE_TMP}/pgbouncer_config\"
        PGB_LOG_DIR=\"\${NODE_TMP}/pgbouncer_logs\"
        PGB_PID_FILE=\"\${NODE_TMP}/pgbouncer_${SLURM_JOB_ID}.pid\"
        
        # Create directories with proper permissions
        mkdir -p \"\${PGB_CONFIG_DIR}\" \"\${PGB_LOG_DIR}\" \"\${PGB_SOCKET_DIR}\"
        chmod 700 \"\${PGB_CONFIG_DIR}\" \"\${PGB_LOG_DIR}\" \"\${PGB_SOCKET_DIR}\"
        
        # Create pgbouncer.ini with connection to head node's PostgreSQL
        cat > \"\${PGB_CONFIG_DIR}/pgbouncer.ini\" << EOF
[databases]
${PG_DB} = host=${PGHOST} port=5432 dbname=${PG_DB} user=${PG_USER} password=${PG_PASSWORD}

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = ${PGB_PORT}
auth_type = md5
auth_file = \${PGB_CONFIG_DIR}/userlist.txt
logfile = \${PGB_LOG_DIR}/pgbouncer.log
pidfile = \${PGB_PID_FILE}
admin_users = ${PG_USER}
stats_users = ${PG_USER}
pool_mode = transaction
max_client_conn = 1000
default_pool_size = 40
min_pool_size = 10
reserve_pool_size = 20
reserve_pool_timeout = 3
max_db_connections = 60
max_user_connections = 60
server_reset_query = DISCARD ALL
server_check_delay = 30
server_check_query = SELECT 1
server_lifetime = 3600
server_idle_timeout = 600
idle_transaction_timeout = 600
EOF

        # Create userlist.txt for authentication
        cat > \"\${PGB_CONFIG_DIR}/userlist.txt\" << EOF
\"${PG_USER}\" \"${PG_PASSWORD}\"
EOF

        # Start PgBouncer using apptainer
        apptainer exec \
            --bind \"\${PGB_CONFIG_DIR}:/etc/pgbouncer\" \
            --bind \"\${PGB_LOG_DIR}:/var/log/pgbouncer\" \
            --bind \"\${PGB_SOCKET_DIR}:/var/run/pgbouncer\" \
            \"${PGB_SIF}\" pgbouncer /etc/pgbouncer/pgbouncer.ini &
        
        # Save PID for later cleanup
        echo \$! > \"\${PGB_PID_FILE}\"
        
        # Wait for PgBouncer to start
        for i in {1..10}; do
            if nc -z localhost ${PGB_PORT} 2>/dev/null; then
                echo \"\${NODE_ID}: PgBouncer started successfully on port ${PGB_PORT}\"
                break
            fi
            sleep 1
        done
    "

    # Batch processing using srun with MPI support
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
        --env "PGHOST=localhost" \
        --env "PGPORT=${PGB_PORT}" \
        --env "PGUSER=${PG_USER}" \
        --env "PGPASSWORD=${PG_PASSWORD}" \
        --env "PGDATABASE=${PG_DB}" \
        --workdir /solver \
        "${SOLVER_SIF}" python -m app.direct_runner run-parallel-batches \
        "${RECOVERY_DIR}" \
        "${RECOVERY_DIR_LOCAL}" \
        "${RECOVERY_SIMULATION_NAME}" \
    2>&1 | tee "${LOG_DIR}/powertwin_batches_${SLURM_JOB_ID}.log"
    
    # Cleanup PgBouncer on each node after batch processing
    print_status "info" "Cleaning up PgBouncer on each compute node..."
    srun --mpi=pmix --exclusive bash -c "
        NODE_ID=\$(hostname -s)
        NODE_TMP=\"${HPC_SHARED_STORAGE}/tmp/node_\${NODE_ID}_${SLURM_JOB_ID}\"
        PGB_PID_FILE=\"\${NODE_TMP}/pgbouncer_${SLURM_JOB_ID}.pid\"
        
        if [ -f \"\${PGB_PID_FILE}\" ]; then
            PGB_PID=\$(cat \"\${PGB_PID_FILE}\")
            if kill -0 \${PGB_PID} 2>/dev/null; then
                kill -TERM \${PGB_PID}
                echo \"\${NODE_ID}: PgBouncer stopped\"
            fi
            rm -f \"\${PGB_PID_FILE}\"
        fi
    "
    
    return 0
}

#------------------------------------------------------------------------------
# FUNCTION: cleanup_resources
# Description: Cleans up resources and monitoring processes
# Arguments: None
# Returns: 0 on success
#------------------------------------------------------------------------------
cleanup_resources() {
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
    
    # Clean up PostgreSQL, PgBouncer, and temporary files
    print_status "info" "Cleaning up resources..."
    #stop_pgbouncer
    stop_postgres
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
    initialize_environment || return 1
    recover_simulation || return 1
    setup_monitoring || return 1
    process_batches || return 1
    cleanup_resources
    
    print_status "info" "PowerTwin simulation completed successfully."
    return 0
}

# Execute the main function
main