#!/bin/bash

#SBATCH --account=cowy-ptheory
#SBATCH --partition=teton
#SBATCH --qos=long

#SBATCH --time=7-00:00:00
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=8              # Fewer MPI tasks per node
#SBATCH --cpus-per-task=4                # More cores per task for local parallelization  
#SBATCH --mem-per-cpu=2G
#SBATCH --mail-user=nicolasreategui0@gmail.com
#SBATCH --mail-type=BEGIN,END,FAIL
#SBATCH --get-user-env

# Job configuration variables - MODIFY THESE FOR YOUR SIMULATION
SIMULATION_NAME="my_simulation"
ASSET_GEOJSON_PATH="/path/to/shared/storage/asset_geometries.geojson"
METADATA_CSV_PATH="/path/to/shared/storage/metadata.csv"
CONFIG_JSON_PATH="/path/to/shared/storage/config.json"
LOCATION="Phoenix-SkyHarbor"
SHARED_STORAGE_PATH="/path/to/shared/storage"

# Calculate total tasks (nodes * ntasks-per-node)
TOTAL_TASKS=$((SLURM_JOB_NUM_NODES * SLURM_NTASKS_PER_NODE))
echo "Total MPI tasks: $TOTAL_TASKS"
echo "Cores per task: $SLURM_CPUS_PER_TASK"
echo "Total cores utilized: $((TOTAL_TASKS * SLURM_CPUS_PER_TASK))"

# Load required modules (adjust for your HPC system)
module load python/3.11
module load openmpi/4.1.4

# Create Python virtual environment on shared storage if it doesn't exist
VENV_PATH="${SHARED_STORAGE_PATH}/powertwin_venv"
if [ ! -d "$VENV_PATH" ]; then
    echo "Creating Python virtual environment at $VENV_PATH"
    python -m venv "$VENV_PATH"
    source "$VENV_PATH/bin/activate"
    pip install --upgrade pip
    pip install -r requirements.txt
else
    echo "Using existing virtual environment at $VENV_PATH"
    source "$VENV_PATH/bin/activate"
fi

# Set up shared directories
mkdir -p "${SHARED_STORAGE_PATH}/simulations"
mkdir -p "${SHARED_STORAGE_PATH}/logs"

# Set environment variables for HPC execution
export PYTHONPATH="${SHARED_STORAGE_PATH}/powertwin-solver/solver:$PYTHONPATH"
export OMP_NUM_THREADS=1  # Prevent OpenMP from interfering with MPI

# Log job information
echo "=========================================="
echo "PowerTwin Solver HPC Job"
echo "Job ID: $SLURM_JOB_ID"
echo "Nodes: $SLURM_JOB_NUM_NODES"
echo "Tasks per node: $SLURM_NTASKS_PER_NODE"
echo "Total tasks: $TOTAL_TASKS"
echo "Simulation: $SIMULATION_NAME"
echo "Shared storage: $SHARED_STORAGE_PATH"
echo "=========================================="

# Change to solver directory
cd "${SHARED_STORAGE_PATH}/powertwin-solver/solver"

# Run the PowerTwin solver with MPI
echo "Starting PowerTwin simulation with MPI..."
mpirun -n $TOTAL_TASKS python -m app.cli start \
    "$SIMULATION_NAME" \
    "$ASSET_GEOJSON_PATH" \
    "$METADATA_CSV_PATH" \
    "$CONFIG_JSON_PATH" \
    "$LOCATION" \
    "$TOTAL_TASKS" \
    --hpc \
    --shared-storage "$SHARED_STORAGE_PATH" \
    2>&1 | tee "${SHARED_STORAGE_PATH}/logs/powertwin_${SLURM_JOB_ID}.log"

echo "PowerTwin simulation completed."
echo "Check logs at: ${SHARED_STORAGE_PATH}/logs/powertwin_${SLURM_JOB_ID}.log"

# Optional: Clean up temporary files
# rm -rf "${SHARED_STORAGE_PATH}/simulations/${SIMULATION_NAME}/temp"