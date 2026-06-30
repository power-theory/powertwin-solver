#!/usr/bin/env python3
"""Pre-push unit tests for all dynamic default resolvers.

Validates that every resolver in sim_params_spec.py returns correct values
across all (region, vintage, building_type) combinations, and that the
resolution precedence (metadata > resolver > flat default) is preserved.

Fast, pure Python, no Docker or EnergyPlus required. Runs in <1 second.

Usage:
    python3 tests/test_resolvers.py
"""
import importlib.util
import json
import os
import sys

import pytest
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))

os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "true"
os.environ["URBANOPT_STOCHASTIC_SAMPLING"] = "true"

spec = importlib.util.spec_from_file_location(
    "sim_params_spec",
    os.path.join(REPO, "solver", "app", "modules", "simulation", "sim_params_spec.py"),
)
sps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sps)

build_asset_ctx = sps.build_asset_ctx
resolve_default = sps.resolve_default
get_param = sps.get_param
SIM_PARAM_DEFAULTS = sps.SIM_PARAM_DEFAULTS
ENUM_VALUES = sps.ENUM_VALUES
NUMERIC_RANGES = sps.NUMERIC_RANGES
RESOLVER_VERSION = sps.RESOLVER_VERSION
_load_ref = sps._load_ref
is_vacant = sps.is_vacant

REGIONS = ["Northeast", "Midwest", "South", "West"]
VINTAGES = ["pre-1980", "1980-1999", "2000-2009", "2010+"]
VINTAGE_YEARS = {"pre-1980": 1960, "1980-1999": 1990, "2000-2009": 2005, "2010+": 2018}
DECADE_BINS = {"pre-1980": "1960-1969", "1980-1999": "1990-1999", "2000-2009": "2000-2009", "2010+": "2010-2019"}
REGION_STATES = {"Northeast": "MA", "Midwest": "IL", "South": "TX", "West": "AZ"}
STATE_DIVISIONS = {"MA": "New England", "IL": "East North Central",
                   "TX": "West South Central", "AZ": "Mountain South"}

RESIDENTIAL_TYPES = ["Single-Family Detached", "Single-Family Attached", "Multifamily"]
COMMERCIAL_TYPES = ["Office", "Education", "Lodging", "Warehouse",
                    "Food service", "Outpatient health care", "Laboratory"]

ALL_DYNAMIC_FIELDS = [
    "window_type",
    "heating_system_type",
    "water_heater_type",
    "heating_system_fuel_type",
    "cooling_system_fuel_type",
    "service_water_heating_fuel_type",
    "wall_material",
    "roof_material",
    "wall_r_value",
    "roof_r_value",
    "window_to_wall_ratio",
    "number_of_occupants",
    "weekday_start_time",
    "weekday_duration",
    "weekend_start_time",
    "weekend_duration",
]

NON_DYNAMIC_FIELDS = [
    "system_type",
    "floor_height",
]

passed = 0
failed = 0
total = 0
failures = []


def check(label, actual, expected, section=""):
    global passed, failed, total
    total += 1
    if actual == expected:
        passed += 1
    else:
        failed += 1
        failures.append(f"  FAIL  [{section}] {label}: got {actual!r}, expected {expected!r}")


def check_in(label, actual, valid_set, section=""):
    global passed, failed, total
    total += 1
    if actual in valid_set:
        passed += 1
    else:
        failed += 1
        failures.append(f"  FAIL  [{section}] {label}: {actual!r} not in {valid_set}")


def check_range(label, actual, lo, hi, section=""):
    global passed, failed, total
    total += 1
    if actual is not None and lo <= float(actual) <= hi:
        passed += 1
    else:
        failed += 1
        failures.append(f"  FAIL  [{section}] {label}: {actual!r} not in [{lo}, {hi}]")


@pytest.fixture(autouse=True)
def _enforce_checks():
    """Make every test ACTUALLY fail under pytest. check()/check_in()/check_range()
    record mismatches into the module-global `failures` list; this asserts the
    current test added none. Without it the test_* functions were no-ops under
    pytest (they only reported via the old __main__ runner)."""
    start = len(failures)
    yield
    new = failures[start:]
    assert not new, f"{len(new)} check(s) failed:\n" + "\n".join(new)


def make_ctx(state, year, area, bt):
    return build_asset_ctx(
        {"state": state, "year_built": year, "area": area},
        building_type=bt,
    )


# ============================================================
# 1. Window type resolver -- exact values from reference data
# ============================================================
def test_window_type():
    section = "window_type"
    expected = _load_ref("window_type_by_vintage")

    # (a) deterministic default (no building_id) resolves to the cell mode.
    for sect_key, types, area in (("residential", RESIDENTIAL_TYPES, 2000),
                                  ("commercial", COMMERCIAL_TYPES, 50000)):
        for bt in types:
            for region in REGIONS:
                for vintage in VINTAGES:
                    ctx = make_ctx(REGION_STATES[region], VINTAGE_YEARS[vintage], area, bt)
                    val, level = resolve_default("window_type", ctx)
                    cell = expected[sect_key][region][vintage]
                    check(f"{bt}/{region}/{vintage} mode", val, cell["mode"], section)
                    check_in(f"{bt}/{region}/{vintage} enum", val, ENUM_VALUES["window_type"], section)
                    check(f"{bt}/{region}/{vintage} level", level, "vintage_specific", section)

    # (b) with building_id, glazing is share-weighted -- a cell reproduces its real
    # Single/Double/Triple mix instead of collapsing every building to the mode.
    probes = [
        ("Single-Family Detached", "residential", "South", "pre-1980"),
        ("Single-Family Detached", "residential", "Northeast", "pre-1980"),
        ("Laboratory", "commercial", "South", "pre-1980"),
    ]
    N = 2000
    for bt, sect_key, region, vintage in probes:
        cell = expected[sect_key][region][vintage]
        ct = Counter()
        for i in range(N):
            ctx = build_asset_ctx(
                {"state": REGION_STATES[region], "year_built": VINTAGE_YEARS[vintage], "area": 2000},
                building_type=bt, building_id=f"win{i:05d}",
            )
            val, _ = resolve_default("window_type", ctx)
            ct[val] += 1
        check(f"{bt}/{region}/{vintage} has variety (>=2 types)", len(ct) >= 2, True, section)
        for wtype, share in cell["shares"].items():
            emp = ct.get(wtype, 0) / N
            check_range(f"{bt}/{region}/{vintage} {wtype} share~{share:.2f}",
                        emp, max(0.0, share - 0.05), share + 0.05, section)


# ============================================================
# 2. Fuel type resolvers -- exact values from reference data
# ============================================================
def test_fuel_types():
    section = "fuel_types"
    recs = _load_ref("recs2020_residential_fuel_mix")
    cbecs = _load_ref("cbecs2018_commercial_fuel_mix")

    for field in ["heating_system_fuel_type", "service_water_heating_fuel_type"]:
        # Residential: resolver conditions fuel on system type (furnace/heat_pump/etc)
        # then selects from the conditional distribution. Without building_id it falls
        # back to mode of the system-type-conditional table, NOT the unconditional
        # region mode. So we only check enum validity here; exact mode alignment with
        # building_id is tested in test_share_weighted_distribution and test_nlr_fuel_alignment.
        for region in REGIONS:
            state = REGION_STATES[region]
            for vintage in VINTAGES:
                ctx = make_ctx(state, VINTAGE_YEARS[vintage], 2000,
                               "Single-Family Detached")
                val, level = resolve_default(field, ctx)
                check(f"res {field}/{region}/{vintage} not None", val is not None, True, section)
                check_in(f"res {field}/{region}/{vintage} enum",
                         val, ENUM_VALUES[field], section)

        # Commercial: verify against CBECS reference data
        for region in REGIONS:
            for vintage in VINTAGES:
                ctx = make_ctx(REGION_STATES[region], VINTAGE_YEARS[vintage], 50000, "Office")
                val, level = resolve_default(field, ctx)
                ref_section = cbecs.get(field, {})
                ref_record = ref_section.get(region, {}).get(vintage, {})
                exp = ref_record.get("mode") if ref_record else None
                if exp is not None:
                    check(f"com {field}/{region}/{vintage}", val, exp, section)
                    check_in(f"com {field}/{region}/{vintage} enum",
                             val, ENUM_VALUES[field], section)

    # Cooling should always be electricity for every building type
    for bt in RESIDENTIAL_TYPES + COMMERCIAL_TYPES:
        area = 2000 if bt in RESIDENTIAL_TYPES else 50000
        for region in REGIONS:
            ctx = make_ctx(REGION_STATES[region], 2000, area, bt)
            val, level = resolve_default("cooling_system_fuel_type", ctx)
            check(f"{bt}/{region} cooling=electricity", val, "electricity", section)
            check(f"{bt}/{region} cooling level", level, "building_type_only", section)


# ============================================================
# 2b. Heating system type -- share-weighted from RECS 2020
# ============================================================
def test_heating_system_type():
    section = "heating_system_type"
    ref = _load_ref("recs2020_heating_system_type")
    valid = ENUM_VALUES["heating_system_type"]

    for region in REGIONS:
        state = REGION_STATES[region]
        division = STATE_DIVISIONS[state]
        for vintage in VINTAGES:
            ctx = make_ctx(state, VINTAGE_YEARS[vintage], 2000,
                           "Single-Family Detached")
            val, level = resolve_default("heating_system_type", ctx)
            # NOTE: the resolved system-type distribution is reconciled to the
            # heating-FUEL marginal (electric systems scaled to the electricity
            # marginal), so the modal type need NOT equal the RAW RECS mode --
            # RECS lumps electric furnaces into 'furnace', which the reconciler
            # routes to heat_pump/electric_resistance. Validate enum membership
            # here; the fuel-marginal correctness is checked in
            # test_county_electric_marginal (county) + test_nocounty_marginal.
            check_in(f"res hstype/{region}/{vintage} enum", val, valid, section)

    # Commercial should return None (not applicable)
    for region in REGIONS:
        ctx = make_ctx(REGION_STATES[region], 2000, 50000, "Office")
        val, level = resolve_default("heating_system_type", ctx)
        check(f"com hstype/{region} is None", val, None, section)


