#!/bin/bash
#SBATCH --job-name=powertwin
#SBATCH --nodes=1                    # Request 1 node
#SBATCH --ntasks-per-node=4         # Request 4 tasks per node
#SBATCH --cpus-per-task=1            # 1 CPU per task (sequential asset processing)
#SBATCH --time=0-01:00:00            # 1-hour runtime
#SBATCH --mem-per-cpu=2G             # Memory per CPU core
#SBATCH --account=cowy-ptheory
#SBATCH --partition=teton            # Teton partition
#SBATCH --output=%x_%j.out
#SBATCH --qos=debug                  #debug or long

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
    
    # Path to the PID file
    PG_PID_FILE="/tmp/postgres_${SLURM_JOB_ID}.pid"
    
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

# Main script execution
main() {
    # Define simulation directories directly
    SIMULATION_DIR="${DATA_DIR}/${SIMULATION_NAME}"
    LOCAL_SIMULATION_DIR="${USER_FILES_DIR}/${SIMULATION_NAME}"
    
    # All validation and setup happens in one place - the master script
    check_sif_files || exit 1
    create_shared_dirs || exit 1
    validate_input_files || exit 1
    check_postgres_data || exit 1
    
    # Start PostgreSQL server
    start_postgres || exit 1
    
    # Display SLURM job information
    print_status "info" "======= SLURM Job Information ======="
    print_status "info" "Job ID: ${SLURM_JOB_ID}"
    print_status "info" "Number of nodes: ${SLURM_JOB_NUM_NODES}"
    print_status "info" "Number of tasks: ${SLURM_NTASKS}"
    print_status "info" "Tasks per node: ${SLURM_NTASKS_PER_NODE}"
    print_status "info" "==================================="
    
    # Create temp directory for tasks
    mkdir -p "${TEMP_DIR}"
    chmod 777 "${TEMP_DIR}"
    
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
        --env "PGHOST=localhost" \
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
    
    # Make directories writable
    mkdir -p "${SIMULATION_DIR}/feature_files"
    chmod 777 "${SIMULATION_DIR}/feature_files"
    chmod 777 "${SIMULATION_DIR}"
    chmod 777 "${LOCAL_SIMULATION_DIR}"
    chmod 777 "${DATA_DIR}"
    
    # Run UrbanOpt initialization
    apptainer exec \
      --cleanenv \
      --bind "${DATA_DIR}:/powertwin_data:rw" \
      --bind "${USER_FILES_DIR}:/powertwin-solver-pg/user_files:rw" \
      --bind "${HPC_SHARED_STORAGE}:${HPC_SHARED_STORAGE}:rw" \
      --bind "${DB_DATA_DIR}:/postgres_data:rw" \
      --bind "${LOG_DIR}:/solver/logs:rw" \
      --bind "${TEMP_DIR}:/tmp/powertwin:rw" \
      --env "SIMULATION_NAME=${SIMULATION_NAME}" \
      --env "SLURM_JOB_ID=${SLURM_JOB_ID}" \
      --env "PYTHONPATH=/solver" \
      --env "PYTHONDONTWRITEBYTECODE=1" \
      --env "POWERTWIN_LOG_DIR=/solver/logs" \
      --env "POSTGRES_USER=${PG_USER}" \
      --env "POSTGRES_PASSWORD=${PG_PASSWORD}" \
      --env "POSTGRES_DB=${PG_DB}" \
      --env "PGHOST=localhost" \
      --env "PGUSER=${PG_USER}" \
      --env "PGPASSWORD=${PG_PASSWORD}" \
      --env "PGDATABASE=${PG_DB}" \
      --workdir /powertwin_data \
      "${SOLVER_SIF}" bash -lc '
      set -e
      SIM_NAME="${SIMULATION_NAME}"
      SIM_DIR="/powertwin_data/${SIM_NAME}"
      ALT_DIR="'${HPC_SHARED_STORAGE}'/data/${SIM_NAME}"
      ZIP="${SIM_DIR}/feature_files.zip"
      ALT_ZIP="${ALT_DIR}/feature_files.zip"

      mkdir -p "$SIM_DIR"

      if [ ! -f "$ZIP" ]; then
        if [ -f "$ALT_ZIP" ]; then
          ln -sf "$ALT_ZIP" "$ZIP"
        else
          for BASE in "$SIM_DIR" "$ALT_DIR"; do
            if [ -d "$BASE/feature_files" ]; then
              mkdir -p /tmp/powertwin/locks
              if mkdir /tmp/powertwin/locks/zip_"$SIM_NAME" 2>/dev/null; then
                ( cd "$BASE" && /usr/bin/python3 - <<EOF
import shutil, os
src = "feature_files"
shutil.make_archive("feature_files", "zip", src)
print("Zipped", os.path.abspath(os.path.join(os.getcwd(), "feature_files.zip")))
EOF
                )
              fi
              [ -f "$BASE/feature_files.zip" ] && ln -sf "$BASE/feature_files.zip" "$ZIP"
              break
            fi
          done
        fi
      fi

      if [ ! -f "$ZIP" ]; then
        echo "[ERROR] feature_files.zip not found in $SIM_DIR or $ALT_DIR" >&2
        exit 2
      fi

      /usr/bin/python3 /solver/app/direct_runner.py initialize-uo \
        "$SIM_DIR" \
        "/powertwin-solver-pg/user_files/${SIM_NAME}" \
        "${SIM_NAME}" \
        --hpc \
        --shared-storage="'${HPC_SHARED_STORAGE}'"
      ' 2>&1 | tee "${LOG_DIR}/powertwin_init_${SLURM_JOB_ID}.log"
    
    INIT_UO_EXIT_CODE=$?
    if [ $INIT_UO_EXIT_CODE -ne 0 ]; then
        print_status "error" "Failed to initialize UrbanOpt"
        stop_postgres
        exit 1
    fi
    
    # STEP 3: Run parallel batch processing with proper SLURM task distribution
    print_status "info" "STEP 3: Running parallel batch processing..."
    
    # Create a file to track task completion
    COMPLETION_FILE="${TEMP_DIR}/completed_tasks.txt"
    touch "${COMPLETION_FILE}"
    
    # Launch batch processing tasks in parallel using srun
    # This properly distributes tasks across nodes and manages dependencies
    for i in $(seq 0 $((SLURM_NTASKS-1))); do
        # Create task-specific temp directory
        TASK_TEMP_DIR="${TEMP_DIR}/task_${i}"
        mkdir -p "${TASK_TEMP_DIR}"
        chmod 777 "${TASK_TEMP_DIR}"
        
        # Add a staggered delay to prevent UrbanOpt initialization conflicts
        sleep $((i*3))
        
        print_status "info" "Launching task for batch ${i}..."
        
        # Use srun to launch each batch task with proper SLURM_PROCID
        srun --ntasks=1 --exclusive \
            apptainer exec \
            --cleanenv \
            --bind "${DATA_DIR}:/powertwin_data:rw" \
            --bind "${USER_FILES_DIR}:/powertwin-solver-pg/user_files:rw" \
            --bind "${HPC_SHARED_STORAGE}:${HPC_SHARED_STORAGE}:rw" \
            --bind "${DB_DATA_DIR}:/postgres_data:rw" \
            --bind "${LOG_DIR}:/solver/logs:rw" \
            --bind "${TASK_TEMP_DIR}:/tmp/powertwin:rw" \
            --env "SIMULATION_NAME=${SIMULATION_NAME}" \
            --env "SLURM_JOB_ID=${SLURM_JOB_ID}" \
            --env "SLURM_JOB_NUM_NODES=${SLURM_JOB_NUM_NODES}" \
            --env "SLURM_NTASKS=${SLURM_NTASKS}" \
            --env "SLURM_PROCID=${i}" \
            --env "PYTHONPATH=/solver" \
            --env "PYTHONDONTWRITEBYTECODE=1" \
            --env "POWERTWIN_LOG_DIR=/solver/logs" \
            --env "TMPDIR=/tmp/powertwin" \
            --env "POSTGRES_USER=${PG_USER}" \
            --env "POSTGRES_PASSWORD=${PG_PASSWORD}" \
            --env "POSTGRES_DB=${PG_DB}" \
            --env "PGHOST=localhost" \
            --env "PGUSER=${PG_USER}" \
            --env "PGPASSWORD=${PG_PASSWORD}" \
            --env "PGDATABASE=${PG_DB}" \
            --workdir /solver \
            "${SOLVER_SIF}" bash -c "echo 'Task ${i} starting batch ${i}' && python3 -m app.direct_runner run-specific-batch '${SIMULATION_DIR}' '${LOCAL_SIMULATION_DIR}' '${SIMULATION_NAME}' ${i} && echo ${i} >> ${COMPLETION_FILE}" \
            2>&1 | tee "${LOG_DIR}/powertwin_batch${i}_${SLURM_JOB_ID}.log" &
    done
    
    # Wait for all background tasks to complete
    wait
    
    # Check if all tasks completed successfully
    COMPLETED_TASKS=$(wc -l < "${COMPLETION_FILE}")
    if [ "${COMPLETED_TASKS}" -eq "${SLURM_NTASKS}" ]; then
        print_status "info" "All ${SLURM_NTASKS} batch tasks completed successfully."
    else
        print_status "warning" "Only ${COMPLETED_TASKS} of ${SLURM_NTASKS} batch tasks completed successfully."
    fi
    
    # Clean up
    print_status "info" "Cleaning up resources..."
    stop_postgres
    
    print_status "info" "PowerTwin simulation completed."
    return 0
}

# Execute the main function
main
