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
import csv
import functools
import re
import time
import urllib.request

log = logging.getLogger('Generate Feature Files')

# Bump when resolver logic or reference data changes.
# Stamped into feature.json so results are traceable to the model that produced them.
# push.sh syncs this to the README header automatically.
RESOLVER_VERSION = '3.3'  # 3.3: a commercial building's interior lighting LPD comes from its ComStock lighting generation (resolve_lighting) and its template (construction and HVAC) follows its state's energy-code adoption years from ComStock 2024.1 (resolved_templates), California on DEER with its CEC zone, and a county-drawn 'Residential forced air furnace' builds as ComStock's unventilated furnace (MEASURE_NATIVE_SYSTEMS; system_type is CONFIRMABLE); 3.2: an explicit floor_height is a house's ceiling height and a saved wall/roof_material is ignored (RETIRED_INPUT_FIELDS); 3.1: an explicit heating_system_type or water_heater_type equal to the unset default only confirms it (CONFIRMABLE_FIELDS); 3.0: CR review fixes

_REF_DATA_DIR = os.path.join(
    os.path.dirname(__file__), '..', '..', '..', 'upload', 'reference_data'
)
_REF_CACHE = {}
def _load_ref(name: str) -> dict:
    """Lazy-load a reference JSON. `name` is the filename without extension.
    County-keyed tables (CR campaign) ship gzipped as `{name}.json.gz`; the plain
    `.json` form is preferred when both exist so a hand-inspected override wins."""
    if name not in _REF_CACHE:
        plain = os.path.join(_REF_DATA_DIR, f'{name}.json')
        if os.path.exists(plain):
            with open(plain, 'r') as f:
                _REF_CACHE[name] = json.load(f)
        else:
            import gzip
            with gzip.open(os.path.join(_REF_DATA_DIR, f'{name}.json.gz'), 'rt') as f:
                _REF_CACHE[name] = json.load(f)
    return _REF_CACHE[name]


def _county_prior(ref_name: str, county_fips):
    """CR campaign (WP-00): three-tier county prior lookup with sample-floor fallback.

    Tables built by tests/tools/build_county_priors.py carry, PER COUNTY, three
    precomputed cells (county / puma-union / division) plus their raw sample sizes,
    and a `_floors` block set by the builder (owner-ratified: residential 30,
    commercial 10; division always serves). County->PUMA is many-to-many, so the
    puma cell is the union of rows in every PUMA intersecting the county,
    precomputed at build time - no geographic mapping ships to the resolver.

    Returns (cell_dict, tier_name) where tier_name is 'county' | 'puma' |
    'division', or (None, None) when the county is unknown to the table.
    """
    for cell, tier in _county_prior_tiers(ref_name, county_fips):
        return (cell, tier)
    return (None, None)


def _county_prior_tiers(ref_name: str, county_fips):
    """Yield every (cell, tier) meeting its floor, county -> puma -> division.
    Consumers whose cells are sub-keyed (e.g. commercial hours keyed by building
    type inside each tier, WP-C1) walk this until a tier actually carries their
    sub-key; plain consumers take the first yield via _county_prior."""
    if not county_fips:
        return
    table = _load_ref(ref_name)
    entry = table.get('counties', {}).get(str(county_fips))
    if not entry:
        return
    floors = table.get('_floors', {})
    cell = entry.get('county')
    if cell and cell.get('_n', 0) >= floors.get('county', 1):
        yield (cell, 'county')
    # PUMA tier: shared cells stored once, referenced by name (or a union key for
    # counties spanning several PUMAs) - the dedup schema that keeps county tables
    # loadable (the first joint table was 239 MB raw with inline duplication).
    if 'puma_union_ref' in entry:
        cell = table.get('_puma_unions', {}).get(entry['puma_union_ref'])
    elif 'puma_ref' in entry:
        cell = table.get('_pumas', {}).get(entry['puma_ref'])
    else:
        cell = None
    if cell and cell.get('_n', 0) >= floors.get('puma', 1):
        yield (cell, 'puma')
    cell = table.get('_divisions', {}).get(entry.get('division_ref', ''))
    if cell and cell.get('_n', 0) >= floors.get('division', 1):
        yield (cell, 'division')

# CR review fix (finding 2), updated 2026-10-03: the reference tables are Git LFS objects (pinned in
# county_tables.sha256); a clone without `git lfs pull` has small pointer files in their place, and a missing table
# silently loses its channel (resolve_default swallows the per-field error). Check ONCE at import and shout - loud
# log, not a crash, so prod degrades visibly. The Dockerfile's sha256 check refuses such an image outright.
_COUNTY_TABLES = (
    'resstock2024_county_ducts', 'resstock2024_county_heating',
    'resstock2024_county_setpoints', 'resstock2024_county_infiltration',
    'resstock2024_county_appliances', 'resstock2024_county_waterheater',
    'resstock2024_county_household', 'resstock2024_county_geometry',
    'resstock2024_county_windows', 'resstock2024_county_insulation',
    'resstock2024_county_cooling', 'resstock2024_county_pv',
    'comstock2024_county_hours', 'comstock2024_county_system',
    'comstock2024_county_fuels', 'comstock2024_county_tstat',
    'comstock2024_county_lighting', 'cec_building_climate_zones',
    # resolver 3.3 plain-JSON tables (tracked, not LFS)
    'comstock2024_state_code_adoption', 'comstock_lighting_lpd', 'insulation_presets',
)


def _county_tables_missing():
    """Tables absent, or present only as a Git LFS pointer (a ~130-byte text stub, never valid JSON or gzip)."""
    ref_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           '..', '..', '..', 'upload', 'reference_data')
    bad = []
    for t in _COUNTY_TABLES:
        path = next((p for p in (os.path.join(ref_dir, f'{t}.json.gz'), os.path.join(ref_dir, f'{t}.json'))
                     if os.path.exists(p)), None)
        if path is None:
            bad.append(t)
            continue
        with open(path, 'rb') as f:
            if f.read(40).startswith(b'version https://git-lfs'):
                bad.append(f'{t} (LFS pointer)')
    return bad


_missing_county_tables = _county_tables_missing()
if _missing_county_tables:
    log.error(
        f"REFERENCE TABLES MISSING OR NOT PULLED ({len(_missing_county_tables)}/{len(_COUNTY_TABLES)}): "
        f"{_missing_county_tables} - run `git lfs pull` (see reference_data/README_county_tables.md); "
        f"every field they serve falls back to division/RECS/ladder behavior while stamping "
        f"RESOLVER_VERSION {RESOLVER_VERSION}.")

# Master switch for the national-stock dynamic-default resolver. Read at call
# time (not module-import time) so a per-request override mutating
# os.environ['URBANOPT_DYNAMIC_DEFAULTS'] inside start_simulation() / a worker
# actually takes effect. When false (the conservative default), get_param
# skips resolve_default() entirely and falls back to the flat
# SIM_PARAM_DEFAULTS -- preserving the pre-dynamic behavior for existing
# assets. Mirror the same env var on the API side (simulationParamsSpec.js)
# so the frontend can surface "dynamic vs flat" default labels consistently.
_get_sim_flag = None  # lazy-bound below; the surrogate collector primes it with a file-loaded
# sim_flags so this module works outside the solver package (no modules.* stubs in sys.modules)


def _env_bool(name: str, default: bool = False) -> bool:
    # Per-sim thread-local override wins over the shared env, so concurrent sims
    # read their own flags (see modules.utils.sim_flags); fall back to os.environ
    # for the global/unset case, preserving the prior behavior.
    global _get_sim_flag
    if _get_sim_flag is None:
        from modules.utils.sim_flags import get_sim_flag
        _get_sim_flag = get_sim_flag
    raw = _get_sim_flag(name)
    if raw is None:
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
# HPXML duct-location literals (BuildResidentialHPXML choice list); the resolver maps
# ResStock's six location labels onto these at draw time (CR WP-R1).
HEATING_EQUIPMENT_DETAILS = ('Furnace', 'WallFurnace', 'Boiler', 'ElectricResistance',
                             'air-to-air', 'mini-split', 'ground-to-air')

DUCT_LOCATIONS = ('conditioned space', 'attic', 'crawlspace',
                  'basement - conditioned', 'basement - unconditioned', 'garage')

TIME_FIELDS = ('weekday_start_time', 'weekday_duration',
               'weekend_start_time', 'weekend_duration')

# The ceiling height a residential building simulates at when floor_height is left unset: URBANopt's residential
# mapper sets geometry_average_ceiling_height = maximum_roof_height / stories with maximum_roof_height defaulting to
# 8 ft per story (urbanopt-cli 1.0.1 example_files/mappers/residential/util.rb:77,179), and our features carry no
# maximum_roof_height. PowerTwin.rb passes only an EXPLICIT floor_height to a house, so an unset house gets this
# value from get_param (feature file and preview alike), not the flat 9 ft (2026-09-29, 2026-09-30).
RESIDENTIAL_UNSET_CEILING_HEIGHT_FT = 8.0

# Fields a user can no longer set (owner 2026-09-29, Insulation Presets Plan): the insulation tier only ever labelled
# the R-value (PowerTwin.rb has read only r_value since 2026-06-08), so a saved tier is ignored and the label always
# resolves from the building's R draw, as for an unedited building. Stored values stay valid for validate_metadata.
RETIRED_INPUT_FIELDS = ('wall_material', 'roof_material')
HPWH = 'heat pump water heater'     # electric by definition; see get_param (2026-09-30)

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
    'floor_height': 9.0,             # last resort only: commercial resolves the prototype's typical story (3.6 m outside its domain), houses 8 ft
    'number_of_occupants': '',

    # Heating equipment detail channel (CR WP-R2): the county donor carries the
    # native HPXML equipment class and the home's duct truth; empty means the
    # coarse enum + type-based duct guess stand alone (division fallback path).
    'heating_equipment_detail': '',
    'heating_has_ducts': '',

    # Duct priors (CR WP-R1): empty means omit so HPXML's own defaults apply when
    # dynamic defaults are off; resolved values come from resstock2024_county_ducts.
    'duct_location': '',
    'duct_leakage_pct': '',
    'duct_insulation_r': '',

    # Appliance fuels + misc gas loads (CR WP-R4): empty means omit (HPXML
    # defaults apply). Drawn from ONE county donor keyed by gas availability.
    'clothes_dryer_fuel': '',
    'cooking_range_fuel': '',
    'misc_gas_grill': '',
    'misc_gas_lighting': '',
    'misc_gas_fireplace': '',

    # Cooling saturation (CR WP-R5): empty means legacy behavior (fuel-driven
    # central AC). Applies only to non-heat-pump homes (donor excludes HP rows).
    'cooling_type_detail': '',
    'cooling_fraction_served': '',

    # Geometry (CR WP-R5): foundation vocabulary matches the vendored util.rb
    # feature values; garage_cars 0 = no garage. Empty = legacy paths stand
    # (region-modal foundation, garage_rate hash).
    'foundation_type': '',
    'garage_cars': '',

    # Household size (CR WP-R5): per-unit bedrooms from the county household
    # donor (joint with occupants). Empty means the area/700 formula stands.
    'bedrooms_per_unit': '',

    # Rooftop PV (CR WP-R6): empty means no PV (the overwhelming majority).
    'pv_system_kw': '',
    'pv_azimuth_deg': '',

    # Commercial thermostat deltas + airtightness (CR WP-C2): empty means omit
    # (measures stay __SKIP__). Deltas vs DOE prototype 70/75 occupied baseline;
    # airtightness m3/(h*m2) at 75 Pa verbatim for set_nist_infiltration_correlations.
    'com_heating_adjustment_f': '',
    'com_cooling_adjustment_f': '',
    'com_airtightness_75pa': '',

    # Setpoints + infiltration (CR WP-R3): empty means omit (mapper falls back to
    # its cited literals / HPXML defaults). Occupied-stock setpoints drawn jointly;
    # infiltration is whole-dwelling ACH at 50 Pa, passed through unconverted.
    'heating_setpoint_f': '',
    'cooling_setpoint_f': '',
    'infiltration_ach50': '',

    # Time strings (empty means omit from feature.json so template default applies)
    'weekday_start_time': '',
    'weekday_duration': '',
    'weekend_start_time': '',
    'weekend_duration': '',
}