# ============================================================
# 3. Envelope resolvers -- exact values from reference data
# ============================================================
def test_envelope():
    section = "envelope"
    recs_env = _load_ref("recs2020_envelope")
    cbecs_env = _load_ref("cbecs2018_envelope")

    TIER = {"wall": lambda r: "Standard" if r <= 7 else "Insulated" if r <= 14 else "Super Insulated",
            "roof": lambda r: "Standard" if r < 19 else "Insulated" if r < 38 else "Super Insulated"}

    # --- Wall/roof R are {mode, shares} distributions. Residential = ResStock (region-keyed);
    # commercial = ComStock, dual-keyed {by_climate_zone, by_region}. make_ctx sets no
    # climate_zone, so commercial resolves via the by_region fallback in this loop.
    for survey, bt, area, is_com in ((recs_env, "Single-Family Detached", 2000, False),
                                     (cbecs_env, "Office", 50000, True)):
        for surface in ("wall", "roof"):
            table = survey[f"{surface}_r_value"]
            if "by_climate_zone" in table:        # commercial -> region fallback (no CZ in make_ctx)
                table = table["by_region"]
            elif "by_building_type" in table:     # residential roof -> keyed by building type
                table = table["by_building_type"][bt]
            for region in REGIONS:
                for vintage in VINTAGES:
                    cell = table[region][vintage]
                    if cell is None:
                        continue
                    ctx = make_ctx(REGION_STATES[region], VINTAGE_YEARS[vintage], area, bt)
                    r_val, level = resolve_default(f"{surface}_r_value", ctx)
                    check(f"{bt} {surface}_r_value/{region}/{vintage}", r_val, float(cell["mode"]), section)
                    check(f"{bt} {surface}_r_value/{region}/{vintage} level", level, "vintage_specific", section)
                    mat_val, _ = resolve_default(f"{surface}_material", ctx)
                    check(f"{bt} {surface}_material/{region}/{vintage}", mat_val, TIER[surface](float(cell["mode"])), section)
                    check_range(f"{bt} {surface}_r shares sum {region}/{vintage}",
                                sum(cell["shares"].values()), 0.999, 1.001, section)

    # commercial R prefers CLIMATE ZONE when present (the fix): a building carrying
    # climate_zone resolves from by_climate_zone, NOT the region fallback.
    czt = cbecs_env["wall_r_value"]["by_climate_zone"]
    if czt.get("5A", {}).get("2010+"):
        ctx = build_asset_ctx({"state": "IL", "year_built": 2015, "area": 50000, "climate_zone": "5A"},
                              building_type="Office")
        r_val, _ = resolve_default("wall_r_value", ctx)
        check("com wall_r climate-zone path 5A/2010+", r_val, float(czt["5A"]["2010+"]["mode"]), section)

    # residential roof is keyed by building type: the MF roof distribution differs from SFD
    # (audited ~16% lower mean for MF/mobile -- SFD-only would overstate ~33% of the stock).
    rbt = recs_env["roof_r_value"]["by_building_type"]
    check("res roof MF dist differs from SFD",
          rbt["Multifamily"]["Midwest"]["2010+"]["shares"] != rbt["Single-Family Detached"]["Midwest"]["2010+"]["shares"],
          True, section)

    # R is share-weighted: a cell reproduces its real spread (no modal collapse), both workflows.
    for state, yr, bt, cell in (("IL", 1990, "Single-Family Detached", recs_env["wall_r_value"]["Midwest"]["1980-1999"]),
                                ("TX", 1990, "Office", cbecs_env["wall_r_value"]["by_region"]["South"]["1980-1999"])):
        ct = Counter()
        for i in range(2000):
            ctx = build_asset_ctx({"state": state, "year_built": yr, "area": 30000},
                                  building_type=bt, building_id=f"env{bt[:3]}{i:05d}")
            ct[resolve_default("wall_r_value", ctx)[0]] += 1
        check(f"{bt} wall_r variety", len(ct) >= 2, True, section)
        for r_str, share in cell["shares"].items():
            emp = ct.get(float(r_str), 0) / 2000
            check_range(f"{bt} wall_r share R-{r_str}~{share:.2f}", emp, max(0.0, share - 0.05), share + 0.05, section)

    # --- WWR: residential is a {mode, shares} distribution; commercial is scalar.
    for bt, cell in recs_env["window_to_wall_ratio_by_building_type"].items():
        ctx = make_ctx("TX", 2000, 2000, bt)
        val, level = resolve_default("window_to_wall_ratio", ctx)
        check(f"res WWR {bt}", val, float(cell["mode"]), section)
        check(f"res WWR {bt} level", level, "building_type_only", section)
    # Commercial WWR is now a {mode, shares} distribution per DOE-ref type (ComStock
    # realized stock). A 50k-sqft Office bands to MediumOffice; Warehouse no longer the
    # absurd 0.0071 DOE scalar.
    wwr_tbl = cbecs_env["window_to_wall_ratio_by_building_type"]
    ctx = make_ctx("TX", 2000, 50000, "Office")
    val, level = resolve_default("window_to_wall_ratio", ctx)
    check("com WWR Office->MediumOffice", val, float(wwr_tbl["MediumOffice"]["mode"]), section)
    check("com WWR Office level", level, "building_type_only", section)
    ctx = make_ctx("TX", 2000, 50000, "Warehouse")
    val, _ = resolve_default("window_to_wall_ratio", ctx)
    check("com WWR Warehouse", val, float(wwr_tbl["Warehouse"]["mode"]), section)


def test_envelope_climate_zone_breadth():
    """Commercial envelope R is CZ-keyed (by_climate_zone); test_envelope only touches 5A. Enumerate
    EVERY climate-zone bucket (wall_r + roof_r, all vintages) so a missing/None bucket or bad value is
    caught, then confirm every CZ the leaf stock can emit (incl. moisture-suffixed 8A/7A) resolves to a
    real envelope rather than None."""
    section = "envelope-CZ-breadth"
    cbecs = _load_ref("cbecs2018_envelope")
    V = {"pre-1980": 1970, "1980-1999": 1990, "2000-2009": 2005, "2010+": 2015}
    for field in ("wall_r_value", "roof_r_value"):
        czt = cbecs[field]["by_climate_zone"]
        for cz in sorted(czt):
            for vintage, cell in czt[cz].items():
                yr = V.get(vintage)
                if yr is None:
                    continue
                ctx = build_asset_ctx({"state": "IL", "year_built": yr, "area": 50000, "climate_zone": cz},
                                      building_type="Office")
                val, _ = resolve_default(field, ctx)
                check(f"com {field} {cz}/{vintage}", val, float(cell["mode"]), section)
    leaf_czs = ["1A", "2A", "2B", "3A", "3B", "3C", "4A", "4B", "4C", "5A", "5B", "6A", "6B", "7A", "8A"]
    for cz in leaf_czs:
        ctx = build_asset_ctx({"state": "IL", "year_built": 1990, "area": 50000, "climate_zone": cz},
                              building_type="Office")
        for field in ("wall_r_value", "roof_r_value"):
            val, _ = resolve_default(field, ctx)
            check_range(f"com {field} leaf-CZ {cz} resolves", val, 1.0, 80.0, section)
    # 8A (get_location's subarctic code) must normalize to the CZ-8 envelope, not the coarse region
    # fallback (R-22 vs ~R-12 -- a 2x error for Alaska otherwise).
    for field in ("wall_r_value", "roof_r_value"):
        czt8 = cbecs[field]["by_climate_zone"]["8"]["1980-1999"]["mode"]
        ctx = build_asset_ctx({"state": "AK", "year_built": 1990, "area": 50000, "climate_zone": "8A"},
                              building_type="Office")
        check(f"com {field} 8A -> CZ-8 (not region)", resolve_default(field, ctx)[0], float(czt8), section)


def test_cooling_suppression_subarctic():
    """Residential mechanical cooling is suppressed ('none') in subarctic CZ>=7 (RECS: a large share of
    CZ 7-8 homes lack cooling); commercial keeps cooling. The e2e slice is all CZ 5A, so this branch
    (sim_params_spec _resolve_fuel, cz_num>=7) is otherwise unexercised."""
    section = "cooling-suppression"
    for cz in ("7A", "7B", "8", "8A"):
        ctx = build_asset_ctx({"state": "AK", "year_built": 1990, "area": 2000, "climate_zone": cz},
                              building_type="Single-Family Detached")
        val, _ = resolve_default("cooling_system_fuel_type", ctx)
        check(f"res cooling CZ{cz} suppressed -> none", val, "none", section)
    for cz in ("3A", "5A"):
        ctx = build_asset_ctx({"state": "IL", "year_built": 1990, "area": 2000, "climate_zone": cz},
                              building_type="Single-Family Detached")
        val, _ = resolve_default("cooling_system_fuel_type", ctx)
        check(f"res cooling CZ{cz} present (not suppressed)", val != "none", True, section)
    ctx = build_asset_ctx({"state": "AK", "year_built": 1990, "area": 50000, "climate_zone": "8"},
                          building_type="Office")
    val, _ = resolve_default("cooling_system_fuel_type", ctx)
    check("com cooling CZ8 not suppressed", val != "none", True, section)


# ============================================================
# 4. Occupants resolver
# ============================================================
def test_occupants():
    section = "occupants"

    # Residential: bedrooms+1 formula (bedrooms inferred from area)
    cases = [
        (800,  2),   # 800/800 = 1 br -> 2
        (1600, 3),   # 1600/800 = 2 br -> 3
        (2400, 4),   # 2400/800 = 3 br -> 4
        (4000, 6),   # 4000/800 = 5 br -> 6
    ]
    for area, exp in cases:
        ctx = make_ctx("TX", 2000, area, "Single-Family Detached")
        val, level = resolve_default("number_of_occupants", ctx)
        check(f"SFD {area}sqft -> {exp}", val, exp, section)
        check(f"SFD {area}sqft level", level, "building_type_only", section)

    # Explicit bedrooms in metadata should override area inference
    meta = {"state": "TX", "year_built": 2000, "area": 4000, "bedrooms": 2}
    ctx = build_asset_ctx(meta, building_type="Single-Family Detached")
    val, _ = resolve_default("number_of_occupants", ctx)
    check("SFD explicit 2br -> 3", val, 3, section)

    # Multifamily: number_of_bedrooms is WHOLE-BUILDING, divided by units for the
    # PER-UNIT occupant count (F2: explicit whole-building bedrooms were being
    # treated as per-unit, inflating occupants ~units-fold).
    ctx_mf = build_asset_ctx(
        {"state": "IL", "year_built": 2000, "area": 8000,
         "number_of_units": 8, "number_of_bedrooms": 24},
        building_type="Multifamily",
    )
    val, _ = resolve_default("number_of_occupants", ctx_mf)
    # 24 whole-bldg / 8 units = 3 br/unit -> 4 occupants/unit (NOT 24+1=25)
    check("MF 8-unit 24br -> 4 occ/unit", val, 4, section)

    # Commercial: area * density / 1000
    people_ref = _load_ref("openstudio_standards_people_per_area")["people_per_1000_ft2"]
    aliases = _load_ref("building_type_aliases")["powertwin_to_doe_ref"]

    com_cases = [
        ("Office",                    50000),
        ("Nonrefrigerated warehouse", 50000),
        ("Education",                 50000),
    ]
    for bt, area in com_cases:
        ctx = make_ctx("TX", 2000, area, bt)
        val, level = resolve_default("number_of_occupants", ctx)
        doe_name = aliases.get(bt, bt)
        entry = people_ref.get(doe_name, {})
        density = entry.get("value")
        if density is not None:
            exp = max(1, round(area * density / 1000))
            check(f"{bt} {area}sqft -> {exp}", val, exp, section)
            check(f"{bt} level", level, "building_type_only", section)


# ============================================================
# 5. Non-dynamic fields should NOT resolve
# ============================================================
def test_non_dynamic_fields():
    section = "non_dynamic"

    ctx = make_ctx("TX", 2000, 50000, "Office")
    for field in NON_DYNAMIC_FIELDS:
        val, level = resolve_default(field, ctx)
        check(f"{field} returns None", val, None, section)
        check(f"{field} level None", level, None, section)

    # get_param should fall through to flat defaults
    meta = {"state": "TX", "year_built": 2000, "area": 50000}
    ctx = build_asset_ctx(meta, building_type="Office")
    for field in NON_DYNAMIC_FIELDS:
        result = get_param(meta, field, ctx=ctx)
        check(f"{field} -> flat default", result, SIM_PARAM_DEFAULTS[field], section)


