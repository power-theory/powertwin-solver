############################################################################################################
# getfeaturefile.py
# This script reads the JSON data from the input file and the area data from the
#   metadata file. It processes each feature and creates a new feature structure with additional properties.
#   It writes the new feature structure to individual feature files in the output directory.
############################################################################################################

import copy
import csv
import json
import math
import os
import shutil
import re

from modules.diagnostics import asset_analysis
from modules.utils import initialize_logger
from modules.simulation.sim_params_spec import CONFIRMABLE_FIELDS, RESIDENTIAL_BUILDING_TYPES, get_param, OCCUPANTS_MAPPING, build_asset_ctx, SQFT_PER_BEDROOM, RESOLVER_VERSION, is_dynamic_defaults_enabled, is_vacant, _load_ref, ENUM_VALUES, NUMERIC_RANGES, TIME_FIELDS

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

# Programmable params whose explicit presence the feature file reports (see explicit_params).
_EXPLICIT_PARAM_KEYS = tuple(sorted(set(ENUM_VALUES) | set(NUMERIC_RANGES) | set(TIME_FIELDS)))


def _coerce_like(raw, resolved):
    """Compare a raw metadata value with what get_param returned for it: numbers as floats,
    everything else as stripped strings, so '19.25' == 19.25 and 'heat_pump' == 'heat_pump'."""
    try:
        return float(raw) if isinstance(resolved, (int, float)) and not isinstance(resolved, bool) else str(raw).strip()
    except (TypeError, ValueError):
        return str(raw).strip()

# Dwelling-unit count per original subtype id (effective_id remapping resolved
# upstream). Used as the per-unit denominator for BOTH the occupants resolver
# (via ctx['units']) and the HPXML number_of_residential_units block, so the two
# must agree -- a mismatch multiplies per-unit occupants by the wrong unit count.
FALLBACK_UNITS_BY_SUBTYPE = {
    1: 1,  # Single-Family Detached
    2: 1,  # Single-Family Attached
    3: 4,  # Multifamily (generic, typical mid-size)
    4: 1,  # Single-Family
    5: 3,  # Multifamily (2 to 4 units), midpoint
    6: 8,  # Multifamily (5 or more units), representative
}


def _residential_unit_count(asset_metadata):
    """Effective dwelling-unit count: explicit metadata first, else the
    subtype representative fallback. Single source of truth so the occupants
    resolver and the HPXML unit count never diverge."""
    # Mirror build_asset_ctx's alias chain (sim_params_spec.py) so the occupants
    # resolver (ctx['units']) and this unit count never diverge -- it accepts a
    # third alias, number_of_residential_units.
    meta_units = (asset_metadata.get('number_of_units')
                  or asset_metadata.get('units')
                  or asset_metadata.get('number_of_residential_units'))
    try:
        return max(1, int(meta_units))
    except (TypeError, ValueError):
        return FALLBACK_UNITS_BY_SUBTYPE.get(asset_metadata.get('_subtype_id'), 1)

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
        # One row per building with a `sensors` map (flow's shape since 2026-09-17). Refuse the
        # legacy per-sensor shape here, before any weather lookup or uo run: clean_report was the
        # only guard and it fired after each asset's full simulation (review 2026-09-20).
        if 'sensors' not in (reader.fieldnames or []):
            raise ValueError(
                f"{metadata_csv} has no `sensors` column: legacy per-sensor metadata file; "
                f"convert it with powertwin-flow tools/compact_metadata.py")

        # Read each row in the CSV file to assign building data to its corresponding building ID
        for row in reader:
            total_rows += 1
            asset_name = row['asset_name']
            asset_subtype_id = row.get('asset_subtype_id', '')
            # Enforce a lowercase key contract at ingestion so downstream lookups
            # never have to check case variants ('state'/'State', etc.).
            asset_geometries_properties = {str(k).lower(): v for k, v in json.loads(row['asset_geometries_properties']).items()}
            asset_metadata = {str(k).lower(): v for k, v in json.loads(row['asset_metadata']).items()}

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

            raw_year = asset_metadata.get('year_built')
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

    # Metadata-quality signal: these fields are NOT required to simulate, but when
    # absent the resolver silently falls back to flat national defaults (biased),
    # so surface the gap loudly rather than let it pass unseen. National-readiness
    # keeps the building via fallbacks, so this warns -- it does not drop anything.
    no_geo_key = sum(1 for md in building_metadata_list.values()
                     if not (md.get('state') or md.get('county_fips')))
    no_vintage = sum(1 for md in building_metadata_list.values()
                     if not md.get('year_built'))
    if no_geo_key:
        logger.warning(f"Metadata quality: {no_geo_key}/{accepted} accepted buildings have neither 'state' nor "
                       f"'county_fips' -- their fuel/envelope/system defaults fall back to flat national averages "
                       f"(biased, e.g. ~100% natural-gas heating) unless a lat/lon county lookup succeeds.")
    if no_vintage:
        logger.warning(f"Metadata quality: {no_vintage}/{accepted} accepted buildings missing year_built -- "
                       f"vintage-specific envelope/system defaults fall back to the all-vintage marginal.")

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