# Range bounds for numeric fields. Out-of-range values are clamped and a warning
# logged. Keep ranges in sync with the corresponding entries in the Node spec.
NUMERIC_RANGES = {
    'duct_leakage_pct': (0.0, 50.0),
    'duct_insulation_r': (0.0, 20.0),
    'cooling_fraction_served': (0.0, 1.0),
    'garage_cars': (0.0, 3.0),
    'bedrooms_per_unit': (1.0, 10.0),
    'pv_system_kw': (0.5, 50.0),
    'pv_azimuth_deg': (0.0, 359.0),
    'com_heating_adjustment_f': (-30.0, 20.0),
    'com_cooling_adjustment_f': (-15.0, 20.0),
    'com_airtightness_75pa': (1.0, 100.0),
    'heating_setpoint_f': (45.0, 85.0),
    'cooling_setpoint_f': (60.0, 90.0),
    'infiltration_ach50': (0.5, 75.0),
    'window_to_wall_ratio': (0.0, 0.8),
    'wall_r_value':         (0.0, 80.0),   # 0.0 = Uninsulated (county insulation draws)
    'roof_r_value':         (0.0, 80.0),
    'floor_height':         (6.0, float('inf')),   # no maximum (owner 2026-10-01: a cap only blocked real tall
                                                   # buildings); the 6 ft minimum catches a metres entry (3.6)
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
        # NOTE (Q/A R-002): the 15 'Residential - *' values from urbanopt's building_properties
        # schema are deliberately EXCLUDED: system_type is consumed only by the commercial
        # create_typical_building_from_model measure, whose choice list rejects them -- they
        # validated here, then failed at measure run time. Residential HVAC comes from
        # heating_system_type/fuel via the HPXML path instead.
    },
    'heating_system_type':             {'heat_pump', 'furnace', 'boiler', 'electric_resistance', 'wood_stove'},
    'heating_equipment_detail':        {'Furnace', 'WallFurnace', 'Boiler', 'ElectricResistance', 'air-to-air', 'mini-split', 'ground-to-air'},
    'heating_has_ducts':               {'Yes', 'No'},
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
# CR WP-R4 appliance channels accept explicit metadata too
ENUM_VALUES['cooling_type_detail'] = {'central air conditioner', 'room air conditioner', 'none'}
# duct_location metadata gate (review finding 7: it previously passed unvalidated)
ENUM_VALUES['duct_location'] = set(DUCT_LOCATIONS)
ENUM_VALUES['foundation_type'] = {'slab', 'crawlspace - vented', 'crawlspace - unvented',
                                  'crawlspace - conditioned', 'basement - unconditioned',
                                  'basement - conditioned', 'ambient'}
ENUM_VALUES['clothes_dryer_fuel'] = {'electricity', 'natural gas', 'propane', 'none'}
ENUM_VALUES['cooking_range_fuel'] = {'electricity', 'natural gas', 'propane', 'none'}
ENUM_VALUES['misc_gas_grill'] = {'Yes', 'No'}
ENUM_VALUES['misc_gas_lighting'] = {'Yes', 'No'}
ENUM_VALUES['misc_gas_fireplace'] = {'Yes', 'No'}

RESOLVER_ENUM_VALUES = {key: set(vals) for key, vals in ENUM_VALUES.items()}
RESOLVER_ENUM_VALUES['heating_system_fuel_type'] |= {'district steam'}
RESOLVER_ENUM_VALUES['service_water_heating_fuel_type'] |= {'district steam'}
RESOLVER_ENUM_VALUES['cooling_system_fuel_type'] |= {'none'}
# CR WP-C1: ComStock in.hvac_system_type labels -> urbanopt's system_type enum.
# 24 of 42 labels are exact enum members; the rest map to the nearest enum member
# PRESERVING HEATING FUEL first (gas/electric/district), then plant class. Every
# ComStock label is mapped and every target is asserted inside ENUM_VALUES
# ['system_type'] by test_regression_guards. Weights cited in tmp/cr notes.
COMSTOCK_HVAC_KEY = {
    # exact enum members (pass-through)
    'PSZ-AC with gas coil': 'PSZ-AC with gas coil',
    'PSZ-HP': 'PSZ-HP',
    'PTHP': 'PTHP',
    'PVAV with gas heat with electric reheat': 'PVAV with gas heat with electric reheat',
    'PVAV with PFP boxes': 'PVAV with PFP boxes',
    'PTAC with electric coil': 'PTAC with electric coil',
    'PTAC with gas coil': 'PTAC with gas coil',
    'PTAC with gas boiler': 'PTAC with gas boiler',
    'VAV chiller with gas boiler reheat': 'VAV chiller with gas boiler reheat',
    'VAV air-cooled chiller with gas boiler reheat': 'VAV air-cooled chiller with gas boiler reheat',
    'VAV chiller with PFP boxes': 'VAV chiller with PFP boxes',
    'DOAS with VRF': 'DOAS with VRF',
    'VAV district chilled water with district hot water reheat': 'VAV district chilled water with district hot water reheat',
    'DOAS with fan coil air-cooled chiller with boiler': 'DOAS with fan coil air-cooled chiller with boiler',
    'DOAS with water source heat pumps with ground source heat pump': 'DOAS with water source heat pumps with ground source heat pump',
    'Direct evap coolers with forced air furnace': 'Direct evap coolers with forced air furnace',
    'PSZ-AC with district hot water': 'PSZ-AC with district hot water',
    'Gas unit heaters': 'Gas unit heaters',
    'DOAS with fan coil chiller with district hot water': 'DOAS with fan coil chiller with district hot water',
    'VAV air-cooled chiller with district hot water reheat': 'VAV air-cooled chiller with district hot water reheat',
    'DOAS with fan coil district chilled water with district hot water': 'DOAS with fan coil district chilled water with district hot water',
    'DOAS with fan coil district chilled water with baseboard electric': 'DOAS with fan coil district chilled water with baseboard electric',
    'VAV district chilled water with gas boiler reheat': 'VAV district chilled water with gas boiler reheat',
    'Baseboard electric': 'Baseboard electric',
    'Direct evap coolers with baseboard gas boiler': 'Direct evap coolers with baseboard gas boiler',
    'DOAS with fan coil air-cooled chiller with district hot water': 'DOAS with fan coil air-cooled chiller with district hot water',
    'Direct evap coolers with baseboard electric': 'Direct evap coolers with baseboard electric',
    # nearest-member remaps (fuel preserved; comments give the delta accepted)
    'PSZ-AC with electric coil': 'PSZ-AC with baseboard electric',        # electric resistance heat, zone vs coil
    'Residential AC with residential forced air furnace': 'PSZ-AC with gas coil',  # DX cooling + gas furnace, rooftop analog
    'PSZ-AC with gas boiler': 'PSZ-AC with gas coil',                     # gas heat kept; hydronic -> coil
    'PVAV with gas boiler reheat': 'PVAV with gas heat with electric reheat',  # gas primary kept; reheat electrified
    'PSZ-AC district chilled water with electric coil': 'PSZ-AC with baseboard electric',  # electric heat kept; district CHW dropped
    'Residential forced air furnace': 'Forced air furnace',               # heat-only furnace; builds as ComStock's (MEASURE_NATIVE_SYSTEMS)
    'VAV air-cooled chiller with PFP boxes': 'VAV chiller with PFP boxes',  # electric reheat kept; chiller condenser type
    'VAV district chilled water with PFP boxes': 'VAV chiller with PFP boxes',  # electric reheat kept; district CHW dropped
    'VAV chiller with district hot water reheat': 'VAV chiller with gas boiler reheat',  # chiller kept; district -> gas hydronic
    'DOAS with fan coil chiller with boiler': 'Fan coil chiller with boiler',  # plants kept; DOAS dropped
    'DOAS with water source heat pumps cooling tower with boiler': 'Fan coil chiller with boiler',  # electric cooling + gas boiler kept
    'DOAS with fan coil chiller with baseboard electric': 'DOAS with fan coil district chilled water with baseboard electric',  # electric heat + DOAS kept
    'DOAS with fan coil air-cooled chiller with baseboard electric': 'DOAS with fan coil district chilled water with baseboard electric',
    'PVAV with district hot water reheat': 'PVAV with gas heat with electric reheat',  # air side kept; district -> gas
    'DOAS with fan coil district chilled water with boiler': 'DOAS with fan coil district chilled water and boiler',  # enum spells 'and'
}

# CR WP-C1: system_type IS resolver-produced now (county ComStock shares mapped
# through COMSTOCK_HVAC_KEY). The gate is the FULL enum: this dict also gates
# EXPLICIT metadata (get_param's D-26 path), so narrowing it to the mapped
# subset would reject legitimate user-set enum values. Mapping targets living
# inside the enum is pinned by its own regression guard instead.
RESOLVER_ENUM_VALUES['system_type'] = set(ENUM_VALUES['system_type'])

# ComStock labels the create_typical measure builds as they are (openstudio-standards cbecs_hvac) although
# urbanopt-geojson's system_type enum lacks them: the feature carries the enum member above (the schema rejects
# anything else) and comstock_system_type, which PowerTwin.rb passes to the measure instead. 'Residential forced air
# furnace' is an unventilated furnace; its remap target 'Forced air furnace' adds a 100% outdoor-air intake downstream
# of the furnace, which 90.1 then gives an energy recovery ventilator whose pretreat setpoint references a mixed-air
# node nothing controls (EnergyPlus checkSetpointNodesAtEnd fatal; 7 of 20 such Teton vacant buildings, 2026-10-01).
MEASURE_NATIVE_SYSTEMS = {'Residential forced air furnace'}


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
    # Validate against RESOLVER_ENUM_VALUES (everything the downstream toolchain can map):
    # explicit 'district steam' heating/SWH and cooling 'none' are documented API levers,
    # and strict ENUM_VALUES would reject them at the boundary (district-steam e2e).
    for key, valid in ENUM_VALUES.items():
        val = metadata.get(key)
        if val in (None, '') or key in RETIRED_INPUT_FIELDS:
            continue    # a retired input is ignored, so an odd stored value must not 400 every re-simulation
        allowed = RESOLVER_ENUM_VALUES.get(key, valid)
        if val not in allowed:
            errors.append(f"invalid {key}={val!r} (must be one of: {sorted(allowed)})")
    for key in TIME_FIELDS:
        val = metadata.get(key)
        if val in (None, ''):
            continue
        if not _TIME_RE.match(str(val)):
            errors.append(f"invalid {key}={val!r} (must be HH:MM, 00:00-23:59)")
    # vacant was previously unvalidated (Q/A R-006) while its consumer's old `is True` check
    # silently mis-simulated stringly-typed values. Accept only bool-like encodings so a bad
    # value is rejected at the API boundary instead of surprising the vacancy model.
    val = metadata.get('vacant')
    if val not in (None, '', True, False, 'true', 'True', 'TRUE', 'false', 'False', 'FALSE',
                   0, 1, '0', '1'):
        errors.append(f"invalid vacant={val!r} (must be a boolean or 'true'/'false')")
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


@functools.lru_cache(maxsize=1)
def _subtype_effective_names() -> dict:
    """Subtype label -> the label of its effective_id row, from upload/asset_subtypes.csv
    ('Single-Family' -> 'Single-Family Detached', 'Multifamily (2 to 4 units)' -> 'Multifamily')."""
    path = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'upload', 'asset_subtypes.csv')
    rows = {}
    with open(path) as fh:
        for row in csv.DictReader(fh):
            rows[int(row['id'])] = row
    return {r['name']: rows[int(r['effective_id'])]['name'] for r in rows.values() if int(r['effective_id']) in rows}


def _effective_building_type(bt):
    """Normalise a subtype label through the effective_id remap so every caller resolves the
    same building type generateFeatureFile simulates. The modal's defaults preview passed the raw
    label ('Single-Family', id 4) straight in; it is not in RESIDENTIAL_BUILDING_TYPES, so every
    residential resolver took the non-residential path and the hints showed natural gas and no
    system type while the simulation (remapped to 'Single-Family Detached') was right
    (asset 448523 review, 2026-09-21). Unknown labels pass through unchanged."""
    if not isinstance(bt, str):
        return bt
    return _subtype_effective_names().get(bt, bt)


