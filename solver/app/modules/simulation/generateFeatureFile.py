############################################################################################################
# getfeaturefile.py
# This script reads the JSON data from the input file and the area data from the
#   metadata file. It processes each feature and creates a new feature structure with additional properties.
#   It writes the new feature structure to individual feature files in the output directory.
############################################################################################################

import csv
import json
import math
import os
import shutil
import re

from modules.diagnostics import asset_analysis
from modules.utils import initialize_logger
from modules.simulation.sim_params_spec import get_param, OCCUPANTS_MAPPING, build_asset_ctx, SQFT_PER_BEDROOM

external_log_dir = os.environ.get('POWERTWIN_LOG_DIR')
logger = initialize_logger('Generate Feature Files', external_log_dir)

# Load asset subtypes from CSV, keyed by id with {name, occupancy_type, effective_id} values
ASSET_SUBTYPES = {}
ASSET_SUBTYPES_CSV = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'upload', 'asset_subtypes.csv')
with open(ASSET_SUBTYPES_CSV, 'r') as f:
    for row in csv.DictReader(f):
        ASSET_SUBTYPES[int(row['id'])] = {
            'name': row['name'],
            'occupancy_type': row['occupancy_type'],
            'effective_id': int(row['effective_id']),
        }

DEFAULT_SUBTYPE_ID = 4  # Single-Family

# Reverse lookup from building_type name to occupancy_type (using effective/self-referencing rows only)
BUILDING_TYPE_TO_OCCUPANCY = {}
for sid, info in ASSET_SUBTYPES.items():
    if info['effective_id'] == sid:
        BUILDING_TYPE_TO_OCCUPANCY[info['name']] = info['occupancy_type']

def sanitize_filename(name):
    sanitized = name.replace("'", "")
    sanitized = re.sub(r'[^\w\-]', '_', sanitized)
    sanitized = re.sub(r'_+', '_', sanitized)
    sanitized = sanitized.strip('_')
    return sanitized

############################################################################################################
# Name: read_metadata()
# Description: This function reads the metadata CSV file and returns the building area and type data.
############################################################################################################
def read_metadata(metadata_csv):
    from modules.utils.weather import get_location

    building_area_list = {}
    building_type_list = {}
    building_name_list = {}
    building_weather_list = {}
    building_climate_zone_list = {}
    building_year_list = {}
    building_metadata_list = {}
    processed_building_ids = set()
    skip_counts = {'missing_area': 0, 'missing_id': 0, 'duplicate_id': 0}
    total_rows = 0


    with open(metadata_csv, 'r') as metadata_file:
        reader = csv.DictReader(metadata_file)

        # Read each row in the CSV file to assign building data to its corresponding building ID
        for row in reader:
            total_rows += 1
            asset_name = row['asset_name']
            asset_subtype_id = row.get('asset_subtype_id', '')
            asset_geometries_properties = json.loads(row['asset_geometries_properties'])
            asset_metadata = json.loads(row['asset_metadata'])

            floor_area = asset_metadata.get('area')
            building_id = str(asset_geometries_properties.get('id')) # Most important id, considered the PK

            if not building_id:
                skip_counts['missing_id'] += 1
                continue
            if not floor_area:
                skip_counts['missing_area'] += 1
                logger.warning(f"Skipping building {building_id}: missing floor area")
                continue
            if building_id in processed_building_ids:
                skip_counts['duplicate_id'] += 1
                logger.debug(f"Skipping building {building_id}: duplicate ID")
                continue

            # Resolve subtype: parse ID, fall back to default if missing/invalid
            try:
                subtype_id = int(asset_subtype_id)
            except (ValueError, TypeError):
                subtype_id = DEFAULT_SUBTYPE_ID

            if subtype_id not in ASSET_SUBTYPES:
                subtype_id = DEFAULT_SUBTYPE_ID

            # Resolve effective subtype via effective_id (handles temporary remappings)
            # https://docs.urbanopt.net/workflows/residential_workflows/building_types.html
            effective_id = ASSET_SUBTYPES[subtype_id]['effective_id']
            effective_subtype = ASSET_SUBTYPES[effective_id]
            building_type = effective_subtype['name']

            processed_building_ids.add(building_id)

            state, weather_title, climate_zone = get_location(asset_metadata)

            # Stash the resolved subtype id on the metadata dict so the
            # residential block in process_feature can derive per-asset unit
            # counts without re-reading the CSV.
            asset_metadata['_subtype_id'] = subtype_id

            building_name_list[building_id] = asset_name
            building_area_list[building_id] = int(floor_area)
            building_type_list[building_id] = building_type
            building_weather_list[building_id] = (state, weather_title)
            building_climate_zone_list[building_id] = climate_zone
            building_metadata_list[building_id] = asset_metadata

            raw_year = asset_metadata.get('year_built') or asset_metadata.get('yearBuilt')
            if raw_year is not None:
                try:
                    building_year_list[building_id] = int(raw_year)
                except (ValueError, TypeError):
                    logger.warning(f"Invalid year_built value for building {building_id}: {raw_year!r}")

    accepted = len(building_area_list)
    skipped = sum(skip_counts.values())
    logger.info(f"Metadata: {accepted} buildings accepted from {total_rows} rows. "
                f"Skipped {skipped} ({skip_counts['missing_area']} missing area, "
                f"{skip_counts['missing_id']} missing ID, {skip_counts['duplicate_id']} duplicate ID)")

    return building_area_list, building_type_list, building_name_list, building_weather_list, building_climate_zone_list, building_year_list, building_metadata_list

