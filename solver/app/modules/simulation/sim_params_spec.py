"""Canonical defaults + enum table refs for programmable urbanopt sim params.

Mirror of powertwin-db/api/lib/simulationParamsSpec.js. Keep them in sync: if
you change a default or add a new field, update both files.

The ingest reads each programmable field from asset_metadata with these defaults
as the fallback. With dynamic defaults OFF, commercial assets still apply the
flat SIM_PARAM_DEFAULTS onto the DOE prototype (not the bare prototype);
residential assets keep the HPXML template defaults (the Ruby residential
override is gated on the dynamic_defaults flag).

Default resolution precedence (set by `get_param` with an asset ctx):
    1. Explicit metadata value, if non-empty -> use it (validated/clamped).
    2. Dynamic resolver from solver/upload/reference_data/ lookup tables -> use it.
    3. Flat SIM_PARAM_DEFAULTS[field] -> last-resort fallback.

The dynamic resolvers are sourced from authoritative national-stock surveys
(EIA RECS 2020 residential, EIA CBECS 2018 commercial, OpenStudio Standards
ASHRAE 90.1-2013 occupant densities, HPXML/Manual J residential occupants
formula). See solver/README.md "Default-value provenance" for the citations.
"""

import hashlib
import json
import logging
import math
import os
import re
import time
import urllib.request

log = logging.getLogger('Generate Feature Files')

# Bump when resolver logic or reference data changes.
# Stamped into feature.json so results are traceable to the model that produced them.
# push.sh syncs this to the README header automatically.
RESOLVER_VERSION = '1.6'

_REF_DATA_DIR = os.path.join(
    os.path.dirname(__file__), '..', '..', '..', 'upload', 'reference_data'
)
_REF_CACHE = {}
def _load_ref(name: str) -> dict:
    """Lazy-load a reference JSON. `name` is the filename without extension."""
    if name not in _REF_CACHE:
        with open(os.path.join(_REF_DATA_DIR, f'{name}.json'), 'r') as f:
            _REF_CACHE[name] = json.load(f)
    return _REF_CACHE[name]

# Master switch for the national-stock dynamic-default resolver. Read at call
# time (not module-import time) so a per-request override mutating
# os.environ['URBANOPT_DYNAMIC_DEFAULTS'] inside start_simulation() / a worker
# actually takes effect. When false (the conservative default), get_param
# skips resolve_default() entirely and falls back to the flat
# SIM_PARAM_DEFAULTS -- preserving the pre-dynamic behavior for existing
# assets. Mirror the same env var on the API side (simulationParamsSpec.js)
# so the frontend can surface "dynamic vs flat" default labels consistently.
def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')


def is_dynamic_defaults_enabled() -> bool:
    return _env_bool('URBANOPT_DYNAMIC_DEFAULTS', False)


def is_stochastic_sampling_enabled() -> bool:
    return _env_bool('URBANOPT_STOCHASTIC_SAMPLING', False)

# 24h HH:MM (00:00 to 23:59). 24:00 is rejected because it collides with
# OpenStudio's implicit end-of-day Time(24:00:00) and trips BOOST_ASSERT.
_TIME_RE = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')
TIME_FIELDS = ('weekday_start_time', 'weekday_duration',
               'weekend_start_time', 'weekend_duration')

SIM_PARAM_DEFAULTS = {
    # Enum-backed. These are the LAST-RESORT fallbacks — see resolve_default()
    # for the dynamic lookups that take precedence when the asset context
    # (state, year_built, building_type, area) is available.
    'system_type': 'Inferred',          # let urbanopt's template default fire
    'heating_system_type': 'furnace',                   # RECS 2020 national mode (60.2%)
    'heating_system_fuel_type': 'natural gas',          # RECS 2020 plurality
    'cooling_system_fuel_type': 'electricity',          # ~99% national
    'service_water_heating_fuel_type': 'natural gas',   # RECS 2020 plurality
    'water_heater_type': 'storage water heater',        # ResStock 2024.2 national mode (~94%)
    'window_type': 'Double Pane',                       # flat fallback; dynamic resolves by vintage
    'wall_material': 'Insulated',                       # post-1980 stock mode
    'roof_material': 'Insulated',                       # post-1980 stock mode

    # Numeric
    'window_to_wall_ratio': 0.20,    # CBECS 2018 commercial median
    'wall_r_value': 13.0,            # ResStock TRG post-1980 SFD avg / IECC CZ4 min
    'roof_r_value': 30.0,            # ResStock TRG post-1980 ceiling avg
    'floor_height': 9.0,             # ASHRAE 90.1 commercial baseline
    'number_of_occupants': '',

    # Time strings (empty means omit from feature.json so template default applies)
    'weekday_start_time': '',
    'weekday_duration': '',
    'weekend_start_time': '',
    'weekend_duration': '',
}

# Range bounds for numeric fields. Out-of-range values are clamped and a warning
# logged. Keep ranges in sync with the corresponding entries in the Node spec.
NUMERIC_RANGES = {
    'window_to_wall_ratio': (0.0, 0.8),
    'wall_r_value':         (1.0, 80.0),
    'roof_r_value':         (1.0, 80.0),
    'floor_height':         (6.0, 20.0),
    'number_of_occupants':  (0, 100000),
}