# Explicit choices that only confirm the resolved default are not edits (build_asset_ctx; generateFeatureFile).
CONFIRMABLE_FIELDS = ('heating_system_type', 'water_heater_type', 'system_type')


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
    bt = _effective_building_type(building_type if building_type is not None else metadata.get('building_type'))
    cz = climate_zone if climate_zone is not None else metadata.get('climate_zone')
    # Scenario fan-out and version-matched A/B (2026-09-19): a metadata `stochastic_seed_id`
    # declares whose draws this asset shares. Every share-weighted draw hashes on the ctx
    # building_id, so a variant minted under a new geometry id would otherwise redraw fuel,
    # system, vacancy and envelope and the lever effect would be buried under new draws. The
    # declared seed wins over the geometry id; absent, behavior is unchanged.
    seed = metadata.get('stochastic_seed_id')
    if seed not in (None, ''):
        bid = str(seed)
    else:
        bid = building_id if building_id is not None else metadata.get('building_id')
    ctx = {
        'building_type': bt,
        'state': state,
        'region': region,
        'division': division,
        'year_built': metadata.get('year_built'),  # raw year for CR county tables (WP-R1+)
        'vintage': _vintage_bin(metadata.get('year_built')),
        'decade_vintage': _decade_vintage_bin(metadata.get('year_built')),
        'area': metadata.get('area') or metadata.get('floor_area'),
        'bedrooms': metadata.get('bedrooms') or metadata.get('number_of_bedrooms'),
        'floor_count': _coerce_int(metadata.get('floor_count')),
        'units': _coerce_int(metadata.get('number_of_units') or metadata.get('units') or metadata.get('number_of_residential_units')),
        'climate_zone': cz,
        'building_id': bid,
        'county_fips': county_fips,
        # Explicit system/heater overrides ride in the ctx so the FUEL resolvers can condition on
        # what the user actually chose instead of a redraw (Q/A R-037). Enum-validated at use.
        'heating_system_type': metadata.get('heating_system_type'),
        'water_heater_type': metadata.get('water_heater_type'),
        # CR review fix (finding 10): the CR-era guards read these ctx keys; without
        # forwarding they were dead code and donor arms redrew fuels the user pinned.
        'heating_system_fuel_type': metadata.get('heating_system_fuel_type'),
        'cooling_system_fuel_type': metadata.get('cooling_system_fuel_type'),
        'foundation_type': metadata.get('foundation_type'),
        'system_type': metadata.get('system_type'),
    }
    # A heating_system_type equal to the one the resolver picks without it only confirms the house's
    # system (a user re-selecting the default the modal showed). Conditioning on it re-drew the fuel
    # and dropped the county equipment detail, so the no-op edit rebuilt the house (2026-09-24:
    # 'furnace' on an electric wall-furnace home became a ducted gas furnace). Every resolver then
    # serves the unedited house, and generateFeatureFile leaves it out of explicit_params.
    # The same holds for the water heater: re-picking the previewed 'storage water heater' conditioned the SWH
    # fuel on it and bypassed the county donor, so the fuel redrew (review 2026-09-28: a Fairfield CT 1950 home
    # went from electricity to fuel oil). Heating first, so the water heater is compared in the ctx the sim uses.
    for field in CONFIRMABLE_FIELDS:
        v = ctx[field]
        if v in ENUM_VALUES.get(field, set()) and resolve_default(field, dict(ctx, **{field: None}))[0] == v:
            ctx[field] = None
            ctx[f'{field}_confirms_default'] = True
    return ctx


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
FALLBACK_LEVELS = ('county_acs', 'county_county', 'county_puma', 'county_division', 'division_vintage', 'division_all', 'vintage_specific', 'region_all', 'building_type_only', 'flat_default')


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


# RECS 2020 microdata (NWEIGHT-weighted): conditioned ft^2 per bedroom = 695 for
# single-family detached, 655 across all homes (Q/A WP-08 recomputation). The
# old value of 800 under-counted bedrooms (and so occupants) by ~15%.
SQFT_PER_BEDROOM = 700

_RESSTOCK_COOLING_MAP = {'Central AC': 'central air conditioner',
                         'Room AC': 'room air conditioner', 'None': 'none'}


def _cooling_donor(ctx):
    """County cooling donor (WP-R5), NON-heat-pump homes only (ResStock ties HP
    cooling to HP heating deterministically; the WP-R2 heating donor owns that).
    Joint (type, partial-conditioning) per building, salt cooling_donor."""
    if ctx.get('building_type') not in RESIDENTIAL_BUILDING_TYPES:
        return (None, None)
    for cell, tier in _county_prior_tiers('resstock2024_county_cooling', ctx.get('county_fips')):
        vb = _duct_vintage_bin(ctx.get('year_built')) or 'all'
        sub = cell.get(vb) or cell.get('all')
        if not sub or not sub.get('donors'):
            continue
        donors = [(d, float(d.get('w', 0))) for d in sub['donors']]
        return (_draw_donor(donors, ctx, 'cooling_donor'), tier)
    return (None, None)


def _resolve_cooling(field, ctx):
    # CR review fix (finding 3): an explicit no-cooling lever must not be
    # overridden by the county donor (the mapper's guard runs BEFORE the donor
    # block, so without this the donor re-enabled AC on de-cooled homes).
    if str(ctx.get('cooling_system_fuel_type') or '').lower() == 'none':
        return (None, None)
    donor, tier = _cooling_donor(ctx)
    if not donor:
        return (None, None)
    level = f'county_{tier}'
    if field == 'cooling_type_detail':
        mapped = _RESSTOCK_COOLING_MAP.get(str(donor.get('ct')))
        return ((mapped, level) if mapped else (None, None))
    pf = str(donor.get('pf'))
    m = re.match(r'^\s*(\d+)% Conditioned', pf)
    if m:
        return (round(float(m.group(1)) / 100.0, 2), level)
    if pf.startswith('<10%'):
        # ResStock's smallest partial-conditioning band; without this the room-AC
        # donor emitted a type but no fraction and HPXML defaulted to 100% served.
        return (0.1, level)
    return (None, None)  # 'None' partial label rides with cooling none


_RESSTOCK_FOUNDATION_MAP = {
    'Slab': 'slab', 'Vented Crawlspace': 'crawlspace - vented',
    'Unvented Crawlspace': 'crawlspace - unvented',
    'Unheated Basement': 'basement - unconditioned',
    'Heated Basement': 'basement - conditioned', 'Ambient': 'ambient'}
_RESSTOCK_GARAGE_CARS = {'None': 0, '1 Car': 1, '2 Car': 2, '3 Car': 3}


def _geometry_donor(ctx):
    """County (foundation, garage) donor (WP-R5): one home's geometry, so the
    foundation feeding the WP-R1 duct remap and the garage stay coherent."""
    if ctx.get('building_type') not in RESIDENTIAL_BUILDING_TYPES:
        return (None, None)
    for cell, tier in _county_prior_tiers('resstock2024_county_geometry', ctx.get('county_fips')):
        sub = cell.get('all')
        if not sub or not sub.get('donors'):
            continue
        donors = [(d, float(d.get('w', 0))) for d in sub['donors']]
        return (_draw_donor(donors, ctx, 'geometry_donor'), tier)
    return (None, None)


def _resolve_geometry(field, ctx):
    donor, tier = _geometry_donor(ctx)
    if not donor:
        return (None, None)
    level = f'county_{tier}'
    if field == 'foundation_type':
        mapped = _RESSTOCK_FOUNDATION_MAP.get(str(donor.get('fnd')))
        return ((mapped, level) if mapped else (None, None))
    cars = _RESSTOCK_GARAGE_CARS.get(str(donor.get('gar')))
    return ((cars, level) if cars is not None else (None, None))


_AREA_BINS = ((500, '0-499'), (750, '500-749'), (1000, '750-999'), (1500, '1000-1499'),
              (2000, '1500-1999'), (2500, '2000-2499'), (3000, '2500-2999'),
              (4000, '3000-3999'))


def _area_bin(unit_area):
    for edge, label in _AREA_BINS:
        if unit_area < edge:
            return label
    return '4000+'


def _household_donor(ctx):
    """County household donor (WP-R5): joint (bedrooms, occupants) conditioned on
    the DWELLING-UNIT area bin, occupied stock only. Replaces the area/700
    bedrooms formula and bedrooms+1 occupants heuristic when a tier serves."""
    if ctx.get('building_type') not in RESIDENTIAL_BUILDING_TYPES:
        return (None, None)
    area = ctx.get('area')
    if not area:
        return (None, None)
    units = max(1, ctx.get('units') or 1)
    ab = _area_bin(float(area) / units)
    for cell, tier in _county_prior_tiers('resstock2024_county_household', ctx.get('county_fips')):
        sub = cell.get(ab)
        if not sub or not sub.get('donors'):
            continue
        donors = [(d, float(d.get('w', 0))) for d in sub['donors']]
        return (_draw_donor(donors, ctx, 'household_donor'), tier)
    return (None, None)


