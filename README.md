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
docker exec -it powertwin-solver-flask /bin/bash
```

### Available Commands

| Command | Description | Usage |
|---------|-------------|-------|
| `solver autorun` | Run simulation using `simulation.json` | `solver autorun` |
| `solver start` | Start a new simulation | `solver start <simulation_name> <asset_geojson_path> <metadata_csv_path> <location> <num_cores>` |
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
solver start <simulation_name> <asset_geojson_path> <metadata_csv_path> <location> <num_cores>
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
        └── metadata.csv
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
3. <simulation_name> in simulation parameters should match name of <simulation_name> in the upload directory
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


## Reference Data Sources

### Weather Files
- **Source:** TMY3 (Typical Meteorological Year 3) weather data from the National Renewable Energy Laboratory (NREL)
- **Stations:** 1,470 USA weather stations defined in `solver/app/urbanopt/master_weather.geojson`
- **Files:** `.epw`, `.ddy`, `.stat` files downloaded on-demand from NREL S3 storage
- **Selection:** Nearest station by haversine distance from building lat/lon coordinates

### Climate Zones
- **Zone Data:** IECC 2021 climate zones by county from `solver/app/urbanopt/ClimateZones.csv` (3,220 counties)
- **County Boundaries:** US Census Bureau cartographic boundaries from [Plotly Datasets](https://github.com/plotly/datasets) stored in `solver/app/urbanopt/us_counties.geojson` (3,221 counties)
- **Lookup:** Shapely STRtree spatial index for point-in-polygon county resolution at building coordinates
- **Fallback:** State-level climate zone mapping if county lookup fails

### Type Mappings
- **Asset Subtypes:** `solver/upload/asset_subtypes.csv` — building subtypes with occupancy categories and simulation type overrides
- **Sensor Types:** `solver/upload/sensor_types.csv` — sensor type to EnergyPlus output column mappings
- **Sensor Type Units:** `solver/upload/sensor_type_units.csv` — expected output units per sensor type

### Unit Conversions (Clean Reports)

EnergyPlus/UrbanOpt outputs are converted to the target units defined in `sensor_types.csv` via `conversion_factor`. The raw EnergyPlus units are kBtu for thermal energy, kWh for electricity, and metric tons (MT) for emissions ([UrbanOpt Reporting Schema](https://docs.urbanopt.net/resources/customization/feature_reports.html)).

| ID | Sensor | EnergyPlus Column | Raw Unit | Output Unit | Factor | Source |
|----|--------|-------------------|----------|-------------|--------|--------|
| 1 | Electricity | `Electricity:Facility` | kWh | kWh | 1 | — |
| 2 | Renewables | `ElectricityProduced:Facility` | kWh | kWh | 1 | — |
| 3 | Hot Water | `WaterSystems:*` (4 fuels summed) | kBtu | MMBtu | 0.001 | 1 MMBtu = 1,000 kBtu |
| 4 | Water | *(not simulated)* | — | Gal | — | No EnergyPlus meter available |
| 5 | Chilled Water | `DistrictCooling:Facility` | kBtu | Ton-Hr | 0.083333 | 1 Ton-Hr = 12,000 BTU = 12 kBtu |
| 6 | CO2 Emissions | `*_Emissions(MT)` (4 sources summed) | MT | MT | 1 | [UrbanOpt schema](https://docs.urbanopt.net/resources/customization/feature_reports.html): "emissions in metric ton (mt)"; [Cambium/NREL](https://docs.nrel.gov/docs/fy24osti/89309.pdf) |
| 7 | Steam | `DistrictHeatingSteam:Facility` | kBtu | lbs | 1.030928 | 970 BTU/lb latent heat of vaporization at atmospheric pressure ([Engineering Toolbox](https://www.engineeringtoolbox.com/saturated-steam-properties-d_273.html)) |
| 8 | Natural Gas | `NaturalGas:Facility` | kBtu | MMBtu | 0.001 | 1 MMBtu = 1,000 kBtu |
| 9 | Propane | `Propane:Facility` | kBtu | Gal | 0.010935 | 91,452 BTU/gal ([EIA](https://www.eia.gov/energyexplained/units-and-calculators/british-thermal-units.php)) |
| 10 | Fuel Oil | `FuelOilNo2:Facility` | kBtu | Gal | 0.007210 | 138,690 BTU/gal ([EIA](https://www.eia.gov/totalenergy/data/monthly/pdf/sec12_2.pdf)) |

**Notes:**
- Propane: EIA thermal conversion factor is 3.841 MMBtu/barrel = 91,452 BTU/gal (NIST combustion enthalpy at 60°F)
- Fuel Oil #2: EIA thermal conversion factor is 5.825 MMBtu/barrel = 138,690 BTU/gal
- Steam: 970 BTU/lb is the standard latent heat of vaporization at 14.7 psia (212°F). Actual value varies with pressure.
- CO2: "MT" = metric ton per UrbanOpt/Cambium convention, consistent with [EPA GHG reporting](https://www.epa.gov/ghgemissions/inventory-us-greenhouse-gas-emissions-and-sinks)

## DOE Ref Template Compatibility

Buildings with `year_built` data are assigned an ASHRAE/DOE template that determines internal loads, schedules, and construction properties. The `lookup_template_by_year_built` method in `solver/upload/PowerTwin.rb` selects templates as follows:

| Year Built | Template |
|---|---|
| < 1980 | DOE Ref Pre-1980 |
| 1980–2004 | DOE Ref 1980-2004 |
| 2005–2007 | 90.1-2004 |
| 2008–2010 | 90.1-2007 |
| 2011–2013 | 90.1-2010 |
| > 2013 | 90.1-2013 |

### Incompatible Building Types

Two building types are **incompatible** with DOE Ref templates and always fall back to `90.1-2004`:

| Building Type | Root Cause |
|---|---|
| **SmallHotel** | `space_type_ratios.rb` uses floor-specific names (`GuestRoom123Occ`, `GuestRoom123Vac`) that only exist in 90.1-2004+ templates. DOE Ref templates define `GuestRoom` without floor suffixes. |
| **Laboratory** | No space type definitions exist in DOE Ref Pre-1980 or DOE Ref 1980-2004 templates. Laboratory is only defined in 90.1-2004+. |

These are controlled by the `DOE_REF_INCOMPATIBLE` constant in `PowerTwin.rb`. LargeHotel (Lodging > 3 floors) uses generic `GuestRoom` and **is** compatible.

### Verified Compatible Types

All 12 remaining commercial building types have been empirically verified against DOE Ref Pre-1980 and DOE Ref 1980-2004 templates by running `create_bar_from_building_type_ratios` + `create_typical_building_from_model` through OpenStudio:

SecondarySchool, SmallOffice, MediumOffice, LargeOffice, RetailStandalone, RetailStripmall, FullServiceRestaurant, LargeHotel, Warehouse, Hospital, Outpatient, MidriseApartment

### Mixed Use Buildings

For Mixed Use buildings, the template applies to **all** component types in a single simulation. If any component type is DOE-Ref-incompatible (e.g., a mixed-use building containing a SmallHotel component), the entire building falls back to `90.1-2004`.

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

