#!/bin/bash
#SBATCH --job-name=test-start
#SBATCH --nodes=4                    
#SBATCH --ntasks-per-node=4         
#SBATCH --cpus-per-task=30          
#SBATCH --time=7-00:00:00           
#SBATCH --mem-per-cpu=6G            
#SBATCH --account=cowy-ptheory
#SBATCH --partition=teton            # Teton partition
#SBATCH --output=%x_%j.out
#SBATCH --qos=long                  #debug or long

# PowerTwin HPC Container Orchestration Script with Direct SLURM Parallelism
# This script uses a job array approach with proper SLURM step management
# where each step has a clear purpose and dependencies are properly handled
#
# This script includes integrated PostgreSQL database initialization functionality
# that was previously in hpc-build-db.sh, creating a unified workflow

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
LOCATION="Denver"

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
SOLVER_SIF="${SIF_DIR}/flask.sif"

# Shared directories
DATA_DIR="${HPC_SHARED_STORAGE}/powertwin_data"
USER_FILES_DIR="${HPC_SHARED_STORAGE}/user_files"
LOG_DIR="${HPC_SHARED_STORAGE}/logs"

TMP_BASE="${HPC_SHARED_STORAGE}/tmp"
export GEM_HOME="${TMP_BASE}/gems_${SLURM_JOB_ID}_${SLURM_PROCID}"
export HOME="${TMP_BASE}/home_${SLURM_JOB_ID}_${SLURM_PROCID}"
NETWORK_DIR="${TMP_BASE}/apptainer_network_${SLURM_JOB_ID}"
PG_PID_FILE="${TMP_BASE}/postgres_${SLURM_JOB_ID}.pid"
STATUS_MONITOR_PID_FILE="${TMP_BASE}/status_monitor_${SLURM_JOB_ID}.pid"
PG_SOCKET_DIR="/tmp/pg_socket_${SLURM_JOB_ID}"

mkdir -p "$GEM_HOME" "$HOME" "$NETWORK_DIR"
DB_DATA_DIR="${DATA_DIR}/postgres_data"  # Use existing PostgreSQL data directory


# Export variables for access in child processes
export POWERTWIN_LOG_DIR="${LOG_DIR}"
export SIMULATION_NAME
export HPC_SHARED_STORAGE
export UPLOAD_DIR
export ASSET_GEOJSON_PATH
export METADATA_CSV_PATH
export CONFIG_JSON_PATH
export LOCATION

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
    
    if [ ! -f "$SOLVER_SIF" ]; then
        print_status "error" "Solver SIF file not found at: $SOLVER_SIF"
        return 1
    fi
    
    print_status "info" "All SIF files found."
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