def _resolve_occupants(ctx: dict) -> int | None:
    """Composite occupants resolver:
        residential: HPXML/Manual J convention -> bedrooms + 1, PER dwelling unit
                     (bedrooms inferred from per-unit area at ~700 ft^2/br when absent)
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
            # CR WP-R5: county household donor first - realized OCCUPANTS directly
            # (joint with bedrooms), not the bedrooms+1 convention, which overstates
            # small households (RECS: 1-2 person homes dominate every bedroom count).
            donor, _tier = _household_donor(ctx)
            if donor:
                try:
                    return max(1, int(float(donor.get('oc'))))
                except (TypeError, ValueError):
                    pass
            area = ctx.get('area')
            if area:
                bedrooms = max(1, round(float(area) / units / SQFT_PER_BEDROOM))
            else:
                bedrooms = 3  # RECS 2020 modal SFD bedroom count (50.4%; '2' is 13.7%)
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
    # Q/A R-035: furnaces/boilers are NOT pure combustion -- RECS puts electricity at up
    # to 64% of a division-vintage's furnaces (ESC 2020+). Draw electricity first at the
    # reconciled electric fraction (the same arithmetic that set the system-type shares,
    # so mass is conserved across the two draws), then split the remainder over the
    # combustion marginal as before.
    parts = _reconcile_internals(ctx)
    e_frac = parts.get('furn_e_frac' if sys_type == 'furnace' else 'boil_e_frac', 0.0) if parts else 0.0
    if e_frac > 0:
        record = {'shares': {'electricity': round(e_frac, 4), '_combustion': round(1 - e_frac, 4)},
                  'mode': 'electricity' if e_frac >= 0.5 else '_combustion'}
        pick = _select_fuel_by_share(record, ctx, salt='heating_system_fuel_type_elec')
        if pick == 'electricity':
            return ('electricity', 'division_vintage')
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


# --- CR WP-R4: county water-heater + appliance donors (gas-line coherent) ---

def _parse_wh_label(label):
    """ResStock in.water_heater_efficiency -> (swh fuel, water_heater_type).
    One label carries both ('Natural Gas Tankless' -> gas + instantaneous)."""
    s = str(label)
    if s.startswith('Electric Heat Pump'):
        return ('electricity', 'heat pump water heater')
    if s == 'FIXME Fuel Oil Indirect':
        return ('fuel oil', 'storage water heater')
    if s == 'Other Fuel':
        return ('wood', 'storage water heater')
    for prefix, fuel in (('Natural Gas', 'natural gas'), ('Electric', 'electricity'),
                         ('Propane', 'propane'), ('Fuel Oil', 'fuel oil')):
        if s.startswith(prefix):
            rest = s[len(prefix):].strip()
            wtype = ('instantaneous water heater' if rest == 'Tankless'
                     else 'storage water heater')
            return (fuel, wtype)
    return (None, None)


def _legacy_sys_type(ctx):
    """Coarse heating system for conditioning: explicit metadata else legacy draw."""
    explicit_sys = ctx.get('heating_system_type')
    if explicit_sys in ENUM_VALUES.get('heating_system_type', set()):
        return explicit_sys
    return _resolve_heating_system_type(ctx)[0]


def _draw_donor(donors, ctx, salt):
    """Shared cumulative hash draw over [(donor, w)]; modal when stochastic off."""
    if not is_stochastic_sampling_enabled() or not ctx.get('building_id'):
        return max(donors, key=lambda t: t[1])[0]
    frac = _building_id_hash_fraction(ctx, salt=salt)
    acc = 0.0
    for d, w in donors:
        acc += w
        if frac <= acc:
            return d
    return donors[-1][0]


def _wh_donor(ctx):
    """County water-heater donor, keyed by the home's HEATING gas arm so
    P(gas SWH | gas heat) survives without a resolver cycle."""
    if ctx.get('building_type') not in RESIDENTIAL_BUILDING_TYPES:
        return (None, None)
    hf, _ = _effective_heating_fuel(ctx, _legacy_sys_type(ctx))
    arm = 'gas' if hf == 'natural gas' else 'nogas'
    for cell, tier in _county_prior_tiers('resstock2024_county_waterheater', ctx.get('county_fips')):
        sub = cell.get(arm)
        if not sub or not sub.get('donors'):
            continue
        donors = [(d, float(d.get('w', 0))) for d in sub['donors']]
        return (_draw_donor(donors, ctx, 'wh_donor'), tier)
    return (None, None)


_APPLIANCE_FUEL_MAP = {'Electric': 'electricity', 'Electric Resistance': 'electricity',
                       'Electric Induction': 'electricity', 'Gas': 'natural gas',
                       'Propane': 'propane', 'None': 'none'}
_APPLIANCE_KEYS = {'clothes_dryer_fuel': 'dr', 'cooking_range_fuel': 'rg',
                   'misc_gas_grill': 'gr', 'misc_gas_lighting': 'gl',
                   'misc_gas_fireplace': 'gf'}


def _appliance_donor(ctx):
    """County appliance donor (dryer/range/grill/lighting/fireplace from ONE
    building), keyed by gas availability (heating OR SWH fuel natural gas) -
    the measured coupling is 10x (gas dryer 24.9% vs 2.2%)."""
    if ctx.get('building_type') not in RESIDENTIAL_BUILDING_TYPES:
        return (None, None)
    sys_type = _legacy_sys_type(ctx)
    hf, _ = _effective_heating_fuel(ctx, sys_type)
    sf, _ = _resolve_swh_fuel(ctx, sys_type)
    arm = 'gas' if 'natural gas' in (hf, sf) else 'nogas'
    for cell, tier in _county_prior_tiers('resstock2024_county_appliances', ctx.get('county_fips')):
        sub = cell.get(arm)
        if not sub or not sub.get('donors'):
            continue
        donors = [(d, float(d.get('w', 0))) for d in sub['donors']]
        return (_draw_donor(donors, ctx, 'appliance_donor'), tier)
    return (None, None)


def _resolve_appliance(field, ctx):
    donor, tier = _appliance_donor(ctx)
    if not donor:
        return (None, None)
    raw = donor.get(_APPLIANCE_KEYS[field])
    level = f'county_{tier}'
    if field in ('clothes_dryer_fuel', 'cooking_range_fuel'):
        mapped = _APPLIANCE_FUEL_MAP.get(str(raw))
        return (mapped, level) if mapped else (None, None)
    # misc gas channels: ResStock stores 'None' or the named load ('Gas Grill')
    return ('No' if str(raw) == 'None' else 'Yes', level)


def _effective_heating_fuel(ctx, sys_type):
    """The fuel this home is ACTUALLY heated with: county donor first (identical
    precedence to resolve_default's heating_system_fuel_type route, explicit
    metadata system outranking the donor), legacy reconciliation fallback.
    Exists so DOWNSTREAM conditioning (SWH fuel) sees the same fuel the heating
    channel emits -- conditioning on an independent legacy draw decorrelates the
    two gas assignments and inflated Teton's gas-anything union 28% -> 41%
    (caught by the tetonr1 sim gate 2026-09-04)."""
    explicit_fuel = ctx.get('heating_system_fuel_type')
    if explicit_fuel in ENUM_VALUES.get('heating_system_fuel_type', set()):
        # CR review fix (finding 10): a metadata-pinned fuel outranks any redraw -
        # the donor arms (SWH/water-heater/appliances) key on THIS fuel, and
        # redrawing it re-created the decorrelation class this campaign fixed.
        return (explicit_fuel, 'building_type_only')
    explicit_sys = ctx.get('heating_system_type')
    if explicit_sys not in ENUM_VALUES.get('heating_system_type', set()):
        hd = _heating_donor(ctx)
        if hd:
            parsed = _parse_heating_donor_label(
                hd['donor'].get('in.hvac_heating_type_and_fuel'))
            if parsed:
                return (parsed[0], f"county_{hd['tier']}")
    return _resolve_heating_fuel(ctx, sys_type)


def _resolve_swh_fuel(ctx, sys_type):
    """Service-water-heating fuel, conditioned on the building's resolved HEATING FUEL via
    RECS P(FUELH2O | FUELHEAT) -- so a gas-heated home gets gas SWH at the observed ~84%
    rate. (Conditioning on the heating SYSTEM TYPE alone, as before, averaged over gas/
    propane/oil furnaces and collapsed to ~the marginal, giving only ~48% gas SWH for
    gas-heated homes.) A heat pump water heater is electric by definition (resolved on its
    own hash salt). Returns (fuel, level)."""
    explicit_wh = ctx.get('water_heater_type')
    if explicit_wh in ENUM_VALUES.get('water_heater_type', set()):
        wh_type = explicit_wh  # explicit override drives the fuel conditioning (Q/A R-037)
    else:
        # CR WP-R4: county-first, and fuel + type come from the SAME donor label
        # (the draw _resolve_water_heater_type makes), so the pair is coherent
        # by construction; RECS conditional below stays the fallback.
        donor, tier = _wh_donor(ctx)
        if donor:
            fuel, _wtype = _parse_wh_label(donor.get('whe'))
            if fuel:
                return (fuel, f'county_{tier}')
        wh_type, _ = _resolve_water_heater_type(ctx)
    if wh_type == 'heat pump water heater':
        return ('electricity', 'building_type_only')
    heating_fuel, hlevel = _effective_heating_fuel(ctx, sys_type)
    if heating_fuel:
        record = _load_ref('recs2020_swh_fuel_by_heating_fuel').get(heating_fuel)
        if record:
            return (_select_fuel_by_share(record, ctx, salt='service_water_heating_fuel_type'), hlevel)
    return (None, None)


_COMSTOCK_FUEL_MAP = {'NaturalGas': 'natural gas', 'Electricity': 'electricity',
                      'FuelOil': 'fuel oil', 'Propane': 'propane',
                      'DistrictHeating': 'district steam'}


def _com_fuel_donor(ctx):
    """County commercial fuel donor (WP-C1): heating + SWH fuel drawn JOINTLY
    per building (the WP-R2 decorrelation lesson - independent draws inflate
    the gas-anything union). Walks county -> division; CBECS stays below."""
    bt = ctx.get('building_type')
    if not bt or bt in RESIDENTIAL_BUILDING_TYPES:
        return (None, None)
    cs_type = _comstock_type(ctx)
    if not cs_type:
        return (None, None)
    for cell, tier in _county_prior_tiers('comstock2024_county_fuels', ctx.get('county_fips')):
        sub = cell.get(cs_type)
        if not sub or not sub.get('donors'):
            continue
        donors = [(d, float(d.get('w', 0))) for d in sub['donors']]
        if not is_stochastic_sampling_enabled() or not ctx.get('building_id'):
            return (max(donors, key=lambda t: t[1])[0], tier)
        frac = _building_id_hash_fraction(ctx, salt='com_fuel_donor')
        acc = 0.0
        for d, w in donors:
            acc += w
            if frac <= acc:
                return (d, tier)
        return (donors[-1][0], tier)
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
    # CR WP-C1: commercial heating/SWH fuel county-first from the ComStock joint
    # donor (heating + SWH from ONE building, preserving their correlation);
    # CBECS division keying below stays the terminal fallback.
    if not is_residential and field in ('heating_system_fuel_type',
                                        'service_water_heating_fuel_type'):
        donor, tier = _com_fuel_donor(ctx)
        if donor:
            key = 'hf' if field == 'heating_system_fuel_type' else 'wf'
            mapped = _COMSTOCK_FUEL_MAP.get(donor.get(key))
            if mapped:
                return (mapped, f'county_{tier}')
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
    # Residential heating/SWH fuel is conditioned on the heating system type. An EXPLICIT
    # metadata system type must drive this (Q/A R-037): the redraw used to ignore the override,
    # pairing a user's heat_pump with a resolver-drawn natural gas -- the exact impossible combo
    # the conditioning exists to prevent.
    if field in ('heating_system_fuel_type', 'service_water_heating_fuel_type') and is_residential:
        explicit_sys = ctx.get('heating_system_type')
        if explicit_sys in ENUM_VALUES.get('heating_system_type', set()):
            sys_type = explicit_sys
        else:
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



def _system_electric_fraction(ctx, sys_type):
    """P(electricity | sys_type, division, vintage) from the RECS fuel-by-system table.
    Q/A R-035: electric furnaces are up to 64% of a division-vintage's furnaces (ESC 2020+);
    treating furnace/boiler as pure combustion reassigned that mass to heat_pump at COP 3."""
    cond = _load_ref("recs2020_fuel_by_system_type").get(sys_type, {}).get("_division", {})
    division = ctx.get('division')
    if not division or division not in cond:
        return 0.0
    div_data = cond[division]
    decade = ctx.get('decade_vintage')
    record = div_data.get(decade) if (decade and decade in div_data) else div_data.get('all')
    if not record:
        return 0.0
    return float(record.get('shares', {}).get('electricity', 0) or 0)


def _reconcile_internals(ctx):
    """Shared arithmetic for the county-first reconciliation (system shares AND the furnace/
    boiler electric-fuel fractions must come from ONE computation or they drift). Returns None
    when no division data, else a dict of the reconciled pieces."""
    record, level = _raw_heating_system_record(ctx)
    if record is None:
        return None
    s = dict(record.get('shares', {}))
    hp = s.get('heat_pump', 0); er = s.get('electric_resistance', 0)
    furn = s.get('furnace', 0); boil = s.get('boiler', 0)
    fe = _system_electric_fraction(ctx, 'furnace')
    be = _system_electric_fraction(ctx, 'boiler')
    e_target = _heating_marginal_target(ctx, 'electricity')
    if e_target is None:
        # no marginal to reconcile against: raw shares, raw conditional fractions
        return {'level': level, 'raw': True, 'shares': s,
                'furn_e_frac': fe, 'boil_e_frac': be}
    w_target = _heating_marginal_target(ctx, 'wood')
    w_t = w_target if w_target is not None else s.get('wood_stove', 0)
    e_t = max(0.0, e_target)
    c_t = max(0.0, 1.0 - e_t - w_t)
    elec_mass = hp + er + furn * fe + boil * be
    comb_mass = furn * (1 - fe) + boil * (1 - be)
    new = {}
    if elec_mass > 0:
        k = e_t / elec_mass
        new['heat_pump'] = k * hp
        new['electric_resistance'] = k * er
        furn_e = k * furn * fe
        boil_e = k * boil * be
    else:
        new['heat_pump'] = 0.0
        new['electric_resistance'] = e_t
        furn_e = boil_e = 0.0
    new['wood_stove'] = w_t
    if comb_mass > 0:
        k = c_t / comb_mass
        furn_c = k * furn * (1 - fe)
        boil_c = k * boil * (1 - be)
    else:
        furn_c, boil_c = c_t, 0.0
    new['furnace'] = furn_e + furn_c
    new['boiler'] = boil_e + boil_c
    return {'level': level, 'raw': False, 'shares': new,
            'furn_e_frac': (furn_e / new['furnace']) if new['furnace'] > 0 else 0.0,
            'boil_e_frac': (boil_e / new['boiler']) if new['boiler'] > 0 else 0.0}


def _reconciled_system_shares(ctx):
    """THE county-first heating reconciliation -- the single place the system-type
    distribution is scaled so the resolved heating-fuel marginals match the
    authoritative source (ACS county when county_fips is known, else RECS
    division). Returns (record, level) with record['shares'] the reconciled
    distribution, or (None, None) when no division data exists.

      electric mass (heat_pump + electric_resistance + the electric fractions of
        furnace/boiler per RECS fuel-by-system, Q/A R-035) -> electricity marginal,
        partitioned proportionally;
      wood_stove -> wood marginal;
      combustion portions of furnace/boiler -> combustion remainder, proportional.
      A furnace/boiler share is the sum of its electric and combustion parts, so
      electric-furnace mass survives instead of inflating heat_pump at COP 3.

    The raw division distribution is returned unchanged when no marginal target
    is available (no county_fips and no division fuel-mix row)."""
    parts = _reconcile_internals(ctx)
    if parts is None:
        return (None, None)
    shares = parts['shares']
    return ({'shares': shares, 'mode': max(shares, key=shares.get)}, parts['level'])


def _resolve_water_heater_type(ctx: dict):
    """Water heater type: county-first from the WP-R4 water-heater donor (one
    label carries fuel AND type, so this stays coherent with the SWH fuel drawn
    from the same donor), RECS 2020 division + vintage as fallback."""
    bt = ctx.get('building_type')
    if bt not in RESIDENTIAL_BUILDING_TYPES:
        return (None, None)
    donor, tier = _wh_donor(ctx)
    if donor:
        _fuel, wtype = _parse_wh_label(donor.get('whe'))
        if wtype:
            return (wtype, f'county_{tier}')
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
    if is_residential:
        # CR WP-R5: county-first pane shares (same enum, same salt - only the
        # keying moves from region x vintage to county x vintage bin).
        cell, tier = _county_prior('resstock2024_county_windows', ctx.get('county_fips'))
        if cell is not None:
            vb = _duct_vintage_bin(ctx.get('year_built')) or 'all'
            sub = cell.get(vb) or cell.get('all')
            if sub:
                return (_select_fuel_by_share(sub, ctx, salt='window_type'),
                        f'county_{tier}')
    table = _load_ref('window_type_by_vintage')
    section = table.get('residential' if is_residential else 'commercial')
    if not section:
        return (None, None)
    record, level = _lookup_region_vintage(section, ctx, fallback_vintage=None)
    if record is None:
        return (None, None)
    return (_select_fuel_by_share(record, ctx, salt='window_type'), level)


def _decimal_hr_to_hhmm(v):
    """8.75 -> '08:45' (ComStock decimal hours -> create_typical HH:MM strings)."""
    h = int(v)
    m = int(round((float(v) - h) * 60))
    if m == 60:
        h, m = h + 1, 0
    return f'{h:02d}:{m:02d}'


_HOURS_DONOR_KEYS = {'weekday_start_time': 'wo', 'weekday_duration': 'wh',
                     'weekend_start_time': 'eo', 'weekend_duration': 'eh'}


def _hours_donor(ctx, cs_type):
    """County operating-window donor (WP-C1): one real ComStock building's joint
    weekday/weekend window for this building type. Tiers are walked until one
    carries the type (county cells only hold types with n >= 5)."""
    if not cs_type:
        return (None, None)
    for cell, tier in _county_prior_tiers('comstock2024_county_hours', ctx.get('county_fips')):
        sub = cell.get(cs_type)
        if not sub or not sub.get('donors'):
            continue
        donors = [(d, float(d.get('w', 0))) for d in sub['donors']]
        if not is_stochastic_sampling_enabled() or not ctx.get('building_id'):
            return (max(donors, key=lambda t: t[1])[0], tier)
        frac = _building_id_hash_fraction(ctx, salt='hours_donor')
        acc = 0.0
        for d, w in donors:
            acc += w
            if frac <= acc:
                return (d, tier)
        return (donors[-1][0], tier)
    return (None, None)


_PV_ORIENT_TO_AZIMUTH = {'North': 0, 'Northeast': 45, 'East': 90, 'Southeast': 135,
                         'South': 180, 'Southwest': 225, 'West': 270, 'Northwest': 315}


def _resolve_pv(field, ctx):
    """County rooftop PV (WP-R6): has_pv share draw (salt pv_present; ~0.9% of
    national stock), then one JOINT (size, orientation) donor among PV owners,
    walking tiers because thin county cells rarely carry PV donors."""
    if ctx.get('building_type') not in RESIDENTIAL_BUILDING_TYPES:
        return (None, None)
    cell, _tier = _county_prior('resstock2024_county_pv', ctx.get('county_fips'))
    if cell is None or not cell.get('has_pv'):
        return (None, None)
    if _select_fuel_by_share(cell['has_pv'], ctx, salt='pv_present') != 'Yes':
        return (None, None)
    for c2, t2 in _county_prior_tiers('resstock2024_county_pv', ctx.get('county_fips')):
        sys_cell = c2.get('sys')
        if not sys_cell or not sys_cell.get('donors'):
            continue
        donors = [(d, float(d.get('w', 0))) for d in sys_cell['donors']]
        donor = _draw_donor(donors, ctx, 'pv_donor')
        if field == 'pv_system_kw':
            m = re.match(r'^\s*(\d+(?:\.\d+)?)\s*kWDC\s*$', str(donor.get('sz')))
            return ((float(m.group(1)), f'county_{t2}') if m else (None, None))
        az = _PV_ORIENT_TO_AZIMUTH.get(str(donor.get('orient')))
        return ((float(az), f'county_{t2}') if az is not None else (None, None))
    return (None, None)


# CR review fix (finding 1): the comstock2024_* tables are keyed by RAW
# in.comstock_building_type names; keying lookups on _doe_ref_name silently
# missed offices/retail/malls (verified: 'Office' in a served county drew ZERO
# county levels). This map is the single translation; None = no ComStock analog
# (SuperMarket, Laboratory, ...) and the legacy CBECS/DOE fallback serves.
_DOE_TO_COMSTOCK = {
    'Retail': 'RetailStandalone', 'StripMall': 'RetailStripmall',
    'Strip shopping center': 'RetailStripmall',
    'FullServiceRestaurant': 'FullServiceRestaurant',
    'QuickServiceRestaurant': 'QuickServiceRestaurant',
    'Warehouse': 'Warehouse', 'PrimarySchool': 'PrimarySchool',
    'SecondarySchool': 'SecondarySchool', 'SmallHotel': 'SmallHotel',
    'LargeHotel': 'LargeHotel', 'Outpatient': 'Outpatient', 'Hospital': 'Hospital',
    'SmallOffice': 'SmallOffice', 'MediumOffice': 'MediumOffice',
    'LargeOffice': 'LargeOffice',
}


def _comstock_type(ctx):
    """ComStock table key for this commercial building, or None (legacy path)."""
    doe = _doe_ref_name(ctx.get('building_type'), ctx)
    if doe == 'Office':
        doe = _office_size_class(ctx)  # Small/Medium/LargeOffice ('Office' when area missing)
    return _DOE_TO_COMSTOCK.get(doe)


_COM_TSTAT_BASE_HTG = 70.0  # DOE prototype occupied heating setpoint (deg F)
_COM_TSTAT_BASE_CLG = 75.0  # DOE prototype occupied cooling setpoint (deg F)


def _com_tstat_cell(ctx, part):
    """Walk tiers of the WP-C2 table until one carries THIS part ('tstat' or
    'air') for the building type - a county whose entry is air-only (sentinel-
    filtered setpoints) must fall through to a deeper tier for thermostats."""
    bt = ctx.get('building_type')
    if not bt or bt in RESIDENTIAL_BUILDING_TYPES:
        return (None, None)
    cs_type = _comstock_type(ctx)
    if not cs_type:
        return (None, None)
    for cell, tier in _county_prior_tiers('comstock2024_county_tstat', ctx.get('county_fips')):
        sub = cell.get(cs_type)
        if sub and sub.get(part):
            return (sub, tier)
    return (None, None)


def _resolve_com_tstat(field, ctx):
    """Commercial thermostat DELTAS vs the DOE prototype 70/75 occupied baseline
    (WP-C2): one joint donor per building (salt com_tstat_donor) so heating and
    cooling shift together, delivered via AdjustThermostatSetpointsByDegrees."""
    sub, tier = _com_tstat_cell(ctx, 'tstat')
    if not sub or not sub.get('tstat', {}).get('donors'):
        return (None, None)
    donors = [(d, float(d.get('w', 0))) for d in sub['tstat']['donors']]
    donor = _draw_donor(donors, ctx, 'com_tstat_donor')
    try:
        if field == 'com_heating_adjustment_f':
            return (round(float(donor['th']) - _COM_TSTAT_BASE_HTG, 1), f'county_{tier}')
        return (round(float(donor['tc']) - _COM_TSTAT_BASE_CLG, 1), f'county_{tier}')
    except (KeyError, TypeError, ValueError):
        return (None, None)


def _resolve_com_airtightness(ctx):
    """Commercial envelope airtightness, m3/(h*m2) at 75 Pa, passed VERBATIM to
    set_nist_infiltration_correlations (its native units - zero conversion)."""
    sub, tier = _com_tstat_cell(ctx, 'air')
    if not sub or not sub.get('air'):
        return (None, None)
    label = _select_fuel_by_share(sub['air'], ctx, salt='com_airtightness')
    try:
        return (float(label), f'county_{tier}')
    except (TypeError, ValueError):
        return (None, None)


def _resolve_system_type(ctx):
    """County-first commercial HVAC system (WP-C1): ComStock share draw for this
    county x building type, mapped onto urbanopt's enum via COMSTOCK_HVAC_KEY.
    Residential buildings and unmapped draws fall through (template default)."""
    raw, tier = _draw_comstock_system(ctx)
    mapped = COMSTOCK_HVAC_KEY.get(raw)
    return (mapped, f'county_{tier}') if mapped else (None, None)


def comstock_native_system(ctx):
    """The ComStock label create_typical builds for a county-drawn system the enum maps elsewhere
    (MEASURE_NATIVE_SYSTEMS), or None. Never for a system_type the metadata sets."""
    if ctx.get('system_type') in ENUM_VALUES.get('system_type', set()) or not is_dynamic_defaults_enabled():
        return None
    raw, _tier = _draw_comstock_system(ctx)
    return raw if raw in MEASURE_NATIVE_SYSTEMS else None


def _draw_comstock_system(ctx):
    bt = ctx.get('building_type')
    if not bt or bt in RESIDENTIAL_BUILDING_TYPES:
        return (None, None)
    # District-steam coherence (unmasked by the keying fix): an explicit or
    # donor-drawn district-steam heating fuel needs the template path, which
    # honors htg_src and builds the district plant - a county system like
    # 'PSZ-AC with gas coil' would silently drop the steam stream (pinned by
    # the district-steam container e2e).
    if str(ctx.get('heating_system_fuel_type') or '').lower() == 'district steam':
        return (None, None)
    donor, _t = _com_fuel_donor(ctx)
    if donor and donor.get('hf') == 'DistrictHeating':
        return (None, None)
    cs_type = _comstock_type(ctx)
    if not cs_type:
        return (None, None)
    for cell, tier in _county_prior_tiers('comstock2024_county_system', ctx.get('county_fips')):
        sub = cell.get(cs_type)
        if not sub:
            continue
        return (_select_fuel_by_share(sub, ctx, salt='system_type'), tier)
    return (None, None)


def _resolve_operating_schedule(field: str, ctx: dict):
    """Weekday/weekend operating window. County-first (WP-C1): ComStock actual
    open-to-close windows via a per-building joint donor - real hours, so ALL
    types are servable, including the 24/7 and bimodal types the DOE-prototype
    fallback must suppress (its window is an occupancy-PEAK artifact). Fallback
    is the DOE Reference Building prototype table at 'building_type_only'. An
    empty string in the DOE table (e.g. School weekends) preserves the URBANopt
    template default by passing through to flat fallback at the caller."""
    bt = ctx.get('building_type')
    if not bt:
        return (None, None)
    doe_ref = _doe_ref_name(bt, ctx)
    # 24/7 stock stays suppressed even county-first: ComStock's operating-hours
    # field for hotels (~12 h mean) and hospitals (~9.4 h) is a front-of-house
    # window, NOT the whole-building occupancy period - forcing it through
    # create_typical's op-hours lever would set back a 24/7 building overnight.
    # Restaurants ARE served county-first (their open-to-close is real; only the
    # DOE prototype's bimodal occupancy-peak window was the artifact).
    COUNTY_HOURS_SUPPRESS = {'SmallHotel', 'LargeHotel', 'Hospital'}
    if bt not in RESIDENTIAL_BUILDING_TYPES and doe_ref not in COUNTY_HOURS_SUPPRESS:
        donor, tier = _hours_donor(ctx, _comstock_type(ctx))
        if donor:
            key = _HOURS_DONOR_KEYS[field]
            try:
                dur = float(donor.get('wh' if key in ('wo', 'wh') else 'eh'))
                val = float(donor.get(key))
            except (TypeError, ValueError):
                dur, val = 0.0, 0.0
            if dur > 0:
                return (_decimal_hr_to_hhmm(val), f'county_{tier}')
            # zero-duration day (e.g. closed weekends): omit both fields for that
            # day so create_typical is not handed a 00:00 window.
            return (None, None)
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


PRESET_LABELS = {'IECC 2009': 'Code minimum, 2009 IECC', 'IECC 2021': 'Current code, 2021 IECC',
                 '90.1-2007': 'Code minimum, ASHRAE 90.1-2007', '90.1-2019': 'Current code, ASHRAE 90.1-2019'}

# The ASHRAE prototype PowerTwin.rb lookup_building_type builds for each commercial subtype (its non-DEER branch),
# by the Ruby's own names; test_insulation_presets pins this against the case statement. Office and Lodging are sized
# below; Mixed use arrives already remapped to Office (asset_subtypes.csv effective_id), as the simulation builds it.
_PRESET_PROTOTYPE = {
    'Education': 'SecondarySchool', 'Enclosed mall': 'RetailStripmall', 'Food sales': 'SuperMarket',
    'Food service': 'FullServiceRestaurant', 'Inpatient health care': 'Hospital', 'Laboratory': 'Laboratory',
    'Nonrefrigerated warehouse': 'Warehouse', 'Nursing': 'Outpatient', 'Outpatient health care': 'Outpatient',
    'Public assembly': 'MediumOffice', 'Public order and safety': 'MediumOffice', 'Refrigerated warehouse': 'Warehouse',
    'Religious worship': 'MediumOffice', 'Retail other than mall': 'RetailStandalone', 'Service': 'MediumOffice',
    'Strip shopping mall': 'RetailStripmall', 'Vacant': 'Warehouse',
}
# openstudio-standards model_get_lookup_name: the name construction sets (and so the constructions) are looked up by
_LOOKUP_NAME = {'SmallOffice': 'Office', 'MediumOffice': 'Office', 'LargeOffice': 'Office',
                'RetailStandalone': 'Retail', 'RetailStripmall': 'StripMall'}

# Typical floor-to-floor height (ft) of each ASHRAE prototype PowerTwin builds: openstudio-standards 0.7.1
# lib/openstudio-standards/geometry/create_bar.rb building_form_defaults `typical_story` (the height create_bar uses
# when given none). The commercial floor_height default since 2026-09-30, replacing the flat 9 ft that the
# urbanopt-geojson zoning step discarded anyway (it built every commercial story at 3.6 m; patched in
# patches/patch_openstudio_standards.py).
PROTOTYPE_TYPICAL_STORY_FT = {
    'SmallOffice': 10.0, 'MediumOffice': 13.0, 'LargeOffice': 13.0, 'RetailStandalone': 20.0, 'RetailStripmall': 17.0,
    'SecondarySchool': 13.0, 'Outpatient': 10.0, 'Hospital': 14.0, 'SmallHotel': 9.0, 'LargeHotel': 10.0,
    'Warehouse': 28.0, 'FullServiceRestaurant': 10.0, 'SuperMarket': 20.0, 'Laboratory': 10.0,
}
# urbanopt-geojson's own zoning story (building.rb, 3.6 m), in ft: the height a commercial building outside the
# prototypes' domain keeps, which is what every commercial building was built at before the zoning patch.
URBANOPT_ZONING_STORY_FT = 3.6 / 0.3048
# ComStock 2024.1 (baseline.parquet, 346,185 buildings) samples no building under 1,000 ft2 (min in.sqft is 1,000 for
# every type) and has no Vacant type; the typical story is a property of the prototypes it builds, not of a shed.
# The floor-fit run gave Vacant (built as the Warehouse prototype) 28 ft stories on one utility stock's buildings with a
# median 382 ft2 a story, and 7 sheds of 79 to 415 ft2 failed in EnergyPlus (2026-09-30).
PROTOTYPE_STORY_MIN_SQFT = 1000.0
PROTOTYPE_STORY_EXCLUDED_TYPES = ('Vacant',)


def _prototype_story_applies(ctx: dict) -> bool:
    """Whether the prototype's typical story fits this commercial building: a type ComStock models (not Vacant, whose
    Warehouse prototype is a load proxy) and a total floor area inside ComStock's sample (1,000 ft2 or more)."""
    if ctx.get('building_type') in PROTOTYPE_STORY_EXCLUDED_TYPES:
        return False
    try:
        area = float(ctx.get('area'))
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(area) and area >= PROTOTYPE_STORY_MIN_SQFT