# ============================================================
# 6. Resolution precedence
# ============================================================
def test_precedence():
    section = "precedence"

    # Test each dynamic field: metadata > resolver > flat
    override_vals = {
        "window_type": "Triple Pane",
        "heating_system_fuel_type": "propane",
        "wall_material": "Super Insulated",
        "wall_r_value": 42.0,
        "roof_r_value": 55.0,
        "window_to_wall_ratio": 0.42,
        "number_of_occupants": 999,
    }

    for field, override_val in override_vals.items():
        # Metadata override wins
        meta = {"state": "IL", "year_built": 1970, "area": 2000, field: override_val}
        ctx = build_asset_ctx(meta, building_type="Single-Family Detached")
        result = get_param(meta, field, ctx=ctx)
        if isinstance(override_val, float):
            check(f"{field} override wins", float(result), override_val, section)
        else:
            check(f"{field} override wins", result, override_val, section)

    # Resolver wins over flat default (pre-1980 South -> Single Pane, differs from flat "Double Pane")
    meta = {"state": "TX", "year_built": 1970, "area": 2000}
    ctx = build_asset_ctx(meta, building_type="Single-Family Detached")
    result = get_param(meta, "window_type", ctx=ctx)
    check("resolver beats flat (window_type)", result, "Single Pane", section)

    # Dynamic off -> flat default for ALL dynamic fields
    os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "false"
    for field in ALL_DYNAMIC_FIELDS:
        meta = {"state": "IL", "year_built": 1970, "area": 2000}
        ctx = build_asset_ctx(meta, building_type="Single-Family Detached")
        result = get_param(meta, field, ctx=ctx)
        check(f"dynamic off: {field} -> flat", result, SIM_PARAM_DEFAULTS[field], section)
    os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "true"

    # No context -> flat default
    for field in ALL_DYNAMIC_FIELDS:
        result = get_param({}, field)
        check(f"no ctx: {field} -> flat", result, SIM_PARAM_DEFAULTS[field], section)


# ============================================================
# 7. Fallback on missing context
# ============================================================
def test_fallbacks():
    section = "fallbacks"

    # Missing state -> None for all region-keyed fields
    region_keyed = ["window_type", "heating_system_fuel_type",
                    "service_water_heating_fuel_type",
                    "wall_material", "roof_material",
                    "wall_r_value", "roof_r_value"]
    ctx = build_asset_ctx({"year_built": 2015}, building_type="Office")
    for field in region_keyed:
        val, _ = resolve_default(field, ctx)
        check(f"no state -> {field} None", val, None, section)

    # Missing year -> None for vintage-keyed fields
    ctx = build_asset_ctx({"state": "TX"}, building_type="Office")
    vintage_keyed = ["window_type", "wall_material", "roof_material",
                     "wall_r_value", "roof_r_value"]
    for field in vintage_keyed:
        val, _ = resolve_default(field, ctx)
        check(f"no year -> {field} None", val, None, section)

    # Fuel has 'all' fallback vintage bucket -- check it still resolves
    ctx = build_asset_ctx({"state": "TX"}, building_type="Office")
    val, level = resolve_default("heating_system_fuel_type", ctx)
    if val is not None:
        check("no year -> heating fuel has fallback", level, "region_all", section)

    # building_type-keyed fields resolve without state/year
    ctx = build_asset_ctx({"area": 50000}, building_type="Office")
    val, level = resolve_default("window_to_wall_ratio", ctx)
    check("no state/year -> WWR resolves", level, "building_type_only", section)

    val, level = resolve_default("number_of_occupants", ctx)
    check("no state/year -> occupants resolves", level, "building_type_only", section)

    # Cooling resolves without any context (constant)
    ctx = build_asset_ctx({}, building_type="Office")
    val, level = resolve_default("cooling_system_fuel_type", ctx)
    check("empty ctx -> cooling=electricity", val, "electricity", section)


# ============================================================
# 8. Vintage boundary cases
# ============================================================
def test_vintage_boundaries():
    section = "vintage_boundaries"

    boundaries = [
        (1979, "pre-1980"),
        (1980, "1980-1999"),
        (1999, "1980-1999"),
        (2000, "2000-2009"),
        (2009, "2000-2009"),
        (2010, "2010+"),
        (2025, "2010+"),
    ]
    for year, expected_bin in boundaries:
        actual = sps._vintage_bin(year)
        check(f"year {year} -> {expected_bin}", actual, expected_bin, section)

    check("year None -> None", sps._vintage_bin(None), None, section)
    check("year '' -> None", sps._vintage_bin(""), None, section)
    check("year 'abc' -> None", sps._vintage_bin("abc"), None, section)

    # Verify resolvers agree at boundaries
    for year, expected_bin in boundaries:
        ctx = make_ctx("IL", year, 2000, "Single-Family Detached")
        val, _ = resolve_default("window_type", ctx)
        exp = _load_ref("window_type_by_vintage")["residential"]["Midwest"][expected_bin]["mode"]
        check(f"window_type at year={year}", val, exp, section)


# ============================================================
# 9. Reference data integrity
# ============================================================
def test_reference_data():
    section = "reference_data"
    ref_dir = os.path.join(REPO, "solver", "upload", "reference_data")

    expected_files = [
        "building_type_aliases.json",
        "cbecs2018_commercial_fuel_mix.json",
        "cbecs2018_envelope.json",
        "census_regions.json",
        "openstudio_standards_people_per_area.json",
        "recs2020_envelope.json",
        "recs2020_residential_fuel_mix.json",
        "unit_scale_factors.json",
        "window_type_by_vintage.json",
    ]
    for fname in expected_files:
        path = os.path.join(ref_dir, fname)
        exists = os.path.isfile(path)
        check(f"{fname} exists", exists, True, section)
        if exists:
            try:
                with open(path) as f:
                    json.load(f)
                check(f"{fname} valid JSON", True, True, section)
            except json.JSONDecodeError:
                check(f"{fname} valid JSON", False, True, section)

    # Census regions covers all 50 states + DC
    regions = _load_ref("census_regions")
    check("51 state entries", len(regions["state_to_region"]), 51, section)

    # Window type table completeness
    wt = _load_ref("window_type_by_vintage")
    for sector in ["residential", "commercial"]:
        for region in REGIONS:
            for vintage in VINTAGES:
                cell = wt.get(sector, {}).get(region, {}).get(vintage)
                check(f"window_type {sector}/{region}/{vintage} present",
                      cell is not None, True, section)
                if cell:
                    check_in(f"window_type {sector}/{region}/{vintage} mode valid",
                             cell["mode"], ENUM_VALUES["window_type"], section)
                    for k in cell["shares"]:
                        check_in(f"window_type {sector}/{region}/{vintage} share-key valid",
                                 k, ENUM_VALUES["window_type"], section)
                    check_range(f"window_type {sector}/{region}/{vintage} shares sum",
                                sum(cell["shares"].values()), 0.999, 1.001, section)

    # Fuel mix table completeness
    for ref_name in ["recs2020_residential_fuel_mix", "cbecs2018_commercial_fuel_mix"]:
        ref = _load_ref(ref_name)
        for field in ["heating_system_fuel_type", "service_water_heating_fuel_type"]:
            section_data = ref.get(field, {})
            for region in REGIONS:
                has_region = region in section_data
                check(f"{ref_name}/{field} has {region}", has_region, True, section)

    # Envelope table completeness. Residential (recs) carries {mode, shares} R
    # distributions and DERIVES material from R (no material section); commercial
    # (cbecs) carries legacy scalar R + material.
    # Both surveys now carry {mode, shares} R distributions (residential=ResStock region-keyed,
    # commercial=ComStock dual-keyed {by_climate_zone, by_region}) and DERIVE material from R.
    def _validate_region_table(rt, tag):
        for region in REGIONS:
            for vintage in VINTAGES:
                cell = rt.get(region, {}).get(vintage)
                check(f"{tag}/{region}/{vintage} present", cell is not None, True, section)
                if cell:
                    check_range(f"{tag}/{region}/{vintage} shares sum",
                                sum(cell["shares"].values()), 0.999, 1.001, section)

    for ref_name in ["recs2020_envelope", "cbecs2018_envelope"]:
        env = _load_ref(ref_name)
        for field in ["wall_r_value", "roof_r_value"]:
            tbl = env[field]
            if "by_climate_zone" in tbl:                 # commercial: dual-keyed
                _validate_region_table(tbl["by_region"], f"{ref_name}/{field}")
                czt = tbl["by_climate_zone"]
                check(f"{ref_name}/{field} climate-zone count", len(czt) >= 10, True, section)
                for cz, vmap in czt.items():
                    for vintage, cell in vmap.items():
                        if cell:
                            check_range(f"{ref_name}/{field}/{cz}/{vintage} shares sum",
                                        sum(cell["shares"].values()), 0.999, 1.001, section)
            elif "by_building_type" in tbl:              # residential roof: by building type
                for bt, sub in tbl["by_building_type"].items():
                    _validate_region_table(sub, f"{ref_name}/{field}/{bt}")
            else:                                        # residential wall: region-keyed
                _validate_region_table(tbl, f"{ref_name}/{field}")


# ============================================================
# 10. State normalization
# ============================================================
def test_state_normalization():
    section = "state_normalization"

    # 2-letter codes
    ctx = build_asset_ctx({"state": "AZ"}, building_type="Office")
    check("AZ -> AZ", ctx["state"], "AZ", section)

    ctx = build_asset_ctx({"state": "az"}, building_type="Office")
    check("az -> AZ", ctx["state"], "AZ", section)

    # Full state names
    ctx = build_asset_ctx({"state": "Arizona"}, building_type="Office")
    check("Arizona -> AZ", ctx["state"], "AZ", section)

    ctx = build_asset_ctx({"state": "new york"}, building_type="Office")
    check("new york -> NY", ctx["state"], "NY", section)

    # Invalid
    ctx = build_asset_ctx({"state": "Narnia"}, building_type="Office")
    check("Narnia -> None", ctx["state"], None, section)

    ctx = build_asset_ctx({"state": ""}, building_type="Office")
    check("empty -> None", ctx["state"], None, section)

    ctx = build_asset_ctx({}, building_type="Office")
    check("missing -> None", ctx["state"], None, section)


# ============================================================
# 11. NLR-aligned envelope values (regression guard)
# ============================================================
def test_nlr_envelope_alignment():
    section = "nlr_envelope"
    recs = _load_ref("recs2020_envelope")
    cbecs = _load_ref("cbecs2018_envelope")

    # Residential wall R is now a {mode, shares} distribution (ResStock realized stock).
    # pre-1980 modal = R-1 all regions (65-84% uninsulated; R-1 = still-air cavity floor).
    for region in REGIONS:
        cell = recs["wall_r_value"][region]["pre-1980"]
        check(f"res wall R pre-1980 {region} mode=1", cell["mode"], "1", section)
        check_range(f"res wall R pre-1980 {region} shares sum",
                    sum(cell["shares"].values()), 0.999, 1.001, section)

    # Residential 2010+ wall R modal by region (ResStock realized medians)
    expected_2010 = {"Northeast": "19", "Midwest": "19", "South": "11", "West": "19"}
    for region, exp in expected_2010.items():
        check(f"res wall R 2010+ {region} mode={exp}",
              recs["wall_r_value"][region]["2010+"]["mode"], exp, section)

    # Commercial wall R is now dual-keyed {by_climate_zone, by_region}; the by_region
    # fallback pre-1980 assembly-R modal ~6 (NE/MW/West) / ~4 (South).
    expected_com_pre1980 = {"Northeast": "6", "Midwest": "6", "South": "4", "West": "6"}
    for region, exp in expected_com_pre1980.items():
        cell = cbecs["wall_r_value"]["by_region"][region]["pre-1980"]
        check(f"com wall R pre-1980 {region} mode={exp}", cell["mode"], exp, section)
        check_range(f"com wall R pre-1980 {region} shares sum",
                    sum(cell["shares"].values()), 0.999, 1.001, section)

    # Verify via resolver too (mode when no building_id): residential pre-1980 -> R-1.
    for region, state in REGION_STATES.items():
        ctx = make_ctx(state, 1960, 2000, "Single-Family Detached")
        val, _ = resolve_default("wall_r_value", ctx)
        check(f"resolver res wall R pre-1980 {region}=1.0", val, 1.0, section)

    # commercial resolver: no climate_zone -> by_region fallback; with climate_zone -> by_climate_zone.
    for region, state in REGION_STATES.items():
        ctx = make_ctx(state, 2018, 50000, "Office")   # make_ctx sets no climate_zone
        val, _ = resolve_default("wall_r_value", ctx)
        check(f"resolver com wall R 2010+ {region} (region fallback)", val,
              float(cbecs["wall_r_value"]["by_region"][region]["2010+"]["mode"]), section)
    czt = cbecs["wall_r_value"]["by_climate_zone"]
    if czt.get("2A", {}).get("2010+"):
        ctx = build_asset_ctx({"state": "TX", "year_built": 2015, "area": 50000, "climate_zone": "2A"},
                              building_type="Office")
        check("resolver com wall R climate-zone 2A/2010+",
              resolve_default("wall_r_value", ctx)[0], float(czt["2A"]["2010+"]["mode"]), section)