SQFT_TO_M2 = 0.09290304  # exact; NIST SP 811 Appendix B.8
WGS84_A_M = 6378137.0  # WGS 84 semi-major axis (NGA.STND.0036)
WGS84_INV_F = 298.257223563  # WGS 84 inverse flattening (NGA.STND.0036)
_WGS84_E2 = (1 / WGS84_INV_F) * (2 - 1 / WGS84_INV_F)
# urbanopt-geojson 1.0.1 helper.rb floor_print_from_polygon: with zoning, a vertex within this distance of an
# earlier one is merged into it (OpenStudio.getCombinedPoint(point, all_points, 1.0)); then removeCollinear and
# getOutwardNormal, and a floor print that is not a polygon fails the building ("Failed to create building spaces")
ZONING_MERGE_TOL_M = 1.0
# A footprint within this relative area of its target is simulated as mapped. The stock's metadata area and the
# outline measured as OpenStudio projects it differ only by measurement: Teton within 0.12%, a second stock systematically higher
# (a systematic projection offset; p99.5 0.68%), natv3 within 0.5% bar the rows whose area came from another
# source (2026-09-28). Below 1% the mapped outline, and the stock results, stay byte-identical.
AREA_FIT_TOL = 0.01
RESIDENTIAL_TYPES = RESIDENTIAL_BUILDING_TYPES  # the HPXML workflow's types (one list: sim_params_spec)
UNSIMULATABLE_TYPES = frozenset({'Uncovered Parking', 'Covered Parking'})


def _floor_count_of(asset_metadata, properties):
    """The floor count process_feature simulates: metadata first, then the feature, whole floors, at least 1.
    floor_count may arrive as int, float, or string (incl. malformed like 'abc' or '3.5'); a bad value must not
    crash feature generation, and a sub-1 value would later divide footprint_area by zero."""
    fc = asset_metadata.get('floor_count') or properties.get('floor_count')
    try:
        fc = int(float(fc))
    except (ValueError, TypeError, OverflowError):
        # OverflowError: int(float('inf')). One non-finite floor_count in a national collection must not kill
        # the whole feature-generation job.
        fc = 1
    return max(1, fc)


def _ring_center(ring):
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def _metres_per_degree(lat):
    """(east, north) metres per degree on the WGS 84 ellipsoid at latitude lat (prime vertical and meridian radii),
    the local scale OpenStudio's toLocalCartesian applies."""
    s = math.sin(math.radians(lat))
    w = 1 - _WGS84_E2 * s * s
    n = WGS84_A_M / math.sqrt(w)
    m = WGS84_A_M * (1 - _WGS84_E2) / w ** 1.5
    return math.radians(1) * n * math.cos(math.radians(lat)), math.radians(1) * m


def _local_xy(ring, lon0, lat0):
    kx, ky = _metres_per_degree(lat0)
    return [((p[0] - lon0) * kx, (p[1] - lat0) * ky) for p in ring]


def _outer_ring(geom):
    """The outer ring the zoning measure builds a commercial floor from: flatten_geometry's part choice, holes
    ignored (the measure does not build them)."""
    if not geom or 'coordinates' not in geom or not geom['coordinates']:
        return None
    if geom.get('type') == 'MultiPolygon':
        geom = copy.deepcopy(geom)
        flatten_geometry(geom)
    return geom['coordinates'][0] if geom.get('type') == 'Polygon' else None


def outer_area_m2(geom):
    """Area in m2 of the outer ring URBANopt builds a commercial floor from, about the ring's vertex mean."""
    ring = _outer_ring(geom)
    if not ring or len(ring) < 3:
        return 0.0
    return _ring_area_unsigned(_local_xy(ring, *_ring_center(ring)))