def _sim_stories(ctx: dict) -> int:
    """The story count the simulation uses (generateFeatureFile._floor_count_of): whole floors, at least 1, and a
    missing or malformed value is 1 (review 2026-09-30: None mapped a Lodging to LargeHotel, which the sim never
    builds for it)."""
    try:
        return max(1, int(float(ctx.get('floor_count'))))
    except (TypeError, ValueError, OverflowError):
        return 1


def _preset_prototype(ctx: dict):
    bt = ctx.get('building_type')
    if not isinstance(bt, str):
        return None
    if bt == 'Office':          # PowerTwin.rb: total floor area < 25,000 small, > 100,000 large, else medium
        try:
            area = float(ctx.get('area'))
        except (TypeError, ValueError, OverflowError):
            return None
        return 'SmallOffice' if area < 25000 else 'LargeOffice' if area > 100000 else 'MediumOffice'
    if bt == 'Lodging':         # PowerTwin.rb: more than 3 stories is a large hotel
        return 'LargeHotel' if _sim_stories(ctx) > 3 else 'SmallHotel'
    return _PRESET_PROTOTYPE.get(bt)


_DOE_REF_INCOMPATIBLE = ('SmallHotel', 'Laboratory')         # PowerTwin.rb DOE_REF_INCOMPATIBLE


