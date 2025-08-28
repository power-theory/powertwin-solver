#!/bin/bash
#SBATCH --job-name=powertwin
#SBATCH --nodes=1                    # Request 4 nodes
#SBATCH --ntasks-per-node=4         # Request 32 tasks per node (matches most common node type)
#SBATCH --cpus-per-task=1            # 1 CPU per task (sequential asset processing)
#SBATCH --time=0-01:00:00            # 7-day runtime
#SBATCH --mem-per-cpu=2G             # Memory per CPU core
#SBATCH --account=cowy-ptheory
#SBATCH --partition=teton            # Teton partition
#SBATCH --output=%x_%j.out
#SBATCH --qos=debug                  #debug or long


# PowerTwin HPC Container Orchestration Script
# This script launches and orchestrates PowerTwin containers in an HPC environment
# with proper shared storage, networking, and database connectivity.
# It also automatically runs the simulation and provides periodic status updates.

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
HPC_SHARED_STORAGE="/project/cowy-ptheory/powertwin"
UPLOAD_DIR="${HPC_SHARED_STORAGE}/upload"
ASSET_GEOJSON_PATH="${UPLOAD_DIR}/${SIMULATION_NAME}/asu-asset-geometries.geojson"
METADATA_CSV_PATH="${UPLOAD_DIR}/${SIMULATION_NAME}/asu_metadata.csv"
CONFIG_JSON_PATH="${UPLOAD_DIR}/${SIMULATION_NAME}/default_config.json"
LOCATION="Phoenix-SkyHarbor"

# Container configuration
PG_USER="postgres"
PG_PASSWORD="admin"
PG_DB="powertwin"
MSS_PORT="8000"
SOLVER_PORT="8080"

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
LOG_DIR="${HPC_SHARED_STORAGE}/logs"
TEMP_DIR="/tmp/powertwin_${SLURM_JOB_ID}"

DB_DATA_DIR="${DATA_DIR}/postgres_data"  # Use existing PostgreSQL data directory
USER_FILES_DIR="${HPC_SHARED_STORAGE}/user_files"
NETWORK_DIR="/tmp/apptainer_network_${SLURM_JOB_ID}"


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
        "error")
            echo -e "${RED}[ERROR]${NC} $2"
            ;;
        "warning")
            echo -e "${YELLOW}[WARNING]${NC} $2"
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
        print_status "error" "MSS SIF file not found: $MSS_SIF"
        return 1
    fi
    
    if [ ! -f "$SOLVER_SIF" ]; then
        print_status "error" "Solver SIF file not found: $SOLVER_SIF"
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
        print_status "error" "Failed to create one or more shared directories"
        return 1
    fi
    
    print_status "info" "Shared directories created successfully."
    return 0
}

# Validate input files
validate_input_files() {
    print_status "info" "Validating input files..."
    
    if [ ! -f "$ASSET_GEOJSON_PATH" ]; then
        print_status "error" "Asset GeoJSON file not found: $ASSET_GEOJSON_PATH"
        return 1
    fi
    
    if [ ! -f "$METADATA_CSV_PATH" ]; then
        print_status "error" "Metadata CSV file not found: $METADATA_CSV_PATH"
        return 1
    fi
    
    if [ ! -f "$CONFIG_JSON_PATH" ]; then
        print_status "error" "Config JSON file not found: $CONFIG_JSON_PATH"
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
        print_status "error" "PostgreSQL data directory not found: ${DB_DATA_DIR}"
        return 1
    fi
    
    # Check for critical PostgreSQL files
    if [ ! -f "${DB_DATA_DIR}/PG_VERSION" ]; then
        print_status "error" "PostgreSQL data directory appears incomplete (missing PG_VERSION)"
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
    
    # Define PID file to track PostgreSQL server process
    PG_PID_FILE="/tmp/postgres_${SLURM_JOB_ID}.pid"
    
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
    
    # Save PID
    PG_PID=$!
    echo ${PG_PID} > "${PG_PID_FILE}"
    
    # Wait for PostgreSQL to start
    print_status "info" "Waiting for PostgreSQL to start..."
    sleep 5
    
    # Check if PostgreSQL is running using a simple query
    for i in {1..20}; do
        if apptainer exec \
            --bind "${DB_DATA_DIR}:/data" \
            "${PG_SIF}" bash -c "PGPASSWORD=${PG_PASSWORD} psql -U ${PG_USER} -h localhost -c 'SELECT 1;'" > /dev/null 2>&1; then
            print_status "info" "PostgreSQL server started successfully."
            return 0
        fi
        print_status "info" "Waiting for PostgreSQL server to start... (attempt $i/20)"
        sleep 3
    done
    
    print_status "error" "Failed to start PostgreSQL server."
    return 1
}

