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
- **Asset Subtypes:** `solver/upload/asset_subtypes.csv` (building subtypes with occupancy categories and simulation type overrides)
- **Sensor Types:** `solver/upload/sensor_types.csv` (sensor type to EnergyPlus output column mappings)
- **Sensor Type Units:** `solver/upload/sensor_type_units.csv` (expected output units per sensor type)
- **Unit Scale Factors:** `solver/upload/reference_data/unit_scale_factors.json` (EnergyPlus unit-suffix to kBtu scaling, used by the column matcher to tolerate urbanopt unit-suffix drift, e.g. `(kBtu)` vs `(kWh)` on the same meter)
- **National-Stock Default Values:** `solver/upload/reference_data/` (see "Default-value provenance" section below)

### Unit Conversions (Clean Reports)

EnergyPlus/UrbanOpt outputs are converted to the target units defined in `sensor_types.csv` via `conversion_factor`. The raw EnergyPlus units are kBtu for thermal energy, kWh for electricity, and metric tons (MT) for emissions ([UrbanOpt Reporting Schema](https://docs.urbanopt.net/resources/customization/feature_reports.html)).

`clean_report.py`'s column matcher is **unit-suffix-aware**: when urbanopt writes a column under a different unit than the CSV's expected one (commonly `DistrictCooling:Facility(kWh)` vs the expected `(kBtu)`), the matcher resolves by prefix and scales values using `unit_scale_factors.json` before applying `conversion_factor`. Adding support for a new unit is a one-line JSON edit (no code change).

| ID | Sensor | EnergyPlus Column | Raw Unit | Output Unit | Factor | Source |
|----|--------|-------------------|----------|-------------|--------|--------|
| 1 | Electricity | `Electricity:Facility` | kWh | kWh | 1 | n/a |
| 2 | Renewables | `ElectricityProduced:Facility` | kWh | kWh | 1 | n/a |
| 3 | Hot Water | `WaterSystems:*` (4 fuels summed) | kBtu | MMBtu | 0.001 | 1 MMBtu = 1,000 kBtu |
| 4 | Water | *(not simulated)* | n/a | Gal | n/a | No EnergyPlus meter available |
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

## Commercial and Residential Workflow Support

The solver scaffolds every per-asset urbanopt project with `uo create --combined`, which copies the residential measure tree (`resources/residential-measures`, `mappers/residential`, `xml_building`) alongside the commercial mappers/measures. `PowerTwin.rb` picks the workflow branch per asset based on the resolved `building_type`:

| `building_type` (effective name) | Workflow path | Measure |
|---|---|---|
| `Single-Family Detached`, `Single-Family Attached`, `Multifamily` | residential | `BuildResidentialModel` (HPXML-driven) |
| everything else | commercial | `create_bar_from_building_type_ratios` + `create_typical_building_from_model` |

Residential routing is driven by `solver/upload/asset_subtypes.csv`'s `effective_id` column. Residential subtypes (id 1, 2, 3, 4, 5, 6) point at their residential canonical name; other subtypes route through the commercial path.

`generateFeatureFile.py` emits the residential-specific feature.json properties (`number_of_stories_above_ground`, `foundation_type`, `attic_type`, `number_of_residential_units`, `number_of_bedrooms`) when the resolved `building_type` is one of the three residential names. Unit counts derive from the original `asset_subtype_id`: Single-Family variants get 1 unit, Multifamily (2 to 4 units) gets 3, Multifamily (5 or more units) gets 8, generic Multifamily gets 4. Bedroom count per unit is `max(1, round(floor_area / 800 / units))` and the emitted `number_of_bedrooms` is `bedrooms_per_unit * units` to satisfy urbanopt's divisibility constraint.

`uo process` is invoked best-effort after `uo run`. Its bundler-activation conflict with the residential workflow (`parallel 1.19.1` vs `1.19.2`) is non-fatal: the per-asset `feature_reports/default_feature_report.csv` we consume is written by the `default_feature_reports` measure during `uo run` and doesn't depend on the scenario-level post-processor.

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

## Programmable Simulation Parameters

Each field below can be set per-asset under `assets.metadata` (snake_case key).
When omitted, the sim falls back to the listed default. Defaults match the
prior hardcoded behavior, so unmodified assets re-simulate identically.

Defaults live in `solver/app/modules/simulation/sim_params_spec.py` (mirrored
in `powertwin-db/api/lib/simulationParamsSpec.js`). Enum option lists are
seeded into `simulation_*_types` tables and served at
`POST /api/types/simulation/sim-types`. The full spec is served at
`GET /api/simulation/params-spec`.

| Field | Type | Flat fallback | Dynamic resolver | Notes |
|---|---|---|---|---|
| `system_type` | enum | `Inferred` | — | omitted from feature.json so urbanopt picks the building_type's template default |
| `heating_system_fuel_type` | enum | `natural gas` | RECS 2020 / CBECS 2018 region+vintage mode | `simulation_fuel_types` |
| `cooling_system_fuel_type` | enum | `electricity` | constant (~99%) | `simulation_fuel_types` |
| `service_water_heating_fuel_type` | enum | `natural gas` | RECS 2020 / CBECS 2018 region+vintage mode | `simulation_fuel_types` |
| `window_type` | enum | `Double Pane` | — | `simulation_window_types` |
| `wall_material` | enum | `Insulated` | RECS 2020 / CBECS 2018 region+vintage tier | `simulation_surface_materials` (solver appends " Wall") |
| `roof_material` | enum | `Insulated` | RECS 2020 / CBECS 2018 region+vintage tier | `simulation_surface_materials` (solver appends " Roof") |
| `window_to_wall_ratio` | number | `0.20` | OpenStudio Standards prototype WWR per building_type (commercial only) | range `[0.0, 0.8]` |
| `wall_r_value` | number | `13.0` | ResStock TRG / CBECS region+vintage avg | range `[1.0, 80.0]` |
| `roof_r_value` | number | `30.0` | ResStock TRG / CBECS region+vintage avg | range `[1.0, 80.0]` |
| `floor_height` | number | `9.0` ft | — | range `[6.0, 20.0]` |
| `weekday_start_time` | time `HH:MM` | `""` (template default) | — | emit only when paired with non-empty `weekday_duration` |
| `weekday_duration` | time `HH:MM` | `""` (template default) | — | must not equal `24:00` (schedule collision) |
| `weekend_start_time` | time `HH:MM` | `""` (template default) | — | emit only when paired with non-empty `weekend_duration` |
| `weekend_duration` | time `HH:MM` | `""` (template default) | — | must not equal `24:00` |
| `number_of_occupants` | number | flat `OCCUPANTS_MAPPING[occupancy_type]` | residential: `bedrooms + 1` (HPXML/Manual J); commercial: `area_sqft × people_per_1000ft² / 1000` (OpenStudio Standards) | range `[0, 100000]`. Empty triggers dynamic resolution; any explicit value (including 0) overrides it. |

Defaults resolve in this precedence:
1. Explicit `assets.metadata.<field>` set, non-empty → use it.
2. Dynamic resolver (see column above) keyed on the asset's `(building_type, state→census_region, year_built→vintage_bin, area, bedrooms)` context → use it.
3. Flat fallback above.

Editing any of these fields on a `Building` asset (asset_type_id=6) re-queues
a simulation via the `notify_asset_update` trigger.

### Default-value provenance

Every default and dynamic resolver in `sim_params_spec.py` traces to an
authoritative NREL / NatLabRockies / EIA source. Lookup tables live in
`solver/upload/reference_data/`.

**Feature flag**: `URBANOPT_DYNAMIC_DEFAULTS` (env var, default `false`).
When `false` (the conservative default), the solver and API both use the
flat `SIM_PARAM_DEFAULTS` directly, preserving the pre-dynamic behavior for
unmodified assets. When `true`, `resolve_default()` consults the lookup
tables below before falling back.

| File | Source | What it provides |
|---|---|---|
| `openstudio_standards_people_per_area.json` | [NREL openstudio-standards `ashrae_90_1_2013.spc_typ.json`](https://github.com/NREL/openstudio-standards/blob/master/lib/openstudio-standards/standards/ashrae_90_1/ashrae_90_1_2013/data/ashrae_90_1_2013.spc_typ.json) | Per-building_type `occupancy_per_area` (people / 1000 ft²) used by the commercial occupancy composite formula. Office uses the WholeBuilding aggregate; Warehouse uses an area-weighted DOE-prototype mix; others use the primary representative space type. |
| `recs2020_residential_fuel_mix.json` | [EIA RECS 2020](https://www.eia.gov/consumption/residential/data/2020/), Tables HC6.5 / HC7.5 / HC8.5 | Per (census_region, vintage_bin) modal heating / cooling / service-water fuel for residential workflow assets. |
| `cbecs2018_commercial_fuel_mix.json` | [EIA CBECS 2018](https://www.eia.gov/consumption/commercial/data/2018/), Tables B14 / B16 / B19 | Same shape for commercial workflow assets, derived from CBECS 2018 commercial floorspace fuel-use tables. |
| `recs2020_envelope.json` | EIA RECS 2020 + [NREL ResStock Technical Reference Guide 2025](https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock/2025/resstock_amy2018_release_1/ResStockTechnicalReferenceGuide_2025_1.pdf) + [NEEA RBSA 2012](https://neea.org/data/residential-building-stock-assessment) + IECC vintage minimums | Per (census_region, vintage_bin) modal wall / roof material tier and R-values for residential, plus per-building_type residential WWR (SFD / SFA / Multifamily) sourced from ResStock TRG and IECC 2021 fenestration limits. |
| `cbecs2018_envelope.json` | EIA CBECS 2018 + [NREL ComStock Reference Documentation V.1](https://www.osti.gov/biblio/1967948) + ASHRAE 90.1 vintage minimums | Per (census_region, vintage_bin) modal wall / roof material tier and R-values for commercial. Also: per-building_type `window_to_wall_ratio` keyed by DOE Reference Building prototype name. |
| `census_regions.json` | [US Census Bureau Statistical Regions and Divisions](https://www2.census.gov/geo/pdfs/maps-data/maps/reference/us_regdiv.pdf) | State→Census-Region (Northeast / Midwest / South / West) map. Carries both 2-letter abbreviation keys and a `state_name_to_abbr` reverse map so `build_asset_ctx` accepts either `'AZ'` or `'Arizona'` (any case). |
| `building_type_aliases.json` | [NREL ComStock Building Type Crosswalks](https://nrel.github.io/ComStock.github.io/docs/resources/explanations/building_type_crosswalks.html) + [NREL openstudio-standards space-type list](https://github.com/NREL/openstudio-standards/blob/master/lib/openstudio-standards/standards/ashrae_90_1/ashrae_90_1_2013/data/ashrae_90_1_2013.spc_typ.json) + [PNNL-32815](https://www.pnnl.gov/main/publications/external/technical_reports/PNNL-32815.pdf) | PowerTwin asset_subtype (CBECS PBA vocabulary) → DOE Reference Building / OpenStudio Standards prototype crosswalk. 13 mapped entries; CBECS categories NREL doesn't model (Religious worship, Public assembly, Service, Nursing, Refrigerated warehouse, Enclosed mall, Vacant) are documented as `_omitted_explicit` so the resolver degrades to flat defaults instead of using fabricated proxies. |
| `unit_scale_factors.json` | EnergyPlus + UrbanOpt schema | EnergyPlus unit-suffix to kBtu scaling (existing; moved into `reference_data/` alongside the new tables). |

**Residential occupants formula (HPXML / ACCA Manual J)**:
`occupants = bedrooms + 1` per the [OpenStudio-HPXML BuildResidentialHPXML README](https://github.com/NREL/OpenStudio-HPXML/blob/master/BuildResidentialHPXML/README.md): *"If NumberofResidents is not provided, it defaults to the number of bedrooms plus one per Manual J."* When `bedrooms` is absent on the asset, the solver infers `bedrooms ≈ round(area_sqft / 600)` (ResStock typical SFD).

**Commercial occupants formula (OpenStudio Standards)**:
`occupants = round(area_sqft × people_per_1000ft² / 1000)` where `people_per_1000ft²` is sourced from the OpenStudio Standards JSON for ASHRAE 90.1-2013 per building_type (above). PowerTwin's CBECS-vocabulary `asset_subtype.name` is first translated to the matching DOE Reference Building prototype name via `building_type_aliases.json` (e.g. `Education → PrimarySchool`, `Lodging → SmallHotel`, `Outpatient health care → Outpatient`). Falls back to the flat `OCCUPANTS_MAPPING` when the translated `building_type` isn't in the density table or has no canonical DOE prototype.

**State normalization**: `build_asset_ctx` accepts the asset's `state` in either 2-letter abbreviation (`AZ`, `CA`) or full-name (`Arizona`, `California`, any case) form. The normalization lookup is the `state_name_to_abbr` map in `census_regions.json`. Asset ingest sources differ in convention, and a state-format mismatch silently kills every region-keyed lookup (heating/SWH fuel, wall/roof material, wall/roof R-value) for that asset; normalization at the resolver entry point eliminates that class of bug.

**HPXML schema reference**:
[HPXML Data Dictionary](https://hpxml.nrel.gov/datadictionary/) — `NumberofResidents` schema (`xs:double`, optional).

**Vintage bins** (used by all RECS/CBECS lookups): `pre-1980`, `1980-1999`, `2000-2009`, `2010+`. Follows RECS published categorization.

**How to update**: when EIA publishes a new RECS / CBECS release, or NREL ships a new openstudio-standards or ResStock TRG, the procedure is:
1. Re-extract the updated values from the canonical source files
2. Bump `_source.extracted` (date) in each JSON
3. Confirm `sim_params_spec.py.SIM_PARAM_DEFAULTS` flat fallbacks still match the new national plurality/mode
4. Update the table above with the new source URLs if any moved

**Resolver coverage (operational note)**: with `URBANOPT_DYNAMIC_DEFAULTS=true`, an asset must have both `state` and `year_built` populated for the resolver to produce a region+vintage value. When either is missing the resolver degrades gracefully:

| Asset metadata has... | Fields that still resolve dynamically | Fields that fall to flat default |
|---|---|---|
| `state` + `year_built` | all 9 dynamic fields | none |
| `state` only (no `year_built`) | cooling fuel, WWR, occupants, heating fuel + SWH fuel (via region `all` bucket) | wall/roof material + R-values |
| `year_built` only (no `state`) | cooling fuel, WWR, occupants | heating/SWH fuel, wall/roof material + R-values |
| neither | cooling fuel, WWR, occupants | heating/SWH fuel, wall/roof material + R-values |

The ingest pipeline should populate `state` and `year_built` where possible to get full benefit. WWR, cooling fuel, and occupants are robust to missing state/vintage because they don't key on region/vintage -- but WWR and occupants do require a `building_type` that maps through `building_type_aliases.json` to a DOE Reference Building prototype (the omitted CBECS categories listed in that file's `_omitted_explicit` block fall to flat defaults for WWR + occupants).

**Resolver provenance API**: `POST /api/simulation/resolve-defaults` (solver) and `POST /api/simulation/params-spec/resolve` (db-api proxy) accept `{metadata, building_type}` and return:

```json
{
  "dynamic_defaults_enabled": true,
  "context":  { "building_type": "Office", "state": "WY", "region": "West",
                "vintage": "1980-1999", "area": 9667, "bedrooms": null },
  "resolved": { "heating_system_fuel_type": "natural gas", "wall_r_value": 11.0,
                "window_to_wall_ratio": 0.33, "number_of_occupants": 48, ... },
  "levels":   { "heating_system_fuel_type": "vintage_specific",
                "wall_r_value": "vintage_specific",
                "window_to_wall_ratio": "building_type_only",
                "number_of_occupants": "building_type_only",
                "floor_height": "flat_default", ... }
}
```

`levels[field]` is one of:

| Level | Meaning | Frontend hint |
|---|---|---|
| `vintage_specific` | Resolver matched the asset's region AND vintage in the stock survey | "based on age + region" (high confidence) |
| `region_all` | Region matched, vintage missing or unmapped; used the region's `all` bucket | "estimated, region average" (medium) |
| `building_type_only` | Resolver used a building-type-keyed table or context-derived formula (no region/vintage) | "estimated, building type" (medium) |
| `flat_default` | Resolver returned nothing; value came from the flat `SIM_PARAM_DEFAULTS` | "national default" (low) |

Every field in `DYNAMIC_FIELDS` always appears in `levels`. A field that does not appear in `resolved` is `flat_default` by definition. The taxonomy is mirrored as `FALLBACK_LEVELS` in `powertwin-db/api/lib/simulationParamsSpec.js` for the frontend.

**Per-request override**: both simulation endpoints accept an optional `dynamic_defaults` field (`'true'` / `'false'`, case-insensitive) that overrides the server-wide `URBANOPT_DYNAMIC_DEFAULTS` env for the duration of that one sim:

| Endpoint | Override carrier | Notes |
|---|---|---|
| `POST /api/simulation/start` (synchronous) | multipart form field | Used by the A/B accuracy test in `tests/score_dynamic_defaults_ab.py` to run back-to-back sims with the flag flipped. |
| `POST /api/simulation/asset_update` (asynchronous) | JSON body field | The powertwin-db listener can opt in by including the field when triggering a per-asset resim. |

In both cases the override is snapshot-and-restore inside the request handler (`os.environ` mutation, see `views.py:start_simulation` and `_run_asset_update_simulation`), so it doesn't leak to the next sim. `SIMULATION_CONCURRENCY=1` (single in-flight sim per Flask process) is the invariant that makes the env mutation safe -- the existing `URBANOPT_REPORTING_FREQUENCY` override depends on the same assumption. `resolve_default()` reads `URBANOPT_DYNAMIC_DEFAULTS` via `is_dynamic_defaults_enabled()` at call time, so the mutation takes effect for feature-file generation that runs in the same thread.

**Residential leap-year patch**: `URBANOPT_SIMULATION_YEAR=2024` (or any leap year) used to break the residential workflow because TMY3 EPWs are 8760 hours and OpenStudio-HPXML's `location.rb:apply_year` rejects the mismatch (it expects 8784 hours for a leap-year sim). `solver/patches/patch_hpxml_leap_year.py` (applied at Dockerfile build time) downgrades the model calendar to `sim_year - 1` when an 8760-hour EPW is paired with a leap year, so EnergyPlus reads its full weather stream against a non-leap calendar. Day-of-week assignment shifts by 1; for stock-survey aggregation that's acceptable noise. Commercial workflows are unaffected because they tolerate the mismatch silently. Without the patch, every residential sim with a leap-year `URBANOPT_SIMULATION_YEAR` fails fast at `BuildResidentialModel.run`.

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