COMMERCIAL_HVAC_EUL_YEARS = 20     # PowerTwin.rb hvac_eul: the HVAC template is the code at the last replacement year


def _state_code_at(state, year):
    """The energy code ComStock 2024.1 assigns a commercial building of `state` built in `year` (the state adoption
    table, comstock2024_state_code_adoption.json): (template, family) or (None, None). Years past a state's last
    sampled year keep its last code."""
    entry = _load_ref('comstock2024_state_code_adoption').get('states', {}).get(str(state or '').upper())
    if not entry:
        return (None, None)
    code = None
    for start, step_code in entry['steps']:
        if year >= start:
            code = step_code
    return (code, entry['family'])


def resolved_templates(ctx: dict, sim_year=None, cec_zone=None):
    """Energy Code Assignment Plan (resolver 3.3): the (construction, HVAC) templates of a commercial building from its
    state's adoption years, in place of PowerTwin.rb's year-only ladder, which assumed every 90.1 edition governed
    everywhere from the year after publication. The HVAC template is the code in force at the last replacement
    (PowerTwin.rb's 20-year rule, unchanged). California's codes are the DEER templates, which openstudio-standards
    accepts only with a CEC climate zone, so they apply only when the caller resolved `cec_zone` (the feature then
    carries it). (None, None) leaves the ladder: dynamic defaults off, a house, no state or year, or California without
    a CEC zone."""
    if not is_dynamic_defaults_enabled() or not ctx or ctx.get('building_type') in RESIDENTIAL_BUILDING_TYPES:
        return (None, None)
    try:
        y = int(float(ctx.get('year_built')))
    except (TypeError, ValueError, OverflowError):
        return (None, None)
    env, family = _state_code_at(ctx.get('state'), y)
    if env is None or (family == 'DEER' and not cec_zone):
        return (None, None)
    if sim_year is None:
        sim_year = int(os.environ.get('URBANOPT_SIMULATION_YEAR', '2025'))
    hvac = env
    if sim_year - y > COMMERCIAL_HVAC_EUL_YEARS:
        eff = min(max(y + COMMERCIAL_HVAC_EUL_YEARS, sim_year - COMMERCIAL_HVAC_EUL_YEARS), sim_year)
        hvac = _state_code_at(ctx.get('state'), eff)[0] or env
    return (env, hvac)


def resolve_lighting(ctx: dict):
    """A commercial building's installed interior lighting power density (W/ft2), replacing the flat 35% cut to the
    90.1 allowance, which had no source (2026-10-01): its lighting generation drawn from ComStock 2024.1's county
    distribution for its ComStock type (comstock2024_county_lighting, division fallback; the mode with stochastic
    sampling off), and that generation's average LPD for the type from NREL/TP-5500-86100 Table 2
    (comstock_lighting_lpd). Returns (lpd, generation, level) or (None, None, None): dynamic defaults off, a house,
    or a type ComStock does not model (the 35% cut stays there)."""
    if not is_dynamic_defaults_enabled() or not ctx or ctx.get('building_type') in RESIDENTIAL_BUILDING_TYPES:
        return (None, None, None)
    cs_type = _comstock_type(ctx)
    lpd_by_gen = _load_ref('comstock_lighting_lpd').get('lpd_w_per_ft2', {}).get(cs_type or '')
    if not lpd_by_gen:
        return (None, None, None)
    for cell, tier in _county_prior_tiers('comstock2024_county_lighting', ctx.get('county_fips')):
        sub = cell.get(cs_type)
        if not sub:
            continue
        gen = _select_fuel_by_share(sub, ctx, salt='lighting_generation')
        lpd = lpd_by_gen.get(str(gen)[:4])
        if lpd is not None:
            return (float(lpd), gen, f'county_{tier}')
    return (None, None, None)


def _doe_ref_guard(template, prototype):
    """SmallHotel and Laboratory cannot build on a DOE Ref template (PowerTwin.rb DOE_REF_INCOMPATIBLE); the ladder
    gave them 90.1-2004 there, and so does the state table."""
    if template and template.startswith('DOE Ref') and prototype in _DOE_REF_INCOMPATIBLE:
        return '90.1-2004'
    return template


def _preset_template(year_built, prototype, ctx=None, cec_zone=None):
    """The template, and so the construction set, a commercial building is built with: the state adoption table when
    it applies (resolved_templates, with the building's CEC zone so a California building gets the DEER template it
    is built on), else PowerTwin.rb lookup_template_by_year_built (non-DEER), pinned against the Ruby by
    test_insulation_presets."""
    if ctx is not None:
        env, _hvac = resolved_templates(ctx, cec_zone=cec_zone)
        if env and env.startswith('DEER'):
            return env      # no ASHRAE construction set applies; insulation_presets offers none (review 2026-10-03)
        if env:
            return _doe_ref_guard(env, prototype)
    try:
        y = int(float(year_built))
    except (TypeError, ValueError, OverflowError):
        return '90.1-2013'      # the site's default template (generateFeatureFile), which a building with no year gets
    doe_ok = prototype not in _DOE_REF_INCOMPATIBLE
    if doe_ok and y < 1980:
        return 'DOE Ref Pre-1980'
    if doe_ok and y <= 2004:
        return 'DOE Ref 1980-2004'
    return '90.1-2004' if y <= 2007 else '90.1-2007' if y <= 2010 else '90.1-2010' if y <= 2013 else '90.1-2013'


def _zone_value(table: dict, key: str, zone: str):
    """A {key: {zone: value}} lookup for an IECC zone like '7A': the DOE Ref templates key subzones ('2A'), the 90.1
    editions whole zones ('7')."""
    by_zone = table.get(key) or {}
    for z in (zone, zone[:1]):
        if z in by_zone:
            return by_zone[z]
    return None


