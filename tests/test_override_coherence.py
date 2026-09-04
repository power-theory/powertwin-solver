"""WP-03 evidence set: explicit-override coherence in the resolver (Q/A R-037, D-26, D-31, R-032).

Reproduce-first: written against the defective behavior; must FAIL before the fix."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "solver", "app"))
os.environ.setdefault("URBANOPT_DYNAMIC_DEFAULTS", "true")
os.environ.setdefault("URBANOPT_STOCHASTIC_SAMPLING", "true")

from modules.simulation.sim_params_spec import (  # noqa: E402
    build_asset_ctx, get_param, validate_metadata,
)

CTX_MD = {"county_fips": "56039", "state": "WY", "area": 2000, "floor_count": 1}


def _ctx(bid="9", **over):
    md = {**CTX_MD, **over}
    return build_asset_ctx(md, building_type="Single-Family Detached", climate_zone="6B",
                           building_id=bid)


def _fuel_for(md_extra, bid="9"):
    md = {**CTX_MD, **md_extra}
    ctx = _ctx(bid=bid, **md_extra)
    return get_param(md, "heating_system_fuel_type", ctx)


def test_explicit_heat_pump_never_pairs_with_combustion_fuel():
    # R-037: explicit heat_pump + blank fuel must resolve an electric-compatible fuel, not a
    # fuel drawn from a RE-DRAWN system type. Scan ids so the redraw would land on furnace/boiler
    # for at least some -- with the fix, every id yields electricity for a heat pump.
    bad = []
    for i in range(1, 60):
        fuel = _fuel_for({"heating_system_type": "heat_pump"}, bid=str(i))
        if fuel not in ("electricity",):
            bad.append((i, fuel))
    assert not bad, f"explicit heat_pump paired with non-electric fuel for ids {bad[:5]}"


def test_explicit_wood_stove_pairs_with_wood():
    for i in range(1, 30):
        fuel = _fuel_for({"heating_system_type": "wood_stove"}, bid=str(i))
        assert fuel == "wood", f"id {i}: wood_stove paired with {fuel!r}"


def test_explicit_heat_pump_water_heater_forces_electric_swh():
    # R-037 sibling: explicit heat pump water heater + blank SWH fuel must be electricity.
    bad = []
    for i in range(1, 60):
        md = {**CTX_MD, "water_heater_type": "heat pump water heater"}
        ctx = _ctx(bid=str(i), water_heater_type="heat pump water heater")
        fuel = get_param(md, "service_water_heating_fuel_type", ctx)
        if fuel != "electricity":
            bad.append((i, fuel))
    assert not bad, f"explicit HPWH paired with non-electric SWH fuel for ids {bad[:5]}"


def test_metadata_enum_gate_rejects_unknown_fuel():
    # D-26: an arbitrary fuel string must not ride through get_param into the feature JSON
    # (Ruby silently drops it and the building simulates template-default physics).
    md = {**CTX_MD, "heating_system_fuel_type": "unobtainium"}
    ok, errs = validate_metadata(md)
    assert not ok and any("heating_system_fuel_type" in e for e in errs), \
        "validate_metadata accepted an unknown fuel"
    val = get_param(md, "heating_system_fuel_type", _ctx())
    assert val != "unobtainium", "get_param passed an un-mappable fuel through to the feature JSON"


def test_zone8_letter_suffix_is_required_by_the_toolchain():
    # R-032 RESOLUTION: ASHRAE-the-standard has no 8A, but openstudio-standards'
    # get_doe_climate_zones choice list contains ONLY 'ASHRAE 169-2013-7A'/'-8A' (no bare 7/8),
    # so the moisture-letter derivation in weather.py is REQUIRED, not a bug. Pin the contract:
    # zone 8 counties must keep a letter, and the reference-data lookups must keep compensating.
    from modules.simulation import sim_params_spec as sps
    src = open(os.path.join(REPO, "solver", "app", "modules", "utils", "weather.py")).read()
    assert "moisture = 'A'" in src, "zone-7/8 moisture derivation removed; ChangeBuildingLocation would reject bare '8'"
    spec_src = open(os.path.join(REPO, "solver", "app", "modules", "simulation", "sim_params_spec.py")).read()
    assert "rstrip('ABC')" in spec_src or 'rstrip("ABC")' in spec_src,         "reference lookups no longer strip the toolchain-required letter"


def test_extended_enum_levers_survive_metadata_gate():
    """The D-26 gates must accept the DOCUMENTED extended levers (RESOLVER_ENUM_VALUES):
    explicit 'district steam' heating fuel drives the mapper's htg_src=DistrictHeating and
    cooling 'none' suppresses cooling. Gating on strict ENUM_VALUES rejected both -- caught
    by the district-steam container e2e (htg_src regressed to the resolved default)."""
    md = {**CTX_MD, "heating_system_fuel_type": "district steam",
          "cooling_system_fuel_type": "none"}
    ok, errs = validate_metadata(md)
    assert ok, f"validate_metadata rejected documented levers: {errs}"
    assert get_param(md, "heating_system_fuel_type", _ctx()) == "district steam"
    assert get_param(md, "cooling_system_fuel_type", _ctx()) == "none"