# Enum value lists for fail-fast validation at the API boundary. MUST stay in
# sync with the matching seed CSVs at db/data/records/simulations/. A startup
# self-test could fetch these from /api/types/simulation/sim-types and assert
# equality. For now the lists are duplicated and kept in sync by convention.
ENUM_VALUES = {
    # urbanopt's building_properties.json system_type enum (95 values) plus our
    # "Inferred" sentinel which the ingest treats as "omit from feature.json".
    'system_type': {
        'Inferred',
        'PTAC with baseboard electric', 'PTAC with baseboard gas boiler',
        'PTAC with baseboard district hot water', 'PTAC with gas unit heaters',
        'PTAC with electric coil', 'PTAC with gas coil', 'PTAC with gas boiler',
        'PTAC with no heat', 'PTAC with district hot water',
        'PTAC with central air source heat pump', 'PTHP',
        'PSZ-AC with gas coil', 'PSZ-AC with baseboard electric',
        'PSZ-AC with no heat', 'PSZ-AC with district hot water',
        'PSZ-AC with central air source heat pump', 'PSZ-HP',
        'Fan coil district chilled water with no heat',
        'Fan coil district chilled water with boiler',
        'Fan coil district chilled water with central air source heat pump',
        'Fan coil district chilled water with gas unit heaters',
        'Fan coil district chilled water with baseboard electric',
        'Fan coil district chilled water with district hot water',
        'Fan coil chiller with district hot water',
        'Fan coil air-cooled chiller with district hot water',
        'Fan coil chiller with boiler', 'Fan coil air-cooled chiller with boiler',
        'Fan coil chiller with central air source heat pump',
        'Fan coil air-cooled chiller with central air source heat pump',
        'Fan coil chiller with no heat',
        'DOAS with fan coil district chilled water with no heat',
        'DOAS with fan coil district chilled water and boiler',
        'DOAS with fan coil district chilled water with central air source heat pump',
        'DOAS with fan coil district chilled water with gas unit heaters',
        'DOAS with fan coil district chilled water with baseboard electric',
        'DOAS with fan coil district chilled water with district hot water',
        'DOAS with fan coil chiller with district hot water',
        'DOAS with fan coil air-cooled chiller with district hot water',
        'DOAS with fan coil air-cooled chiller with boiler',
        'DOAS with fan coil chiller with central air source heat pump',
        'DOAS with fan coil air-cooled chiller with central air source heat pump',
        'DOAS with fan coil chiller with no heat', 'DOAS with VRF', 'VRF',
        'DOAS with water source heat pumps with ground source heat pump',
        'Forced air furnace', 'Baseboard district hot water', 'Baseboard electric',
        'Baseboard gas boiler', 'Baseboard central air source heat pump',
        'Window AC with no heat', 'Window AC with forced air furnace',
        'Window AC with baseboard district hot water',
        'Window AC with baseboard electric', 'Window AC with unit heaters',
        'Window AC with baseboard gas boiler',
        'Window AC with baseboard central air source heat pump',
        'Direct evap coolers with baseboard district hot water',
        'Direct evap coolers with baseboard electric',
        'Direct evap coolers with baseboard gas boiler',
        'Direct evap coolers with baseboard central air source heat pump',
        'Direct evap coolers with no heat',
        'Direct evap coolers with gas unit heaters',
        'Direct evap coolers with forced air furnace', 'Gas unit heaters',
        'VAV chiller with gas boiler reheat', 'VAV chiller with gas coil reheat',
        'VAV chiller with central air source heat pump reheat',
        'VAV chiller with PFP boxes',
        'VAV air-cooled chiller with gas boiler reheat',
        'VAV air-cooled chiller with central air source heat pump reheat',
        'VAV air-cooled chiller with district hot water reheat',
        'VAV air-cooled chiller with gas coil reheat',
        'VAV air-cooled chiller with no reheat with gas unit heaters',
        'VAV district chilled water with gas boiler reheat',
        'VAV district chilled water with central air source heat pump reheat',
        'VAV district chilled water with no reheat with zone heat pump',
        'VAV chiller with no reheat with baseboard electric',
        'VAV air-cooled chiller with no reheat with zone heat pump',
        'VAV district chilled water with district hot water reheat',
        'VAV district chilled water with gas coil reheat',
        'PVAV with gas heat with electric reheat',
        'PVAV with central air source heat pump reheat', 'PVAV with PFP boxes',
        'Residential - electric resistance and no cooling',
        'Residential - electric resistance and central air conditioner',
        'Residential - electric resistance and room air conditioner',
        'Residential - electric resistance and evaporative cooler',
        'Residential - furnace and no cooling',
        'Residential - furnace and central air conditioner',
        'Residential - furnace and room air conditioner',
        'Residential - furnace and evaporative cooler',
        'Residential - boiler and no cooling',
        'Residential - boiler and central air conditioner',
        'Residential - boiler and room air conditioner',
        'Residential - boiler and evaporative cooler',
        'Residential - air-to-air heat pump', 'Residential - mini-split heat pump',
        'Residential - ground-to-air heat pump',
    },
    'heating_system_type':             {'heat_pump', 'furnace', 'boiler', 'electric_resistance', 'wood_stove'},
    'water_heater_type':               {'storage water heater', 'instantaneous water heater', 'heat pump water heater'},
    # urbanopt's heating_system_fuel_type enum (5 values). cooling_* and SWH_*
    # are NOT in urbanopt's schema so they bypass schema validation; we share
    # the same valid set for UI/spec consistency.
    'heating_system_fuel_type':        {'electricity', 'natural gas', 'fuel oil', 'propane', 'wood'},
    'cooling_system_fuel_type':        {'electricity', 'natural gas', 'fuel oil', 'propane', 'wood'},
    'service_water_heating_fuel_type': {'electricity', 'natural gas', 'fuel oil', 'propane', 'wood'},
    'window_type':                     {'Single Pane', 'Double Pane', 'Triple Pane'},
    # Both reference the same insulation tier list; solver appends " Wall"
    # or " Roof" when building the urbanopt feature.json (see generateFeatureFile.py).
    'wall_material':                   {'Standard', 'Insulated', 'Super Insulated'},
    'roof_material':                   {'Standard', 'Insulated', 'Super Insulated'},
}

# Values the dynamic resolver is ALLOWED to emit per field. This is the safety
# net get_param() enforces on resolver output (ENUM_VALUES only gates raw user
# metadata). It is a superset of ENUM_VALUES where the resolver intentionally
# produces extra values the downstream Ruby toolchain understands:
#   - 'district steam' -> DistrictHeating for commercial heating/SWH (CBECS)
#   - 'none' -> suppressed cooling in subarctic climate zones (residential)
# Anything outside this set is resolver/reference-data drift (e.g. an unmapped
# fuel like 'solar'); get_param logs it and falls back to SIM_PARAM_DEFAULTS so
# it can never silently reach the feature JSON and vanish in the Ruby fuel_map.
RESOLVER_ENUM_VALUES = {key: set(vals) for key, vals in ENUM_VALUES.items()}
RESOLVER_ENUM_VALUES['heating_system_fuel_type'] |= {'district steam'}
RESOLVER_ENUM_VALUES['service_water_heating_fuel_type'] |= {'district steam'}
RESOLVER_ENUM_VALUES['cooling_system_fuel_type'] |= {'none'}
# system_type is template-driven, never produced by the dynamic resolver; drop
# it so its 100-value enum doesn't gate anything.
RESOLVER_ENUM_VALUES.pop('system_type', None)


# Default number_of_occupants per occupancy_type, used as a flat last-resort
# fallback when the dynamic resolver (area * people_per_1000_ft^2, or
# bedrooms+1 for residential) can't resolve due to missing context. Values
# calibrated to per-archetype averages, not 100-unit-apartment-flavored
# blanket numbers. Mirrored in powertwin-db/api/lib/simulationParamsSpec.js.
OCCUPANTS_MAPPING = {
    'Educational':      355,    # ASHRAE 62.1 avg school size
    'Business':         100,    # ASHRAE 62.1 office at ~20,000 ft^2
    'SmallResidential': 3,      # RECS 2020 mean SFD household size
    'BigResidential':   20,     # avg 5-unit multifamily * (bedrooms+1)
    'Vacant':           1,      # defensive: commercial templates reject 0
    'Industrial':       50,     # CBECS 2018 mfg/lab avg
    'Storage':          5,      # CBECS Warehouse sparse
    'FoodMercantile':   50,     # ASHRAE 62.1 restaurant 100-seat
    'Institutional':    40,
    'Health Care':      60,     # ASHRAE 62.1 outpatient avg
    'Assembly':         200,    # ASHRAE 62.1 assembly room avg
    'Mercantile':       150,
    'Mixed':            100,    # routes Office-equivalent in urbanopt
    'Parking':          1,      # defensive
    'Unknown':          1,      # defensive
}