# Stop PostgreSQL server
stop_postgres() {
    print_status "info" "Stopping PostgreSQL server..."
    
    # Define PID file
    PG_PID_FILE="/tmp/postgres_${SLURM_JOB_ID}.pid"
    
    # Check if PID file exists
    if [ -f "${PG_PID_FILE}" ]; then
        # Get PID
        PG_PID=$(cat "${PG_PID_FILE}")
        
        # Kill process
        kill ${PG_PID} 2>/dev/null || true
        
        # Wait for process to terminate
        for i in {1..10}; do
            if ! kill -0 ${PG_PID} 2>/dev/null; then
                print_status "info" "PostgreSQL server stopped."
                rm -f "${PG_PID_FILE}"
                return 0
            fi
            print_status "info" "Waiting for PostgreSQL server to stop... (attempt $i/10)"
            sleep 1
        done
        
        # Force kill if still running
        kill -9 ${PG_PID} 2>/dev/null || true
        rm -f "${PG_PID_FILE}"
    else
        print_status "warning" "PostgreSQL PID file not found. Server may not be running."
    fi
    
    return 0
}

# Start MSS container (no longer needed with direct runner)
start_mss() {
    print_status "info" "MSS container no longer needed with direct runner."
    return 0
}

# Start Solver container (no longer needed with direct runner)
start_solver() {
    print_status "info" "Solver container no longer needed with direct runner - using direct execution instead."
    return 0
}

# Run PowerTwin simulation using SLURM native execution
run_simulation_mpi() {
    print_status "info" "Running PowerTwin simulation with SLURM..."
    
    # Calculate total tasks and cores from SLURM environment
    TOTAL_TASKS=$((SLURM_JOB_NUM_NODES * SLURM_NTASKS_PER_NODE))
    TOTAL_CORES=$((TOTAL_TASKS * SLURM_CPUS_PER_TASK))
    
    print_status "info" "Using ${TOTAL_TASKS} tasks across ${SLURM_JOB_NUM_NODES} nodes"
    print_status "info" "Each task will use ${SLURM_CPUS_PER_TASK} cores (${TOTAL_CORES} total cores)"
    
    # Create feature files first (on main node only)
    print_status "info" "Creating feature files using direct runner..."
    
    # Run feature files creation and capture the log 
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
        --env "PGHOST=localhost" \
        --env "PGUSER=${PG_USER}" \
        --env "PGPASSWORD=${PG_PASSWORD}" \
        --env "PGDATABASE=${PGDATABASE}" \
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
        return 1
    fi
    
    print_status "info" "Feature files created successfully."
    
    # Define simulation directories directly - we know where they are
    SIMULATION_DIR="${DATA_DIR}/${SIMULATION_NAME}"
    LOCAL_SIMULATION_DIR="${USER_FILES_DIR}/${SIMULATION_NAME}"
    
    print_status "info" "Using simulation directory: ${SIMULATION_DIR}"
    print_status "info" "Using local simulation directory: ${LOCAL_SIMULATION_DIR}"
    
    # Run UrbanOpt initialization in parallel using srun
    print_status "info" "Initializing UrbanOpt using direct runner with srun..."
    
    # Prepare the urbanopt_simulation directory to avoid read-only filesystem errors
    mkdir -p "${SIMULATION_DIR}/urbanopt_simulation"
    chmod 777 "${SIMULATION_DIR}/urbanopt_simulation"
    
    # Give full permissions to feature_files directory
    mkdir -p "${SIMULATION_DIR}/feature_files"
    chmod 777 "${SIMULATION_DIR}/feature_files"
    
    # Give permissions to parent directories to ensure we can write to them
    chmod 777 "${SIMULATION_DIR}"
    chmod 777 "${LOCAL_SIMULATION_DIR}"
    chmod 777 "${DATA_DIR}"
    
    # Create temporary writable directory for each task
    TEMP_DIR="/tmp/powertwin_${SLURM_JOB_ID}"
    mkdir -p "${TEMP_DIR}"
    chmod 777 "${TEMP_DIR}"
    
    srun --ntasks=$TOTAL_TASKS apptainer exec \
        --bind "${DATA_DIR}:/powertwin_data:rw" \
        --bind "${USER_FILES_DIR}:/powertwin-solver-pg/user_files:rw" \
        --bind "${HPC_SHARED_STORAGE}:${HPC_SHARED_STORAGE}:rw" \
        --bind "${DB_DATA_DIR}:/postgres_data" \
        --bind "${LOG_DIR}:/solver/logs" \
        --bind "${TEMP_DIR}:/tmp/powertwin:rw" \
        --env "POWERTWIN_LOG_DIR=/solver/logs" \
        --env "TMPDIR=/tmp/powertwin" \
        --env "HOME=/tmp/powertwin" \
        --env "POSTGRES_USER=${PG_USER}" \
        --env "POSTGRES_PASSWORD=${PG_PASSWORD}" \
        --env "POSTGRES_DB=${PG_DB}" \
        --env "PGHOST=localhost" \
        --env "PGUSER=${PG_USER}" \
        --env "PGPASSWORD=${PG_PASSWORD}" \
        --env "PGDATABASE=${PGDATABASE}" \
        --workdir /solver \
        "${SOLVER_SIF}" bash -c "cd /solver && python -m app.direct_runner initialize-uo \
        \"/powertwin_data/${SIMULATION_NAME}\" \
        \"/powertwin-solver-pg/user_files/${SIMULATION_NAME}\" \
        \"${SIMULATION_NAME}\" \
        --hpc \
        --shared-storage=\"${HPC_SHARED_STORAGE}\"" \
        2>&1 | tee "${LOG_DIR}/powertwin_solver_${SLURM_JOB_ID}.log"
    
    SIMULATION_EXIT_CODE=${PIPESTATUS[0]}
    if [ $SIMULATION_EXIT_CODE -ne 0 ]; then
        print_status "error" "UrbanOpt initialization failed with exit code ${SIMULATION_EXIT_CODE}"
        return 1
    fi
    
    print_status "info" "Simulation initialization completed successfully."
    return 0
}