def _floor_print_area_m2(ring):
    """Area left after the zoning measure's vertex merge (0 when fewer than 3 points or all collinear)."""
    kept = []
    for x, y in _local_xy(ring[:-1] if ring[0] == ring[-1] else ring, *_ring_center(ring)):
        hit = next(((a, b) for a, b in kept if math.hypot(x - a, y - b) <= ZONING_MERGE_TOL_M), None)
        kept.append(hit or (x, y))
    pts = [p for i, p in enumerate(kept) if p != kept[i - 1]]
    return _ring_area_unsigned(pts) if len(set(pts)) >= 3 else 0.0


def scale_polygon(geom, k):
    """Scale every ring of a lon/lat Polygon in place by k about the outer ring's vertex mean (area x k^2)."""
    lon0, lat0 = _ring_center(geom['coordinates'][0])
    geom['coordinates'] = [[[lon0 + k * (p[0] - lon0), lat0 + k * (p[1] - lat0), *p[2:]] for p in r]
                           for r in geom['coordinates']]


def _square_polygon(geom, area_m2):
    """Axis-aligned square of area_m2 centred on the outer ring's vertex mean (fallback when scaling collapses)."""
    lon0, lat0 = _ring_center(geom['coordinates'][0])
    kx, ky = _metres_per_degree(lat0)
    h = math.sqrt(area_m2) / 2
    dlon, dlat = h / kx, h / ky
    geom['coordinates'] = [[[lon0 - dlon, lat0 - dlat], [lon0 + dlon, lat0 - dlat], [lon0 + dlon, lat0 + dlat],
                            [lon0 - dlon, lat0 + dlat], [lon0 - dlon, lat0 - dlat]]]


def fit_footprint_to_area(geom, k, wing_target_m2, building_id):
    """Scale one (flattened) Polygon by its asset's factor k so the zoning measure builds wing_target_m2 per floor.
    Returns 'unchanged' (within AREA_FIT_TOL: simulated as mapped), 'scaled', 'square' (the scaled outline would
    not survive the measure's vertex merge; a square of the target area is used) or 'kept' (even that square would
    not; the mapped outline is simulated)."""
    if not geom or geom.get('type') != 'Polygon' or abs(k * k - 1.0) <= AREA_FIT_TOL:
        return 'unchanged'
    original = copy.deepcopy(geom['coordinates'])
    scale_polygon(geom, k)
    if _floor_print_area_m2(geom['coordinates'][0]) > 0:
        return 'scaled'
    if math.sqrt(wing_target_m2) >= 2 * ZONING_MERGE_TOL_M:
        _square_polygon(geom, wing_target_m2)
        logger.warning(f"Building {building_id}: scaled footprint does not survive the zoning vertex merge; "
                       f"simulated as a {wing_target_m2:.1f} m2 square")
        return 'square'
    geom['coordinates'] = original
    logger.warning(f"Building {building_id}: footprint of {wing_target_m2:.2f} m2 is below the zoning measure's "
                   f"resolution; the mapped outline is simulated instead")
    return 'kept'


def annotate_asset_footprints(features, area_list, type_list, name_list, weather_list, metadata_list):
    """Pre-pass for the commercial fit. Every footprint row of a multi-wing asset repeats the whole asset's area, so
    the factor that makes the asset build its area is taken over the footprints that will actually be fitted:
    commercial rows process_feature keeps (not residential, not parking, with a type, name and weather). Stashes on
    each such row its outer footprint and its asset's total simulated floor area (footprint x floors summed)."""
    rows, pool = {}, {}
    for f in features:
        props = f.get('properties') or {}
        bid = str(props.get('id'))
        if bid not in area_list or bid not in type_list or bid not in name_list:
            continue
        btype = type_list[bid]
        state, wfile = weather_list.get(bid, (None, None))
        if btype in RESIDENTIAL_TYPES or btype in UNSIMULATABLE_TYPES or not state or not wfile:
            continue
        asset = str(props.get('asset_id') or bid)
        fp = outer_area_m2(f.get('geometry'))
        rows[bid] = (asset, fp)
        pool[asset] = pool.get(asset, 0.0) + fp * _floor_count_of(metadata_list.get(bid, {}), props)
    for bid, (asset, fp) in rows.items():
        metadata_list[bid]['_footprint_m2'] = fp
        metadata_list[bid]['_asset_floor_area_m2'] = pool[asset]