def validate_metadata(metadata):
    """Validate programmable sim params on an asset's metadata.

    Returns (ok, errors) where errors is a list of strings. ok is True iff
    errors is empty. Empty/missing values are accepted (will fall back to
    SIM_PARAM_DEFAULTS in get_param).
    """
    errors = []
    for key, valid in ENUM_VALUES.items():
        val = metadata.get(key)
        if val in (None, ''):
            continue
        if val not in valid:
            errors.append(f"invalid {key}={val!r} (must be one of: {sorted(valid)})")
    for key in TIME_FIELDS:
        val = metadata.get(key)
        if val in (None, ''):
            continue
        if not _TIME_RE.match(str(val)):
            errors.append(f"invalid {key}={val!r} (must be HH:MM, 00:00-23:59)")
    # Numeric range / type validation is handled by get_param at read time
    # (clamps + warns instead of rejecting).
    return (len(errors) == 0, errors)


RESIDENTIAL_BUILDING_TYPES = {
    'Single-Family Detached', 'Single-Family Attached', 'Multifamily',
}


def _vintage_bin(year_built) -> str | None:
    """Map a year_built (int or numeric string) into the RECS/CBECS vintage
    buckets. Returns None when year is missing/invalid."""
    try:
        y = int(year_built)
    except (TypeError, ValueError):
        return None
    if y < 1980:    return 'pre-1980'
    if y < 2000:    return '1980-1999'
    if y < 2010:    return '2000-2009'
    return '2010+'


def _decade_vintage_bin(year_built) -> str | None:
    """Map year_built to decade-level RECS vintage bins used by
    heating_system_type, water_heater_type, and conditioned fuel JSONs."""
    try:
        y = int(year_built)
    except (TypeError, ValueError):
        return None
    if y < 1950:    return 'pre-1950'
    if y < 1960:    return '1950-1959'
    if y < 1970:    return '1960-1969'
    if y < 1980:    return '1970-1979'
    if y < 1990:    return '1980-1989'
    if y < 2000:    return '1990-1999'
    if y < 2010:    return '2000-2009'
    if y < 2020:    return '2010-2019'
    return '2020+'


def _normalize_state(raw: str | None) -> str | None:
    """Normalize a state value from asset metadata to a 2-letter abbreviation.

    Accepts either a 2-letter code ('AZ') or a full state name ('Arizona',
    'arizona', 'NEW YORK'). Returns the upper-case abbreviation if the input
    matches a known state in census_regions.json, else None.

    Asset ingest sources differ: some store 'AZ', others store 'Arizona'.
    Without normalization the full-name variant misses the state_to_region
    lookup and the resolver loses every region/vintage-keyed field for that
    asset (silently falling back to flat defaults)."""
    if raw is None:
        return None
    value = str(raw).strip().upper()
    if not value:
        return None
    refs = _load_ref('census_regions')
    if value in refs['state_to_region']:
        return value
    return refs.get('state_name_to_abbr', {}).get(value)


def _coerce_int(val):
    """int(val) or None when missing/non-numeric."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


_FIPS_CACHE = {}

def _derive_county_fips(lat, lon) -> str | None:
    """Derive 5-digit county FIPS from lat/lon via FCC Census Block API.
    Results are cached per (rounded lat, lon) to minimize API calls."""
    if lat is None or lon is None:
        return None
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    key = (round(lat_f, 2), round(lon_f, 2))
    if key in _FIPS_CACHE:
        return _FIPS_CACHE[key]
    # This runs synchronously inside feature-file generation (under the sim
    # lock), so bound the worst-case block: 3 attempts x 4s timeout + short
    # backoff (~13s worst case) rather than retrying into the minutes. A miss
    # caches None and the building simply resolves without county-level fuel.
    url = (f"https://geo.fcc.gov/api/census/block/find?"
           f"latitude={lat_f}&longitude={lon_f}&censusYear=2020&format=json")
    attempts = 3
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=4) as resp:
                data = json.loads(resp.read())
            fips = data.get('County', {}).get('FIPS')
            _FIPS_CACHE[key] = fips
            return fips
        except Exception:
            if attempt < attempts - 1:
                time.sleep(0.5 * (attempt + 1))
    _FIPS_CACHE[key] = None
    return None


# 2-digit state FIPS -> USPS abbreviation (INCITS 38). The first two digits of a
# 5-digit county FIPS are the state code, so this lets build_asset_ctx recover a
# state (and thus census division/region) from county_fips alone.
_STATE_FIPS_TO_ABBR = {
    '01': 'AL', '02': 'AK', '04': 'AZ', '05': 'AR', '06': 'CA', '08': 'CO',
    '09': 'CT', '10': 'DE', '11': 'DC', '12': 'FL', '13': 'GA', '15': 'HI',
    '16': 'ID', '17': 'IL', '18': 'IN', '19': 'IA', '20': 'KS', '21': 'KY',
    '22': 'LA', '23': 'ME', '24': 'MD', '25': 'MA', '26': 'MI', '27': 'MN',
    '28': 'MS', '29': 'MO', '30': 'MT', '31': 'NE', '32': 'NV', '33': 'NH',
    '34': 'NJ', '35': 'NM', '36': 'NY', '37': 'NC', '38': 'ND', '39': 'OH',
    '40': 'OK', '41': 'OR', '42': 'PA', '44': 'RI', '45': 'SC', '46': 'SD',
    '47': 'TN', '48': 'TX', '49': 'UT', '50': 'VT', '51': 'VA', '53': 'WA',
    '54': 'WV', '55': 'WI', '56': 'WY',
    '60': 'AS', '66': 'GU', '69': 'MP', '72': 'PR', '78': 'VI',
}


def _normalize_county_fips(value) -> str | None:
    """Normalize county_fips to a 5-digit string, tolerating the two real
    representation variants: a float from a pandas NaN-upcast FIPS column
    (6037.0 -> '06037') and an int/short string with a dropped leading zero
    (6037 -> '06037'). Without this, str(6037.0)[:2] would be '60' -> American
    Samoa. A junk/out-of-range value needs no rejection here -- its prefix simply
    isn't in _STATE_FIPS_TO_ABBR, so state resolves to None (honest flat fallback)."""
    if not value:
        return None
    return str(value).split('.')[0].zfill(5)