def insulation_presets(ctx: dict, older_climate_zone: str | None = None, cec_zone: str | None = None) -> dict:
    """Code R-value presets for the defaults preview (Insulation Presets Plan, owner 2026-09-29): the R-value is the
    only insulation input and the modal offers these to fill it.
    - Zones: the current-code presets (2021 IECC, 90.1-2019) use the 2021 zone of the building's coordinates, the one
      the simulation uses; the code-minimum presets (2009 IECC, 90.1-2007) use `older_climate_zone`, the county's
      zone on the map those editions were written for (weather.get_climate_zone(..., '15')), falling back to the
      2021 zone (review 2026-09-30: 373 of 3,222 counties changed zones between the maps).
    - Houses: IECC 2009 / 2021 ceiling (roof_r_value) and cavity wall minimums; only with dynamic defaults on, since
      PowerTwin.rb applies a house's wall/roof R only then.
    - Commercial: the insulation-layer R that meets ASHRAE 90.1-2007 / 2019 for the construction the building is
      actually built with (prototype -> template by year -> openstudio-standards lookup name -> construction set; a
      hotel is residential only where the template has a residential set). No preset where the template's own
      insulation layer is R-1 or less (the openstudio-ee measures skip such a construction) or the value is below
      R-1 (the product minimum; the roof measure rejects it).
    - California commercial buildings built on a DEER template (`cec_zone` resolved): no preset. The presets are
      90.1 R-values for openstudio-standards' ASHRAE construction sets, and a 90.1-2007 R on a DEER construction does
      not meet 90.1-2007 (a 2010 Sacramento office: 42% over U_max; review 2026-10-03).
    Returns {field: [{label, value, source}, ...]} in order code minimum, current code; the current-code entry is
    dropped where it is below the code minimum (90.1-2019 relaxed some semiheated metal-building walls)."""
    cz = str(ctx.get('climate_zone') or '').upper()
    if not cz[:1].isdigit():
        return {}
    old = str(older_climate_zone or '').upper()
    old = old if old[:1].isdigit() else cz
    bt = ctx.get('building_type')
    if not isinstance(bt, str):
        return {}
    if bt in RESIDENTIAL_BUILDING_TYPES and not is_dynamic_defaults_enabled():
        return {}   # PowerTwin.rb applies a house's wall/roof R only with dynamic defaults on (review 2026-09-30)
    ref = _load_ref('insulation_presets')
    out = {'wall_r_value': [], 'roof_r_value': []}
    if bt in RESIDENTIAL_BUILDING_TYPES:
        for ed, zone in (('IECC 2009', old), ('IECC 2021', cz)):
            t = ref['residential'][ed]
            key = 'M4' if zone.startswith('4C') else zone[0]
            for field, col in (('wall_r_value', 'wall_cavity'), ('roof_r_value', 'ceiling')):
                v = t[col].get(key)
                if v is not None:
                    out[field].append({'label': PRESET_LABELS[ed], 'value': float(v), 'source': ed})
    else:
        com = ref['commercial']
        proto = _preset_prototype(ctx)
        template = _preset_template(ctx.get('year_built'), proto, ctx, cec_zone)
        if template.startswith('DEER'):
            return {}
        lookup = _LOOKUP_NAME.get(proto, proto)
        cset = com['construction_sets'].get(template, {}).get(lookup or '')
        if cset:
            residential = proto in ('SmallHotel', 'LargeHotel') and lookup in com['residential_sets'].get(template, [])
            for field, surf in (('wall_r_value', 'wall'), ('roof_r_value', 'roof')):
                ctype, cat = cset[surf]
                key = f"{surf}|{ctype}|{'Residential' if residential else cat}"
                built = _zone_value(com['template_insulation_r'].get(template, {}), key, cz)
                if built is not None and built <= 1.0:
                    continue    # the measure would leave this construction unaltered
                for ed, zone in (('90.1-2007', old), ('90.1-2019', cz)):
                    v = _zone_value(com['insulation_r'][ed], key, zone)
                    if v is not None and v >= 1.0:
                        out[field].append({'label': PRESET_LABELS[ed], 'value': float(v),
                                           'source': f'ASHRAE {ed}, {ctype} {surf} ({proto}, built to {template})'})
    for field, items in out.items():
        if len(items) == 2 and items[1]['value'] < items[0]['value']:
            del items[1]
    return {f: v for f, v in out.items() if v}


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
    # CR WP-R5: residential wall/ceiling R county-first (nominal R parsed from
    # ResStock insulation labels; ceiling shares conditional on an attic
    # existing). Material tier stays DERIVED from the same R draw (invariant).
    if is_residential:
        part = 'wall' if surface == 'wall' else 'ceiling'
        vb = _duct_vintage_bin(ctx.get('year_built')) or 'all'
        # Walk tiers PER SURFACE: a vintage bin holding only 'wall' must not
        # block the same cell's 'all' bucket or a deeper tier's ceiling data
        # (review finding: 186 wall-only gap cells sent roof R to the national
        # table while county ceiling data existed).
        for cell, tier in _county_prior_tiers('resstock2024_county_insulation', ctx.get('county_fips')):
            sub = ((cell.get(vb) or {}).get(part)) or ((cell.get('all') or {}).get(part))
            if not sub:
                continue
            r = float(_select_fuel_by_share(sub, ctx, salt=f'{surface}_r_value'))
            if field.endswith('material'):
                return (_r_to_tier(r, surface), f'county_{tier}')
            return (r, f'county_{tier}')
    rec = _envelope_R_cell(survey.get(f'{surface}_r_value'), ctx)
    if not isinstance(rec, dict):
        return (None, None)
    r = float(_select_fuel_by_share(rec, ctx, salt=f'{surface}_r_value'))
    if field.endswith('material'):
        return (_r_to_tier(r, surface), 'vintage_specific')
    return (r, 'vintage_specific')



# --- CR WP-R1: county-resolved duct priors -----------------------------------

_RESSTOCK_DUCT_LOC_TO_HPXML = {
    'Living Space': 'conditioned space',
    'Attic': 'attic',
    'Crawlspace': 'crawlspace',
    'Heated Basement': 'basement - conditioned',
    'Unheated Basement': 'basement - unconditioned',
    'Garage': 'garage',
}

# resolver-ctx year -> the duct table's vintage bins (builder: build_county_ducts.py)
def _duct_vintage_bin(year_built):
    try:
        y = int(year_built)
    except (TypeError, ValueError):
        return None
    if y < 1960: return 'pre-1960'
    if y < 1980: return '1960-79'
    if y < 2000: return '1980-99'
    if y < 2010: return '2000-09'
    return '2010+'


def _parse_duct_leakage_label(label):
    """'20% Leakage to Outside, R-4' -> (20.0, 4.0); 'Uninsulated' -> R 0.
    Split semantics (supply 2/3, return 1/3) are applied in the MAPPER, cited to
    ResStock options_lookup v3.3.0; here we carry the total and the R."""
    m = re.match(r'\s*(\d+(?:\.\d+)?)% Leakage to Outside,\s*(.+)$', str(label))
    if not m:
        return (None, None)
    pct = float(m.group(1))
    ins = m.group(2).strip()
    if ins.lower() == 'uninsulated':
        r = 0.0
    else:
        rm = re.match(r'R-(\d+(?:\.\d+)?)', ins)
        r = float(rm.group(1)) if rm else 0.0
    return (pct, r)


def _resolve_ducts(field, ctx):
    """County-first duct priors (WP-R1). Applies only to homes the mapper builds as
    DUCTED (the mapper guards on system type); which homes are ducted at all remains
    the heating-equipment joint's job (WP-R2). Deterministic per building: location
    and leakage are separate ResStock parameters, drawn on their own salts."""
    if ctx.get('building_type') not in RESIDENTIAL_BUILDING_TYPES:
        return (None, None)
    cell, tier = _county_prior('resstock2024_county_ducts', ctx.get('county_fips'))
    if cell is None:
        return (None, None)
    vb = _duct_vintage_bin(ctx.get('year_built')) or 'all'
    sub = cell.get(vb) or cell.get('all')
    if not sub:
        return (None, None)
    level = f'county_{tier}'
    if field == 'duct_location':
        raw = _select_fuel_by_share(sub['location'], ctx, salt='duct_location')
        mapped = _RESSTOCK_DUCT_LOC_TO_HPXML.get(raw)
        return (mapped, level) if mapped else (None, None)
    label = _select_fuel_by_share(sub['leakage'], ctx, salt='duct_leakage')
    pct, r = _parse_duct_leakage_label(label)
    if pct is None:
        return (None, None)
    if field == 'duct_leakage_pct':
        return (pct, level)
    if field == 'duct_insulation_r':
        return (r, level)
    return (None, None)



# --- CR WP-R2: county-resolved heating-equipment JOINT (donor draw + ACS raking) ---

# donor label -> (coarse enum value, HPXML detail, heat pump subtype or None)
_DONOR_EQUIPMENT_MAP = {
    'Fuel Furnace': ('furnace', 'Furnace', None),
    'Electric Furnace': ('furnace', 'Furnace', None),
    'Fuel Wall/Floor Furnace': ('furnace', 'WallFurnace', None),
    'Electric Wall Furnace': ('furnace', 'WallFurnace', None),
    'Fuel Boiler': ('boiler', 'Boiler', None),
    'Electric Boiler': ('boiler', 'Boiler', None),
    'Baseboard': ('electric_resistance', 'ElectricResistance', None),
    'ASHP': ('heat_pump', None, 'air-to-air'),
    'MSHP': ('heat_pump', None, 'mini-split'),
    'Ducted Heat Pump': ('heat_pump', None, 'air-to-air'),
    'Non-Ducted Heat Pump': ('heat_pump', None, 'mini-split'),
    # Shared (MF central) systems approximated as boilers - the dominant real
    # configuration for shared heat; noted in the ledger row.
    'Shared Heating': ('boiler', 'Boiler', None),
}
_DONOR_FUEL_PREFIX = {'Electricity': 'electricity', 'Natural Gas': 'natural gas',
                      'Propane': 'propane', 'Fuel Oil': 'fuel oil', 'Other Fuel': 'wood'}


def _parse_heating_donor_label(label):
    """'Natural Gas Fuel Furnace' -> (fuel, coarse, hpxml_detail, hp_subtype) or None."""
    label = str(label)
    for pref, fuel in _DONOR_FUEL_PREFIX.items():
        if label.startswith(pref + ' '):
            rest = label[len(pref) + 1:]
            m = _DONOR_EQUIPMENT_MAP.get(rest)
            if m:
                return (fuel, m[0], m[1], m[2])
            return None
    return None


def _rake_donors_to_acs(donors, county_fips):
    """Reweight donor list so the implied FUEL marginal matches the ACS county
    marginal (census marginals win where they exist - owner-ratified), with the two
    correction rules verified in scoping: (1) renormalize the ACS shares (they can
    sum to ~0.997); (2) an ACS fuel with zero donors is unservable - drop it and
    renormalize the remaining targets so no mass silently vanishes. Returns a new
    list of (donor, weight) with weights summing to 1, or None when no ACS row."""
    acs = _load_ref('acs2022_county_fuel').get('fuel_share', {}).get(str(county_fips))
    if not acs:
        return None
    parsed = []
    for d in donors:
        pl = _parse_heating_donor_label(d.get('in.hvac_heating_type_and_fuel'))
        if pl:
            parsed.append((d, pl))
    if not parsed:
        return None
    group_w = {}
    for d, (fuel, *_rest) in parsed:
        group_w[fuel] = group_w.get(fuel, 0.0) + float(d.get('w', 0))
    targets = {f: v for f, v in acs.items() if v > 0 and group_w.get(f, 0) > 0}
    t_tot = sum(targets.values())
    if t_tot <= 0:
        return None
    targets = {f: v / t_tot for f, v in targets.items()}
    out = []
    for d, (fuel, *_rest) in parsed:
        tgt = targets.get(fuel)
        if tgt is None:
            continue  # donor fuel absent from ACS entirely: excluded, renormalized below
        out.append((d, float(d.get('w', 0)) * tgt / group_w[fuel]))
    w_tot = sum(w for _, w in out)
    if w_tot <= 0:
        return None
    return [(d, w / w_tot) for d, w in out]


def _heating_donor(ctx):
    """Deterministic county heating donor for this building, raked to ACS.
    Returns the donor dict or None (division/RECS fallback then applies)."""
    if ctx.get('building_type') not in RESIDENTIAL_BUILDING_TYPES:
        return None
    cell, tier = _county_prior('resstock2024_county_heating', ctx.get('county_fips'))
    if cell is None:
        return None
    vb = _duct_vintage_bin(ctx.get('year_built')) or 'all'
    sub = cell.get(vb) or cell.get('all')
    if not sub or not sub.get('donors'):
        return None
    raked = _rake_donors_to_acs(sub['donors'], ctx.get('county_fips'))
    if not raked:
        return None
    if not is_stochastic_sampling_enabled() or not ctx.get('building_id'):
        # stochastic off (or no id): deterministic MODE, matching _select_fuel_by_share
        best = max(raked, key=lambda t: t[1])
        return {'donor': best[0], 'tier': tier}
    # cumulative hash draw (same id-hash discipline as _select_fuel_by_share)
    frac = _building_id_hash_fraction(ctx, salt="heating_donor")
    acc = 0.0
    for d, w in raked:
        acc += w
        if frac <= acc:
            return {'donor': d, 'tier': tier}
    return {'donor': raked[-1][0], 'tier': tier}


# --- CR WP-R3: county-resolved thermostat setpoints + envelope infiltration ---

def _setpoint_donor(ctx):
    """Deterministic county setpoint donor (WP-R3): heating and cooling setpoints
    drawn JOINTLY (same-household setback behavior correlates across seasons).
    Occupied stock only by construction of the table; the pipeline's own vacancy
    channel owns vacant setbacks. No raking (no census setpoint marginal exists)."""
    if ctx.get('building_type') not in RESIDENTIAL_BUILDING_TYPES:
        return None
    cell, tier = _county_prior('resstock2024_county_setpoints', ctx.get('county_fips'))
    if cell is None:
        return None
    sub = cell.get('all')
    if not sub or not sub.get('donors'):
        return None
    donors = [(d, float(d.get('w', 0))) for d in sub['donors']]
    if not is_stochastic_sampling_enabled() or not ctx.get('building_id'):
        best = max(donors, key=lambda t: t[1])
        return {'donor': best[0], 'tier': tier}
    frac = _building_id_hash_fraction(ctx, salt='setpoint_donor')
    acc = 0.0
    for d, w in donors:
        acc += w
        if frac <= acc:
            return {'donor': d, 'tier': tier}
    return {'donor': donors[-1][0], 'tier': tier}