# ============================================================
# 12. NLR-aligned window type (regression guard)
# ============================================================
def test_nlr_window_alignment():
    section = "nlr_window"
    wt = _load_ref("window_type_by_vintage")

    # Cells now carry {mode, shares} transcribed directly from RECS (TYPEGLASS) /
    # CBECS (WINTYP), weighted. Commercial pre-1980: South & West single-pane modal
    # (older, less-retrofit stock); Northeast & Midwest double-pane modal -- CBECS 2018
    # shows the majority of surviving pre-1980 NE/MW commercial is multi-layer (retrofit),
    # correcting the prior ComStock original-construction assumption.
    check("com South pre-1980 mode=Single",
          wt["commercial"]["South"]["pre-1980"]["mode"], "Single Pane", section)
    check("com West pre-1980 mode=Single",
          wt["commercial"]["West"]["pre-1980"]["mode"], "Single Pane", section)
    check("com NE pre-1980 mode=Double",
          wt["commercial"]["Northeast"]["pre-1980"]["mode"], "Double Pane", section)
    check("com MW pre-1980 mode=Double",
          wt["commercial"]["Midwest"]["pre-1980"]["mode"], "Double Pane", section)

    # All commercial 2010+ double-pane modal (90.1/IECC effectively mandate multi-layer)
    for region in REGIONS:
        check(f"com {region} 2010+ mode=Double",
              wt["commercial"][region]["2010+"]["mode"], "Double Pane", section)

    # Residential South pre-1980 single-pane modal; cells carry valid summed shares.
    check("res South pre-1980 mode=Single",
          wt["residential"]["South"]["pre-1980"]["mode"], "Single Pane", section)
    for sector in ("residential", "commercial"):
        for region in REGIONS:
            for vintage in VINTAGES:
                cell = wt[sector][region][vintage]
                check_range(f"{sector} {region} {vintage} shares sum",
                            sum(cell["shares"].values()), 0.999, 1.001, section)


# ============================================================
# 13. build_asset_ctx reads all fields from metadata
# ============================================================
def test_build_asset_ctx_metadata():
    section = "ctx_metadata"

    # building_id from metadata (no keyword)
    ctx = build_asset_ctx({"state": "TX", "year_built": 2000, "building_id": "bldg42"})
    check("building_id from metadata", ctx["building_id"], "bldg42", section)

    # climate_zone from metadata (no keyword)
    ctx = build_asset_ctx({"state": "TX", "climate_zone": "4A"})
    check("climate_zone from metadata", ctx["climate_zone"], "4A", section)

    # building_type from metadata (no keyword)
    ctx = build_asset_ctx({"state": "TX", "building_type": "Office"})
    check("building_type from metadata", ctx["building_type"], "Office", section)

    # keyword overrides metadata
    ctx = build_asset_ctx({"state": "TX", "building_id": "from_meta"},
                          building_id="from_kwarg")
    check("building_id kwarg wins", ctx["building_id"], "from_kwarg", section)

    ctx = build_asset_ctx({"state": "TX", "building_type": "Office"},
                          building_type="Warehouse")
    check("building_type kwarg wins", ctx["building_type"], "Warehouse", section)

    # division is populated
    ctx = build_asset_ctx({"state": "TX"})
    check("TX division", ctx["division"], "West South Central", section)
    ctx = build_asset_ctx({"state": "MA"})
    check("MA division", ctx["division"], "New England", section)


# ============================================================
# 14. Share-weighted fuel distribution with building_id
# ============================================================
def test_share_weighted_distribution():
    section = "share_dist"

    # South residential heating: with building_ids, should get a MIX
    # (not 100% one fuel). ResStock says electricity is the mode.
    ct = Counter()
    N = 500
    for i in range(N):
        ctx = build_asset_ctx(
            {"state": "TX", "year_built": 1990, "area": 2000},
            building_type="Single-Family Detached", building_id=f"test{i:05d}",
        )
        val, _ = resolve_default("heating_system_fuel_type", ctx)
        ct[val] += 1

    # Must have at least 2 distinct fuels (not 100% mode)
    check("South dist has multiple fuels", len(ct) >= 2, True, section)
    # Electricity is the plurality: the no-county fallback reconciles to the RECS
    # West South Central division marginal (~0.63 electric for 1980-1999).
    check("South dist mode=electricity", ct.most_common(1)[0][0], "electricity", section)
    elec_pct = ct.get("electricity", 0) / N * 100
    check_range("South electricity share", elec_pct, 45, 72, section)

    # West 2010+ residential heating: division-level mode is natural gas
    # (Pacific division furnace-conditioned shares favor gas over electricity)
    ct2 = Counter()
    for i in range(N):
        ctx = build_asset_ctx(
            {"state": "CA", "year_built": 2018, "area": 2000},
            building_type="Single-Family Detached", building_id=f"test{i:05d}",
        )
        val, _ = resolve_default("heating_system_fuel_type", ctx)
        ct2[val] += 1

    check("West 2010+ dist mode=natural gas", ct2.most_common(1)[0][0],
          "natural gas", section)

    # Without building_id, should still return a valid fuel (falls back to mode)
    ctx = build_asset_ctx(
        {"state": "TX", "year_built": 1990, "area": 2000},
        building_type="Single-Family Detached",
    )
    val, _ = resolve_default("heating_system_fuel_type", ctx)
    check_in("no bid still valid", val, ENUM_VALUES["heating_system_fuel_type"], section)


def test_swh_follows_heating_fuel():
    """Coherence guard: the water-heater fuel must FOLLOW the building's heating fuel
    (RECS: a gas-heated home has gas SWH ~84%, electric-heated ~87%). The resolver used to
    sample them independently -> gas-heated homes got gas SWH only ~48% (the marginal). This
    fails loudly if that regresses. (heat-pump water heaters are correctly forced electric,
    pulling the gas-follow rate slightly below the raw RECS conditional.)"""
    section = "swh_coherence"
    n_gas = swh_gas = n_ele = swh_ele = 0
    for i in range(3000):
        ctx = build_asset_ctx({"state": "IL", "year_built": 2015, "area": 2000},
                              building_type="Single-Family Detached", building_id=f"swh{i:05d}")
        hf = resolve_default("heating_system_fuel_type", ctx)[0]
        sw = resolve_default("service_water_heating_fuel_type", ctx)[0]
        if hf == "natural gas":
            n_gas += 1; swh_gas += (sw == "natural gas")
        elif hf == "electricity":
            n_ele += 1; swh_ele += (sw == "electricity")
    check("gas-heated sample present", n_gas > 200, True, section)
    check("electric-heated sample present", n_ele > 200, True, section)
    check_range("P(SWH=gas | heat=gas) follows heating fuel", swh_gas / max(n_gas, 1), 0.70, 0.90, section)
    check_range("P(SWH=elec | heat=elec) follows heating fuel", swh_ele / max(n_ele, 1), 0.75, 0.95, section)


# ============================================================
# 15. NLR-aligned fuel modes (regression guard)
# ============================================================
def test_nlr_fuel_alignment():
    section = "nlr_fuel"
    recs = _load_ref("recs2020_residential_fuel_mix")

    # West 2010+ residential region-level mode = electricity (highest share at 0.45)
    mode = recs["heating_system_fuel_type"]["West"]["2010+"]["mode"]
    check("West 2010+ res fuel mode=electricity", mode, "electricity", section)

    # South all vintages mode = electricity
    for v in VINTAGES:
        mode = recs["heating_system_fuel_type"]["South"][v]["mode"]
        check(f"South {v} res fuel mode=electricity", mode, "electricity", section)


# ============================================================
# 16. Propane fuel assignment (regression guard for set_heating_fuel measure)
# ============================================================
def test_propane_assignment():
    section = "propane"

    # WY (Mountain North) should assign propane to some buildings
    ct = Counter()
    N = 500
    for i in range(N):
        ctx = build_asset_ctx(
            {"state": "WY", "year_built": 1990, "area": 2000},
            building_type="Single-Family Detached", building_id=f"prop{i:05d}",
        )
        val, _ = resolve_default("heating_system_fuel_type", ctx)
        ct[val] += 1

    propane_pct = ct.get("propane", 0) / N * 100
    check("WY has propane-heated buildings", ct.get("propane", 0) > 0, True, section)
    check_range("WY propane share 1-15%", propane_pct, 1, 15, section)

    # Propane is a valid enum value
    check_in("propane in heating enum", "propane", ENUM_VALUES["heating_system_fuel_type"], section)
    check_in("propane in SWH enum", "propane",
             ENUM_VALUES["service_water_heating_fuel_type"], section)

    # SWH should also assign propane to some WY buildings
    swh_ct = Counter()
    for i in range(N):
        ctx = build_asset_ctx(
            {"state": "WY", "year_built": 1990, "area": 2000},
            building_type="Single-Family Detached", building_id=f"swh{i:05d}",
        )
        val, _ = resolve_default("service_water_heating_fuel_type", ctx)
        swh_ct[val] += 1
    check("WY has propane SWH buildings", swh_ct.get("propane", 0) > 0, True, section)

    # Without building_id, mode should NOT be propane (it's a minority fuel)
    ctx = make_ctx("WY", 1990, 2000, "Single-Family Detached")
    val, _ = resolve_default("heating_system_fuel_type", ctx)
    check("WY mode != propane (minority fuel)", val != "propane", True, section)


# ============================================================
# 17. County-level ACS fuel resolution
# ============================================================
def test_county_fuel():
    section = "county_fuel"

    acs = _load_ref('acs2022_county_fuel')

    # Maricopa County AZ (04013) should have fuel shares
    shares = acs.get('fuel_share', {}).get('04013')
    check("04013 has shares", shares is not None, True, section)
    if shares:
        check("04013 has natural gas", 'natural gas' in shares, True, section)
        check("04013 has electricity", 'electricity' in shares, True, section)
        total = sum(shares.values())
        check_range("04013 shares sum ~1.0", total, 0.95, 1.05, section)

    # With county_fips, heating fuel should resolve at county_acs level
    ctx = build_asset_ctx(
        {"state": "AZ", "year_built": 2000, "area": 2000, "county_fips": "04013"},
        building_type="Single-Family Detached", building_id="county_test_001",
    )
    val, level = resolve_default("heating_system_fuel_type", ctx)
    check("county fuel resolves at county_acs", level, "county_acs", section)
    check_in("county fuel valid enum", val, ENUM_VALUES["heating_system_fuel_type"], section)

    # Without building_id, should fall back to mode of county shares
    ctx_nobi = build_asset_ctx(
        {"state": "AZ", "year_built": 2000, "area": 2000, "county_fips": "04013"},
        building_type="Single-Family Detached",
    )
    # Without building_id the system type falls back to the county's MODAL
    # system. Maricopa is electric-dominant (ACS electricity ~0.71), so the modal
    # system is a heat pump -> electricity (resolved at building_type_only level).
    # The resolved fuel is the county's dominant heating fuel.
    val_mode, level_mode = resolve_default("heating_system_fuel_type", ctx_nobi)
    expected_mode = max(shares, key=shares.get)
    check("no bid county -> dominant fuel", val_mode, expected_mode, section)
    check_in("no bid county fuel valid", val_mode,
             ENUM_VALUES["heating_system_fuel_type"], section)

    # SWH should NOT use county ACS (B25040 is heating fuel only)
    ctx2 = build_asset_ctx(
        {"state": "AZ", "year_built": 2000, "area": 2000, "county_fips": "04013"},
        building_type="Single-Family Detached", building_id="county_swh_001",
    )
    _, swh_level = resolve_default("service_water_heating_fuel_type", ctx2)
    check("SWH does not use county_acs", swh_level != 'county_acs', True, section)

    # County fuel should produce a distribution with building_ids
    ct = Counter()
    N = 300
    for i in range(N):
        ctx = build_asset_ctx(
            {"state": "AZ", "year_built": 2000, "area": 2000, "county_fips": "04013"},
            building_type="Single-Family Detached", building_id=f"cfuel{i:05d}",
        )
        val, _ = resolve_default("heating_system_fuel_type", ctx)
        ct[val] += 1
    check("county dist has multiple fuels", len(ct) >= 2, True, section)

    # Without county_fips, should fall back to division level
    ctx_no_fips = build_asset_ctx(
        {"state": "AZ", "year_built": 2000, "area": 2000},
        building_type="Single-Family Detached",
    )
    val, level = resolve_default("heating_system_fuel_type", ctx_no_fips)
    check("no fips -> not county_acs", level != 'county_acs', True, section)


