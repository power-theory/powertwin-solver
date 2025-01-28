# Powertwin Solver


## HOW TO RUN
```
docker-compose -f docker-compose.yml build
docker-compose -f docker-compose.yml up
```
## Autorun Simulation
1. Modify the simulation.json (demo has been provided)
2. Click autorun at the top of homepage

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
```
docker top <container_id> 
```

1. Docker container or simulation has stopped 
2. Restart Docker container (Optional: Check batch status) 
3. Corrupted simulation should be the name of the simulation that you want to recover (Optional: choose a specific batch)
4. Recovery simulation name is the new simulation that you want to create
5. Allocate however many cores, does not have to be the same amount
6. Start the recovery


## General Tree
```
📦 app
├── powertwin-db
├── scripts/
│   ├── diagnostics
│   ├── logger
│   └── simulation
├── static/
│   ├── json
│   └── script.js
├── templates/
│   └── base.html
├── test
├── app.py
└── requirements.txt
```

## Runtime Generation Tree
```
powertwin-db/
├── weather_files
├── PowerTwin.rb
└── user_files/
    └── <simulation_name>/
        ├── feature_files.zip
        ├── metadata.csv
        ├── geojson.json
        ├── uosim_time.csv
        ├── feature_files/
        │   └── <id>_<id_name>.json
        └── urbanopt_simulation/
            ├── batch_0
            ├── batch_1
            └── ...
```
The runtime generation tree describes the expected files create during runtime.
The powertwin-db ideally will be moved outside of this repo into a proper database along with the uosim_time.csv to be moved to a PostgreSQL database to take advantage of the fast lookup and data management system


## Temporary fixes
-Currently this program does not support Mixed use, Laboratory, Single Family Detached, Vacant subtypes, and due to UrbanOpt restraints, cannot support Multifamily, Multifamily (2 to 4 units), Multifamily (5 or more units) subtypes
-Occupancy assumptions are currently being made relative to the building subtype with a set value for each
-Only select few weather files




## Useful Repositories
- [Powertwin Cleaner](https://github.com/nicotegui/powertwin_cleaner)
- [Powertwin Json Setup](https://github.com/nicotegui/powertwin_jsonsetup)
- [Powertwin Accuracy](https://github.com/nicotegui/powertwin_accuracy)