def build_asset_ctx(metadata: dict, building_type: str | None = None,
                    climate_zone: str | None = None,
                    building_id: str | None = None) -> dict:
    """Distill the lookup-key context out of an asset's metadata dict. Centralized
    here so the resolvers all see the same shape: {building_type, region, division,
    vintage, state, area, bedrooms, floor_count, units, climate_zone, building_id,
    county_fips}. Missing keys are None; resolvers fall back accordingly."""
    # Enforce a lowercase key contract up front so lookups below never have to
    # check case variants ('state'/'State', etc.).
    metadata = {str(k).lower(): v for k, v in metadata.items()}

    county_fips = _normalize_county_fips(metadata.get('county_fips'))
    if not county_fips:
        county_fips = _derive_county_fips(
            metadata.get('latitude'), metadata.get('longitude'))

    state = _normalize_state(metadata.get('state'))
    # Footprint-sourced assets often carry county_fips + lat/lon but no state.
    # Recover the state from the county FIPS prefix (first two digits are the
    # state FIPS code) when it's absent -- otherwise division stays None and the
    # county-first heating-fuel reconciliation drops to the flat 'natural gas'
    # default, discarding the ACS county marginal already in hand.
    if not state and county_fips:
        state = _STATE_FIPS_TO_ABBR.get(county_fips[:2])
    refs = _load_ref('census_regions')
    region = refs['state_to_region'].get(state) if state else None
    division = refs.get('state_to_division', {}).get(state) if state else None
    bt = building_type if building_type is not None else metadata.get('building_type')
    cz = climate_zone if climate_zone is not None else metadata.get('climate_zone')
    bid = building_id if building_id is not None else metadata.get('building_id')
    return {
        'building_type': bt,
        'state': state,
        'region': region,
        'division': division,
        'vintage': _vintage_bin(metadata.get('year_built')),
        'decade_vintage': _decade_vintage_bin(metadata.get('year_built')),
        'area': metadata.get('area') or metadata.get('floor_area'),
        'bedrooms': metadata.get('bedrooms') or metadata.get('number_of_bedrooms'),
        'floor_count': _coerce_int(metadata.get('floor_count')),
        'units': _coerce_int(metadata.get('number_of_units') or metadata.get('units') or metadata.get('number_of_residential_units')),
        'climate_zone': cz,
        'building_id': bid,
        'county_fips': county_fips,
    }


# Fallback-level taxonomy returned alongside each dynamically-resolved value.
# Lets callers (e.g. the preview endpoint + frontend confidence indicators)
# distinguish "matched the exact (region, vintage) cell" from "fell back to
# the region-wide average" from "no region/vintage applied".
#   vintage_specific   - region + vintage both matched a stock-survey cell
#   region_all         - region matched, vintage missing / no cell; used the
#                        region's 'all' bucket
#   building_type_only - resolver uses a building-type-keyed table (commercial
#                        WWR, occupant density, cooling constant) or a
#                        context-derived formula (residential bedrooms+1).
#                        Independent of region + vintage.
#   flat_default       - resolver returned None; caller will use
#                        SIM_PARAM_DEFAULTS[field]. Emitted by the preview
#                        endpoint, not by the internal resolvers.
FALLBACK_LEVELS = ('county_acs', 'division_vintage', 'division_all', 'vintage_specific', 'region_all', 'building_type_only', 'flat_default')


def _doe_ref_name(building_type: str | None, ctx: dict | None = None) -> str | None:
    """Translate a PowerTwin asset_subtype name (CBECS survey vocabulary) to
    the closest DOE Reference Building prototype name used by NLR
    openstudio-standards lookup tables. Returns the input unchanged when it
    already matches a DOE prototype, or None for inputs with no sensible
    proxy (Parking, Vacant) so the resolver degrades to flat defaults.

    Lodging is story-aware: >3 stories maps to LargeHotel (matching Ruby's
    lookup_building_type split). Pass ctx to enable this; without it the
    static alias (SmallHotel) is returned.

    See solver/upload/reference_data/building_type_aliases.json for the map
    and the rationale per row."""
    if not building_type:
        return None
    aliases = _load_ref('building_type_aliases').get('powertwin_to_doe_ref', {})
    name = aliases.get(building_type, building_type)
    if building_type == 'Lodging' and ctx:
        fc = ctx.get('floor_count')
        if fc is not None and int(fc) > 3:
            return 'LargeHotel'
    return name


def _office_size_class(ctx: dict) -> str:
    """Office WWR size-bands to match PowerTwin.rb lookup_building_type (total
    floor area: <25k SmallOffice, >100k LargeOffice, else MediumOffice). Occupancy
    and schedule still key on plain 'Office', so this refines the WWR lookup only."""
    try:
        area = float(ctx.get('area'))
    except (TypeError, ValueError):
        return 'Office'
    if area < 25000:
        return 'SmallOffice'
    if area > 100000:
        return 'LargeOffice'
    return 'MediumOffice'


SQFT_PER_BEDROOM = 800

def _resolve_occupants(ctx: dict) -> int | None:
    """Composite occupants resolver:
        residential: HPXML/Manual J convention -> bedrooms + 1, PER dwelling unit
                     (bedrooms inferred from per-unit area at ~800 ft^2/br when absent)
        commercial:  area_sqft * people_per_1000ft^2 / 1000
                     (people_per_1000ft^2 from OpenStudio Standards 90.1-2013,
                     keyed by DOE Reference Building prototype name)
    Returns None if context is too sparse, letting caller fall back further."""
    bt = ctx.get('building_type')
    if not bt:
        return None
    if bt in RESIDENTIAL_BUILDING_TYPES:
        # Occupants are PER dwelling unit. number_of_bedrooms in metadata is a
        # WHOLE-BUILDING count (same convention as area), so divide by units for
        # both the explicit and the area-inferred branch -- otherwise an MF
        # building's whole-building bedroom count is treated as per-unit and
        # occupants inflate ~units-fold.
        units = max(1, ctx.get('units') or 1)
        bedrooms = ctx.get('bedrooms')
        if bedrooms in (None, ''):
            area = ctx.get('area')
            if area:
                bedrooms = max(1, round(float(area) / units / SQFT_PER_BEDROOM))
            else:
                bedrooms = 2  # HPXML modal SFD assumption
        else:
            try:
                bedrooms = max(1, round(int(bedrooms) / units))
            except (TypeError, ValueError):
                return None
        try:
            return int(bedrooms) + 1
        except (TypeError, ValueError):
            return None
    # commercial path
    table = _load_ref('openstudio_standards_people_per_area')['people_per_1000_ft2']
    entry = table.get(_doe_ref_name(bt, ctx))
    if not entry or entry.get('value') is None:
        return None
    area = ctx.get('area')
    if not area:
        return None
    return max(1, round(float(area) * entry['value'] / 1000))


def _lookup_region_vintage(table: dict, ctx: dict, fallback_vintage: str = 'all'):
    """Walk a (region -> vintage -> {mode, shares}) lookup. Returns
    (record, level) where level is 'vintage_specific' on exact match,
    'region_all' on fallback to the region's 'all' bucket, or (None, None)
    when no region matches."""
    region = ctx.get('region')
    if not region:
        return (None, None)
    region_table = table.get(region)
    if not region_table:
        return (None, None)
    vintage = ctx.get('vintage')
    if vintage and vintage in region_table:
        return (region_table[vintage], 'vintage_specific')
    fb = region_table.get(fallback_vintage)
    if fb is not None:
        return (fb, 'region_all')
    return (None, None)