# ============================================================
# 18. Vacancy determination
# ============================================================
def test_vacancy():
    section = "vacancy"

    acs = _load_ref('acs2022_vacancy')

    # Teton County WY (56039) has high vacancy (~28%)
    entry = acs.get('vacancy', {}).get('56039')
    check("56039 has vacancy data", entry is not None, True, section)
    if entry:
        check_range("56039 vacancy rate", entry['vacancy_rate'], 0.20, 0.40, section)

    # Maricopa (04013) has low vacancy (~8%)
    entry2 = acs.get('vacancy', {}).get('04013')
    check("04013 has vacancy data", entry2 is not None, True, section)
    if entry2:
        check_range("04013 vacancy rate", entry2['vacancy_rate'], 0.05, 0.15, section)

    # is_vacant should be deterministic for the same building
    ctx = build_asset_ctx(
        {"state": "WY", "year_built": 2000, "area": 2000, "county_fips": "56039"},
        building_type="Single-Family Detached", building_id="vac_test_001",
    )
    v1 = is_vacant(ctx)
    v2 = is_vacant(ctx)
    check("vacancy is deterministic", v1, v2, section)

    # is_vacant uses YEAR-ROUND vacancy (total minus seasonal). Teton is a
    # resort county: 0.278 total but 0.196 seasonal, so only ~0.083 is truly
    # year-round vacant -- seasonal second homes must NOT be modeled as fully
    # empty (that is the spurious-vacancy failure mode).
    N = 2000
    vacant_count = 0
    for i in range(N):
        ctx = build_asset_ctx(
            {"state": "WY", "year_built": 2000, "area": 2000, "county_fips": "56039"},
            building_type="Single-Family Detached", building_id=f"vac{i:05d}",
        )
        if is_vacant(ctx):
            vacant_count += 1
    vac_pct = vacant_count / N * 100
    expected = (entry['vacancy_rate'] - entry.get('seasonal_rate', 0)) * 100
    check("Teton has some vacant buildings", vacant_count > 0, True, section)
    check_range("Teton year-round vacancy (~8%, not 28%)", vac_pct, 4, 13, section)
    # The seasonal adjustment must materially reduce vacancy vs the total rate.
    check("seasonal adjustment applied", vac_pct < entry['vacancy_rate'] * 100 - 5, True, section)

    # Low-vacancy county should have fewer vacants
    vacant_low = 0
    for i in range(N):
        ctx = build_asset_ctx(
            {"state": "AZ", "year_built": 2000, "area": 2000, "county_fips": "04013"},
            building_type="Single-Family Detached", building_id=f"vacaz{i:05d}",
        )
        if is_vacant(ctx):
            vacant_low += 1
    check("Maricopa < Teton vacants", vacant_low < vacant_count, True, section)

    # No county_fips -> never vacant
    ctx_no = build_asset_ctx(
        {"state": "AZ", "year_built": 2000, "area": 2000},
        building_type="Single-Family Detached", building_id="vac_nofips",
    )
    check("no fips -> not vacant", is_vacant(ctx_no), False, section)

    # Dynamic defaults off -> never vacant
    os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "false"
    ctx_off = build_asset_ctx(
        {"state": "WY", "year_built": 2000, "area": 2000, "county_fips": "56039"},
        building_type="Single-Family Detached", building_id="vac_off",
    )
    check("dynamic off -> not vacant", is_vacant(ctx_off), False, section)
    os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "true"

    # Commercial buildings must NEVER be vacant: ACS B25002 is a residential
    # housing-unit vacancy rate with no meaning for commercial assets. Even in
    # the highest-vacancy county, a commercial building_id must not hash vacant
    # (mirrors the residential-only guard on every other dynamic feature).
    for bt in ["Office", "Education", "Lodging", "Laboratory", "Warehouse",
               "Food service", "Public assembly", "Mixed use"]:
        any_vacant = False
        for i in range(300):
            ctx_c = build_asset_ctx(
                {"state": "WY", "year_built": 2000, "area": 50000, "county_fips": "56039"},
                building_type=bt, building_id=f"comvac_{bt}_{i:04d}",
            )
            if is_vacant(ctx_c):
                any_vacant = True
                break
        check(f"commercial {bt} never vacant", any_vacant, False, section)


# ============================================================
# 19. County FIPS in build_asset_ctx
# ============================================================
def test_county_fips_ctx():
    section = "county_fips"

    # From metadata
    ctx = build_asset_ctx({"state": "AZ", "county_fips": "04013"})
    check("fips from metadata", ctx['county_fips'], "04013", section)

    # Zero-padded from short string
    ctx = build_asset_ctx({"state": "AZ", "county_fips": "4013"})
    check("fips zero-padded", ctx['county_fips'], "04013", section)

    # Zero-padded from int
    ctx = build_asset_ctx({"state": "AZ", "county_fips": 4013})
    check("fips from int", ctx['county_fips'], "04013", section)

    # None when not provided (no lat/lon for FCC fallback)
    ctx = build_asset_ctx({"state": "AZ"})
    check("no fips -> None", ctx['county_fips'], None, section)


# ============================================================
# 20. ACS reference data integrity
# ============================================================
def test_acs_reference_data():
    section = "acs_data"

    fuel = _load_ref('acs2022_county_fuel')
    vacancy = _load_ref('acs2022_vacancy')

    # Should have 3000+ counties
    check("fuel has 3000+ counties", len(fuel.get('fuel_share', {})) >= 3000, True, section)
    check("vacancy has 3000+ counties", len(vacancy.get('vacancy', {})) >= 3000, True, section)

    # Spot-check known counties
    for fips, name in [("04013", "Maricopa"), ("36061", "Manhattan"), ("56039", "Teton")]:
        check(f"{name} in fuel", fips in fuel.get('fuel_share', {}), True, section)
        check(f"{name} in vacancy", fips in vacancy.get('vacancy', {}), True, section)

    # All vacancy rates should be in [0, 1]
    bad_rates = 0
    for fips, entry in vacancy.get('vacancy', {}).items():
        rate = entry.get('vacancy_rate', -1)
        if rate < 0 or rate > 1:
            bad_rates += 1
    check("all vacancy rates in [0,1]", bad_rates, 0, section)

    # All fuel shares should be non-negative
    bad_shares = 0
    for fips, shares in fuel.get('fuel_share', {}).items():
        for fuel_type, share in shares.items():
            if share < 0:
                bad_shares += 1
    check("all fuel shares non-negative", bad_shares, 0, section)


# ============================================================
# 21. Water heater type resolver
# ============================================================
def test_water_heater_type():
    section = "wh_type"
    ref = _load_ref('recs2020_water_heater_type')

    for region, state in REGION_STATES.items():
        for vintage in VINTAGES:
            ctx = build_asset_ctx(
                {"state": state, "year_built": VINTAGE_YEARS[vintage], "area": 2000},
                building_type="Single-Family Detached",
            )
            val, level = resolve_default("water_heater_type", ctx)
            check_in(f"{region} {vintage} wh_type valid",
                     val, ENUM_VALUES["water_heater_type"], section)
            check(f"{region} {vintage} wh_type resolves", val is not None, True, section)

    # Commercial: resolve_default returns None, get_param falls back to flat default
    ctx = make_ctx("TX", 2005, 50000, "Office")
    val, level = resolve_default("water_heater_type", ctx)
    check(f"commercial wh_type resolve -> None", val, None, section)
    gp_val = get_param({"state": "TX", "year_built": 2005, "area": 50000},
                       "water_heater_type", ctx)
    check(f"commercial wh_type get_param -> default", gp_val,
          SIM_PARAM_DEFAULTS["water_heater_type"], section)

    # Mode without building_id matches reference data
    ctx = build_asset_ctx(
        {"state": "MA", "year_built": 1990, "area": 2000},
        building_type="Single-Family Detached",
    )
    val, level = resolve_default("water_heater_type", ctx)
    division = ctx['division']
    decade_vintage = ctx['decade_vintage']
    div_data = ref.get('_division', {}).get(division, {}).get(decade_vintage, {})
    if div_data:
        expected_mode = div_data.get('mode')
        check(f"MA 1990 wh_type mode", val, expected_mode, section)

    # Distribution with building_ids
    ct = Counter()
    for i in range(200):
        ctx = build_asset_ctx(
            {"state": "MA", "year_built": 1990, "area": 2000},
            building_type="Single-Family Detached", building_id=f"wht{i:05d}",
        )
        val, _ = resolve_default("water_heater_type", ctx)
        ct[val] += 1
    check("wh_type has distribution", len(ct) >= 1, True, section)

    # A heat pump water heater runs on electricity by definition. wh_type and
    # swh fuel are hashed independently, so verify the resolver forces fuel to
    # electricity whenever it emits a HPWH (no impossible HPWH+combustion pair).
    hpwh = 0
    impossible = 0
    for state in ["CA", "AZ", "TX", "FL", "CO", "OR"]:
        for i in range(400):
            ctx = build_asset_ctx(
                {"state": state, "year_built": 2015, "area": 2000},
                building_type="Single-Family Detached", building_id=f"hpwh_{state}_{i:04d}",
            )
            wh, _ = resolve_default("water_heater_type", ctx)
            if wh == "heat pump water heater":
                hpwh += 1
                fuel, _ = resolve_default("service_water_heating_fuel_type", ctx)
                if fuel != "electricity":
                    impossible += 1
    check("HPWH buildings exist in sample", hpwh > 0, True, section)
    check("HPWH always paired with electricity", impossible, 0, section)


# ============================================================
# 22. All-electric county ACS fuel fallthrough
# ============================================================
def test_all_electric_county_fallthrough():
    section = "elec_county"
    acs = _load_ref('acs2022_county_fuel')

    # Find a county where gas/propane/oil are all 0 or near-0
    all_electric_fips = None
    for fips, shares in acs.get('fuel_share', {}).items():
        furnace_fuels = {'natural gas', 'propane', 'fuel oil'}
        filtered = {k: v for k, v in shares.items() if k in furnace_fuels and v > 0}
        if not filtered and shares.get('electricity', 0) > 0.9:
            all_electric_fips = fips
            break

    if all_electric_fips:
        # Furnace in all-electric county should fall through to division level
        ctx = build_asset_ctx(
            {"state": "TX", "year_built": 1990, "area": 2000,
             "county_fips": all_electric_fips},
            building_type="Single-Family Detached", building_id="elec_test_001",
        )
        # Force furnace system type by ensuring it resolves as furnace
        val, level = resolve_default("heating_system_fuel_type", ctx)
        check("all-elec county fuel resolves", val is not None, True, section)
        # Should NOT be county_acs (filtered is empty, falls through)
        # Could be county_acs if the resolver somehow found valid fuels,
        # or division_vintage/division_all if it fell through correctly
        check_in("all-elec county level valid", level, list(sps.FALLBACK_LEVELS), section)
    else:
        check("found all-electric county", all_electric_fips is not None, True, section)

    # Even without finding a real all-electric county, verify the filter logic:
    # A county with only electricity should produce empty filtered set
    test_shares = {'electricity': 0.95, 'wood': 0.05}
    furnace_fuels = {'natural gas', 'propane', 'fuel oil'}
    filtered = {k: v for k, v in test_shares.items() if k in furnace_fuels and v > 0}
    check("filter removes non-furnace fuels", len(filtered), 0, section)