# Stop containers (no longer needed with direct runner)
stop_containers() {
    print_status "info" "Container instances no longer used with direct runner."
    return 0
}

# Cleanup function
cleanup() {
    print_status "info" "Performing cleanup..."
    
    # Stop PostgreSQL server
    stop_postgres
    
    # Stop any running containers
    stop_containers
    
    # Remove temporary network directory
    rm -rf "${NETWORK_DIR}" 2>/dev/null || true
    
    # Remove temporary directory for each task
    if [ -d "${TEMP_DIR}" ]; then
        rm -rf "${TEMP_DIR}" 2>/dev/null || true
    fi
    
    print_status "info" "Cleanup completed."
}

# =====================================================
# Main Script Execution
# =====================================================

# Set up trap to ensure cleanup on exit
trap cleanup EXIT

# Print header
echo "======================================================"
echo "PowerTwin HPC Container Orchestration"
echo "======================================================"
echo "Job ID: ${SLURM_JOB_ID}"
echo "Simulation name: ${SIMULATION_NAME}"
echo "Nodes: ${SLURM_JOB_NUM_NODES}"
echo "Tasks per node: ${SLURM_NTASKS_PER_NODE}"
echo "CPUs per task: ${SLURM_CPUS_PER_TASK}"
echo "Shared storage: ${HPC_SHARED_STORAGE}"
echo "PostgreSQL connection: ${PGHOST}:5432 (user: ${PGUSER}, database: ${PGDATABASE})"
echo "======================================================"

# Verify SIF files exist
if ! check_sif_files; then
    print_status "error" "Failed to verify SIF files. Exiting."
    exit 1
fi

# Create necessary directories
if ! create_shared_dirs; then
    print_status "error" "Failed to create shared directories. Exiting."
    exit 1
fi

# Validate input files
if ! validate_input_files; then
    print_status "error" "Failed to validate input files. Exiting."
    exit 1
fi

# Check PostgreSQL data directory
if ! check_postgres_data; then
    print_status "error" "Failed to validate PostgreSQL data directory. Exiting."
    exit 1
fi

# Start PostgreSQL server
if ! start_postgres; then
    print_status "error" "Failed to start PostgreSQL server. Exiting."
    exit 1
fi

# Start MSS container
if ! start_mss; then
    print_status "error" "Failed to start MSS container. Exiting."
    exit 1
fi

# Start Solver container
if ! start_solver; then
    print_status "error" "Failed to start Solver container. Exiting."
    exit 1
fi

# Run the simulation using SLURM and direct CLI start (more reliable for HPC)
if ! run_simulation_mpi; then
    print_status "error" "Failed to start SLURM simulation. Exiting."
    exit 1
fi

# Success message
print_status "info" "PowerTwin HPC job completed successfully!"
print_status "info" "Results available at: ${USER_FILES_DIR}/${SIMULATION_NAME}/cleaned_reports"

exit 0