def _building_id_hash_fraction(ctx: dict, salt: str = '') -> float:
    """Deterministic [0, 1) fraction from building_id for share-weighted
    assignment. The salt parameter ensures independent hash lanes for
    different fields (system type vs fuel vs SWH) so selections don't
    have systematic correlation artifacts."""
    bid = ctx.get('building_id') or ''
    digest = hashlib.md5(f'{bid}:{salt}'.encode()).hexdigest()
    return int(digest[:8], 16) / 0x100000000


def _select_fuel_by_share(record: dict, ctx: dict, salt: str = '') -> str:
    """Select fuel proportionally from the share distribution using a
    deterministic hash of the building ID. Falls back to mode if no
    building_id is available or stochastic sampling is disabled."""
    shares = record.get('shares')
    if not shares:
        return record.get('mode')
    bid = ctx.get('building_id')
    if not bid or not is_stochastic_sampling_enabled():
        return record.get('mode')
    frac = _building_id_hash_fraction(ctx, salt)
    cumulative = 0.0
    for fuel, share in sorted(shares.items(), key=lambda x: -x[1]):
        cumulative += share
        if frac < cumulative:
            return fuel
    return record.get('mode')


def _lookup_division_vintage(table: dict, ctx: dict, fallback_vintage: str = 'all'):
    """Walk a (division -> vintage -> {mode, shares}) lookup under the
    '_division' key. Returns (record, level) or (None, None)."""
    division_table = table.get('_division')
    if not division_table:
        return (None, None)
    division = ctx.get('division')
    if not division:
        return (None, None)
    div_section = division_table.get(division)
    if not div_section:
        return (None, None)
    vintage = ctx.get('vintage')
    if vintage and vintage in div_section:
        return (div_section[vintage], 'division_vintage')
    fb = div_section.get(fallback_vintage)
    if fb is not None:
        return (fb, 'division_all')
    return (None, None)


def _heating_fuel_marginal(ctx, fuel):
    """ACS B25040 share of homes heating with `fuel` for ctx's county, or None
    if no county_fips / no data. The authoritative LOCAL heating-fuel marginal
    the county-first reconciliation targets."""
    cf = ctx.get('county_fips')
    if not cf:
        return None
    cs = _load_ref('acs2022_county_fuel').get('fuel_share', {}).get(cf)
    return cs.get(fuel) if cs else None


def _heating_division_marginal(ctx, fuel):
    """RECS 2020 division+vintage heating-fuel marginal for `fuel` (the direct
    recs2020_residential_fuel_mix share). Used as the reconciliation target when
    NO county_fips is available, so the no-geo fallback's resolved fuel marginal
    matches the authoritative RECS division marginal instead of drifting from it
    (the system-type and fuel-by-system tables, composed, don't reproduce it)."""
    section = _load_ref('recs2020_residential_fuel_mix').get('heating_system_fuel_type', {})
    dt = section.get('_division', {})
    division = ctx.get('division')
    if not division or division not in dt:
        return None
    div = dt[division]
    vintage = ctx.get('vintage')
    rec = div.get(vintage) if vintage and vintage in div else div.get('all')
    return rec.get('shares', {}).get(fuel, 0) if rec else None


def _heating_marginal_target(ctx, fuel):
    """County-first reconciliation target for `fuel`: the ACS county marginal
    when county_fips is present, else the RECS division marginal."""
    m = _heating_fuel_marginal(ctx, fuel)
    return m if m is not None else _heating_division_marginal(ctx, fuel)


def _raw_heating_system_record(ctx):
    """(record, level) of the division+vintage heating system-type shares, or
    (None, None). Shared by the system-type resolver and the county-first fuel
    reconciliation so both read the same always-electric system share."""
    dt = _load_ref('recs2020_heating_system_type').get('_division')
    division = ctx.get('division')
    if not dt or not division or division not in dt:
        return (None, None)
    div_section = dt[division]
    decade = ctx.get('decade_vintage')
    if decade and decade in div_section:
        return (div_section[decade], 'division_vintage')
    if 'all' in div_section:
        return (div_section['all'], 'division_all')
    return (None, None)


def _fuel_by_system(ctx, field, sys_type, combustion_only):
    """Heating fuel conditional on the resolved heating system type, from the RECS
    recs2020_fuel_by_system_type {fuel | system}-by-division-vintage table (the
    all-electric-cell fallback in _resolve_heating_fuel). When combustion_only is set the
    shares are filtered to gas/propane/oil (natural-gas fallback for sparse electric/wood-
    only cells). Returns (fuel, level) or (None, None). (SWH fuel no longer routes here --
    it conditions on the resolved heating FUEL via recs2020_swh_fuel_by_heating_fuel.)"""
    cond_section = _load_ref('recs2020_fuel_by_system_type').get(sys_type, {}).get('_division', {})
    division = ctx.get('division')
    if not division or division not in cond_section:
        return (None, None)
    div_data = cond_section[division]
    decade = ctx.get('decade_vintage')
    record = div_data.get(decade) if (decade and decade in div_data) else div_data.get('all')
    if not record:
        return (None, None)
    if combustion_only:
        comb = {k: v for k, v in record.get('shares', {}).items()
                if k in ('natural gas', 'propane', 'fuel oil') and v > 0}
        if comb:
            total = sum(comb.values())
            normed = {k: round(v / total, 4) for k, v in comb.items()}
            record = {'shares': normed, 'mode': max(normed, key=normed.get)}
        else:
            # Sparse RECS cell with only electricity/wood; combustion is required.
            # Mirror the Ruby safeguard that forces such cells to natural gas.
            record = {'shares': {'natural gas': 1.0}, 'mode': 'natural gas'}
    return (_select_fuel_by_share(record, ctx, salt=field), 'division_vintage')


def _resolve_heating_fuel(ctx, sys_type):
    """Heating fuel for a resolved system type. Physically determined for the
    electric/wood systems; the combustion split for furnace/boiler comes from
    the heating-fuel marginal (ACS county else RECS division) -- the same
    authoritative source the system-type reconciliation uses, so the within-
    combustion gas/propane/oil split matches the marginal. All-electric cells
    (no combustion fuel locally) fall back to the fuel-by-system table.
    Returns (fuel, level) or (None, None) when the system type is unknown."""
    if sys_type in ('heat_pump', 'electric_resistance'):
        return ('electricity', 'building_type_only')
    if sys_type == 'wood_stove':
        return ('wood', 'building_type_only')
    if sys_type not in ('furnace', 'boiler'):
        return (None, None)
    if ctx.get('county_fips'):
        cs = _load_ref('acs2022_county_fuel').get('fuel_share', {}).get(ctx['county_fips'])
        comb = {k: (cs or {}).get(k, 0) for k in ('natural gas', 'propane', 'fuel oil')}
        level = 'county_acs'
    else:
        comb = {k: (_heating_division_marginal(ctx, k) or 0)
                for k in ('natural gas', 'propane', 'fuel oil')}
        level = 'division_vintage'
    comb = {k: v for k, v in comb.items() if v > 0}
    ctot = sum(comb.values())
    if ctot > 0:
        normed = {k: round(v / ctot, 4) for k, v in comb.items()}
        record = {'shares': normed, 'mode': max(normed, key=normed.get)}
        return (_select_fuel_by_share(record, ctx, salt='heating_system_fuel_type'), level)
    # All-electric county/division: no combustion marginal -> fuel-by-system.
    return _fuel_by_system(ctx, 'heating_system_fuel_type', sys_type, combustion_only=True)