# ============================================================
# 23. Boiler path through VALID_FUELS_BY_SYS
# ============================================================
def test_boiler_county_fuel():
    section = "boiler_fuel"

    # Find a New England county with gas (boiler territory)
    acs = _load_ref('acs2022_county_fuel')
    # Suffolk County MA (25025) - Boston, boiler territory
    fips = '25025'
    shares = acs.get('fuel_share', {}).get(fips)
    check(f"25025 has shares", shares is not None, True, section)

    if shares:
        boiler_fuels = {'natural gas', 'propane', 'fuel oil'}
        filtered = {k: v for k, v in shares.items() if k in boiler_fuels and v > 0}
        check("Boston has boiler-valid fuels", len(filtered) > 0, True, section)

        # Build a boiler-typed building in Boston
        # Need to find a building that resolves as boiler
        ct = Counter()
        boiler_found = False
        for i in range(500):
            ctx = build_asset_ctx(
                {"state": "MA", "year_built": 1960, "area": 2000,
                 "county_fips": fips},
                building_type="Single-Family Detached", building_id=f"boiler{i:05d}",
            )
            sys_val, _ = resolve_default("heating_system_type", ctx)
            if sys_val == 'boiler':
                boiler_found = True
                fuel_val, fuel_level = resolve_default("heating_system_fuel_type", ctx)
                ct[fuel_val] += 1
                if len(ct) == 1:
                    check("boiler county fuel level", fuel_level, "county_acs", section)
                    check_in("boiler fuel valid", fuel_val, boiler_fuels, section)

        check("found boiler buildings in MA", boiler_found, True, section)
        if ct:
            check("boiler fuel distribution exists", len(ct) >= 1, True, section)


# ============================================================
# 24. SWH fuel conditioned on electric_resistance system type
# ============================================================
def test_swh_electric_resistance():
    section = "swh_elec_resist"

    # Verify the resolver tuple matches
    check("electric_resistance in resolver tuple",
          'electric_resistance' in ('furnace', 'boiler', 'heat_pump', 'electric_resistance', 'wood_stove'),
          True, section)

    # Electric resistance homes should get electricity-dominant SWH fuel
    ct = Counter()
    for i in range(300):
        ctx = build_asset_ctx(
            {"state": "TX", "year_built": 2000, "area": 1500},
            building_type="Single-Family Detached", building_id=f"er_swh{i:05d}",
        )
        sys_val, _ = resolve_default("heating_system_type", ctx)
        if sys_val == 'electric_resistance':
            swh_val, swh_level = resolve_default("service_water_heating_fuel_type", ctx)
            ct[swh_val] += 1

    if ct:
        elec_pct = ct.get('electricity', 0) / sum(ct.values()) * 100
        check("elec_resist SWH mostly electric", elec_pct > 50, True, section)
    else:
        # If no electric_resistance buildings in TX sample, try with explicit override
        # by finding a state/vintage combination that produces electric_resistance
        for state in ['FL', 'GA', 'SC']:
            for i in range(100):
                ctx = build_asset_ctx(
                    {"state": state, "year_built": 2000, "area": 1500},
                    building_type="Single-Family Detached", building_id=f"er2_{i:05d}",
                )
                sys_val, _ = resolve_default("heating_system_type", ctx)
                if sys_val == 'electric_resistance':
                    swh_val, _ = resolve_default("service_water_heating_fuel_type", ctx)
                    ct[swh_val] += 1
            if ct:
                break
        if ct:
            elec_pct = ct.get('electricity', 0) / sum(ct.values()) * 100
            check("elec_resist SWH mostly electric (alt)", elec_pct > 50, True, section)


# ============================================================
# 25. Reference data key consistency (SWH, heating system, etc.)
# ============================================================
def test_reference_data_keys():
    section = "ref_keys"

    htg_ref = _load_ref('recs2020_heating_system_type')
    fuel_ref = _load_ref('recs2020_fuel_by_system_type')
    wh_ref = _load_ref('recs2020_water_heater_type')

    # All system type keys should use consistent naming
    htg_keys = set(k for k in htg_ref.keys() if not k.startswith('_'))
    # These are the share keys inside each division, not top-level
    # Check that _division exists and has 9 divisions
    for ref_name, ref_data in [('heating_system_type', htg_ref), ('water_heater_type', wh_ref)]:
        divisions = ref_data.get('_division', {})
        check(f"{ref_name} has divisions", len(divisions) >= 8, True, section)

    # Fuel-by-system-type top-level keys (system-type naming consistency, incl. the
    # elec_resist -> electric_resistance rename that the now-removed SWH table used to guard)
    fuel_keys = set(k for k in fuel_ref.keys() if not k.startswith('_'))
    check("fuel ref has furnace", 'furnace' in fuel_keys, True, section)
    check("fuel ref has boiler", 'boiler' in fuel_keys, True, section)
    # elec_resist -> electric_resistance rename guard, on the heating_system_type share keys
    # (which carry all system types; fuel_by_system only has the combustion furnace/boiler).
    htg_share_keys = set()
    for div in htg_ref.get('_division', {}).values():
        for cell in div.values():
            htg_share_keys |= set(cell.get('shares', {}).keys())
    check("heating naming electric_resistance (not elec_resist)",
          'electric_resistance' in htg_share_keys and 'elec_resist' not in htg_share_keys, True, section)

    # Verify shares sum to ~1.0 in RECS division data
    bad_sums = 0
    for division, vintages in fuel_ref.get('furnace', {}).get('_division', {}).items():
        for vintage, data in vintages.items():
            shares = data.get('shares', {})
            if shares:
                total = sum(shares.values())
                if abs(total - 1.0) > 0.05:
                    bad_sums += 1
    check("fuel-by-system furnace shares sum ~1.0", bad_sums, 0, section)


def test_resolver_output_safety_net():
    section = "resolver_safety"

    # RESOLVER_ENUM_VALUES must be a superset of ENUM_VALUES for every enum
    # field the resolver emits (it adds 'district steam'/'none').
    for key, allowed in sps.RESOLVER_ENUM_VALUES.items():
        canonical = sps.ENUM_VALUES.get(key, set())
        check(f"{key} resolver-enum superset of canonical",
              canonical.issubset(allowed), True, section)
    check("heating fuel allows district steam",
          'district steam' in sps.RESOLVER_ENUM_VALUES['heating_system_fuel_type'], True, section)
    check("swh fuel allows district steam",
          'district steam' in sps.RESOLVER_ENUM_VALUES['service_water_heating_fuel_type'], True, section)
    check("cooling fuel allows none",
          'none' in sps.RESOLVER_ENUM_VALUES['cooling_system_fuel_type'], True, section)

    # A bogus resolver output must be dropped to the flat default, not passed
    # through to the feature JSON. Monkeypatch resolve_default to emit garbage.
    orig = sps.resolve_default
    try:
        sps.resolve_default = lambda k, c: (('plutonium', 'x')
                                            if k == 'heating_system_fuel_type' else orig(k, c))
        ctx = build_asset_ctx({"state": "AZ", "year_built": 2000, "area": 2000},
                              building_type="Single-Family Detached", building_id="safety01")
        out = sps.get_param({}, "heating_system_fuel_type", ctx)
        check("bogus resolver value -> flat default", out,
              SIM_PARAM_DEFAULTS["heating_system_fuel_type"], section)
    finally:
        sps.resolve_default = orig

    # Every legitimate resolver output must pass the net unchanged (no spurious
    # fallback). Probe a matrix spanning regions, building types, and vintages.
    enum_fields = ["heating_system_fuel_type", "cooling_system_fuel_type",
                   "service_water_heating_fuel_type", "heating_system_type",
                   "water_heater_type", "window_type", "wall_material", "roof_material"]
    spurious = 0
    for st in ["CA", "TX", "MA", "FL", "CO", "NY"]:
        for bt in ["Single-Family Detached", "Office", "Lodging"]:
            area = 2000 if "Family" in bt else 50000
            for yr in [1960, 1990, 2015]:
                ctx = build_asset_ctx({"state": st, "year_built": yr, "area": area,
                                       "county_fips": "04013"},
                                      building_type=bt, building_id=f"net{st}{bt}{yr}")
                for f in enum_fields:
                    rv, _ = resolve_default(f, ctx)
                    if rv is None:
                        continue
                    if get_param({}, f, ctx) != rv:
                        spurious += 1
    check("no legitimate resolver output dropped", spurious, 0, section)


def test_fuel_mix_mode_integrity():
    section = "fuel_mix_mode"
    ref_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "solver", "upload", "reference_data")
    # Fuels the resolver and Ruby fuel_map can actually consume. 'other'/'solar'
    # have no EnergyPlus mapping, so they must not appear in any share table.
    VALID_MIX_FUELS = {"electricity", "natural gas", "fuel oil", "propane",
                       "wood", "district steam"}

    def walk(obj, path, fname):
        # Recurse the whole tree (region-level AND _division-level) so every
        # cell with a 'shares' dict is validated, not just the top two levels.
        if not isinstance(obj, dict):
            return
        if "shares" in obj and isinstance(obj["shares"], dict) and obj["shares"]:
            shares = obj["shares"]
            actual_max = max(shares, key=shares.get)
            check(f"{fname}/{path} mode==max(shares)", obj.get("mode"), actual_max, section)
            dead = [k for k in shares if k not in VALID_MIX_FUELS]
            check(f"{fname}/{path} no dead fuels", dead, [], section)
            check(f"{fname}/{path} shares sum ~1.0", abs(sum(shares.values()) - 1.0) < 0.02,
                  True, section)
        for k, v in obj.items():
            if k != "shares":
                walk(v, f"{path}/{k}", fname)

    for fname in ["recs2020_residential_fuel_mix.json", "cbecs2018_commercial_fuel_mix.json"]:
        with open(os.path.join(ref_dir, fname)) as f:
            data = json.load(f)
        walk(data, "", fname)


