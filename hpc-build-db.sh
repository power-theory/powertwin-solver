#!/bin/bash
#SBATCH --job-name=build_db
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --time=0-00:30:00
#SBATCH --mem=4G
#SBATCH --account=cowy-ptheory
#SBATCH --partition=teton
#SBATCH --output=%x_%j.out
#SBATCH --qos=debug

# Script to initialize a PostgreSQL database for PowerTwin on HPC
# This creates the required database structure that will be used by the PowerTwin simulations

set -e  # Exit immediately if a command exits with a non-zero status

# Load required modules
module --force purge
module load arcc/1.0
module load slurm
module load apptainer/1.4.1

# =====================================================
# Configuration Variables - MODIFY THESE AS NEEDED
# =====================================================
HPC_SHARED_STORAGE="/project/cowy-ptheory/colorado_powertwin"
PG_USER="postgres"
PG_PASSWORD="admin"
PG_DB="powertwin"

# SIF files location
SIF_DIR="${HPC_SHARED_STORAGE}/sif_containers"
PG_SIF="${SIF_DIR}/postgres17.sif"  # Use a dedicated PostgreSQL container

# Database directory
DB_DATA_DIR="${HPC_SHARED_STORAGE}/powertwin_data/postgres_data"
LOG_DIR="${HPC_SHARED_STORAGE}/logs"
PG_SOCKET_DIR="/tmp/pg_socket_${SLURM_JOB_ID}"


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

# =====================================================
# Database Initialization Functions
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
    if [ ! -f "${PG_SIF}" ]; then
        print_status "error" "PostgreSQL SIF file not found: ${PG_SIF}"
        print_status "info" "You need to create it with: apptainer build postgres.sif docker://postgres:14"
        return 1
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

# Start PostgreSQL server for initialization
start_postgres_server() {
    print_status "info" "Starting PostgreSQL server..."
    
    # Define PID file to track PostgreSQL server process
    PG_PID_FILE="${HPC_SHARED_STORAGE}/postgres.pid"
    
    # Start PostgreSQL server in background
    apptainer exec \
        --bind "${DB_DATA_DIR}:/data" \
        "${PG_SIF}" bash -c "postgres -D /data -h 0.0.0.0" &
    
    # Save PID
    echo $! > "${PG_PID_FILE}"
    
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

# Create database and required tables
create_database_schema() {
    print_status "info" "Creating database and schema..."
    
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

# Stop PostgreSQL server
stop_postgres_server() {
    print_status "info" "Stopping PostgreSQL server..."
    
    # Define PID file
    PG_PID_FILE="${HPC_SHARED_STORAGE}/postgres.pid"
    
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

# Verify database connection
verify_database() {
    print_status "info" "Verifying database connection..."
    
    # Try to connect to database and get version
    DB_VERSION=$(apptainer exec \
        --bind "${DB_DATA_DIR}:/data" \
        "${PG_SIF}" bash -c "PGPASSWORD=${PG_PASSWORD} psql -U ${PG_USER} -h localhost -d ${PG_DB} -c \"SELECT version();\"")
    
    echo "$DB_VERSION"
    
    print_status "info" "Database connection verified successfully."
    return 0
}

# =====================================================
# Main Script Execution
# =====================================================

echo "======================================================"
echo "PowerTwin PostgreSQL Database Initialization"
echo "======================================================"
echo "Job ID: ${SLURM_JOB_ID}"
echo "Database directory: ${DB_DATA_DIR}"
echo "Database user: ${PG_USER}"
echo "Database name: ${PG_DB}"
echo "======================================================"

# Step 1: Initialize PostgreSQL data directory
if ! initialize_pg_data_dir; then
    print_status "error" "Failed to initialize PostgreSQL data directory. Exiting."
    exit 1
fi

# Step 2: Start PostgreSQL server
if ! start_postgres_server; then
    print_status "error" "Failed to start PostgreSQL server. Exiting."
    exit 1
fi

# Step 3: Create database and required tables
if ! create_database_schema; then
    print_status "error" "Failed to create database schema. Exiting."
    stop_postgres_server
    exit 1
fi

# Step 4: Verify database connection
if ! verify_database; then
    print_status "error" "Failed to verify database connection. Exiting."
    stop_postgres_server
    exit 1
fi

# Step 5: Stop PostgreSQL server
stop_postgres_server

print_status "success" "PostgreSQL database initialization completed successfully!"
print_status "info" "Database is ready for use with PowerTwin simulations."
print_status "info" "Database location: ${DB_DATA_DIR}"

exit 0