def _resolve_swh_fuel(ctx, sys_type):
    """Service-water-heating fuel, conditioned on the building's resolved HEATING FUEL via
    RECS P(FUELH2O | FUELHEAT) -- so a gas-heated home gets gas SWH at the observed ~84%
    rate. (Conditioning on the heating SYSTEM TYPE alone, as before, averaged over gas/
    propane/oil furnaces and collapsed to ~the marginal, giving only ~48% gas SWH for
    gas-heated homes.) A heat pump water heater is electric by definition (resolved on its
    own hash salt). Returns (fuel, level)."""
    wh_type, _ = _resolve_water_heater_type(ctx)
    if wh_type == 'heat pump water heater':
        return ('electricity', 'building_type_only')
    heating_fuel, hlevel = _resolve_heating_fuel(ctx, sys_type)
    if heating_fuel:
        record = _load_ref('recs2020_swh_fuel_by_heating_fuel').get(heating_fuel)
        if record:
            return (_select_fuel_by_share(record, ctx, salt='service_water_heating_fuel_type'), hlevel)
    return (None, None)


def _resolve_fuel(field: str, ctx: dict):
    """Share-weighted fuel from RECS 2020 (residential) or CBECS 2018
    (commercial) keyed by division + vintage, falling back to region.
    Uses proportional assignment via building_id hash instead of
    winner-take-all MODE. Cooling is gated on climate zone (suppressed
    for CZ >= 7). Heating fuel is conditioned on heating system type
    for residential buildings to avoid impossible combinations."""
    bt = ctx.get('building_type')
    is_residential = bt in RESIDENTIAL_BUILDING_TYPES
    survey = _load_ref('recs2020_residential_fuel_mix' if is_residential
                       else 'cbecs2018_commercial_fuel_mix')
    section = survey.get(field)
    if not section:
        return (None, None)
    if '_constant_default' in section:
        cz = ctx.get('climate_zone') or ''
        cz_num = int(cz[0]) if cz and cz[0].isdigit() else 0
        # Subarctic cooling suppression is residential-only: 50-70% of CZ 7-8
        # homes lack mechanical cooling. Commercial buildings in those zones
        # keep cooling, so don't emit 'none' (which Ruby can't map anyway).
        if field == 'cooling_system_fuel_type' and cz_num >= 7 and is_residential:
            return ('none', 'building_type_only')
        return (section['_constant_default']['mode'], 'building_type_only')
    # Residential heating/SWH fuel is conditioned on the heating system type.
    if field in ('heating_system_fuel_type', 'service_water_heating_fuel_type') and is_residential:
        sys_type, _ = _resolve_heating_system_type(ctx)
        if field == 'heating_system_fuel_type':
            r = _resolve_heating_fuel(ctx, sys_type)
        else:
            r = _resolve_swh_fuel(ctx, sys_type)
        if r != (None, None):
            return r
    # Try division-level first, then region-level
    record, level = _lookup_division_vintage(section, ctx)
    if record is None:
        record, level = _lookup_region_vintage(section, ctx)
    if record is None:
        return (None, None)
    return (_select_fuel_by_share(record, ctx, salt=field), level)


def _resolve_heating_system_type(ctx: dict):
    """Share-weighted heating system type (heat_pump, furnace, boiler,
    electric_resistance, wood_stove) from RECS 2020 by division + vintage.
    Only applies to residential; commercial uses system_type from the
    urbanopt template.

    County-first reconciliation: when county_fips is present, scale the system
    groups so the resolved heating-FUEL marginals match the authoritative ACS
    B25040 county shares, while keeping each group's internal mix from the RECS
    division:
      - electric systems (heat_pump + electric_resistance) -> ACS electricity
        share, split by the division's heat_pump:electric_resistance ratio (so
        electric-rich counties get real heat pumps at COP>1, not COP-1 furnaces,
        and gas-dominant counties don't overshoot electric);
      - wood_stove -> ACS wood share (prevents the division wood share, up to
        ~10% in the Pacific division, from being applied to counties where ACS
        reports near-zero wood heating);
      - furnace + boiler -> the combustion remainder, split by the division
        furnace:boiler ratio (these carry gas/propane/oil, set on the fuel side).
    The division distribution is used unchanged when county data is absent."""
    bt = ctx.get('building_type')
    if bt not in RESIDENTIAL_BUILDING_TYPES:
        return (None, None)
    record, level = _reconciled_system_shares(ctx)
    if record is None:
        return (None, None)
    return (_select_fuel_by_share(record, ctx, salt='heating_system_type'), level)


def _reconciled_system_shares(ctx):
    """THE county-first heating reconciliation -- the single place the system-type
    distribution is scaled so the resolved heating-fuel marginals match the
    authoritative source (ACS county when county_fips is known, else RECS
    division). Returns (record, level) with record['shares'] the reconciled
    distribution, or (None, None) when no division data exists.

      electric systems (heat_pump + electric_resistance) -> electricity marginal,
        split by the division heat_pump:electric_resistance ratio;
      wood_stove                                         -> wood marginal;
      furnace + boiler                                   -> combustion remainder,
        split by the division furnace:boiler ratio.

    The raw division distribution is returned unchanged when no marginal target
    is available (no county_fips and no division fuel-mix row)."""
    record, level = _raw_heating_system_record(ctx)
    if record is None:
        return (None, None)
    e_target = _heating_marginal_target(ctx, 'electricity')
    if e_target is None:
        return (record, level)
    s = dict(record.get('shares', {}))
    hp = s.get('heat_pump', 0); er = s.get('electric_resistance', 0)
    furn = s.get('furnace', 0); boil = s.get('boiler', 0)
    elec_div = hp + er
    comb_div = furn + boil
    w_target = _heating_marginal_target(ctx, 'wood')
    w_t = w_target if w_target is not None else s.get('wood_stove', 0)
    e_t = max(0.0, e_target)
    c_t = max(0.0, 1.0 - e_t - w_t)
    new = {}
    if elec_div > 0:
        new['heat_pump'] = e_t * hp / elec_div
        new['electric_resistance'] = e_t * er / elec_div
    else:
        # division shows no electric systems; treat new electric homes as
        # resistance (COP 1) rather than overstating heat-pump efficiency.
        new['heat_pump'] = 0.0
        new['electric_resistance'] = e_t
    new['wood_stove'] = w_t
    if comb_div > 0:
        new['furnace'] = c_t * furn / comb_div
        new['boiler'] = c_t * boil / comb_div
    else:
        new['furnace'] = c_t
        new['boiler'] = 0.0
    return ({'shares': new, 'mode': max(new, key=new.get)}, level)