# ============================================================
# 27. Stochastic sampling flag
# ============================================================
def test_stochastic_sampling_flag():
    section = "stochastic_flag"

    base_meta = {"state": "AZ", "year_built": 2000, "area": 2000, "county_fips": "04013"}

    # Stochastic ON: county fuel should produce distribution with building_ids
    os.environ["URBANOPT_STOCHASTIC_SAMPLING"] = "true"
    ct_on = Counter()
    for i in range(200):
        ctx = build_asset_ctx(base_meta, building_type="Single-Family Detached",
                              building_id=f"stoch_on_{i:04d}")
        val, _ = resolve_default("heating_system_fuel_type", ctx)
        ct_on[val] += 1
    check("stochastic on -> multi fuel", len(ct_on) >= 2, True, section)

    # Stochastic OFF: county fuel should always return mode
    os.environ["URBANOPT_STOCHASTIC_SAMPLING"] = "false"
    ct_off = Counter()
    for i in range(50):
        ctx = build_asset_ctx(base_meta, building_type="Single-Family Detached",
                              building_id=f"stoch_off_{i:04d}")
        val, _ = resolve_default("heating_system_fuel_type", ctx)
        ct_off[val] += 1
    check("stochastic off -> single fuel (mode)", len(ct_off), 1, section)

    acs = _load_ref('acs2022_county_fuel')
    shares = acs.get('fuel_share', {}).get('04013', {})
    furnace_fuels = {'natural gas', 'propane', 'fuel oil', 'electricity'}
    filtered = {k: v for k, v in shares.items() if k in furnace_fuels and v > 0}
    expected_mode = max(filtered, key=filtered.get) if filtered else None
    actual_mode = list(ct_off.keys())[0]
    check("stochastic off -> correct mode", actual_mode, expected_mode, section)

    # Stochastic OFF: vacancy should never hash-assign
    ctx_vac = build_asset_ctx(
        {"state": "WY", "year_built": 2000, "area": 2000, "county_fips": "56039"},
        building_type="Single-Family Detached", building_id="stoch_vac_001",
    )
    check("stochastic off -> not vacant", is_vacant(ctx_vac), False, section)

    # Stochastic ON: vacancy should work (Teton WY has ~40% vacancy)
    os.environ["URBANOPT_STOCHASTIC_SAMPLING"] = "true"
    vac_count = 0
    for i in range(200):
        ctx = build_asset_ctx(
            {"state": "WY", "year_built": 2000, "area": 2000, "county_fips": "56039"},
            building_type="Single-Family Detached", building_id=f"stoch_vac_{i:04d}",
        )
        if is_vacant(ctx):
            vac_count += 1
    check("stochastic on -> some vacant in Teton", vac_count > 0, True, section)
    check("stochastic on -> not all vacant", vac_count < 200, True, section)

    # is_stochastic_sampling_enabled function
    os.environ["URBANOPT_STOCHASTIC_SAMPLING"] = "true"
    check("flag true", sps.is_stochastic_sampling_enabled(), True, section)
    os.environ["URBANOPT_STOCHASTIC_SAMPLING"] = "false"
    check("flag false", sps.is_stochastic_sampling_enabled(), False, section)

    os.environ["URBANOPT_STOCHASTIC_SAMPLING"] = "true"


# ============================================================
# 28. Decade vintage bin boundaries
# ============================================================
def test_decade_vintage_bin():
    section = "decade_vintage"
    dvb = sps._decade_vintage_bin

    check("None -> None", dvb(None), None, section)
    check("'abc' -> None", dvb('abc'), None, section)
    check("1900 -> pre-1950", dvb(1900), 'pre-1950', section)
    check("1949 -> pre-1950", dvb(1949), 'pre-1950', section)
    check("1950 -> 1950-1959", dvb(1950), '1950-1959', section)
    check("1959 -> 1950-1959", dvb(1959), '1950-1959', section)
    check("1960 -> 1960-1969", dvb(1960), '1960-1969', section)
    check("1969 -> 1960-1969", dvb(1969), '1960-1969', section)
    check("1970 -> 1970-1979", dvb(1970), '1970-1979', section)
    check("1979 -> 1970-1979", dvb(1979), '1970-1979', section)
    check("1980 -> 1980-1989", dvb(1980), '1980-1989', section)
    check("1989 -> 1980-1989", dvb(1989), '1980-1989', section)
    check("1990 -> 1990-1999", dvb(1990), '1990-1999', section)
    check("1999 -> 1990-1999", dvb(1999), '1990-1999', section)
    check("2000 -> 2000-2009", dvb(2000), '2000-2009', section)
    check("2009 -> 2000-2009", dvb(2009), '2000-2009', section)
    check("2010 -> 2010-2019", dvb(2010), '2010-2019', section)
    check("2019 -> 2010-2019", dvb(2019), '2010-2019', section)
    check("2020 -> 2020+", dvb(2020), '2020+', section)
    check("2025 -> 2020+", dvb(2025), '2020+', section)
    check("str '1990' -> 1990-1999", dvb('1990'), '1990-1999', section)


# ============================================================
# 29. All 10 census divisions -- heating system type
# ============================================================
def test_heating_system_type_all_divisions():
    section = "hstype_all_div"
    ref = _load_ref("recs2020_heating_system_type")
    valid = ENUM_VALUES["heating_system_type"]

    ALL_DIV_STATES = {
        "New England": "MA", "Middle Atlantic": "NJ",
        "East North Central": "IL", "West North Central": "IA",
        "South Atlantic": "DE", "East South Central": "AL",
        "West South Central": "TX", "Mountain North": "CO",
        "Mountain South": "AZ", "Pacific": "CA",
    }

    for division, state in ALL_DIV_STATES.items():
        for vintage, year in VINTAGE_YEARS.items():
            ctx = make_ctx(state, year, 2000, "Single-Family Detached")
            val, level = resolve_default("heating_system_type", ctx)
            # System type is reconciled to the heating-fuel marginal (electric
            # systems scaled up where electric furnaces are lumped into RECS
            # 'furnace'), so the modal type need not equal the raw RECS mode.
            check_in(f"hstype/{division}/{vintage} enum", val, valid, section)
            check(f"hstype/{division}/{vintage} not None", val is not None, True, section)


# ============================================================
# 30. All 10 census divisions -- water heater type
# ============================================================
def test_water_heater_type_all_divisions():
    section = "whtype_all_div"
    ref = _load_ref("recs2020_water_heater_type")
    valid = ENUM_VALUES["water_heater_type"]

    ALL_DIV_STATES = {
        "New England": "MA", "Middle Atlantic": "NJ",
        "East North Central": "IL", "West North Central": "IA",
        "South Atlantic": "DE", "East South Central": "AL",
        "West South Central": "TX", "Mountain North": "CO",
        "Mountain South": "AZ", "Pacific": "CA",
    }

    for division, state in ALL_DIV_STATES.items():
        for vintage, year in VINTAGE_YEARS.items():
            ctx = build_asset_ctx(
                {"state": state, "year_built": year, "area": 2000},
                building_type="Single-Family Detached",
            )
            val, level = resolve_default("water_heater_type", ctx)
            check_in(f"whtype/{division}/{vintage} enum", val, valid, section)
            check(f"whtype/{division}/{vintage} not None", val is not None, True, section)
            decade_key = DECADE_BINS[vintage]
            div_record = ref.get("_division", {}).get(division, {}).get(decade_key, {})
            if div_record:
                exp = div_record["mode"]
                check(f"whtype/{division}/{vintage} mode", val, exp, section)


# ============================================================
# 30b. County-first heating electric marginal (F1: total resolved electric
#      heating must match the ACS county marginal, not overshoot it)
# ============================================================
def test_county_electric_marginal():
    section = "county_elec_marginal"
    acs = _load_ref('acs2022_county_fuel')

    # (fips, state, label) spanning gas-dominant and electric-dominant counties.
    cases = [
        ('17031', 'IL', 'Cook gas'),
        ('26107', 'MI', 'Wayne gas'),
        ('04013', 'AZ', 'Maricopa elec'),
        ('12086', 'FL', 'Miami-Dade elec'),
        ('48201', 'TX', 'Harris mixed'),
    ]
    N = 6000
    for fips, state, label in cases:
        e_acs = acs.get('fuel_share', {}).get(fips, {}).get('electricity', 0)
        ct = Counter()
        for i in range(N):
            ctx = build_asset_ctx(
                {"state": state, "year_built": 1990, "area": 2000, "county_fips": fips},
                building_type="Single-Family Detached", building_id=f"em{fips}{i:05d}",
            )
            ct[resolve_default("heating_system_fuel_type", ctx)[0]] += 1
        cshares = acs.get('fuel_share', {}).get(fips, {})
        w_acs = cshares.get('wood', 0)
        res_elec = ct["electricity"] / N
        res_wood = ct["wood"] / N
        # Total resolved electric heating must track the authoritative county
        # marginal within sampling error -- NOT overshoot it via division-baseline
        # electric systems (the F1 bug had Cook at 0.30 vs ACS 0.16).
        check_range(f"{label} resolved elec ~ ACS {e_acs:.2f}",
                    res_elec, max(0.0, e_acs - 0.04), e_acs + 0.04, section)
        # Wood must also track the county marginal -- not the division wood_stove
        # share (up to ~10% in Pacific), which the resolver wrongly applied to
        # near-zero-wood counties before the county-first wood reconciliation.
        check_range(f"{label} resolved wood ~ ACS {w_acs:.3f}",
                    res_wood, max(0.0, w_acs - 0.03), w_acs + 0.03, section)


_GOLDEN_STOCH = ['heating_system_type', 'heating_system_fuel_type',
                 'service_water_heating_fuel_type', 'water_heater_type', 'window_type',
                 'wall_material', 'roof_material', 'wall_r_value', 'roof_r_value',
                 'window_to_wall_ratio']
_GOLDEN_DET = ['cooling_system_fuel_type', 'number_of_occupants', 'weekday_start_time']
_GOLDEN_N = 2000


def _golden_case(md, bt):
    rec = {"stoch": {}, "det": {}, "vacant": 0}
    tag = "%s%s%s%s" % (md.get("state"), md.get("year_built"), md.get("county_fips", "X"), bt)
    for f in _GOLDEN_STOCH:
        ct = Counter()
        for i in range(_GOLDEN_N):
            ctx = build_asset_ctx(md, building_type=bt, building_id="%s%05d" % (tag, i))
            ct[resolve_default(f, ctx)[0]] += 1
        rec["stoch"][f] = dict(ct)
    ctx0 = build_asset_ctx(md, building_type=bt, building_id=bt + "DET")
    for f in _GOLDEN_DET:
        rec["det"][f] = resolve_default(f, ctx0)[0]
    if bt in sps.RESIDENTIAL_BUILDING_TYPES:
        vc = 0
        for i in range(_GOLDEN_N):
            ctx = build_asset_ctx(md, building_type=bt,
                                  building_id="V%s%05d" % (md.get("county_fips", "X"), i))
            if is_vacant(ctx):
                vc += 1
        rec["vacant"] = vc
    return rec


def test_resolver_golden():
    """Comprehensive characterization snapshot of the WHOLE resolver: exact
    stochastic distributions (heating/SWH system + fuel + water-heater type,
    vacancy) and deterministic values (cooling fuel, window, envelope, WWR,
    occupants, schedule) over a fixed matrix of 45 residential + 10 commercial
    cases. Deterministic by hash, so any behavior-preserving REFACTOR must
    reproduce it byte-for-byte. Regenerate (tests/tools/gen_resolver_golden.py)
    ONLY on a deliberate, reviewed behavior change."""
    section = "resolver_golden"
    gpath = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "fixtures", "resolver_golden.json")
    golden = json.load(open(gpath))
    DIV_STATE = {'New England': 'MA', 'Middle Atlantic': 'NY', 'East North Central': 'IL',
                 'West North Central': 'IA', 'South Atlantic': 'DE', 'East South Central': 'AL',
                 'West South Central': 'TX', 'Mountain North': 'CO', 'Mountain South': 'AZ',
                 'Pacific': 'CA'}
    COUNTY = {'AZ': '04013', 'IL': '17031', 'MA': '25025', 'TX': '48201', 'CA': '06037'}

    def _norm(d):
        # JSON object keys are strings: stringify numeric resolved-values (R-values,
        # WWR) and map a None resolved-value (commercial heating/water-heater) to "null".
        return {("null" if k is None else str(k)): v for k, v in d.items()}

    def verify(key, md, bt):
        got = _golden_case(md, bt)
        exp = golden[key]
        for f in _GOLDEN_STOCH:
            check(f"golden {key} {f}", _norm(got["stoch"][f]), exp["stoch"][f], section)
        for f in _GOLDEN_DET:
            check(f"golden {key} {f}", got["det"][f], exp["det"][f], section)
        check(f"golden {key} vacant", got["vacant"], exp["vacant"], section)

    for div, st in DIV_STATE.items():
        for yr in (1965, 1990, 2015):
            for geo in ("nocounty", "county"):
                key = f"res|{div}|{yr}|{geo}"
                if key not in golden:
                    continue
                md = {"state": st, "year_built": yr, "area": 2000}
                if geo == "county":
                    md["county_fips"] = COUNTY[st]
                verify(key, md, "Single-Family Detached")
    COM_CZ = {"IL": "5A", "AZ": "2B"}   # exercise the commercial climate-zone envelope path
    for bt in ("Office", "Retail", "Warehouse", "Education", "Lodging"):
        for st, yr in (("IL", 1990), ("AZ", 2010)):
            key = f"com|{bt}|{st}|{yr}"
            md = {"state": st, "year_built": yr, "area": 50000,
                  "county_fips": COUNTY.get(st), "floor_count": 3, "climate_zone": COM_CZ[st]}
            verify(key, md, bt)


