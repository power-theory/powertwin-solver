# PowerTwin Solver HPC Setup Guide

This guide explains how to run PowerTwin Solver on HPC clusters using SLURM and MPI.

## Prerequisites

1. **HPC Cluster Requirements:**
   - SLURM job scheduler
   - MPI implementation (OpenMPI or MPICH)
   - Python 3.11+
   - Shared storage accessible from all compute nodes

2. **Software Dependencies:**
   - All requirements from `requirements.txt` including `mpi4py`
   - UrbanOpt CLI tools installed on all nodes

## Setup Instructions

### 1. Prepare Shared Storage

Create a shared storage directory accessible from all compute nodes:

```bash
# Example shared storage structure
/shared/storage/powertwin/
├── powertwin-solver/          # Clone of this repository
├── simulations/               # Simulation input files
│   ├── asset_geometries.geojson
│   ├── metadata.csv
│   └── config.json
├── powertwin_venv/           # Python virtual environment
└── logs/                     # Job logs
```

### 2. Install Dependencies

On the shared storage, create a Python virtual environment:

```bash
cd /path/to/shared/storage
python -m venv powertwin_venv
source powertwin_venv/bin/activate
pip install --upgrade pip
cd powertwin-solver/solver
pip install -r requirements.txt
```

### 3. Configure SLURM Script

Edit `hpc_submit.sh` and update these variables:

```bash
# Job configuration
SIMULATION_NAME="your_simulation_name"
ASSET_GEOJSON_PATH="/path/to/shared/storage/asset_geometries.geojson"
METADATA_CSV_PATH="/path/to/shared/storage/metadata.csv"
CONFIG_JSON_PATH="/path/to/shared/storage/config.json"
LOCATION="Phoenix-SkyHarbor"  # Or your target location
SHARED_STORAGE_PATH="/path/to/shared/storage"

# Adjust SLURM parameters as needed
#SBATCH --nodes=4                    # Number of nodes
#SBATCH --ntasks-per-node=32         # Tasks per node
#SBATCH --time=7-00:00:00           # Wall time
#SBATCH --mem-per-cpu=2G            # Memory per CPU
```

### 4. Submit Job

```bash
sbatch hpc_submit.sh
```

## How It Works

### Local vs HPC Mode

The solver automatically detects the execution environment:

- **Local Mode (default):** Uses `joblib` for multiprocessing on a single node
- **HPC Mode (`--hpc` flag):** Uses MPI for distributed processing across multiple nodes

### Hybrid Parallelization Strategy

The solver now uses **dual-level parallelization** for maximum efficiency:

#### Level 1: Inter-Node (MPI)
- **Batch Distribution:** Assets are divided into batches, then batches are distributed across MPI ranks
- **Node Coordination:** Each MPI rank processes its assigned batches independently  
- **Shared Storage:** All nodes access input/output files through shared storage

#### Level 2: Intra-Node (Local Multiprocessing)
- **Within Each Batch:** Assets within a batch are processed in parallel using `SLURM_CPUS_PER_TASK` cores
- **Threading Backend:** Uses joblib with threading to avoid GIL issues with I/O-bound operations
- **Automatic Detection:** Reads `SLURM_CPUS_PER_TASK` to determine cores per MPI task

### Optimized Resource Configuration

**Recommended SLURM Configuration:**
```bash
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=8     # Fewer MPI tasks per node
#SBATCH --cpus-per-task=4       # More cores per task
```

This gives you:
- **32 MPI tasks total** (4 nodes × 8 tasks/node)
- **4 cores per MPI task** for local parallelization
- **128 total cores utilized** (32 tasks × 4 cores/task)

## Example Usage

### Local Mode (Single Node)
```bash
python -m app.cli start my_sim asset.geojson metadata.csv config.json Phoenix-SkyHarbor 16
```

### HPC Mode (Multi-Node)
```bash
# Via SLURM script
sbatch hpc_submit.sh

# Or directly with mpirun (if needed)
mpirun -n 128 python -m app.cli start my_sim asset.geojson metadata.csv config.json Phoenix-SkyHarbor 128 --hpc --shared-storage /shared/storage
```

## Monitoring

### Check Job Status
```bash
squeue -u $USER
```

### View Logs
```bash
tail -f /shared/storage/logs/powertwin_${SLURM_JOB_ID}.log
```

### Check MPI Ranks
The solver logs will show which MPI rank is processing which batches:
```
Rank 0: Processing batches [0, 1, 2]
Rank 1: Processing batches [3, 4, 5]
...
```

## Troubleshooting

### Common Issues

1. **MPI Not Available:**
   - Error: "MPI mode requested but MPI/SLURM not available"
   - Solution: Load MPI module and ensure `mpi4py` is installed

2. **Shared Storage Access:**
   - Error: "Permission denied" or "No such file or directory"
   - Solution: Ensure shared storage is mounted and accessible from all nodes

3. **Memory Issues:**
   - Error: Out of memory errors
   - Solution: Adjust `--mem-per-cpu` in SLURM script or reduce batch sizes

4. **Database Connections:**
   - Error: Database connection failures from worker nodes
   - Solution: Ensure database is accessible from all compute nodes

### Performance Tuning

- **Batch Size:** Larger batches reduce overhead but increase memory usage
- **Node Count:** More nodes = more parallelism but higher communication overhead
- **Tasks per Node:** Balance with available memory and CPU cores

## File Structure During Execution

```
/shared/storage/
├── simulations/
│   └── my_simulation/
│       ├── feature_files.zip
│       ├── feature_files/           # Extracted feature files
│       └── urbanopt_simulation/     # UrbanOpt project
├── node_0/                         # Rank 0 local work directory
├── node_1/                         # Rank 1 local work directory
├── ...
└── logs/
    └── powertwin_12345.log         # Job log
```

Each MPI rank creates its own local work directory to avoid file conflicts during parallel execution.