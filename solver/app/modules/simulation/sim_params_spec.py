"""Canonical defaults + enum table refs for programmable urbanopt sim params.

Mirror of powertwin-db/api/lib/simulationParamsSpec.js. Keep them in sync: if
you change a default or add a new field, update both files.

The ingest reads each programmable field from asset_metadata with these defaults
as the fallback, so unmodified assets re-simulate identically to pre-spec
behavior.

Default resolution precedence (set by `get_param` with an asset ctx):
    1. Explicit metadata value, if non-empty -> use it (validated/clamped).
    2. Dynamic resolver from solver/upload/reference_data/ lookup tables -> use it.
    3. Flat SIM_PARAM_DEFAULTS[field] -> last-resort fallback.

The dynamic resolvers are sourced from authoritative national-stock surveys
(EIA RECS 2020 residential, EIA CBECS 2018 commercial, OpenStudio Standards
ASHRAE 90.1-2013 occupant densities, HPXML/Manual J residential occupants
formula). See solver/README.md "Default-value provenance" for the citations.
"""

import json
import logging
import math
import os
import re

log = logging.getLogger('Generate Feature Files')

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
    'heating_system_fuel_type': 'natural gas',          # RECS 2020 plurality
    'cooling_system_fuel_type': 'electricity',          # ~99% national
    'service_water_heating_fuel_type': 'natural gas',   # RECS 2020 plurality
    'window_type': 'Double Pane',                       # 90% post-1990 stock
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


def build_asset_ctx(metadata: dict, building_type: str | None = None) -> dict:
    """Distill the lookup-key context out of an asset's metadata dict. Centralized
    here so the resolvers all see the same shape: {building_type, region, vintage,
    state, area, bedrooms}. Missing keys are None; resolvers fall back accordingly."""
    state = _normalize_state(metadata.get('state'))
    region = _load_ref('census_regions')['state_to_region'].get(state) if state else None
    return {
        'building_type': building_type,
        'state': state,
        'region': region,
        'vintage': _vintage_bin(metadata.get('year_built') or metadata.get('yearBuilt')),
        'area': metadata.get('area') or metadata.get('floor_area'),
        'bedrooms': metadata.get('bedrooms') or metadata.get('number_of_bedrooms'),
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
FALLBACK_LEVELS = ('vintage_specific', 'region_all', 'building_type_only', 'flat_default')


def _doe_ref_name(building_type: str | None) -> str | None:
    """Translate a PowerTwin asset_subtype name (CBECS survey vocabulary) to
    the closest DOE Reference Building prototype name used by NREL
    openstudio-standards lookup tables. Returns the input unchanged when it
    already matches a DOE prototype, or None for inputs with no sensible
    proxy (Parking, Vacant) so the resolver degrades to flat defaults.

    See solver/upload/reference_data/building_type_aliases.json for the map
    and the rationale per row."""
    if not building_type:
        return None
    aliases = _load_ref('building_type_aliases').get('powertwin_to_doe_ref', {})
    return aliases.get(building_type, building_type)


def _resolve_occupants(ctx: dict) -> int | None:
    """Composite occupants resolver:
        residential: HPXML/Manual J convention -> bedrooms + 1
                     (bedrooms inferred from area at ~600 ft^2/br when absent)
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
                bedrooms = max(1, round(float(area) / 600))
            else:
                bedrooms = 2  # HPXML modal SFD assumption
        try:
            return int(bedrooms) + 1
        except (TypeError, ValueError):
            return None
    # commercial path
    table = _load_ref('openstudio_standards_people_per_area')['people_per_1000_ft2']
    entry = table.get(_doe_ref_name(bt))
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


def _resolve_fuel(field: str, ctx: dict):
    """Mode fuel from RECS 2020 (residential) or CBECS 2018 (commercial)
    keyed by region + vintage. Cooling returns the constant 'electricity'
    tagged as 'building_type_only' (no region/vintage applied)."""
    bt = ctx.get('building_type')
    is_residential = bt in RESIDENTIAL_BUILDING_TYPES
    survey = _load_ref('recs2020_residential_fuel_mix' if is_residential
                       else 'cbecs2018_commercial_fuel_mix')
    section = survey.get(field)
    if not section:
        return (None, None)
    if '_constant_default' in section:
        return (section['_constant_default']['mode'], 'building_type_only')
    record, level = _lookup_region_vintage(section, ctx)
    if record is None:
        return (None, None)
    return (record.get('mode'), level)


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
        lookup_bt = bt if is_residential else _doe_ref_name(bt)
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
        if field in ('heating_system_fuel_type', 'cooling_system_fuel_type',
                     'service_water_heating_fuel_type'):
            return _resolve_fuel(field, ctx)
        if field in ('wall_material', 'roof_material',
                     'wall_r_value', 'roof_r_value', 'window_to_wall_ratio'):
            return _resolve_envelope(field, ctx)
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
