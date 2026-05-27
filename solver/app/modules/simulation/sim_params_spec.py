"""Canonical defaults + enum table refs for programmable urbanopt sim params.

Mirror of powertwin-db/api/lib/simulationParamsSpec.js. Keep them in sync: if
you change a default or add a new field, update both files.

The ingest reads each programmable field from asset_metadata with these defaults
as the fallback, so unmodified assets re-simulate identically to pre-spec
behavior.
"""

import logging
import re

log = logging.getLogger('Generate Feature Files')

# 24h HH:MM (00:00 to 23:59). 24:00 is rejected because it collides with
# OpenStudio's implicit end-of-day Time(24:00:00) and trips BOOST_ASSERT.
_TIME_RE = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')
TIME_FIELDS = ('weekday_start_time', 'weekday_duration',
               'weekend_start_time', 'weekend_duration')

SIM_PARAM_DEFAULTS = {
    # Enum-backed
    'system_type': 'VAV district chilled water with district hot water reheat',
    'heating_system_fuel_type': 'electricity',
    'cooling_system_fuel_type': 'electricity',
    'service_water_heating_fuel_type': 'electricity',
    'window_type': 'Double Pane',
    'wall_material': 'Super Insulated',
    'roof_material': 'Super Insulated',

    # Numeric
    'window_to_wall_ratio': 0.15,
    'wall_r_value': 15.0,
    'roof_r_value': 15.0,
    'floor_height': 9.0,
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


# Default number_of_occupants per occupancy_type, used when an asset doesn't
# override number_of_occupants in its metadata. Mirrored in
# powertwin-db/api/lib/simulationParamsSpec.js and served via the params-spec
# endpoint so the frontend resolves the same per-asset default the ingest uses.
OCCUPANTS_MAPPING = {
    'Educational':      355,
    'Business':         100,
    'SmallResidential': 4,
    'BigResidential':   355,
    'Vacant':           1,
    'Industrial':       100,
    'Storage':          10,
    'FoodMercantile':   30,
    'Institutional':    40,
    'Health Care':      60,
    'Assembly':         200,
    'Mercantile':       150,
    'Mixed':            355,
    'Parking':          1,
    'Unknown':          1,
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


def get_param(metadata, key):
    """Read a programmable param from asset_metadata with default fallback.

    For numeric keys with a range, clamps the value into [min, max] and logs a
    warning if clamping was needed. Returns the typed value (float for numeric,
    str for everything else).
    """
    raw = metadata.get(key)
    default = SIM_PARAM_DEFAULTS[key]

    if raw is None or raw == '':
        return default

    if key in NUMERIC_RANGES:
        lo, hi = NUMERIC_RANGES[key]
        try:
            val = float(raw)
        except (ValueError, TypeError):
            log.warning(f"non-numeric {key}={raw!r}, using default {default}")
            return default
        if val < lo or val > hi:
            clamped = max(lo, min(hi, val))
            log.warning(f"{key}={val} out of range [{lo}, {hi}], clamping to {clamped}")
            return clamped
        return val

    # enum + time fields: return as string
    return str(raw)