def _ring_area_unsigned(ring):
    """Shoelace formula for the unsigned area of a coordinate ring."""
    n = len(ring)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += ring[i][0] * ring[j][1]
        area -= ring[j][0] * ring[i][1]
    return abs(area) / 2.0


def _ring_perimeter(ring):
    """Euclidean perimeter of a coordinate ring (in coordinate units)."""
    total = 0.0
    for i in range(len(ring) - 1):
        dx = ring[i + 1][0] - ring[i][0]
        dy = ring[i + 1][1] - ring[i][1]
        total += math.sqrt(dx * dx + dy * dy)
    return total


def flatten_geometry(geom):
    """Flatten MultiPolygon to Polygon by selecting the largest polygon by area.

    Previous implementation merged all rings from all polygons, which created
    topologically invalid geometry (second polygon's outer ring became a hole).
    """
    if not geom or 'type' not in geom or 'coordinates' not in geom:
        return False

    gt = geom['type']
    coords = geom['coordinates']

    if gt == 'MultiPolygon':
        best_idx = 0
        best_area = 0.0
        for i, poly in enumerate(coords):
            a = _ring_area_unsigned(poly[0]) if poly else 0.0
            if a > best_area:
                best_area = a
                best_idx = i
        if len(coords) > 1:
            logger.debug(f"MultiPolygon with {len(coords)} parts — using largest (area={best_area:.1f})")
        geom['type'] = 'Polygon'
        geom['coordinates'] = coords[best_idx]
        return True
    return False


