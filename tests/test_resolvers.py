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

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))

os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "true"

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

REGIONS = ["Northeast", "Midwest", "South", "West"]
VINTAGES = ["pre-1980", "1980-1999", "2000-2009", "2010+"]
VINTAGE_YEARS = {"pre-1980": 1960, "1980-1999": 1990, "2000-2009": 2005, "2010+": 2018}
REGION_STATES = {"Northeast": "MA", "Midwest": "IL", "South": "TX", "West": "AZ"}

RESIDENTIAL_TYPES = ["Single-Family Detached", "Single-Family Attached", "Multifamily"]
COMMERCIAL_TYPES = ["Office", "Education", "Lodging", "Warehouse",
                    "Food service", "Outpatient health care", "Laboratory"]

ALL_DYNAMIC_FIELDS = [
    "window_type",
    "heating_system_fuel_type",
    "cooling_system_fuel_type",
    "service_water_heating_fuel_type",
    "wall_material",
    "roof_material",
    "wall_r_value",
    "roof_r_value",
    "window_to_wall_ratio",
    "number_of_occupants",
]

NON_DYNAMIC_FIELDS = [
    "system_type",
    "floor_height",
    "weekday_start_time",
    "weekday_duration",
    "weekend_start_time",
    "weekend_duration",
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

    for bt in RESIDENTIAL_TYPES:
        for region in REGIONS:
            for vintage in VINTAGES:
                ctx = make_ctx(REGION_STATES[region], VINTAGE_YEARS[vintage], 2000, bt)
                val, level = resolve_default("window_type", ctx)
                exp = expected["residential"][region][vintage]
                check(f"{bt}/{region}/{vintage}", val, exp, section)
                check(f"{bt}/{region}/{vintage} level", level, "vintage_specific", section)

    for bt in COMMERCIAL_TYPES:
        for region in REGIONS:
            for vintage in VINTAGES:
                ctx = make_ctx(REGION_STATES[region], VINTAGE_YEARS[vintage], 50000, bt)
                val, level = resolve_default("window_type", ctx)
                exp = expected["commercial"][region][vintage]
                check(f"{bt}/{region}/{vintage}", val, exp, section)
                check(f"{bt}/{region}/{vintage} level", level, "vintage_specific", section)


# ============================================================
# 2. Fuel type resolvers -- exact values from reference data
# ============================================================
def test_fuel_types():
    section = "fuel_types"
    recs = _load_ref("recs2020_residential_fuel_mix")
    cbecs = _load_ref("cbecs2018_commercial_fuel_mix")

    for field in ["heating_system_fuel_type", "service_water_heating_fuel_type"]:
        # Residential: verify against RECS reference data
        for region in REGIONS:
            for vintage in VINTAGES:
                ctx = make_ctx(REGION_STATES[region], VINTAGE_YEARS[vintage], 2000,
                               "Single-Family Detached")
                val, level = resolve_default(field, ctx)
                ref_section = recs.get(field, {})
                ref_record = ref_section.get(region, {}).get(vintage, {})
                exp = ref_record.get("mode") if ref_record else None
                if exp is not None:
                    check(f"res {field}/{region}/{vintage}", val, exp, section)
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
# 3. Envelope resolvers -- exact values from reference data
# ============================================================
def test_envelope():
    section = "envelope"
    recs_env = _load_ref("recs2020_envelope")
    cbecs_env = _load_ref("cbecs2018_envelope")

    # Wall/roof material and R-values: verify against reference data
    for field in ["wall_material", "roof_material", "wall_r_value", "roof_r_value"]:
        # Residential
        ref_table = recs_env.get(field, {})
        for region in REGIONS:
            for vintage in VINTAGES:
                ctx = make_ctx(REGION_STATES[region], VINTAGE_YEARS[vintage], 2000,
                               "Single-Family Detached")
                val, level = resolve_default(field, ctx)
                exp = ref_table.get(region, {}).get(vintage)
                if exp is not None:
                    check(f"res {field}/{region}/{vintage}", val, exp, section)
                    check(f"res {field}/{region}/{vintage} level",
                          level, "vintage_specific", section)

        # Commercial
        ref_table = cbecs_env.get(field, {})
        for region in REGIONS:
            for vintage in VINTAGES:
                ctx = make_ctx(REGION_STATES[region], VINTAGE_YEARS[vintage], 50000, "Office")
                val, level = resolve_default(field, ctx)
                exp = ref_table.get(region, {}).get(vintage)
                if exp is not None:
                    check(f"com {field}/{region}/{vintage}", val, exp, section)
                    check(f"com {field}/{region}/{vintage} level",
                          level, "vintage_specific", section)

    # WWR: building-type keyed
    for bt, exp_wwr in recs_env.get("window_to_wall_ratio_by_building_type", {}).items():
        ctx = make_ctx("TX", 2000, 2000, bt)
        val, level = resolve_default("window_to_wall_ratio", ctx)
        check(f"res WWR {bt}", val, exp_wwr, section)
        check(f"res WWR {bt} level", level, "building_type_only", section)

    # Commercial WWR requires DOE ref name translation, spot-check Office
    ctx = make_ctx("TX", 2000, 50000, "Office")
    val, level = resolve_default("window_to_wall_ratio", ctx)
    exp_wwr = cbecs_env.get("window_to_wall_ratio_by_building_type", {}).get("Office")
    if exp_wwr is not None:
        check("com WWR Office", val, exp_wwr, section)
        check("com WWR Office level", level, "building_type_only", section)


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

    # Resolver wins over flat default (pre-1980 Midwest -> Single Pane)
    meta = {"state": "IL", "year_built": 1970, "area": 2000}
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
        exp = _load_ref("window_type_by_vintage")["residential"]["Midwest"][expected_bin]
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
                val = wt.get(sector, {}).get(region, {}).get(vintage)
                check(f"window_type {sector}/{region}/{vintage} present",
                      val is not None, True, section)
                if val:
                    check_in(f"window_type {sector}/{region}/{vintage} valid",
                             val, ENUM_VALUES["window_type"], section)

    # Fuel mix table completeness
    for ref_name in ["recs2020_residential_fuel_mix", "cbecs2018_commercial_fuel_mix"]:
        ref = _load_ref(ref_name)
        for field in ["heating_system_fuel_type", "service_water_heating_fuel_type"]:
            section_data = ref.get(field, {})
            for region in REGIONS:
                has_region = region in section_data
                check(f"{ref_name}/{field} has {region}", has_region, True, section)

    # Envelope table completeness
    for ref_name in ["recs2020_envelope", "cbecs2018_envelope"]:
        ref = _load_ref(ref_name)
        for field in ["wall_material", "roof_material", "wall_r_value", "roof_r_value"]:
            section_data = ref.get(field, {})
            for region in REGIONS:
                for vintage in VINTAGES:
                    val = section_data.get(region, {}).get(vintage)
                    check(f"{ref_name}/{field}/{region}/{vintage} present",
                          val is not None, True, section)


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
# Run all tests
# ============================================================
def main():
    tests = [
        ("Window type resolver",      test_window_type),
        ("Fuel type resolvers",       test_fuel_types),
        ("Envelope resolvers",        test_envelope),
        ("Occupants resolver",        test_occupants),
        ("Non-dynamic fields",        test_non_dynamic_fields),
        ("Resolution precedence",     test_precedence),
        ("Fallback behavior",         test_fallbacks),
        ("Vintage boundaries",        test_vintage_boundaries),
        ("Reference data integrity",  test_reference_data),
        ("State normalization",       test_state_normalization),
    ]

    for name, fn in tests:
        before = total
        fn()
        count = total - before
        print(f"  {name}: {count} checks")

    print(f"\n{'='*60}")
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for f in failures:
            print(f)
    print(f"\nRESULTS: {passed}/{total} passed, {failed} failed")
    print(f"{'='*60}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