def load_asset_geojson(path, area_list, type_list, name_list, weather_list, metadata_list):
    """Read an asset GeoJSON and run the commercial-fit pre-pass: every loader that feeds process_feature uses this."""
    with open(path, 'r') as fh:
        geojson_data = json.load(fh)
    annotate_asset_footprints(geojson_data['features'], area_list, type_list, name_list, weather_list, metadata_list)
    return geojson_data


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

    if building_type in UNSIMULATABLE_TYPES:
        logger.warning(f"Skipping {building_name} (building_id {building_id}): {building_type} has no building energy model")
        return None

    asset_metadata = building_metadata_list.get(building_id, {})

    floor_count = _floor_count_of(asset_metadata, properties)

    # Commercial geometry is rebuilt from this polygon x stories by urban_geometry_creation_zoning (PowerTwin.rb),
    # which discards create_bar's area-based bar, so the polygon is what sets the simulated floor area. Fit it to
    # the metadata area (owner 2026-09-28: the area a user types is the area simulated). One factor per asset over
    # its fitted footprints (every row of a multi-wing asset carries the whole asset's area); each footprint takes
    # its share of the area, weighted by footprint x floors, so its prototype pick and occupants match its geometry.
    if building_type not in RESIDENTIAL_TYPES:
        if '_asset_floor_area_m2' not in asset_metadata:
            if all(building_weather_list.get(building_id, (None, None))):  # weatherless rows are dropped below anyway
                logger.warning(f"Building {building_id}: commercial footprint not fitted to its area "
                               f"(load the GeoJSON with load_asset_geojson)")
        else:
            fp_m2, pool_m2 = asset_metadata['_footprint_m2'], asset_metadata['_asset_floor_area_m2']
            area_ft2 = float(asset_metadata['area'])
            if fp_m2 > 0 and pool_m2 > 0 and area_ft2 > 0:
                share = fp_m2 * floor_count / pool_m2
                if share < 1.0 - 1e-12:
                    floor_area = max(1, int(area_ft2 * share))
                fit_footprint_to_area(feature.get('geometry'), math.sqrt(area_ft2 * SQFT_TO_M2 / pool_m2),
                                      area_ft2 * SQFT_TO_M2 * share / floor_count, building_id)

    # Resolve the dwelling-unit count up front (residential only) so the
    # occupants resolver's per-unit denominator matches the HPXML
    # number_of_residential_units written below -- otherwise an MF building
    # without explicit number_of_units gets whole-building occupants multiplied
    # by the subtype fallback unit count (e.g. 11 occ/unit x 8 units = 88).
    if building_type in RESIDENTIAL_TYPES:
        residential_units = _residential_unit_count(asset_metadata)
    else:
        residential_units = None

    # Build the asset context once; every get_param call below uses it to
    # resolve dynamic defaults from the national-stock lookup tables
    # (reference_data/) before falling back to SIM_PARAM_DEFAULTS.
    climate_zone = building_climate_zone_list.get(building_id)
    ctx_metadata = {**asset_metadata, 'area': floor_area, 'floor_count': floor_count}
    if residential_units is not None:
        ctx_metadata['number_of_units'] = residential_units
    ctx = build_asset_ctx(ctx_metadata,
                          building_type=building_type, climate_zone=climate_zone,
                          building_id=building_id)
    occupancy_subtype = BUILDING_TYPE_TO_OCCUPANCY.get(building_type, "Unknown")
    # Track explicitness separately: an explicit metadata count is the highest precedence tier and
    # must survive the stochastic vacancy draw below (Q/A R-007) -- the same protection explicit
    # `vacant` already had.
    # Explicit only if the value actually parses: an unusable count ('abc') must not
    # suppress the vacancy draw on the strength of key presence alone (code-review).
    _explicit_occ_raw = asset_metadata.get('number_of_occupants')
    try:
        occupants_explicit = _explicit_occ_raw is not None and str(_explicit_occ_raw).strip() != '' \
            and int(float(str(_explicit_occ_raw))) >= 0
    except (TypeError, ValueError):
        occupants_explicit = False
    occ_override = get_param(asset_metadata, 'number_of_occupants', ctx)
    try:
        number_of_occupants = int(occ_override)
    except (TypeError, ValueError):
        # Unresolved count. The flat OCCUPANTS_MAPPING constants are WHOLE-BUILDING archetype
        # numbers; delivering them raw was two defects (Q/A D-20 + L-018):
        #   - commercial: PowerTwin.rb feeds the value to set_people_per_floor_area target_total
        #     UNGATED, so a 300k ft2 hotel got pinned to 20 people whenever dynamic defaults were
        #     off. Omit instead (None -> key dropped below) so the measure's area-scaled
        #     occupancy default applies.
        #   - residential: the consumer is geometry_unit_num_occupants (PER UNIT), so divide the
        #     whole-building constant by the resolved unit count.
        if residential_units is not None:
            number_of_occupants = max(
                1, round(OCCUPANTS_MAPPING.get(occupancy_subtype, 1) / residential_units))
        else:
            number_of_occupants = None

    # Create new properties (must be first)
    new_properties = {
        'id': str(properties.pop('id')),
        'asset_id': str(properties.pop('asset_id'))
    }
    new_properties.update(properties)

    # Deliver the resolved occupant count to the feature JSON for EVERY building. PowerTwin.rb reads
    # res_props[:number_of_occupants] (residential geometry_unit_num_occupants / commercial
    # set_people_per_floor_area); previously it was written only in the vacant branch below, so
    # non-vacant buildings fell back to the HPXML measure's bedroom-based default instead of the
    # explicitly resolved count. Coincides with the resolver for SFD; MAY differ for a multifamily
    # unit -- unverified (the leaf slice's only MF rep is vacant). This is a delivery-robustness fix,
    # NOT a confirmed defect: the 800181 symptom that prompted it was vacancy (0 occupants is correct).
    if number_of_occupants is not None:
        new_properties["number_of_occupants"] = number_of_occupants

    # Programmable sim params: read from asset_metadata via get_param, which
    # checks dynamic (national-stock) defaults before the flat fallback. See
    # sim_params_spec.py / api/lib/simulationParamsSpec.js.
    floor_height = get_param(asset_metadata, 'floor_height', ctx)
    window_to_wall_ratio = get_param(asset_metadata, 'window_to_wall_ratio', ctx)

    # Guard against floor_count > floor_area (absurd metadata) producing a
    # zero footprint, which downstream geometry/area math can't use.
    footprint_area = max(1, int(floor_area / floor_count))
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
        # occupants delivered conditionally above/below; None means omit (Q/A D-20)
        "floor_height": floor_height,
        "window_to_wall_ratio": window_to_wall_ratio,
        "windows": [{
            "window_area": window_area,
            "window_type": get_param(asset_metadata, 'window_type', ctx),
        }],
        "heating_system_type":             get_param(asset_metadata, 'heating_system_type', ctx),
        "heating_system_fuel_type":        get_param(asset_metadata, 'heating_system_fuel_type', ctx),
        "cooling_system_fuel_type":        get_param(asset_metadata, 'cooling_system_fuel_type', ctx),
        "service_water_heating_fuel_type": get_param(asset_metadata, 'service_water_heating_fuel_type', ctx),
        "water_heater_type":              get_param(asset_metadata, 'water_heater_type', ctx),
        "constructions": {
            # Metadata stores the insulation tier ("Standard", "Insulated",
            # "Super Insulated"). The surface-suffixed material NAME is legacy:
            # PowerTwin.rb consumes only r_value (its own comment says the tier
            # label is no longer consumed); the string stays for readability.
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
    # "Inferred" and will reject the whole feature otherwise. CR WP-C1: ctx is
    # passed now, so the county ComStock system draw (mapped via
    # COMSTOCK_HVAC_KEY) fires for commercial buildings when metadata is silent.
    sys_type = get_param(asset_metadata, 'system_type', ctx)
    if sys_type and sys_type != 'Inferred':
        new_properties["system_type"] = sys_type

    # CR WP-C2: commercial thermostat deltas + airtightness (resolver returns
    # nothing for residential buildings; empty = omit, measures stay skipped).
    for f in ('com_heating_adjustment_f', 'com_cooling_adjustment_f',
              'com_airtightness_75pa'):
        v = get_param(asset_metadata, f, ctx)
        if v not in (None, ''):
            new_properties[f] = v

    # Operating hours: only emit when both start and duration are set AND duration
    # isn't 24:00 (which collides with the implicit end-of-day Time(24:00:00) in
    # OpenStudio's ScheduleDay and trips a BOOST_ASSERT).
    wkdy_start = get_param(asset_metadata, 'weekday_start_time', ctx)
    wkdy_dur   = get_param(asset_metadata, 'weekday_duration', ctx)
    if wkdy_start and wkdy_dur and wkdy_dur != '24:00':
        new_properties['weekday_start_time'] = wkdy_start
        new_properties['weekday_duration']   = wkdy_dur
    wknd_start = get_param(asset_metadata, 'weekend_start_time', ctx)
    wknd_dur   = get_param(asset_metadata, 'weekend_duration', ctx)
    if wknd_start and wknd_dur and wknd_dur != '24:00':
        new_properties['weekend_start_time'] = wknd_start
        new_properties['weekend_duration']   = wknd_dur

    new_properties["resolver_version"] = RESOLVER_VERSION
    new_properties["dynamic_defaults"] = is_dynamic_defaults_enabled()
    # Provenance for the mapper (asset 448523 review, 2026-09-21): which programmable params the
    # metadata supplied (and get_param accepted) versus resolved defaults, and whether this
    # feature is a scenario fan-out variant (carries stochastic_seed_id; provenance only since
    # 2026-09-24, a variant's explicit heat pump builds exactly like a user's). A heating_system_type
    # that only confirms the resolved default is not explicit (build_asset_ctx).
    confirmed = {f for f in CONFIRMABLE_FIELDS if ctx.get(f'{f}_confirms_default')}
    new_properties["explicit_params"] = sorted(
        k for k in _EXPLICIT_PARAM_KEYS
        if k not in confirmed and asset_metadata.get(k) not in (None, '')
        and get_param(asset_metadata, k, ctx) == _coerce_like(asset_metadata.get(k), get_param(asset_metadata, k, ctx)))
    new_properties["scenario_variant"] = asset_metadata.get('stochastic_seed_id') not in (None, '')
    if ctx.get('region'):
        new_properties["region"] = ctx['region']

    # Vacancy precedence (Q/A R-005/R-007/D-28):
    #   explicit vacant (bool-like)  >  explicit occupant count  >  'Vacant' subtype  >  ACS draw
    # The old `is True` identity check silently simulated a JSON string "true" as OCCUPIED and,
    # worse, suppressed the stochastic draw -- a stringly-typed override was LESS vacant than
    # omitting the key. Bool-like encodings are now honored; unrecognized values are logged and
    # fall through to the draw instead of silently deciding either way.
    # Tuples on purpose: 'in' against a SET hashes the operand, and metadata JSON can
    # legally carry unhashable values ([] / {}) which would raise TypeError before the
    # unrecognized-value warning could fire (code-review). Tuple membership uses ==.
    _TRUE_LIKE = (True, 'true', 'True', 'TRUE', 1, '1')
    _FALSE_LIKE = (False, 'false', 'False', 'FALSE', 0, '0', '')
    vacant_override = asset_metadata.get('vacant')
    if vacant_override in _TRUE_LIKE:
        vacant = True
    elif vacant_override in _FALSE_LIKE or (vacant_override is None and occupants_explicit):
        # explicit occupant count implies occupied unless vacant is explicitly true (the model
        # draw must not zero a user-stated count)
        vacant = False
    elif vacant_override is not None:
        logger.warning(
            f"Building {building_id}: unrecognized vacant value {vacant_override!r}; "
            f"falling back to the ACS vacancy model")
        vacant = is_vacant(ctx)
    elif building_type == 'Vacant':
        # subtype 7 is literally 'Vacant'; is_vacant() is residential-only so these never drew
        # vacant and simulated with archetype occupancy (Q/A D-28)
        vacant = True
    else:
        vacant = is_vacant(ctx)
    if vacant:
        new_properties["vacant"] = True
        number_of_occupants = 0
        new_properties["number_of_occupants"] = 0

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
    # Reuse the unit count resolved up front (residential_units) so the HPXML
    # number_of_residential_units matches the occupants resolver's per-unit
    # denominator exactly. See _residential_unit_count.
    if building_type in RESIDENTIAL_TYPES:
        units = residential_units
        # Use supplied whole-building bedroom count when present (divided by units),
        # mirroring _resolve_occupants -- only infer from area when absent, so the
        # HPXML bedroom count and the resolver's occupant count never diverge.
        supplied_bedrooms = asset_metadata.get('bedrooms') or asset_metadata.get('number_of_bedrooms')
        try:
            bedrooms_per_unit = max(1, round(int(supplied_bedrooms) / units))
        except (TypeError, ValueError):
            # CR WP-R5: county household donor (joint with the occupants the
            # resolver draws from the SAME donor) before the area/700 formula.
            county_br = get_param(asset_metadata, 'bedrooms_per_unit', ctx)
            try:
                bedrooms_per_unit = max(1, int(float(county_br)))
            except (TypeError, ValueError):
                bedrooms_per_unit = max(1, round(floor_area / SQFT_PER_BEDROOM / units))
        if building_type == "Multifamily":
            foundation = "slab"
        else:
            # CR WP-R5: county geometry donor first (realized local foundation mix;
            # supersedes the region-modal + CZ<=3-slab heuristics where a tier
            # serves - the county data already reflects its own climate). Legacy
            # region-modal path stands when the donor is absent.
            foundation = get_param(asset_metadata, 'foundation_type', ctx)
            if foundation in (None, ''):
                try:
                    foundation_ref = _load_ref('recs2020_envelope').get('foundation_type', {})
                except Exception:
                    foundation_ref = {}
                cz_num = None
                try:
                    cz_num = int(str(ctx.get('climate_zone', '') or '')[:1])
                except (ValueError, IndexError):
                    pass
                region = ctx.get('region')
                foundation = foundation_ref.get(region, 'slab')
                if cz_num and cz_num <= 3:
                    foundation = 'slab'
        # BuildResidentialHPXML rejects >6 above-grade floors outright (22 tall
        # structures classified residential crashed in one stock's gml arm); clamp with the
        # real floor count preserved in metadata for area/occupant math upstream.
        new_properties.update({
            "number_of_stories_above_ground": min(int(floor_count or 1), 6),
            "foundation_type": foundation,
            "attic_type": "attic - unvented",
            "number_of_residential_units": units,
            "number_of_bedrooms": bedrooms_per_unit * units,
        })
        # CR WP-R5: garage car count from the same geometry donor (0 = none);
        # the mapper sizes 12 ft per car and clamps to the unit footprint.
        g_cars = get_param(asset_metadata, 'garage_cars', ctx)
        if g_cars not in (None, ''):
            new_properties["garage_cars"] = int(float(g_cars))

        # CR WP-R2: heating equipment detail channel (native HPXML class + duct truth
        # from the same county donor; empty when the division fallback served).
        eq_detail = get_param(asset_metadata, 'heating_equipment_detail', ctx)
        if eq_detail not in (None, ''):
            new_properties["heating_equipment_detail"] = eq_detail
        hd_flag = get_param(asset_metadata, 'heating_has_ducts', ctx)
        if hd_flag not in (None, ''):
            new_properties["heating_has_ducts"] = hd_flag

        # CR WP-R1: county-resolved duct priors. Emitted only when resolved (empty
        # flat default = omit, so the dynamic-OFF arm keeps HPXML's own defaults).
        # The mapper applies them only to systems it builds as ducted.
        duct_loc = get_param(asset_metadata, 'duct_location', ctx)
        if duct_loc not in (None, ''):
            d_leak = get_param(asset_metadata, 'duct_leakage_pct', ctx)
            d_ins = get_param(asset_metadata, 'duct_insulation_r', ctx)
            # Emit the trio only when leakage ALSO resolved: an explicit metadata
            # location with unresolved leakage used to emit '' and build perfectly
            # sealed R-0 ducts via Ruby ''.to_f (review finding 7).
            if d_leak not in (None, ''):
                new_properties.update({
                    "duct_location": duct_loc,
                    "duct_leakage_pct": d_leak,
                    "duct_insulation_r": d_ins if d_ins not in (None, '') else 0.0,
                })

        # CR WP-R3: occupied-stock setpoints (joint donor) + envelope ACH50.
        # CR WP-R4: appliance fuels + misc gas loads (one gas-line-coherent donor).
        # Empty = omit; the mapper keeps its cited literals / HPXML defaults.
        for f in ('heating_setpoint_f', 'cooling_setpoint_f', 'infiltration_ach50',
                  'clothes_dryer_fuel', 'cooking_range_fuel',
                  'misc_gas_grill', 'misc_gas_lighting', 'misc_gas_fireplace',
                  'pv_system_kw', 'pv_azimuth_deg',
                  'cooling_type_detail', 'cooling_fraction_served'):
            v = get_param(asset_metadata, f, ctx)
            if v not in (None, ''):
                new_properties[f] = v

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
        
        # Check if weather data is valid. A missing weather FILE means we cannot simulate;
        # a missing state (after the metadata fallback in get_location) is pathological.
        # The old message claimed "missing coordinates" even when coordinates were present.
        if state is None or weather_file is None:
            logger.warning(f"Skipping building_id {building_id}: unresolved weather "
                           f"(state={state!r}, weather_file={weather_file!r})")
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
        # AK/HI deliberately absent: the schema's future-subregion enum is CONUS-only, so 'AKGDc'
        # and 'HIc' fail validation and kill the whole project section. Unmapped -> key omitted.
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
        'SD': '6A', 'TN': '4A',
        # Territories (Q/A R-029): absent entries sent these buildings into the mapper's empty
        # rescue, silently simulating with the osw default Buffalo 6A climate arguments while the
        # weather file WAS correctly assigned. All are IECC 1A tropical.
        'PR': '1A', 'VI': '1A', 'GU': '1A', 'AS': '1A', 'MP': '1A', 'TX': '2A', 'UT': '5B', 'VT': '6A',
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

    # The site_properties schema constrains each electricity_emissions_* subregion to an enum, and
    # the FUTURE enum is CONUS-only (eGRID future projections exclude AK/HI). Emitting a value
    # outside the enum fails validation for the WHOLE project section -- URBANopt then rejects the
    # feature file and the building never simulates -- so omit an unmapped subregion instead.
    # (`required: []`, so absent keys validate; the annual/hourly enums do cover AK/HI.)
    for _key in ("electricity_emissions_future_subregion",
                 "electricity_emissions_hourly_historical_subregion",
                 "electricity_emissions_annual_historical_subregion"):
        if final_json["project"].get(_key) is None:
            del final_json["project"][_key]

    # Same schema-gate problem for fuels: the building_properties fuel enum has no 'district steam'
    # (a real CBECS commercial fuel the resolver emits), and an invalid value rejects the whole
    # feature at `uo run` load -- before the mapper's fuel_map ('district steam' -> DistrictHeating)
    # could ever run. Rehome it to an unvalidated *_extended key (custom keys pass validation); the
    # mapper reads the extended key first and drives create_typical htg_src=DistrictHeating, which
    # EnergyPlus reports as DistrictHeating -> captured by the Steam sensor.
    _fprops = final_json["features"][0]["properties"]
    for _f in ("heating_system_fuel_type", "service_water_heating_fuel_type"):
        if str(_fprops.get(_f, "")).lower() == "district steam":
            _fprops[_f + "_extended"] = _fprops.pop(_f)

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

        geojson_data = load_asset_geojson(ASSET_GEOJSON, building_area_list, building_type_list, building_name_list,
                                          building_weather_list, building_metadata_list)

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
    geojson_data = load_asset_geojson(ASSET_GEOJSON, building_area_list, building_type_list, building_name_list,
                                      building_weather_list, building_metadata_list)


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
def _mark_unsimulatable_failed(weather_list, area_list, name_list, type_list, meta_list, simulation_name):
    """A building whose weather/coordinates couldn't be resolved CANNOT be simulated -- EnergyPlus
    needs a location. Such a building is excluded from the model (process_feature drops it), but it
    must NOT silently vanish: record each as a FAILED asset (status + failure_reason) so the drop is
    counted + attributable in failure_analysis, not dropped off the books. Returns the failed ids."""
    from modules.diagnostics.db import insert_asset, update_status
    failed = [bid for bid, (state, wfile) in weather_list.items() if not state or not wfile]
    if not failed:
        return failed
    reason = "missing coordinates (no lat/lon): cannot resolve a weather station; excluded from EnergyPlus"
    for bid in failed:
        meta = meta_list.get(bid) or {}
        insert_asset(bid, meta.get('state'), None, area_list.get(bid, 0),
                     int(float(meta.get('floor_count') or 1)), 0,
                     name_list.get(bid, str(bid)), type_list.get(bid, ''), simulation_name)
        update_status("Failed", asset_id=bid, simulation_name=simulation_name, failure_reason=reason)
    logger.warning(f"{len(failed)} asset(s) marked FAILED ({reason}); ids: {failed}")
    return failed


def create_featurefiles(SIMULATION_DIR, LOCAL_DIR, asset_geojson, metadata_csv, num_cores, simulation_name):
    logger.info("Creating feature files...")


    FEATURE_FILES_DIR = os.path.join(SIMULATION_DIR, 'feature_files')
    LOCAL_FEATURE_FILES_DIR = os.path.join(LOCAL_DIR, 'feature_files')
    os.makedirs(FEATURE_FILES_DIR, exist_ok=True)

    # Metadata requires the area, subtype and name of the building to be present from the metadata
    building_area_list, building_type_list, building_name_list, building_weather_list, building_climate_zone_list, building_year_list, building_metadata_list = read_metadata(metadata_csv)

    # Coordinates are REQUIRED -- EnergyPlus needs a location. Buildings whose weather couldn't be
    # resolved (no lat/lon) can't be simulated; mark them FAILED so the exclusion is visible in the
    # diagnostics rather than a silent drop. process_feature still excludes them from the model below.
    _mark_unsimulatable_failed(building_weather_list, building_area_list, building_name_list,
                               building_type_list, building_metadata_list, simulation_name)

    geojson_data = load_asset_geojson(asset_geojson, building_area_list, building_type_list, building_name_list,
                                      building_weather_list, building_metadata_list)


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