############################################################################################################
# Name: process_feature()
# Description: This function processes each feature and creates a new feature structure with additional properties.
#   It returns the new feature structure.
############################################################################################################
def process_feature(feature, building_area_list, building_type_list, building_name_list, building_weather_list, building_climate_zone_list, building_year_list, building_metadata_list):
    # Flatten nested geometries if present
    if 'geometry' in feature:
        flatten_geometry(feature['geometry'])

    properties = feature['properties']
    #logger.debug(f"Processing feature with properties: {properties}")
    asset_id = str(properties.get('asset_id'))
    building_id = str(properties.get('id'))

    # Essential data missing in metadata
    if building_id not in building_area_list or building_id not in building_type_list or building_id not in building_name_list:
        return None

    floor_area = building_area_list[building_id]
    building_type = building_type_list[building_id]
    building_name = sanitize_filename(building_name_list[building_id])

    UNSIMULATABLE_TYPES = {'Uncovered Parking', 'Covered Parking'}
    if building_type in UNSIMULATABLE_TYPES:
        logger.warning(f"Skipping {building_name} (building_id {building_id}): {building_type} has no building energy model")
        return None

    asset_metadata = building_metadata_list.get(building_id, {})

    floor_count = asset_metadata.get('floor_count') or properties.get('floor_count')
    if floor_count == str(floor_count):
        floor_count = int(floor_count)
    if floor_count is None:
        floor_count = 1

    # Build the asset context once; every get_param call below uses it to
    # resolve dynamic defaults from the national-stock lookup tables
    # (reference_data/) before falling back to SIM_PARAM_DEFAULTS.
    ctx = build_asset_ctx({**asset_metadata, 'area': floor_area}, building_type=building_type)
    occupancy_subtype = BUILDING_TYPE_TO_OCCUPANCY.get(building_type, "Unknown")
    occ_override = get_param(asset_metadata, 'number_of_occupants', ctx)
    try:
        number_of_occupants = int(occ_override)
    except (TypeError, ValueError):
        number_of_occupants = OCCUPANTS_MAPPING.get(occupancy_subtype, 1)

    # Create new properties (must be first)
    new_properties = {
        'id': str(properties.pop('id')),
        'asset_id': str(properties.pop('asset_id'))
    }
    new_properties.update(properties)

    # Programmable sim params: read from asset_metadata via get_param, which
    # checks dynamic (national-stock) defaults before the flat fallback. See
    # sim_params_spec.py / api/lib/simulationParamsSpec.js.
    floor_height = get_param(asset_metadata, 'floor_height', ctx)
    window_to_wall_ratio = get_param(asset_metadata, 'window_to_wall_ratio', ctx)

    footprint_area = int(floor_area / floor_count)
    # Use GeoJSON geometry to derive the shape factor for a more accurate
    # perimeter estimate. The ratio of actual perimeter to the perimeter of a
    # square with the same area captures aspect ratio and irregularity. For a
    # square this ratio is 1.0; for a 10:1 rectangle it's ~1.74.
    shape_factor = 1.0
    geom = feature.get('geometry')
    if geom and geom.get('type') == 'Polygon':
        outer_ring = (geom.get('coordinates') or [None])[0]
        if outer_ring and len(outer_ring) >= 4:
            geo_area = _ring_area_unsigned(outer_ring)
            geo_perim = _ring_perimeter(outer_ring)
            if geo_area > 0:
                square_perim = 4 * math.sqrt(geo_area)
                shape_factor = geo_perim / square_perim

    side_length = footprint_area ** 0.5
    perimeter = 4 * side_length * shape_factor
    exterior_wall_area = perimeter * floor_count * floor_height
    window_area = int(window_to_wall_ratio * exterior_wall_area)

    # https://github.com/urbanopt/urbanopt-geojson-gem/blob/master/lib/urbanopt/geojson/schema/building_properties.json
    new_properties.update({
        "name": building_name,
        "floor_area": int(floor_area),
        "footprint_area": footprint_area,
        "type": "Building",
        "building_type": building_type,
        "number_of_stories": floor_count,
        "number_of_occupants": number_of_occupants,
        "floor_height": floor_height,
        "window_to_wall_ratio": window_to_wall_ratio,
        "windows": [{
            "window_area": window_area,
            "window_type": get_param(asset_metadata, 'window_type', ctx),
        }],
        "heating_system_fuel_type":        get_param(asset_metadata, 'heating_system_fuel_type', ctx),
        "cooling_system_fuel_type":        get_param(asset_metadata, 'cooling_system_fuel_type', ctx),
        "service_water_heating_fuel_type": get_param(asset_metadata, 'service_water_heating_fuel_type', ctx),
        "constructions": {
            # Metadata stores just the insulation tier ("Standard",
            # "Insulated", "Super Insulated"); urbanopt's construction-set
            # lookup expects the surface-suffixed name, so append it here.
            "wall": {
                "material": f"{get_param(asset_metadata, 'wall_material', ctx)} Wall",
                "r_value":  get_param(asset_metadata, 'wall_r_value', ctx),
            },
            "roof": {
                "material": f"{get_param(asset_metadata, 'roof_material', ctx)} Roof",
                "r_value":  get_param(asset_metadata, 'roof_r_value', ctx),
            },
        },
    })

    # "Inferred" means "let urbanopt's template pick", so omit the field from
    # feature.json. urbanopt's building_properties.json enum doesn't include
    # "Inferred" and will reject the whole feature otherwise.
    sys_type = get_param(asset_metadata, 'system_type')
    if sys_type and sys_type != 'Inferred':
        new_properties["system_type"] = sys_type

    # Operating hours: only emit when both start and duration are set AND duration
    # isn't 24:00 (which collides with the implicit end-of-day Time(24:00:00) in
    # OpenStudio's ScheduleDay and trips a BOOST_ASSERT).
    wkdy_start = get_param(asset_metadata, 'weekday_start_time')
    wkdy_dur   = get_param(asset_metadata, 'weekday_duration')
    if wkdy_start and wkdy_dur and wkdy_dur != '24:00':
        new_properties['weekday_start_time'] = wkdy_start
        new_properties['weekday_duration']   = wkdy_dur
    wknd_start = get_param(asset_metadata, 'weekend_start_time')
    wknd_dur   = get_param(asset_metadata, 'weekend_duration')
    if wknd_start and wknd_dur and wknd_dur != '24:00':
        new_properties['weekend_start_time'] = wknd_start
        new_properties['weekend_duration']   = wknd_dur

    if building_id in building_year_list:
        new_properties["year_built"] = building_year_list[building_id]

    # PowerTwin.rb routes building_type in {Single-Family Detached,
    # Single-Family Attached, Multifamily} through BuildResidentialModel; every
    # other building_type goes through the commercial BAR/typical pipeline.
    # Emit the residential measure args for any asset whose effective
    # building_type is one of those three. urbanopt requires
    # number_of_bedrooms to be divisible by number_of_residential_units, so
    # round bedrooms-per-unit then multiply back.
    #
    # Dwelling-unit count per original subtype id (effective_id remapping is
    # already resolved upstream). Listed explicitly (rather than relying on a
    # default fallback) so a missing entry for a future residential subtype
    # surfaces as a visible review failure instead of silently defaulting to 1.
    if building_type in ('Single-Family Detached', 'Single-Family Attached', 'Multifamily'):
        FALLBACK_UNITS_BY_SUBTYPE = {
            1: 1,  # Single-Family Detached
            2: 1,  # Single-Family Attached
            3: 4,  # Multifamily (generic, typical mid-size)
            4: 1,  # Single-Family
            5: 3,  # Multifamily (2 to 4 units), midpoint
            6: 8,  # Multifamily (5 or more units), representative
        }
        meta_units = asset_metadata.get('number_of_units') or asset_metadata.get('units')
        try:
            units = max(1, int(meta_units))
        except (TypeError, ValueError):
            units = FALLBACK_UNITS_BY_SUBTYPE.get(asset_metadata.get('_subtype_id'), 1)
        bedrooms_per_unit = max(1, round(floor_area / SQFT_PER_BEDROOM / units))
        foundation = "slab" if building_type == "Multifamily" else "basement - conditioned"
        new_properties.update({
            "number_of_stories_above_ground": floor_count,
            "foundation_type": foundation,
            "attic_type": "attic - unvented",
            "number_of_residential_units": units,
            "number_of_bedrooms": bedrooms_per_unit * units,
        })

    # Remove useless properties
    new_properties.pop('height', None)
    new_properties.pop('base', None)
    new_properties.pop('floor_count', None)

    # Combine geometry and properties
    new_feature = {
        "type": "Feature",
        "geometry": feature['geometry'],
        "properties": new_properties
    }
    
    # Get weather data from building_weather_list
    if building_id in building_weather_list:
        state, weather_file = building_weather_list[building_id]
        
        # Check if weather data is valid
        if state is None or weather_file is None:
            logger.warning(f"Invalid weather data for building_id {building_id} (missing coordinates)")
            return None
            
        weather_filename = weather_file + '.epw'
    else:
        # Fallback if weather data not found
        logger.warning(f"No weather data found for building_id {building_id}")
        return None

    # Map state to emissions regions
    future_emissions_mapping = {
        'FL': 'FRCCc', 'MS': 'SRMVc', 'NE': 'MROWc', 'OR': 'NWPPc', 'CA': 'CAMXc',
        'VA': 'SRVCc', 'AR': 'SRMVc', 'TX': 'ERCTc', 'OH': 'RFCWc', 'UT': 'NWPPc',
        'MT': 'NWPPc', 'TN': 'SRTVc', 'ID': 'NWPPc', 'WI': 'MROEc', 'WV': 'RFCWc',
        'NC': 'SRVCc', 'LA': 'SRMVc', 'IL': 'SRMWc', 'OK': 'SPSOc', 'IA': 'MROWc',
        'WA': 'NWPPc', 'SD': 'MROWc', 'MN': 'MROWc', 'KY': 'SRTVc', 'MI': 'RFCMc',
        'KS': 'SPNOc', 'NJ': 'RFCEc', 'NY': 'NYSTc', 'IN': 'RFCWc', 'VT': 'NEWEc',
        'NM': 'AZNMc', 'WY': 'RMPAc', 'GA': 'SRSOc', 'MO': 'SRMWc', 'DC': 'RFCEc',
        'SC': 'SRVCc', 'PA': 'RFCEc', 'CO': 'RMPAc', 'AZ': 'AZNMc', 'ME': 'NEWEc',
        'AL': 'SRSOc', 'MD': 'RFCEc', 'NH': 'NEWEc', 'MA': 'NEWEc', 'ND': 'MROWc',
        'NV': 'NWPPc', 'CT': 'NEWEc', 'DE': 'RFCEc', 'RI': 'NEWEc',
        'AK': 'AKGDc', 'HI': 'HIc'
    }

    hourly_historical_mapping = {
        'FL': 'Florida', 'MS': 'Midwest', 'NE': 'Midwest', 'OR': 'Northwest', 'CA': 'California',
        'VA': 'Carolinas', 'AR': 'Midwest', 'TX': 'Texas', 'OH': 'Midwest', 'UT': 'Northwest',
        'MT': 'Northwest', 'TN': 'Tennessee', 'ID': 'Northwest', 'WI': 'Midwest', 'WV': 'Midwest',
        'NC': 'Carolinas', 'LA': 'Midwest', 'IL': 'Midwest', 'OK': 'Central', 'IA': 'Midwest',
        'WA': 'Northwest', 'SD': 'Midwest', 'MN': 'Midwest', 'KY': 'Tennessee', 'MI': 'Midwest',
        'KS': 'Central', 'NJ': 'Mid-Atlantic', 'NY': 'New York', 'IN': 'Midwest', 'VT': 'New England',
        'NM': 'Southwest', 'WY': 'Rocky Mountains', 'GA': 'Southeast', 'MO': 'Midwest', 'DC': 'Mid-Atlantic',
        'SC': 'Carolinas', 'PA': 'Mid-Atlantic', 'CO': 'Rocky Mountains', 'AZ': 'Southwest', 'ME': 'New England',
        'AL': 'Southeast', 'MD': 'Mid-Atlantic', 'NH': 'New England', 'MA': 'New England', 'ND': 'Midwest',
        'NV': 'Northwest', 'CT': 'New England', 'DE': 'Mid-Atlantic', 'RI': 'New England',
        'AK': 'Northwest', 'HI': 'Southwest'
    }

    annual_historical_mapping = {
        'FL': 'FRCC', 'MS': 'SRMV', 'NE': 'MROW', 'OR': 'NWPP', 'CA': 'CAMX',
        'VA': 'SRVC', 'AR': 'SRMV', 'TX': 'ERCT', 'OH': 'RFCW', 'UT': 'NWPP',
        'MT': 'NWPP', 'TN': 'SRTV', 'ID': 'NWPP', 'WI': 'MROE', 'WV': 'RFCW',
        'NC': 'SRVC', 'LA': 'SRMV', 'IL': 'SRMW', 'OK': 'SPSO', 'IA': 'MROW',
        'WA': 'NWPP', 'SD': 'MROW', 'MN': 'MROW', 'KY': 'SRTV', 'MI': 'RFCM',
        'KS': 'SPNO', 'NJ': 'RFCE', 'NY': 'NYCW', 'IN': 'RFCW', 'VT': 'NEWE',
        'NM': 'AZNM', 'WY': 'RMPA', 'GA': 'SRSO', 'MO': 'SRMW', 'DC': 'RFCE',
        'SC': 'SRVC', 'PA': 'RFCE', 'CO': 'RMPA', 'AZ': 'AZNM', 'ME': 'NEWE',
        'AL': 'SRSO', 'MD': 'RFCE', 'NH': 'NEWE', 'MA': 'NEWE', 'ND': 'MROW',
        'NV': 'NWPP', 'CT': 'NEWE', 'DE': 'RFCE', 'RI': 'NEWE',
        'AK': 'AKGD', 'HI': 'HIMS'
    }
    
    # Fallback state-level climate zones (used when FCC county-level lookup is unavailable)
    climate_zone_fallback = {
        'AL': '3A', 'AK': '7A', 'AZ': '2B', 'AR': '3A', 'CA': '3B',
        'CO': '5B', 'CT': '5A', 'DE': '4A', 'FL': '2A', 'GA': '3A',
        'HI': '1A', 'ID': '5B', 'IL': '5A', 'IN': '5A', 'IA': '6A',
        'KS': '4A', 'KY': '4A', 'LA': '2A', 'ME': '6A', 'MD': '4A',
        'MA': '5A', 'MI': '6A', 'MN': '6A', 'MS': '3A', 'MO': '4A',
        'MT': '6B', 'NE': '5A', 'NV': '3B', 'NH': '6A', 'NJ': '4A',
        'NM': '4B', 'NY': '5A', 'NC': '4A', 'ND': '7A', 'OH': '5A',
        'OK': '3A', 'OR': '4C', 'PA': '5A', 'RI': '5A', 'SC': '3A',
        'SD': '6A', 'TN': '4A', 'TX': '2A', 'UT': '5B', 'VT': '6A',
        'VA': '4A', 'WA': '4C', 'WV': '5A', 'WI': '6A', 'WY': '6B',
        'DC': '4A'
    }

    future_subregion = future_emissions_mapping.get(state)
    hourly_subregion = hourly_historical_mapping.get(state)
    annual_subregion = annual_historical_mapping.get(state)

    # Use county-level climate zone if available, fall back to state-level
    climate_zone = building_climate_zone_list.get(building_id)
    if climate_zone is None:
        climate_zone = climate_zone_fallback.get(state)
    
    # Create the final JSON structure
    final_json = {
        "type": "FeatureCollection",
        "mappers": [],
        "project": {
            "id": f"{building_id}",
            "name": f"{building_name}",
            "description": f"Feature file for building with asset id:{asset_id} and id: {building_id}",
            "begin_date": f"{os.environ.get('URBANOPT_SIMULATION_YEAR', '2025')}-01-01T00:00:00.000Z",
            "end_date": f"{os.environ.get('URBANOPT_SIMULATION_YEAR', '2025')}-12-31T23:00:00.000Z",
            "default_template": "90.1-2013",
            "cec_climate_zone": None,
            "import_surrounding_buildings_as_shading": None,
            "surface_elevation": None,
            "tariff_filename": None,
            "timesteps_per_hour": 1,
            "emissions": True,
            "climate_zone": climate_zone,
            "weather_filename": weather_filename,
            "electricity_emissions_future_subregion": future_subregion,
            "electricity_emissions_hourly_historical_subregion": hourly_subregion,
            "electricity_emissions_annual_historical_subregion": annual_subregion,
            "electricity_emissions_future_year": "2026",
            "electricity_emissions_hourly_historical_year": "2019",
            "electricity_emissions_annual_historical_year": "2019"
        },
        "scenarios": [
            {
                "feature_mappings": [],
                "id": f"{building_id}",
                "name": f"{building_name} Scenario"
            }
        ],
        "features": [new_feature]
    }
    

    return final_json, building_id, building_name