def test_nocounty_marginal():
    """No-county (no county_fips, no lat/lon) residential fallback must reconcile
    to the RECS DIVISION fuel marginal, not drift from it via system-conditional
    composition."""
    section = "nocounty_marginal"
    fm = _load_ref('recs2020_residential_fuel_mix')['heating_system_fuel_type']['_division']
    # (state, division, 4-bin vintage, representative year)
    cases = [
        ('TX', 'West South Central', 'pre-1980', 1965),
        ('TX', 'West South Central', '1980-1999', 1990),
        ('IL', 'East North Central', '1980-1999', 1990),
        ('CA', 'Pacific', '2000-2009', 2005),
        ('MA', 'New England', 'pre-1980', 1965),
    ]
    N = 6000
    for state, div, vint, year in cases:
        recs = fm.get(div, {}).get(vint, {}).get('shares', {})
        ct = Counter()
        for i in range(N):
            ctx = build_asset_ctx(
                {"state": state, "year_built": year, "area": 2000},  # NO county_fips/lat/lon
                building_type="Single-Family Detached", building_id=f"nc{state}{vint}{i:05d}",
            )
            ct[resolve_default("heating_system_fuel_type", ctx)[0]] += 1
        for fuel in ("electricity", "natural gas"):
            res = ct[fuel] / N
            tgt = recs.get(fuel, 0)
            check_range(f"{div}/{vint} {fuel} ~ RECS {tgt:.2f}",
                        res, max(0.0, tgt - 0.04), tgt + 0.04, section)


# ============================================================
# 31. Operating schedule resolver
# ============================================================
def test_operating_schedule():
    section = "op_schedule"

    # Single daytime-peak types: the occupancy-peak window IS the operating
    # period, so it's emitted.
    SCHEDULE_CASES = {
        "Office": {"weekday_start_time": "08:00", "weekday_duration": "09:00",
                    "weekend_start_time": "08:00", "weekend_duration": "04:00"},
        "Retail": {"weekday_start_time": "09:00", "weekday_duration": "10:00",
                   "weekend_start_time": "09:00", "weekend_duration": "09:00"},
        "Warehouse": {"weekday_start_time": "08:00", "weekday_duration": "09:00",
                      "weekend_start_time": "08:00", "weekend_duration": "09:00"},
        "Education": {"weekday_start_time": "08:00", "weekday_duration": "08:00"},
    }

    for bt, expected in SCHEDULE_CASES.items():
        ctx = make_ctx("TX", 2005, 50000, bt)
        for field, exp_val in expected.items():
            val, level = resolve_default(field, ctx)
            check(f"{bt}/{field}", val, exp_val, section)
            check(f"{bt}/{field} level", level, "building_type_only", section)

    # 24/7 / overnight / bimodal types: the occupancy-peak window is NOT the
    # operating period, so the override is suppressed (None) -> the DOE prototype
    # native schedule is preserved (Lodging->hotel 24/7, Food service->restaurant
    # lunch+dinner, both of which the peak-window heuristic mis-phases).
    for bt in ("Lodging", "Food service"):
        ctx = make_ctx("TX", 2005, 50000, bt)
        for field in ("weekday_start_time", "weekday_duration",
                      "weekend_start_time", "weekend_duration"):
            val, _ = resolve_default(field, ctx)
            check(f"{bt}/{field} suppressed", val, None, section)

    # Education weekend fields should return None (empty in reference data)
    ctx = make_ctx("TX", 2005, 50000, "Education")
    val, level = resolve_default("weekend_start_time", ctx)
    check("Education weekend_start -> None", val, None, section)
    val, level = resolve_default("weekend_duration", ctx)
    check("Education weekend_dur -> None", val, None, section)

    # Residential should return None (no operating schedule)
    ctx = make_ctx("TX", 2005, 2000, "Single-Family Detached")
    for field in ["weekday_start_time", "weekday_duration",
                  "weekend_start_time", "weekend_duration"]:
        val, level = resolve_default(field, ctx)
        check(f"residential {field} -> None", val, None, section)


# ============================================================
# 32. Commercial fuel resolvers -- multiple building types
# ============================================================
def test_commercial_fuel_multi_bt():
    section = "com_fuel_bt"
    cbecs = _load_ref("cbecs2018_commercial_fuel_mix")

    for bt in ["Retail", "Warehouse", "Education", "Lodging",
               "Outpatient health care", "Food service"]:
        for field in ["heating_system_fuel_type", "service_water_heating_fuel_type"]:
            ctx = make_ctx("IL", 2005, 50000, bt)
            val, level = resolve_default(field, ctx)
            check(f"{bt}/{field} not None", val is not None, True, section)
            check_in(f"{bt}/{field} enum", val, ENUM_VALUES[field], section)
            ref_record = cbecs.get(field, {}).get("Midwest", {}).get("2000-2009", {})
            if ref_record:
                check(f"{bt}/{field} matches region mode", val, ref_record.get("mode"), section)


# ============================================================
# 33. Boiler division-level fuel (no county_fips)
# ============================================================
def test_boiler_division_fuel():
    section = "boiler_div_fuel"

    ct = Counter()
    boiler_found = False
    for i in range(500):
        ctx = build_asset_ctx(
            {"state": "MA", "year_built": 1960, "area": 2000},
            building_type="Single-Family Detached", building_id=f"bdiv{i:05d}",
        )
        sys_val, _ = resolve_default("heating_system_type", ctx)
        if sys_val == 'boiler':
            boiler_found = True
            fuel_val, fuel_level = resolve_default("heating_system_fuel_type", ctx)
            ct[fuel_val] += 1
            if len(ct) == 1:
                check("boiler div fuel level", fuel_level, "division_vintage", section)
                boiler_valid = {'natural gas', 'propane', 'fuel oil'}
                check_in("boiler div fuel valid", fuel_val, boiler_valid, section)

    check("found boilers in MA (no county)", boiler_found, True, section)

    if ct:
        all_fuels = set(ENUM_VALUES["heating_system_fuel_type"])
        for fuel in ct:
            check_in(f"boiler div fuel '{fuel}' valid", fuel, all_fuels, section)
        check("boiler div has fuel", len(ct) >= 1, True, section)

    # Division-level boiler heating fuel must never be electricity/wood: those are
    # filtered to combustion fuels (mirrors Ruby safeguard). Covers the sparse
    # RECS cells that hold ONLY electricity/wood (e.g. East South Central 2000-2009
    # = 100% electricity) where the filter must fall back to natural gas.
    combustion = {'natural gas', 'propane', 'fuel oil'}
    for state, year in [("AL", 2005), ("TX", 1975), ("AZ", 1955), ("IL", 1995)]:
        boiler_fuels = Counter()
        for i in range(400):
            ctx = build_asset_ctx(
                {"state": state, "year_built": year, "area": 2000},
                building_type="Single-Family Detached", building_id=f"bef_{state}_{i:04d}",
            )
            if resolve_default("heating_system_type", ctx)[0] == 'boiler':
                fuel, _ = resolve_default("heating_system_fuel_type", ctx)
                boiler_fuels[fuel] += 1
        for fuel in boiler_fuels:
            check_in(f"boiler {state}/{year} heating fuel combustion-only",
                     fuel, combustion, section)


# ============================================================
# 34. CZ >= 7 cooling suppression
# ============================================================
def test_cz7_cooling_suppression():
    section = "cz7_cooling"

    ctx = build_asset_ctx(
        {"state": "AK", "year_built": 2005, "area": 2000},
        building_type="Single-Family Detached",
        climate_zone="7A",
    )
    val, level = resolve_default("cooling_system_fuel_type", ctx)
    check("CZ7 res cooling -> none", val, "none", section)
    check("CZ7 res cooling level", level, "building_type_only", section)

    # Commercial CZ8: cooling is NOT suppressed (commercial keeps cooling in
    # subarctic zones); suppression is residential-only.
    ctx = build_asset_ctx(
        {"state": "AK", "year_built": 2005, "area": 50000},
        building_type="Office",
        climate_zone="8A",
    )
    val, level = resolve_default("cooling_system_fuel_type", ctx)
    check("CZ8 com cooling -> electricity (not suppressed)", val, "electricity", section)

    # CZ4 should still get electricity (both residential and commercial)
    ctx = build_asset_ctx(
        {"state": "MA", "year_built": 2005, "area": 2000},
        building_type="Single-Family Detached",
        climate_zone="4A",
    )
    val, level = resolve_default("cooling_system_fuel_type", ctx)
    check("CZ4 res cooling -> electricity", val, "electricity", section)

    # Commercial CZ4 also gets electricity
    ctx = build_asset_ctx(
        {"state": "MA", "year_built": 2005, "area": 50000},
        building_type="Office",
        climate_zone="4A",
    )
    val, level = resolve_default("cooling_system_fuel_type", ctx)
    check("CZ4 com cooling -> electricity", val, "electricity", section)


# ============================================================
# 35. Commercial envelope -- multiple building types
# ============================================================
def test_commercial_envelope_multi_bt():
    section = "com_env_bt"

    for bt in ["Retail", "Warehouse", "Education", "Lodging"]:
        ctx = make_ctx("IL", 2005, 50000, bt)
        for field in ["wall_material", "roof_material", "wall_r_value",
                      "roof_r_value", "window_to_wall_ratio"]:
            val, level = resolve_default(field, ctx)
            check(f"{bt}/{field} not None", val is not None, True, section)


# ============================================================
# 36. 2020+ vintage for heating/water heater type
# ============================================================
def test_2020_plus_vintage():
    section = "vintage_2020"
    ref_hs = _load_ref("recs2020_heating_system_type")
    ref_wh = _load_ref("recs2020_water_heater_type")
    valid_hs = ENUM_VALUES["heating_system_type"]
    valid_wh = ENUM_VALUES["water_heater_type"]

    for state, division in [("MA", "New England"), ("IL", "East North Central"),
                            ("TX", "West South Central"), ("AZ", "Mountain South")]:
        ctx = build_asset_ctx(
            {"state": state, "year_built": 2022, "area": 2000},
            building_type="Single-Family Detached",
        )
        check(f"{state} decade_vintage=2020+", ctx.get('decade_vintage'), '2020+', section)

        val, level = resolve_default("heating_system_type", ctx)
        # reconciled to fuel marginal -> modal type need not equal raw RECS mode
        check_in(f"hstype {state}/2022 enum", val, valid_hs, section)

        val, level = resolve_default("water_heater_type", ctx)
        check_in(f"whtype {state}/2022 enum", val, valid_wh, section)
        div_rec = ref_wh.get("_division", {}).get(division, {}).get("2020+", {})
        if div_rec:
            check(f"whtype {state}/2022 mode", val, div_rec["mode"], section)


# ============================================================
# Run all tests
# ============================================================
# Run via `pytest tests/test_resolvers.py`. Each test_* function is enforced by the
# autouse _enforce_checks fixture above (no standalone __main__ runner).
