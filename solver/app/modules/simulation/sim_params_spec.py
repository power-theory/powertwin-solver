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

log = logging.getLogger('Generate Feature Files')

# Bump when resolver logic or reference data changes.
# Stamped into feature.json so results are traceable to the model that produced them.
# push.sh syncs this to the README header automatically.
RESOLVER_VERSION = '1.5'

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
    'water_heater_type': 'storage water heater',        # RECS 2020 national mode (73.2%)
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


def build_asset_ctx(metadata: dict, building_type: str | None = None,
                    climate_zone: str | None = None,
                    building_id: str | None = None) -> dict:
    """Distill the lookup-key context out of an asset's metadata dict. Centralized
    here so the resolvers all see the same shape: {building_type, region, division,
    vintage, state, area, bedrooms, floor_count, units, climate_zone, building_id}.
    Missing keys are None; resolvers fall back accordingly."""
    state = _normalize_state(metadata.get('state') or metadata.get('State'))
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
        'vintage': _vintage_bin(metadata.get('year_built') or metadata.get('yearBuilt')),
        'area': metadata.get('area') or metadata.get('floor_area'),
        'bedrooms': metadata.get('bedrooms') or metadata.get('number_of_bedrooms'),
        'floor_count': _coerce_int(metadata.get('floor_count')),
        'units': _coerce_int(metadata.get('number_of_units') or metadata.get('units') or metadata.get('number_of_residential_units')),
        'climate_zone': cz,
        'building_id': bid,
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
FALLBACK_LEVELS = ('division_vintage', 'division_all', 'vintage_specific', 'region_all', 'building_type_only', 'flat_default')


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
        bedrooms = ctx.get('bedrooms')
        if bedrooms in (None, ''):
            area = ctx.get('area')
            if area:
                units = max(1, ctx.get('units') or 1)
                bedrooms = max(1, round(float(area) / units / SQFT_PER_BEDROOM))
            else:
                bedrooms = 2  # HPXML modal SFD assumption
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
    building_id is available."""
    shares = record.get('shares')
    if not shares:
        return record.get('mode')
    bid = ctx.get('building_id')
    if not bid:
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
        if field == 'cooling_system_fuel_type' and cz_num >= 7:
            return ('none', 'building_type_only')
        return (section['_constant_default']['mode'], 'building_type_only')
    # For residential heating/SWH fuel, condition on system type.
    # Heating: avoids impossible combos (furnace+wood, boiler+electricity).
    # SWH: matches RECS conditional P(FUELH2O | EQUIPM) so gas-heated homes
    # get gas SWH at the observed rate, not the unconditioned marginal.
    if field in ('heating_system_fuel_type', 'service_water_heating_fuel_type') and is_residential:
        sys_type, _ = _resolve_heating_system_type(ctx)
        if field == 'heating_system_fuel_type':
            if sys_type == 'heat_pump' or sys_type == 'electric_resistance':
                return ('electricity', 'building_type_only')
            if sys_type == 'wood_stove':
                return ('wood', 'building_type_only')
        ref_name = ('recs2020_fuel_by_system_type' if field == 'heating_system_fuel_type'
                     else 'recs2020_swh_fuel_by_system_type')
        if sys_type in ('furnace', 'boiler', 'heat_pump', 'elec_resist', 'wood_stove'):
            cond = _load_ref(ref_name)
            cond_section = cond.get(sys_type, {}).get('_division', {})
            division = ctx.get('division')
            if division and division in cond_section:
                div_data = cond_section[division]
                vintage = ctx.get('vintage')
                record = None
                if vintage and vintage in div_data:
                    record = div_data[vintage]
                elif 'all' in div_data:
                    record = div_data['all']
                if record:
                    return (_select_fuel_by_share(record, ctx, salt=field), 'division_vintage')
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
    urbanopt template."""
    bt = ctx.get('building_type')
    if bt not in RESIDENTIAL_BUILDING_TYPES:
        return (None, None)
    survey = _load_ref('recs2020_heating_system_type')
    division_table = survey.get('_division')
    if not division_table:
        return (None, None)
    division = ctx.get('division')
    if not division or division not in division_table:
        return (None, None)
    div_section = division_table[division]
    vintage = ctx.get('vintage')
    record = None
    level = None
    if vintage and vintage in div_section:
        record = div_section[vintage]
        level = 'division_vintage'
    elif 'all' in div_section:
        record = div_section['all']
        level = 'division_all'
    if record is None:
        return (None, None)
    return (_select_fuel_by_share(record, ctx, salt='heating_system_type'), level)


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
    vintage = ctx.get('vintage')
    record = None
    level = None
    if vintage and vintage in div_section:
        record = div_section[vintage]
        level = 'division_vintage'
    elif 'all' in div_section:
        record = div_section['all']
        level = 'division_all'
    if record is None:
        return (None, None)
    return (_select_fuel_by_share(record, ctx, salt='water_heater_type'), level)


def _resolve_window_type(ctx: dict):
    """Modal window type from ComStock/ResStock national-stock distributions,
    keyed by (region, vintage). Commercial and residential use separate tables
    because residential cold-climate 2010+ stock is modal Triple Pane (IECC
    2012+ CZ5-7 prescriptive U-factor) while commercial stays Double Pane."""
    bt = ctx.get('building_type')
    is_residential = bt in RESIDENTIAL_BUILDING_TYPES
    table = _load_ref('window_type_by_vintage')
    section = table.get('residential' if is_residential else 'commercial')
    if not section:
        return (None, None)
    record, level = _lookup_region_vintage(section, ctx, fallback_vintage=None)
    if record is None:
        return (None, None)
    return (record, level)


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
    table = _load_ref('openstudio_standards_operating_schedules').get(
        'operating_hours_by_building_type', {})
    entry = table.get(_doe_ref_name(bt, ctx))
    if entry is None:
        return (None, None)
    val = entry.get(field)
    if not val:
        return (None, None)
    return (val, 'building_type_only')


def _resolve_envelope(field: str, ctx: dict):
    """Mode envelope value (material tier or R-value) keyed by region +
    vintage. WWR is building-type-keyed for both residential (RECS) and
    commercial (CBECS); commercial keys use DOE Reference Building prototype
    names, so PowerTwin's CBECS-survey-style building_type goes through
    `_doe_ref_name` before the lookup. Envelope JSONs do not currently
    carry an 'all' vintage bucket, so missing year_built returns (None, None)
    for material / r_value fields."""
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
        val = survey.get('window_to_wall_ratio_by_building_type', {}).get(lookup_bt)
        return (val, 'building_type_only' if val is not None else None)

    region_table = survey.get(field)
    if not region_table:
        return (None, None)
    region = ctx.get('region')
    if not region or region not in region_table:
        return (None, None)
    vintage = ctx.get('vintage')
    if vintage and vintage in region_table[region]:
        return (region_table[region][vintage], 'vintage_specific')
    return (None, None)


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
            return dynamic

    return SIM_PARAM_DEFAULTS[key]