############################################################################################################
# Name: create_single_featurefile()
# Description: This function creates a single feature file for the specified asset ID.
############################################################################################################
def create_bulk_featurefiles(failed_asset_ids, SIMULATION_DIR, LOCAL_RECOVERY_DIR, simulation_name):
    """Efficiently create feature files for multiple failed assets by reading data files once."""
    if not failed_asset_ids:
        logger.info("No failed assets to process")
        return True
    
    logger.info(f"Creating feature files for {len(failed_asset_ids)} failed assets...")
    
    FEATURE_FILES_DIR = os.path.join(SIMULATION_DIR, 'feature_files')
    os.makedirs(FEATURE_FILES_DIR, exist_ok=True)
    
    METADATA_CSV = os.path.join(LOCAL_RECOVERY_DIR, f'{simulation_name}_metadata.csv')
    ASSET_GEOJSON = os.path.join(LOCAL_RECOVERY_DIR, f'{simulation_name}_asset.geojson')

    # Read files once instead of for each asset
    try:
        logger.debug("Reading metadata and geojson files...")
        building_area_list, building_type_list, building_name_list, building_weather_list, building_climate_zone_list, building_year_list, building_metadata_list = read_metadata(METADATA_CSV)

        with open(ASSET_GEOJSON, 'r') as geojson_file:
            geojson_data = json.load(geojson_file)

    except Exception as e:
        logger.error(f"Error reading data files: {e}")
        return False

    # Convert failed asset IDs to a set for O(1) lookups
    failed_assets_set = set(failed_asset_ids)
    processed_count = 0

    # Process only features for failed assets
    for feature in geojson_data['features']:
        properties = feature.get('properties', {})
        building_id = int(properties.get('id'))

        # Process feature only if it's in our failed assets list
        if building_id in failed_assets_set:
            result = process_feature(feature, building_area_list, building_type_list,
                                  building_name_list, building_weather_list, building_climate_zone_list, building_year_list, building_metadata_list)
            if result:
                final_json, _, building_name = result
                new_building_name = sanitize_filename(building_name)
                feature_file_path = os.path.join(FEATURE_FILES_DIR, f'{building_id}_{new_building_name}.json')

                try:
                    with open(feature_file_path, 'w') as feature_file:
                        json.dump(final_json, feature_file, indent=4)
                    processed_count += 1
                    logger.debug(f"Feature file updated for failed asset_id: {building_id}")
                except Exception as e:
                    logger.error(f"Error writing feature file for asset {building_id}: {e}")
            else:
                logger.warning(f"Could not process feature for failed asset {building_id}")
    
    logger.info(f"Successfully processed {processed_count}/{len(failed_asset_ids)} failed assets")
    return processed_count > 0