def _resolve_water_heater_type(ctx: dict):
    """Share-weighted water heater type from RECS 2020 by division + vintage.
    Residential only."""
    bt = ctx.get('building_type')
    if bt not in RESIDENTIAL_BUILDING_TYPES:
        return (None, None)
    survey = _load_ref('recs2020_water_heater_type')
    division_table = survey.get('_division')
    if not division_table:
        return (None, None)
    division = ctx.get('division')
    if not division or division not in division_table:
        return (None, None)
    div_section = division_table[division]
    decade = ctx.get('decade_vintage')
    record = None
    level = None
    if decade and decade in div_section:
        record = div_section[decade]
        level = 'division_vintage'
    elif 'all' in div_section:
        record = div_section['all']
        level = 'division_all'
    if record is None:
        return (None, None)
    return (_select_fuel_by_share(record, ctx, salt='water_heater_type'), level)


def _resolve_window_type(ctx: dict):
    """Window glazing type from survey-transcribed national-stock distributions,
    keyed by (region, vintage). residential = RECS 2020 TYPEGLASS, commercial =
    CBECS 2018 WINTYP (see window_type_by_vintage.json). Each cell carries
    {mode, shares}; with stochastic sampling on, the glazing is share-weighted by
    the building_id hash so a cell reproduces its real Single/Double/Triple mix
    instead of collapsing every building to the mode."""
    bt = ctx.get('building_type')
    is_residential = bt in RESIDENTIAL_BUILDING_TYPES
    table = _load_ref('window_type_by_vintage')
    section = table.get('residential' if is_residential else 'commercial')
    if not section:
        return (None, None)
    record, level = _lookup_region_vintage(section, ctx, fallback_vintage=None)
    if record is None:
        return (None, None)
    return (_select_fuel_by_share(record, ctx, salt='window_type'), level)


def _resolve_operating_schedule(field: str, ctx: dict):
    """Per-building-type weekday/weekend operating window pulled from the
    DOE Reference Building prototype occupancy schedules in
    openstudio_standards_operating_schedules.json. Provenance is always
    'building_type_only' (no region/vintage axis). An empty string in the
    table (e.g. School weekends) preserves the URBANopt template default
    by passing through to flat fallback at the caller."""
    bt = ctx.get('building_type')
    if not bt:
        return (None, None)
    doe_ref = _doe_ref_name(bt, ctx)
    # The table window is the occupancy-peak run (longest contiguous hours with
    # occupancy >= 50% of the day peak). For prototypes that are effectively 24/7
    # or overnight/bimodal, that window is NOT the building's operating period --
    # applying it via create_typical's modify_op_hrs lever would force the
    # building into setback during genuinely occupied hours (e.g. a 24/7 hotel
    # run only 16:00-24:00). For these, emit nothing so the DOE prototype's
    # correct native schedule is preserved.
    # 24/7 (hotel, hospital, apartment) or bimodal (restaurant: lunch+dinner).
    # Outpatient/Office/School/Retail keep their single daytime-peak window.
    OP_HOURS_SUPPRESS = {
        'SmallHotel', 'LargeHotel', 'Hospital',
        'MidriseApartment', 'HighriseApartment',
        'FullServiceRestaurant', 'QuickServiceRestaurant',
    }
    if doe_ref in OP_HOURS_SUPPRESS:
        return (None, None)
    table = _load_ref('openstudio_standards_operating_schedules').get(
        'operating_hours_by_building_type', {})
    entry = table.get(doe_ref)
    if entry is None:
        return (None, None)
    val = entry.get(field)
    if not val:
        return (None, None)
    return (val, 'building_type_only')


def _r_to_tier(r: float, surface: str) -> str:
    """Derive the material tier (simulation_surface_materials enum) from the resolved
    R-value so a building's tier and R always agree. Thresholds follow the documented
    ResStock tier bands (wall cavity vs roof/ceiling)."""
    if surface == 'wall':
        return 'Standard' if r <= 7 else 'Insulated' if r <= 14 else 'Super Insulated'
    return 'Standard' if r < 19 else 'Insulated' if r < 38 else 'Super Insulated'


def _envelope_R_cell(r_table, ctx):
    """Select the {mode, shares} R cell for the building's (geography, vintage).
    Commercial tables are dual-keyed {by_climate_zone, by_region}: prefer the building's
    climate zone (codes are CZ-driven; census region smears a ~5x R range), fall back to
    census region when climate_zone is absent. Residential roof is keyed by building type
    then region (MF/mobile differ from SFD); residential wall is region-keyed directly."""
    vintage = ctx.get('vintage')
    if not r_table or not vintage:
        return None
    if 'by_climate_zone' in r_table:                        # commercial: CZ -> bare-zone -> region
        cz = ctx.get('climate_zone')
        czt = r_table['by_climate_zone']
        if cz and czt.get(cz, {}).get(vintage):
            return czt[cz][vintage]
        # CZ 8 isn't moisture-subdivided in the table (keyed '8'), but get_location can emit '8A'.
        # Try the bare zone number before the (coarser, ~2x-off) census-region fallback.
        cz_bare = cz.rstrip('ABC') if cz else None
        if cz_bare and cz_bare != cz and czt.get(cz_bare, {}).get(vintage):
            return czt[cz_bare][vintage]
        return r_table.get('by_region', {}).get(ctx.get('region'), {}).get(vintage)
    if 'by_building_type' in r_table:                       # residential roof: type -> region
        btt = r_table['by_building_type']
        sub = btt.get(ctx.get('building_type')) or btt.get('Single-Family Detached')
        return (sub or {}).get(ctx.get('region'), {}).get(vintage)
    return r_table.get(ctx.get('region'), {}).get(vintage)  # residential wall: region-keyed


def _resolve_envelope(field: str, ctx: dict):
    """Envelope R + material from realized-stock {mode, shares} DISTRIBUTIONS, share-weighted
    per building by the building_id hash; material tier is DERIVED from the same R draw so
    tier and R always agree (see _envelope_R_cell for the keying: residential wall region-keyed,
    residential roof by building type, commercial dual-keyed climate-zone/region). WWR is
    building-type-keyed -- residential = ResStock window-areas distribution; commercial =
    ComStock realized distribution per DOE-ref type, with a scalar fallback for the few types
    ComStock does not model. Missing geography/vintage returns (None, None)."""
    bt = ctx.get('building_type')
    is_residential = bt in RESIDENTIAL_BUILDING_TYPES
    survey = _load_ref('recs2020_envelope' if is_residential else 'cbecs2018_envelope')

    if field == 'window_to_wall_ratio':
        if is_residential:
            lookup_bt = bt
        else:
            lookup_bt = _doe_ref_name(bt, ctx)
            # Only literal Office is area-banded; Ruby pins the other Office-aliased types to MediumOffice.
            if bt == 'Office':
                lookup_bt = _office_size_class(ctx)
        rec = survey.get('window_to_wall_ratio_by_building_type', {}).get(lookup_bt)
        if rec is None:
            return (None, None)
        if isinstance(rec, dict):   # {mode, shares} distribution (residential)
            return (float(_select_fuel_by_share(rec, ctx, salt='window_to_wall_ratio')), 'building_type_only')
        return (rec, 'building_type_only')   # legacy scalar (commercial)

    # wall/roof material + R both resolve off the R-value table so tier and R agree.
    # All envelope R cells are {mode, shares} distributions now; the resolved R is
    # share-weighted by the building_id hash and the material tier is derived from it.
    surface = 'wall' if field.startswith('wall') else 'roof'
    rec = _envelope_R_cell(survey.get(f'{surface}_r_value'), ctx)
    if not isinstance(rec, dict):
        return (None, None)
    r = float(_select_fuel_by_share(rec, ctx, salt=f'{surface}_r_value'))
    if field.endswith('material'):
        return (_r_to_tier(r, surface), 'vintage_specific')
    return (r, 'vintage_specific')


