# PowerTwin Solver v1.2

## HOW TO RUN
```sh
docker compose -f docker-compose-local.yml build
docker compose -f docker-compose-local.yml up
```

## Autorun Simulation
1. Modify the simulation.json located in app/upload prior to building (demo has been provided)
2. Click autorun at the top of homepage or run autorun command

## Starting a Simulation
To begin a simulation there are 2 required files. The geojson file and the metadata csv.
Geojson must contain all the geometry and required properties id, asset_id, and floor_count.
Metadata csv for the simluation must contain building area, building type, and building name however clean report will require additional features.

1. Upload Geojson and Metadata csv files
2. Adjust the feature file configuration for any custom changes, otherwise default configuration will apply
3. Assign the number of cores
4. Name the simulation
5. Start the simulation

## Recovering a Stopped Simulation (or Batch)
In the event of a stopped simulation, as long as the simulation directory remains the simulation may still be recovered and you may even change the amount of assigned cores or select a specific batch you would like to run.

To check PID status run this command on the local machines CLI, app.py should have 2 processes any additional belong to the simulation.
```sh
docker ps
docker top <container_id> 
```

1. Docker container or simulation has stopped 
2. Restart Docker container (Optional: Check batch status) 
3. Corrupted simulation should be the name of the simulation that you want to recover (Optional: choose a specific batch)
4. Recovery simulation name is the new simulation that you want to create
5. Allocate however many cores, does not have to be the same amount
6. Start the recovery

## Command Line Interface

Access the PowerTwin Solver CLI by opening a new terminal session in your container:

```sh
docker exec -it <container_id_or_name> /bin/bash
```

### Available Commands

| Command | Description | Usage |
|---------|-------------|-------|
| `solver autorun` | Run simulation using `simulation.json` | `solver autorun` |
| `solver start` | Start a new simulation | `solver start <simulation_name> <asset_geojson_path> <metadata_csv_path> <config_json_path> <location> <num_cores>` |
| `solver status` | Check simulation status | `solver status <simulation_name> [-b <batch_id>]` |
| `solver stop` | Stop running simulation | `solver stop` |
| `solver delete` | Delete a simulation | `solver delete <simulation_name>` |
| `solver recover` | Recover corrupted simulation | `solver recover <corrupted_simulation_name> <recovery_simulation_name> <num_cores> [-b <batch_id>]` |
| `solver get_config` | Get asset configuration | `solver get_config <simulation_name> <asset_id>` |
| `solver get_data` | Export database data | `solver get_data` |
| `solver logs` | View simulation logs | `solver logs` |

### Command Details

#### Start Simulation
```sh
solver start <simulation_name> <asset_geojson_path> <metadata_csv_path> <config_json_path> <location> <num_cores>
```
- Starts a new simulation with specified parameters
- Required files:
  - Asset GeoJSON file with geometry and properties
  - Metadata CSV with building information
  - Configuration JSON for custom settings
- Supports multiple locations and core allocation
- OPTIONAL: Set up simulation.json onfiguration file and use autorun command

#### Recovery Process
```sh
solver recover <corrupted_simulation_name> <recovery_simulation_name> <num_cores> [-b <batch_id>]
```
- Recovers simulations from interruptions
- Optional batch recovery with `-b` flag
- Flexible core reallocation
- Preserves existing progress

#### Monitoring and Debugging
```sh

# Export database data
solver get_data

# Export logs
solver logs 

# Check simulation status
solver status <simulation_name>

```

## HPC Deployment Guide

### Prerequisites

- Access to an HPC environment with SLURM scheduler
- Apptainer/Singularity module available
- Adequate storage allocation in your HPC project directory

Download directory locally and build with Docker
```bash
docker compose -f docker-compose-local.yml build
docker tag powertwin-solver-powertwin-solver-flask:latest <docker_username>/powertwin-solver-flask:latest
docker push <docker_username>/powertwin-solver-flask:latest
```

### Step 1: Set Up Directory Structure

Create the following directory structure in your HPC shared storage:

```bash
/<project_directory>/
├── sif_containers/     # Container images
└── upload/             # Input files
    └── <simulation_name>/
        ├── asset-geometries.geojson
        ├── metadata.csv
        └── default_config.json
```

### Step 2: Build Container Images

Convert the Docker images to Apptainer/Singularity format:

```bash
# Load the Apptainer module
module load apptainer/1.4.1

# Build required container images in your sif_containers directory
cd /<project_directory>/sif_containers

# Required: Solver container
apptainer build flask.sif docker://<docker_username>/powertwin-solver-flask:latest

```

### Step 3: Configure and Run Simulations

1. Modify simulation parameters in the HPC scripts as needed (Paths injest files)
2. HPC_SHARED_DIR and <project_directory> should be the same as <HPC_SHARED_STORAGE>
3. <simulation_name> in simulation parameters should match name of upload/<simulation_name>
4. Submit jobs using SLURM:

```bash
# Default
sbatch apptainer/sql-start.sh

# Auto recovery mode (if needed)
sbatch apptainer/sql-start-auto.sh
```

### Step 4: Monitor Progress

-NOTE: There is already a simulation status checker built into the bash script.

-Check simulation status with:
```bash
# View job status
squeue -u $USER

# Check log files
tail -f powertwin_*_<job_id>.out

# Post consolidation database statistics
python read_sqlite_db.py <path_to_db>
```


## Future Development Roadmap

### Building Type Support
- Implement support for additional building types:
  - Mixed-use buildings with multiple function spaces
  - Laboratory facilities with specialized equipment requirements
  - Single Family Detached homes
  - Various Multifamily configurations (2-4 units, 5+ units)
  - Vacant buildings with minimal systems

### Occupancy Modeling
- Develop dynamic occupancy modeling system
- Replace static subtype-based occupancy values with data-driven estimates
- Implement time-of-day and seasonal occupancy variations

### Feature Configuration
- Enhance feature file configuration options
- Add support for precise measurement specifications
- Implement validation for configuration parameters

### Data Management
- Migrate cleaned simulation data to PostgreSQL database
- Utilize URBANopt process command capabilities
- Implement automated data backup and archiving

### Performance Optimization
- Enhance status monitoring for parallel operations
- Balance resource allocation for multi-batch simulations