def create_single_featurefile(asset_id, SIMULATION_DIR, LOCAL_RECOVERY_DIR, simulation_name):
    """Create a single feature file for the specified asset ID. Consider using create_bulk_featurefiles for better performance."""
    FEATURE_FILES_DIR = os.path.join(SIMULATION_DIR, 'feature_files')
    os.makedirs(FEATURE_FILES_DIR, exist_ok=True)
    
    METADATA_CSV = os.path.join(LOCAL_RECOVERY_DIR, f'{simulation_name}_metadata.csv')
    ASSET_GEOJSON = os.path.join(LOCAL_RECOVERY_DIR, f'{simulation_name}_asset.geojson')

    # Metadata requires the area, subtype and name of the building to be present from the metadata
    building_area_list, building_type_list, building_name_list, building_weather_list, building_climate_zone_list, building_year_list, building_metadata_list = read_metadata(METADATA_CSV)
    with open(ASSET_GEOJSON, 'r') as geojson_file:
        geojson_data = json.load(geojson_file)


    # Process each feature in the GeoJSON data
    for feature in geojson_data['features']:
        # Extract building_id from feature properties
        properties = feature.get('properties', {})
        building_id = int(properties.get('id'))

        # Process feature only if it matches the asset_id
        if building_id == int(asset_id):
            result = process_feature(feature, building_area_list, building_type_list,
                                  building_name_list, building_weather_list, building_climate_zone_list, building_year_list, building_metadata_list)
            if result:
                final_json, _, building_name = result
                new_building_name = sanitize_filename(building_name)
                feature_file_path = os.path.join(FEATURE_FILES_DIR, f'{asset_id}_{new_building_name}.json')
                with open(feature_file_path, 'w') as feature_file:
                    json.dump(final_json, feature_file, indent=4)
                logger.debug(f"Feature file created for asset_id: {asset_id}")
                return True
    
    logger.debug(f"No matching feature found for asset_id: {asset_id}")
    return False