def resolve_default(field: str, ctx: dict):
    """Single entry-point for dynamic default resolution. Returns
    (value, level) where level is one of FALLBACK_LEVELS, or (None, None)
    when no rule applies (caller then uses SIM_PARAM_DEFAULTS and labels
    it 'flat_default'). See module docstring for precedence.

    Gated by the URBANOPT_DYNAMIC_DEFAULTS env var; returns (None, None)
    when the feature is off so the flat fallback always wins.
    """
    if not is_dynamic_defaults_enabled() or not ctx:
        return (None, None)
    try:
        if field == 'number_of_occupants':
            val = _resolve_occupants(ctx)
            return (val, 'building_type_only' if val is not None else None)
        if field == 'heating_system_type':
            return _resolve_heating_system_type(ctx)
        if field == 'water_heater_type':
            return _resolve_water_heater_type(ctx)
        if field in ('heating_system_fuel_type', 'cooling_system_fuel_type',
                     'service_water_heating_fuel_type'):
            return _resolve_fuel(field, ctx)
        if field == 'window_type':
            return _resolve_window_type(ctx)
        if field in ('wall_material', 'roof_material',
                     'wall_r_value', 'roof_r_value', 'window_to_wall_ratio'):
            return _resolve_envelope(field, ctx)
        if field in ('weekday_start_time', 'weekday_duration',
                     'weekend_start_time', 'weekend_duration'):
            return _resolve_operating_schedule(field, ctx)
    except (KeyError, IndexError, ValueError, TypeError, OSError) as exc:
        # OSError covers FileNotFoundError (missing reference JSON); ValueError
        # already covers JSONDecodeError. resolver must always degrade -- never
        # let a missing/corrupt reference file kill the sim.
        log.warning(f"resolve_default({field}) raised {type(exc).__name__}: {exc}")
        return (None, None)
    return (None, None)


def get_param(metadata, key, ctx=None):
    """Read a programmable param from asset_metadata with the resolution
    precedence documented at the top of this module.

    `ctx` is the asset context dict from build_asset_ctx(); when omitted,
    falls back to the static SIM_PARAM_DEFAULTS without the dynamic step
    (legacy behavior preserved for callers that don't have building_type
    resolved yet).
    """
    raw = metadata.get(key)
    if raw is not None and raw != '':
        if key in NUMERIC_RANGES:
            lo, hi = NUMERIC_RANGES[key]
            try:
                val = float(raw)
            except (ValueError, TypeError):
                log.warning(f"non-numeric {key}={raw!r}, falling back to default")
                raw = None
            else:
                if val < lo or val > hi:
                    clamped = max(lo, min(hi, val))
                    log.warning(f"{key}={val} out of range [{lo}, {hi}], clamping to {clamped}")
                    return clamped
                return val
        else:
            return str(raw)

    # Dynamic resolution from national-stock lookups
    if ctx is not None:
        dynamic, _level = resolve_default(key, ctx)
        if dynamic is not None:
            # Safety net: never let resolver/reference-data drift emit an enum
            # value the downstream toolchain can't map. Such a value would be
            # written to the feature JSON and silently dropped by the Ruby
            # fuel_map. Allowed values are RESOLVER_ENUM_VALUES (a superset of
            # ENUM_VALUES incl. 'district steam'/'none'); anything else falls
            # back to the flat default with a loud warning.
            allowed = RESOLVER_ENUM_VALUES.get(key)
            if allowed is not None and dynamic not in allowed:
                log.warning(
                    f"resolver emitted {key}={dynamic!r} outside allowed enum "
                    f"{sorted(allowed)}; falling back to default "
                    f"{SIM_PARAM_DEFAULTS[key]!r}")
            else:
                # Numeric resolver output gets the same range clamp as the raw
                # metadata path above, so an over-large area * density occupant
                # count can't exceed the field ceiling.
                if key in NUMERIC_RANGES:
                    try:
                        lo, hi = NUMERIC_RANGES[key]
                        val = float(dynamic)
                        if val < lo or val > hi:
                            clamped = max(lo, min(hi, val))
                            log.warning(f"resolver {key}={val} out of range "
                                        f"[{lo}, {hi}], clamping to {clamped}")
                            return type(dynamic)(clamped) if isinstance(dynamic, int) else clamped
                    except (TypeError, ValueError):
                        pass
                return dynamic

    return SIM_PARAM_DEFAULTS[key]


def is_vacant(ctx: dict) -> bool:
    """Deterministically mark a building as vacant based on ACS county-level
    vacancy rate. Uses the building_id hash so the same building is always
    vacant or occupied across runs. Requires both dynamic_defaults and
    stochastic_sampling to be enabled.

    Residential-only: the rate comes from ACS B25002/B25004 (HOUSING-UNIT
    vacancy), which has no meaning for commercial assets. Mirrors the
    building_type guard on every other dynamic-defaults feature (garage,
    fuel, envelope). Commercial vacancy would need a CBECS/commercial
    occupancy source, not housing-unit vacancy."""
    if not is_dynamic_defaults_enabled():
        return False
    if not is_stochastic_sampling_enabled():
        return False
    if ctx.get('building_type') not in RESIDENTIAL_BUILDING_TYPES:
        return False
    county_fips = ctx.get('county_fips')
    if not county_fips:
        return False
    try:
        acs = _load_ref('acs2022_vacancy')
        entry = acs.get('vacancy', {}).get(county_fips)
        if not entry:
            return False
        # Use year-round vacancy only: subtract the seasonal fraction (B25004
        # second homes / vacation units) from the total ACS vacancy. Seasonal
        # homes ARE occupied part of the year, so modeling them as fully vacant
        # (0 occupants, pipe-freeze setpoints) wrongly empties them -- in resort
        # counties seasonal is most of the vacancy (Teton WY: 0.278 total,
        # 0.196 seasonal -> only ~0.083 truly vacant).
        rate = entry.get('vacancy_rate', 0) - entry.get('seasonal_rate', 0)
        if rate <= 0:
            return False
        if not ctx.get('building_id'):
            return False
        frac = _building_id_hash_fraction(ctx, salt='vacancy')
        return frac < rate
    except Exception:
        return False