# Check PostgreSQL data directory
check_postgres_data() {
    print_status "info" "Checking PostgreSQL data directory..."
    
    # Check if the PostgreSQL data directory exists and has data files
    if [ ! -d "${DB_DATA_DIR}" ]; then
        print_status "warning" "PostgreSQL data directory not found. Will initialize new database."
        return 1
    fi
    
    # Check for critical PostgreSQL files
    if [ ! -f "${DB_DATA_DIR}/PG_VERSION" ]; then
        print_status "warning" "Invalid PostgreSQL data directory: ${DB_DATA_DIR}/PG_VERSION not found. Will initialize new database."
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

# =====================================================
# Database Initialization Functions (integrated from hpc-build-db.sh)
# =====================================================

# Initialize PostgreSQL data directory
initialize_pg_data_dir() {
    print_status "info" "Initializing PostgreSQL data directory..."
    
    # Check if directory already exists with PG_VERSION
    if [ -f "${DB_DATA_DIR}/PG_VERSION" ]; then
        print_status "warning" "PostgreSQL data directory already exists. Skipping initialization."
        return 0
    fi
    
    # Create database directory
    mkdir -p "${DB_DATA_DIR}"
    chmod 700 "${DB_DATA_DIR}"  # PostgreSQL requires strict permissions
    
    # Create socket directory for PostgreSQL
    mkdir -p "${PG_SOCKET_DIR}"
    chmod 777 "${PG_SOCKET_DIR}"
    
    # Create log directory
    mkdir -p "${LOG_DIR}"
    
    # Initialize PostgreSQL data directory using apptainer
    print_status "info" "Creating new PostgreSQL database cluster..."
    
    # Check if PostgreSQL SIF file exists
    PG_SIF="${SIF_DIR}/postgres17.sif"
    if [ ! -f "${PG_SIF}" ]; then
        print_status "warning" "PostgreSQL 17 container not found. Creating it..."
        apptainer build "${PG_SIF}" docker://postgres:17
    fi
    
    # Use a PostgreSQL container to initialize the database
    apptainer exec \
        --bind "${DB_DATA_DIR}:/data" \
        "${PG_SIF}" bash -c "mkdir -p /data && initdb -D /data -U ${PG_USER} --pwfile=<(echo '${PG_PASSWORD}') -E UTF8 --locale=C.UTF-8"
    
    # Configure PostgreSQL to allow connections
    cat > "${DB_DATA_DIR}/pg_hba.conf" << EOF
# TYPE  DATABASE        USER            ADDRESS                 METHOD
local   all             all                                     trust
host    all             all             127.0.0.1/32            trust
host    all             all             ::1/128                 trust
host    all             all             0.0.0.0/0               trust
EOF
    
    # Update postgresql.conf to listen on all interfaces
    cat > "${DB_DATA_DIR}/postgresql.conf" << EOF
listen_addresses = '*'
port = 5432
unix_socket_directories = '/tmp'
max_connections = 1000
shared_buffers = 2GB
work_mem = 32MB
maintenance_work_mem = 128MB
dynamic_shared_memory_type = posix
max_wal_size = 1GB
min_wal_size = 80MB
log_timezone = 'UTC'
datestyle = 'iso, mdy'
timezone = 'UTC'
lc_messages = 'C.UTF-8'
lc_monetary = 'C.UTF-8'
lc_numeric = 'C.UTF-8'
lc_time = 'C.UTF-8'
default_text_search_config = 'pg_catalog.english'
EOF
    
    print_status "info" "PostgreSQL data directory initialized successfully."
    return 0
}

# Create database and required tables
create_database_schema() {
    print_status "info" "Creating database and schema..."
    
    # Get the PostgreSQL SIF file
    PG_SIF="${SIF_DIR}/postgres17.sif"
    
    # Create PowerTwin database if it doesn't exist
    apptainer exec \
        --bind "${DB_DATA_DIR}:/data" \
        "${PG_SIF}" bash -c "createdb -U ${PG_USER} -h localhost ${PG_DB} || echo 'Database already exists'"
    
    # Create powertwin_solver table and required schema
    apptainer exec \
        --bind "${DB_DATA_DIR}:/data" \
        "${PG_SIF}" bash -c "PGPASSWORD=${PG_PASSWORD} psql -U ${PG_USER} -h localhost -d ${PG_DB} -c \"
        CREATE TABLE IF NOT EXISTS powertwin_solver (
            id SERIAL PRIMARY KEY,
            simulation_name VARCHAR(255) NOT NULL,
            asset_id VARCHAR(255) NOT NULL,
            batch INTEGER,
            order_rank INTEGER,
            status VARCHAR(50) DEFAULT 'pending',
            uorun_time DECIMAL(10,2) DEFAULT 0,
            uoprocess_time DECIMAL(10,2) DEFAULT 0,
            start_time TIMESTAMP,
            end_time TIMESTAMP,
            complexity INTEGER DEFAULT 0,
            floor_area DECIMAL(10,2) DEFAULT 0,
            number_of_stories INTEGER DEFAULT 0,
            asset_name VARCHAR(255),
            subtype VARCHAR(50),
            location VARCHAR(255),
            error_message TEXT,
            UNIQUE(simulation_name, asset_id),
            UNIQUE(asset_id)
        );
        
        CREATE INDEX IF NOT EXISTS idx_powertwin_solver_simulation_name ON powertwin_solver(simulation_name);
        CREATE INDEX IF NOT EXISTS idx_powertwin_solver_asset_id ON powertwin_solver(asset_id);
        CREATE INDEX IF NOT EXISTS idx_powertwin_solver_status ON powertwin_solver(status);
        CREATE INDEX IF NOT EXISTS idx_powertwin_solver_batch ON powertwin_solver(batch);
        \""
    
    # Check if table was created successfully
    TABLE_CHECK=$(apptainer exec \
        --bind "${DB_DATA_DIR}:/data" \
        "${PG_SIF}" bash -c "PGPASSWORD=${PG_PASSWORD} psql -U ${PG_USER} -h localhost -d ${PG_DB} -c \"SELECT to_regclass('public.powertwin_solver');\"")
    
    if echo "$TABLE_CHECK" | grep -q "powertwin_solver"; then
        print_status "info" "powertwin_solver table created successfully."
    else
        print_status "error" "Failed to create powertwin_solver table."
        return 1
    fi
    
    # List all tables to verify
    print_status "info" "Listing all tables in database:"
    apptainer exec \
        --bind "${DB_DATA_DIR}:/data" \
        "${PG_SIF}" bash -c "PGPASSWORD=${PG_PASSWORD} psql -U ${PG_USER} -h localhost -d ${PG_DB} -c \"SELECT table_name FROM information_schema.tables WHERE table_schema='public';\""
    
    return 0
}

# Verify database connection
verify_database() {
    print_status "info" "Verifying database connection..."
    
    # Get the PostgreSQL SIF file
    PG_SIF="${SIF_DIR}/postgres17.sif"
    
    # Try to connect to database and get version
    DB_VERSION=$(apptainer exec \
        --bind "${DB_DATA_DIR}:/data" \
        "${PG_SIF}" bash -c "PGPASSWORD=${PG_PASSWORD} psql -U ${PG_USER} -h localhost -d ${PG_DB} -c \"SELECT version();\"")
    
    echo "$DB_VERSION"
    
    print_status "info" "Database connection verified successfully."
    return 0
}

# Start PostgreSQL server
start_postgres() {
    print_status "info" "Starting PostgreSQL server..."
        
    # Make sure we're using a compatible container
    # Check PostgreSQL version in the container
    PG_SIF="${SIF_DIR}/postgres17.sif"
    if [ ! -f "${PG_SIF}" ]; then
        print_status "warning" "PostgreSQL 17 container not found. Creating it..."
        apptainer build "${PG_SIF}" docker://postgres:17
    fi
    
    # Start PostgreSQL server in background
    apptainer exec \
        --bind "${DB_DATA_DIR}:/data" \
        "${PG_SIF}" bash -c "postgres -D /data -h 0.0.0.0" &
    
    # Save PID for later cleanup
    echo $! > "${PG_PID_FILE}"
    
    # Wait for PostgreSQL to start up (max 30 seconds)
    print_status "info" "Waiting for PostgreSQL to start..."
    for i in {1..30}; do
        if apptainer exec "${PG_SIF}" bash -c "pg_isready -h localhost -p 5432" &>/dev/null; then
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
    print_status "info" "Cleaning up temporary files in /tmp..."
    
    # Remove apptainer network directory
    if [ -d "${NETWORK_DIR}" ]; then
        rm -rf "${NETWORK_DIR}"
        print_status "info" "Removed apptainer network directory: ${NETWORK_DIR}"
    fi
    
    # Clean up PostgreSQL PID file if it exists
    if [ -f "${PG_PID_FILE}" ]; then
        rm -f "${PG_PID_FILE}"
        print_status "info" "Removed PostgreSQL PID file: ${PG_PID_FILE}"
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
    
    if [ -d "$HOME" ] && [[ "$HOME" == /tmp/home_* ]]; then
        rm -rf "$HOME"
        print_status "info" "Removed temporary HOME: ${HOME}"
    fi
    
    # Clean up OpenStudio temporary directories
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
            --env "SIMULATION_NAME=${simulation_name}" \
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
simulation_name = '${simulation_name}'
read_simulation_status(simulation_name)
" >> "${log_file}" 2>&1
        
        # Sleep for the specified interval
        sleep ${interval_seconds}
    done
}

# Main script execution
# This script now includes integrated database initialization that will:
# 1. Check if PostgreSQL data directory exists
# 2. If not, initialize a new PostgreSQL cluster 
# 3. Create the required database and tables
# 4. Then proceed with the normal PowerTwin simulation workflow
main() {

    # Define simulation directories directly
    SIMULATION_DIR="${DATA_DIR}/${SIMULATION_NAME}"
    LOCAL_SIMULATION_DIR="${USER_FILES_DIR}/${SIMULATION_NAME}"
    
    
    # Check if PostgreSQL data directory exists, initialize if needed
    if ! check_postgres_data; then
        print_status "info" "Initializing new PostgreSQL database..."
        
        # Initialize PostgreSQL data directory
        if ! initialize_pg_data_dir; then
            print_status "error" "Failed to initialize PostgreSQL data directory. Exiting."
            exit 1
        fi
        
        # Start PostgreSQL server for initialization
        if ! start_postgres; then
            print_status "error" "Failed to start PostgreSQL server for initialization. Exiting."
            exit 1
        fi
        
        # Create database and required tables
        if ! create_database_schema; then
            print_status "error" "Failed to create database schema. Exiting."
            stop_postgres
            exit 1
        fi
        
        # Verify database connection
        if ! verify_database; then
            print_status "error" "Failed to verify database connection. Exiting."
            stop_postgres
            exit 1
        fi
        
        # Stop PostgreSQL server after initialization
        stop_postgres
        
        print_status "info" "PostgreSQL database initialization completed successfully!"
    fi


    # All validation and setup happens in one place - the master script
    check_sif_files || exit 1
    create_shared_dirs || exit 1
    validate_input_files || exit 1
    
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
    
    # STEP 2: Run UrbanOpt initialization as a separate SLURM step
    print_status "info" "STEP 2: Initializing UrbanOpt..."

    INIT_UO_OUTPUT=$(apptainer exec \
      --bind "${DATA_DIR}:/powertwin_data:rw" \
      --bind "${USER_FILES_DIR}:/powertwin-solver-pg/user_files:rw" \
      --bind "${HPC_SHARED_STORAGE}:${HPC_SHARED_STORAGE}:rw" \
      --bind "${DB_DATA_DIR}:/postgres_data:rw" \
      --bind "${LOG_DIR}:/solver/logs:rw" \
      --env "SIMULATION_NAME=${SIMULATION_NAME}" \
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
      --workdir /powertwin_data \
      "${SOLVER_SIF}" python -m app.direct_runner initialize-uo \
        "${SIMULATION_DIR}" \
        "${LOCAL_SIMULATION_DIR}" \
        "${SIMULATION_NAME}" \
        --hpc 2>&1 | tee "${LOG_DIR}/powertwin_init_${SLURM_JOB_ID}.log")

    TOTAL_BATCHES=$(echo "$INIT_UO_OUTPUT" | grep -oP 'returned \K[0-9]+(?= batches)' | tail -1)
    if [[ -z "$TOTAL_BATCHES" ]]; then
        print_status "error" "Could not determine total batch count from UrbanOpt initialization."
        stop_postgres
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

    
    # STEP 3: Run parallel batch processing with proper SLURM task distribution
    print_status "info" "STEP 3: Running parallel batch processing..."


    srun --mpi=pmix --exclusive \
    apptainer exec \
        --bind "${DATA_DIR}:/powertwin_data:rw" \
        --bind "${USER_FILES_DIR}:/powertwin-solver-pg/user_files:rw" \
        --bind "${HPC_SHARED_STORAGE}:${HPC_SHARED_STORAGE}:rw" \
        --bind "${DB_DATA_DIR}:/postgres_data:rw" \
        --bind "${LOG_DIR}:/solver/logs:rw" \
        --env "GEM_HOME=${GEM_HOME}" \
        --env "GEM_PATH=${GEM_PATH}" \
        --env "SIMULATION_NAME=${SIMULATION_NAME}" \
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
        "${SIMULATION_DIR}" \
        "${LOCAL_SIMULATION_DIR}" \
        "${SIMULATION_NAME}" \
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
                --env "SIMULATION_NAME=${SIMULATION_NAME}" \
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
    simulation_name = '${SIMULATION_NAME}'
    read_simulation_status(simulation_name)
    " >> "${STATUS_LOG_FILE}" 2>&1
        fi
        rm -f "${STATUS_MONITOR_PID_FILE}"
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