############################################################################################################
# Name: create_featurefiles()
# Description: This function reads the JSON data from the input file and the area data from the
#   metadata file. It processes each feature and creates a new feature structure with additional properties.
#   It writes the new feature structure to individual feature files in the output directory.
############################################################################################################
def create_featurefiles(SIMULATION_DIR, LOCAL_DIR, asset_geojson, metadata_csv, num_cores, simulation_name):
    logger.info("Creating feature files...")


    FEATURE_FILES_DIR = os.path.join(SIMULATION_DIR, 'feature_files')
    LOCAL_FEATURE_FILES_DIR = os.path.join(LOCAL_DIR, 'feature_files')
    os.makedirs(FEATURE_FILES_DIR, exist_ok=True)

    # Metadata requires the area, subtype and name of the building to be present from the metadata
    building_area_list, building_type_list, building_name_list, building_weather_list, building_climate_zone_list, building_year_list, building_metadata_list = read_metadata(metadata_csv)

    with open(asset_geojson, 'r') as file:
        geojson_data = json.load(file)


    # Process each feature in the GeoJSON data
    logger.info("Processing features...")
    total_features = len(geojson_data['features'])
    created = 0
    skipped_features = 0
    for feature in geojson_data['features']:
        result = process_feature(feature, building_area_list, building_type_list, building_name_list, building_weather_list, building_climate_zone_list, building_year_list, building_metadata_list)
        # If the result is not None, write the feature file
        if result:
            final_json, building_id, building_name = result
            new_building_name = sanitize_filename(building_name)
            feature_file_path = os.path.join(FEATURE_FILES_DIR, f'{building_id}_{new_building_name}.json')
            with open(feature_file_path, 'w') as feature_file:
                json.dump(final_json, feature_file, indent=4)
            created += 1
        else:
            skipped_features += 1

    logger.info(f"Feature files: {created} created from {total_features} GeoJSON features "
                f"({skipped_features} skipped due to missing metadata or weather)")
    # Run the asset analysis to organize the assets to their batch
    asset_analysis(SIMULATION_DIR, num_cores, simulation_name)

    logger.debug("Zipping the output directory...")
    shutil.make_archive(LOCAL_FEATURE_FILES_DIR, 'zip', FEATURE_FILES_DIR)
    zip_file_path = shutil.make_archive(FEATURE_FILES_DIR, 'zip', FEATURE_FILES_DIR)

    logger.debug("Removing the unzipped directory...")
    shutil.rmtree(FEATURE_FILES_DIR)

    logger.info(f"Zip file created at: {zip_file_path}")

############################################################################################################
# Name: main()
# Description: This function is the entry point for the script. Used for testing purposes.
############################################################################################################
if __name__ == "__main__":
    asset_geojson = 'powertwin-solver-pg/uploaded_files/asset.geojson'
    metadata_csv = 'powertwin-solver-pg/uploaded_files/metadata.csv'
    SIMULATION_DIR = 'powertwin-solver-pg/uploaded_files'
    LOCAL_DIR = 'powertwin-solver-pg/user_files'
    num_cores = 1
    simulation_name = 'simulation'

    create_featurefiles(SIMULATION_DIR, LOCAL_DIR, asset_geojson, metadata_csv, num_cores, simulation_name)