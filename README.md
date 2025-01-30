# Powertwin Solver v1.0.1

## HOW TO RUN
```sh
docker-compose -f docker-compose.yml build
docker-compose -f docker-compose.yml up
```
## Autorun Simulation
1. Modify the simulation.json (demo has been provided)
2. Click autorun at the top of homepage or run autorun command

## Starting a Simulation
To begin a simulation there are 2 required files. The geojson file and the metadata csv.
Geojson must contain all the geometry and required properties id, asset_id, and floorCount.
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

## CLI Commands
This CLI tool allows you to manage simulations and related tasks for the Powertwin Solver. Below are the available commands and their usage. Open a new terminal with the follow command: 
```sh
docker exec -it <container_id_or_name> /bin/bash
```

### Autorun Simulation
Automatically run a simulation using the configuration defined in simulation.json.
```sh
python cli.py autorun
```

### Start Simulation
```sh
python cli.py start <simulation_name> <asset_geojson_path> <metadata_csv_path> <config_json_path> <location> <num_cores>
```
- `simulation_name`: Name of the simulation.
- `asset_geojson_path`: Path to the asset geojson file.
- `metadata_csv_path`: Path to the metadata CSV file.
- `config_json_path`: Path to the config JSON file.
- `location`: Location of the simulation.
- `num_cores`: Number of cores to use.

### Get Simulation Status
Get the status of a simulation.
```sh
python cli.py status <simulation_name> [--batch_id <batch_id>]
```
- `simulation_name`: Name of the simulation.
- `--batch_id`: (Optional) ID of the batch.

### Stop Simulation
Stop the currently running simulation.
```sh
python cli.py stop
```

### Recover Simulation
Recover a simulation from a corrupted state.
```sh
python cli.py recover <corrupted_simulation_name> <recover_simulation_name> <num_cores> [--batch_id <batch_id>]
```
- `corrupted_simulation_name`: Name of the corrupted simulation.
- `recover_simulation_name`: Name of the recovery simulation.
- `num_cores`: Number of cores to use.
- `--batch_id`: (Optional) ID of the batch.


### Get Asset Configuration
Get the configuration of a specific asset in a simulation.
```sh
python cli.py get_config <simulation_name> <asset_id>
```
- `simulation_name`: Name of the simulation.
- `asset_id`: ID of the asset.

### Get Logs
Retrieve the logs of the simulation.
```sh
python cli.py get_logs
```

## General Tree
```
🏠 app/
├── scripts/
│   ├── diagnostics
│   ├── helper
│   └── simulation
├── static/
│   ├── json
│   └── script.js
├── templates/
│   ├── base.html
│   └── testing.html
├── upload/
│   ├── demo_data
│   └── simulation.json
├── urbanopt/
│   ├── weather_files
│   ├── PowerTwin.rb
│   └── weather_map.csv
├── app.py
└── cli.py

```

## Runtime Generation Tree
```
⚡powertwin-data/
└── user_files/
    └── <simulation_name>/
        ├── feature_files.zip
        ├── <simulation_name>_metadata.csv
        ├── <simulation_name>_geojson.json
        ├── uosim_time.csv
        ├── feature_files/
        │   └── <asset_id>_<id_name>.json
        └── urbanopt_simulation/
            ├── batch_0
            ├── batch_1
            └── ...
```
The runtime generation tree describes the expected files create during runtime.
The powertwin-db is a shared volume between the powertwin-db and powertwin-solver container, this volume is then saved locally into powertwin_data.
*Plans to move uosim_time.csv and possibly the cleaned reports into a PostgreSQL db for efficiency 


## Temporary fixes
-Currently this program does not support Mixed use, Laboratory, Single Family Detached, Vacant subtypes, and due to UrbanOpt restraints, cannot support Multifamily, Multifamily (2 to 4 units), Multifamily (5 or more units) subtypes
-Occupancy assumptions are currently being made relative to the building subtype with a set value for each
-Only select few weather locations supported


## Useful Repositories
- [Powertwin Cleaner](https://github.com/nicotegui/powertwin_cleaner)
- [Powertwin Json Setup](https://github.com/nicotegui/powertwin_jsonsetup)
- [Powertwin Accuracy](https://github.com/nicotegui/powertwin_accuracy)