def _parse_f_label(label):
    """'70F' -> 70.0; anything else -> None."""
    m = re.match(r'^\s*(\d+(?:\.\d+)?)F\s*$', str(label))
    return float(m.group(1)) if m else None


def _resolve_setpoints(field, ctx):
    sd = _setpoint_donor(ctx)
    if not sd:
        return (None, None)
    key = 'in.heating_setpoint' if field == 'heating_setpoint_f' else 'in.cooling_setpoint'
    val = _parse_f_label(sd['donor'].get(key))
    if val is None:
        return (None, None)
    return (val, f"county_{sd['tier']}")


# WP-R3b HOLD (owner-ratified 2026-09-04): the county infiltration prior is
# faithfully transcribed and delivered correctly, but the single-knob ablation
# (tetonall vs tetonabl1) showed it ALONE adds +45 pts residential electricity
# NMBE / +87 gas on Teton - the bias lives elsewhere in the stack (candidates:
# HPXML AIM-2 weather-driven basis vs ResStock's, real-metadata geometry x
# leakage interaction). Disabled until WP-R3b root-causes that; table, tests,
# and wiring stay in place so re-enabling is this one constant.
INFILTRATION_PRIOR_ENABLED = False


# The domain gate maps our building-type names onto the donor-population
# geometry classes whose observed floor-area support the table's _domain block
# carries (in.geometry_building_type_recs; Multifamily uses the 5+ class, whose
# support equals the 2-4 class in ResStock 2024.2).
_INFILTRATION_DOMAIN_TYPE = {
    'Single-Family Detached': 'Single-Family Detached',
    'Single-Family Attached': 'Single-Family Attached',
    'Multifamily': 'Multi-Family with 5+ Units',
}


def _infiltration_in_domain(ctx):
    """WP-R3b: serve the county ACH50 only for units inside the donor
    population's floor-area support. ResStock's labels are whole-dwelling
    values whose energy effect scales with value x volume; on real-metadata
    buildings far larger than any donor (lodges and condo complexes classed
    residential, up to 193k sqft in Teton) they are extrapolation, and the
    single-knob ablation showed that slice alone carried the +45 NMBE-pt
    regression. The bounds are computed by the builder from the donor rows
    (table _domain.max_unit_sqft) - nothing here chooses a number. Unknown
    floor area is out of domain (the fallback default handles it)."""
    try:
        area = float(ctx.get('area'))
    except (TypeError, ValueError):
        return False
    units = ctx.get('units') or 1
    unit_cfa = area / max(int(units), 1)
    table = _load_ref('resstock2024_county_infiltration')
    bounds = (table.get('_domain') or {}).get('max_unit_sqft') or {}
    key = _INFILTRATION_DOMAIN_TYPE.get(ctx.get('building_type'))
    bound = bounds.get(key) or bounds.get('_overall')
    if not bound:
        return False
    return unit_cfa <= float(bound)


def _resolve_infiltration(ctx):
    """County ACH50 (WP-R3, HELD by WP-R3b - see INFILTRATION_PRIOR_ENABLED).
    Labels are 'N ACH50', whole-dwelling at 50 Pa; the mapper passes N straight
    to HPXML (air_leakage_units='ACH', pressure 50) with no conversion. Only
    served inside the donor floor-area domain (_infiltration_in_domain)."""
    if not INFILTRATION_PRIOR_ENABLED:
        return (None, None)
    if ctx.get('building_type') not in RESIDENTIAL_BUILDING_TYPES:
        return (None, None)
    if not _infiltration_in_domain(ctx):
        return (None, None)
    cell, tier = _county_prior('resstock2024_county_infiltration', ctx.get('county_fips'))
    if cell is None:
        return (None, None)
    vb = _duct_vintage_bin(ctx.get('year_built')) or 'all'
    sub = cell.get(vb) or cell.get('all')
    if not sub:
        return (None, None)
    label = _select_fuel_by_share(sub, ctx, salt='infiltration')
    m = re.match(r'^\s*(\d+(?:\.\d+)?)\s*ACH50\s*$', str(label))
    if not m:
        return (None, None)
    return (float(m.group(1)), f'county_{tier}')


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
        if field == 'floor_height':
            # commercial: the prototype's typical story, which the patched zoning step now builds (houses keep
            # URBANopt's 8 ft unless a user sets floor_height, see RESIDENTIAL_UNSET_CEILING_HEIGHT_FT)
            if ctx.get('building_type') in RESIDENTIAL_BUILDING_TYPES:
                return (None, None)
            if not _prototype_story_applies(ctx):
                return (URBANOPT_ZONING_STORY_FT, 'building_type_only')
            ft = PROTOTYPE_TYPICAL_STORY_FT.get(_preset_prototype(ctx))
            return (ft, 'building_type_only') if ft is not None else (URBANOPT_ZONING_STORY_FT, 'building_type_only')
        if field == 'number_of_occupants':
            val = _resolve_occupants(ctx)
            if val is None:
                return (None, None)
            # Report county provenance when the WP-R5 household donor served
            # (same deterministic draw, so this re-check names the actual source).
            if (ctx.get('building_type') in RESIDENTIAL_BUILDING_TYPES
                    and ctx.get('bedrooms') in (None, '')):
                donor, tier = _household_donor(ctx)
                if donor:
                    return (val, f'county_{tier}')
            return (val, 'building_type_only')
        if field == 'heating_system_type':
            if ctx.get('heating_system_type') in ENUM_VALUES.get('heating_system_type', set()):
                return _resolve_heating_system_type(ctx)
            hd = _heating_donor(ctx)
            if hd:
                fuel, coarse, detail, hp = _parse_heating_donor_label(
                    hd['donor'].get('in.hvac_heating_type_and_fuel'))
                return (coarse, f"county_{hd['tier']}")
            return _resolve_heating_system_type(ctx)
        if field == 'heating_equipment_detail':
            if ctx.get('heating_system_type') in ENUM_VALUES.get('heating_system_type', set()):
                return (None, None)  # explicit system: no donor detail channel
            hd = _heating_donor(ctx)
            if hd:
                fuel, coarse, detail, hp = _parse_heating_donor_label(
                    hd['donor'].get('in.hvac_heating_type_and_fuel'))
                return (detail or hp, f"county_{hd['tier']}")
            return (None, None)
        if field == 'heating_has_ducts':
            # The duct truth describes the HOUSE, not the equipment, so it is served even when the
            # system type is explicit: an explicit heat pump on a ductless house builds as a
            # mini-split instead of inheriting a new duct run (asset 448523 review, 2026-09-21).
            hd = _heating_donor(ctx)
            if hd:
                return (hd['donor'].get('in.hvac_has_ducts'), f"county_{hd['tier']}")
            return (None, None)
        if field == 'water_heater_type':
            return _resolve_water_heater_type(ctx)
        if field == 'heating_system_fuel_type':
            # An EXPLICIT metadata system type outranks the county donor: fuel must
            # condition on the system the user pinned (WP-03 contract, re-caught by
            # test_override_coherence when the donor path bypassed it). The donor-vs-
            # legacy precedence lives in _effective_heating_fuel so the SWH fuel
            # conditioning consumes the SAME fuel this channel emits.
            explicit_sys = ctx.get('heating_system_type')
            if explicit_sys in ENUM_VALUES.get('heating_system_type', set()):
                return _resolve_fuel(field, ctx)
            hd = _heating_donor(ctx)
            if hd:
                parsed = _parse_heating_donor_label(
                    hd['donor'].get('in.hvac_heating_type_and_fuel'))
                if parsed:
                    return (parsed[0], f"county_{hd['tier']}")
            return _resolve_fuel(field, ctx)
        if field in ('cooling_system_fuel_type',
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
        if field in ('duct_location', 'duct_leakage_pct', 'duct_insulation_r'):
            return _resolve_ducts(field, ctx)
        if field in ('heating_setpoint_f', 'cooling_setpoint_f'):
            return _resolve_setpoints(field, ctx)
        if field == 'infiltration_ach50':
            return _resolve_infiltration(ctx)
        if field in _APPLIANCE_KEYS:
            return _resolve_appliance(field, ctx)
        if field in ('cooling_type_detail', 'cooling_fraction_served'):
            return _resolve_cooling(field, ctx)
        if field in ('foundation_type', 'garage_cars'):
            if field == 'foundation_type' and ctx.get('foundation_type') in ENUM_VALUES['foundation_type']:
                return (None, None)  # explicit metadata foundation stands
            return _resolve_geometry(field, ctx)
        if field == 'bedrooms_per_unit':
            if ctx.get('bedrooms') not in (None, ''):
                return (None, None)  # explicit metadata bedrooms stand
            donor, tier = _household_donor(ctx)
            if donor:
                try:
                    return (int(donor['br']), f'county_{tier}')
                except (KeyError, TypeError, ValueError):
                    return (None, None)
            return (None, None)
        if field in ('pv_system_kw', 'pv_azimuth_deg'):
            return _resolve_pv(field, ctx)
        if field in ('com_heating_adjustment_f', 'com_cooling_adjustment_f'):
            return _resolve_com_tstat(field, ctx)
        if field == 'com_airtightness_75pa':
            return _resolve_com_airtightness(ctx)
        if field == 'system_type':
            if ctx.get('system_type') in ENUM_VALUES.get('system_type', set()):
                return (None, None)  # explicit metadata system stands (WP-03 contract)
            return _resolve_system_type(ctx)
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
    if key in RETIRED_INPUT_FIELDS:
        # A label only (the tier is no longer an input): the tier of the R-value the building actually uses, explicit
        # or resolved. Labelling the resolver's own draw contradicted an explicit R (review 2026-09-30: R-30 said
        # 'Standard'); for an unedited building the two are the same draw, so nothing changes there.
        surface = key.split('_', 1)[0]
        r = get_param(metadata, f'{surface}_r_value', ctx)
        return _r_to_tier(float(r), surface) if r is not None else SIM_PARAM_DEFAULTS[key]
    # Exact match, like the water_heater_type enum gate below: a case variant the gate rejects must not make the fuel
    # electric under a storage heater nobody chose (review 2026-10-03).
    if key == 'service_water_heating_fuel_type' and metadata.get('water_heater_type') == HPWH:
        # A heat pump water heater is electric by definition. HPXML built an explicit HPWH as electric even beside an
        # explicit 'natural gas' fuel while the feature file and preview kept saying gas (2026-09-30 house test), so
        # the type wins everywhere, visibly, with dynamic defaults on or off.
        if str(metadata.get(key) or 'electricity').strip().lower() != 'electricity':
            log.warning(f"service_water_heating_fuel_type={metadata.get(key)!r} conflicts with a heat pump water heater; "
                        f"using electricity")
        return 'electricity'
    raw = metadata.get(key)
    if raw is not None and raw != '':
        # Enum gate on the METADATA path too (Q/A D-26): an arbitrary explicit string used to
        # ride into the feature JSON and get silently dropped by the Ruby fuel_map
        # (template-default physics, no log). Gate against RESOLVER_ENUM_VALUES, the full set
        # the downstream toolchain can map -- gating on strict ENUM_VALUES rejected the
        # documented explicit 'district steam' / cooling 'none' levers (caught by the
        # district-steam container e2e).
        _meta_allowed = RESOLVER_ENUM_VALUES.get(key, ENUM_VALUES.get(key))
        if _meta_allowed is not None and str(raw) not in _meta_allowed:
            log.warning(
                f"ignoring invalid metadata {key}={raw!r} (not in enum); resolving default instead")
            raw = None
    if raw is not None and raw != '':
        if key in NUMERIC_RANGES:
            lo, hi = NUMERIC_RANGES[key]
            try:
                val = float(raw)
                if not math.isfinite(val):   # 'inf', '1e999', 'nan': no range clamps them (floor_height has no maximum)
                    raise ValueError(raw)
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

    if key == 'floor_height' and ctx is not None and ctx.get('building_type'):
        # What URBANopt builds when nothing sets the height (2026-09-30): a house's 8 ft ceiling, a commercial
        # building's 3.6 m zoning story. The flat 9 ft built neither; it reached the feature file as a house's
        # floor_height and, with the zoning patch, would have built every commercial story at 9 ft with dynamic
        # defaults off, which must stay plain URBANopt.
        return (RESIDENTIAL_UNSET_CEILING_HEIGHT_FT if ctx.get('building_type') in RESIDENTIAL_BUILDING_TYPES
                else URBANOPT_ZONING_STORY_FT)

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
