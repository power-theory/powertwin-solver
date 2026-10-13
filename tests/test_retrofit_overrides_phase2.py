"""Retrofit overrides, phase 2 (vault Dev/powertwin-solver/Retrofit Overrides Plan, 2026-10-13): explicit residential
equipment ratings (AFUE, SEER2, HSPF2) and appliances (refrigerator, clothes dryer, other plug loads per dwelling
unit; no washer or dishwasher efficiency, which HPXML only takes with a full EnergyGuide label), a commercial plug-load cut, commercial airtightness guarded against prototypes without NIST coefficients, and
duct sealing delivered when the county priors left the duct location unresolved. Empty fields change nothing."""
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))
os.environ.setdefault("POWERTWIN_LOG_DIR", "/tmp")

from modules.simulation import generateFeatureFile as G  # noqa: E402
from modules.simulation.sim_params_spec import NIST_PROTOTYPES  # noqa: E402

HOUSE = {"county_fips": "56039", "state": "WY", "year_built": 1993, "area": 1800.0, "floor_count": 1,
         "latitude": 43.48, "longitude": -110.76}
OFFICE = dict(HOUSE, area=20000.0, floor_count=2, year_built=1995)
RB = open(os.path.join(REPO, "solver", "upload", "PowerTwin.rb")).read()
RATINGS = {"heating_afue": 0.95, "cooling_seer2": 18.0, "heat_pump_hspf2": 9.5}
APPLIANCES = {"refrigerator_kwh_per_year": 350.0, "clothes_dryer_cef": 3.93, "plug_load_kwh_per_year": 1500.0}


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv("URBANOPT_DYNAMIC_DEFAULTS", "true")
    monkeypatch.setenv("URBANOPT_STOCHASTIC_SAMPLING", "false")


def _props(metadata, subtype, bid="15000"):
    feature = {"type": "Feature", "properties": {"id": bid, "asset_id": bid, "type": "Building"},
               "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}}
    out = G.process_feature(feature, {bid: float(metadata["area"])}, {bid: subtype}, {bid: "t"},
                            {bid: ("WY", "USA_WY_Jackson.Hole.725776_TMY3.epw")}, {bid: "6B"},
                            {bid: metadata["year_built"]}, {bid: dict(metadata)})
    assert out is not None
    return out[0]["features"][0]["properties"] if "features" in out[0] else out[0]["properties"]


def test_ratings_reach_feature_file(hosted):
    plain = _props(HOUSE, "Single-Family Detached")
    assert not set(RATINGS) & set(plain)
    assert {k: _props(dict(HOUSE, **RATINGS), "Single-Family Detached")[k] for k in RATINGS} == RATINGS
    assert not set(RATINGS) & set(_props(dict(OFFICE, **RATINGS), "Office"))   # residential path only


def test_mapper_ratings_apply_only_to_rated_systems():
    # source check: each rating is gated on the system it rates and set with its rating type (the simulated arms of
    # the plan's log show the built arguments)
    i = RB.index("if PowerTwinRefs.present?(res_props[:heating_afue])")
    block = RB[i:RB.index("# Vacant buildings: no occupants", i)]
    assert "%w[Furnace WallFurnace Boiler].include?(args[:heating_system_type].to_s)" in block
    assert "args[:cooling_system_cooling_efficiency_type] = 'SEER2'" in block
    assert "args[:heat_pump_cooling_efficiency_type] = 'SEER2'" in block
    assert "args[:heat_pump_heating_efficiency_type] = 'HSPF2'" in block
    # after the turnover and explicit-equipment rules, before vacancy
    assert RB.index("eff_hvac_year = [res_year + 20, sim_year - 20].max") < i < RB.index("if res_props[:vacant]")


def test_appliances_reach_feature_file_and_mapper(hosted):
    house = _props(dict(HOUSE, **APPLIANCES), "Single-Family Detached")
    assert {k: house[k] for k in APPLIANCES} == APPLIANCES
    for arg in ("refrigerator_rated_annual_kwh", "clothes_dryer_efficiency"):
        assert f"args[:{arg}]" in RB
    assert "args[:misc_plug_loads_other_annual_kwh] = res_props[:plug_load_kwh_per_year].to_f" in RB
    # HPXML takes a washer or dishwasher efficiency only with the full EnergyGuide label, so neither is an input
    assert "args[:clothes_washer_efficiency]" not in RB and "args[:dishwasher_efficiency]" not in RB


def test_commercial_plug_load_cut_and_airtightness_guard(hosted):
    office = _props(dict(OFFICE, plug_load_reduction_pct=20, com_airtightness_75pa=5.0), "Office")
    assert office["plug_load_reduction_pct"] == 20 and office["com_airtightness_75pa"] == 5.0
    assert "plug_load_reduction_pct" not in _props(dict(HOUSE, plug_load_reduction_pct=20), "Single-Family Detached")
    assert "Laboratory" not in NIST_PROTOTYPES
    lab = _props(dict(OFFICE, com_airtightness_75pa=5.0), "Laboratory")
    assert "com_airtightness_75pa" not in lab            # no NIST coefficients: dropped, not a failed build
    assert "&& !props[:vacant]" in RB                    # a vacant building keeps its own equipment cut


def test_explicit_duct_sealing_without_resolved_location(hosted):
    """A house with no county: the explicit leakage arrives, with the duct location only if one resolved (none: the
    mapper seals the ducts where OS-HPXML places them, without moving them)."""
    plain = _props(dict(HOUSE, county_fips=None), "Single-Family Detached")
    sealed = _props(dict(HOUSE, county_fips=None, duct_leakage_pct=4.0), "Single-Family Detached")
    assert sealed["duct_leakage_pct"] == 4.0
    assert sealed.get("duct_location") == plain.get("duct_location")
    assert "elsif ducted && (PowerTwinRefs.present?(res_props[:duct_leakage_pct])" in RB
    ins_only = _props(dict(HOUSE, county_fips=None, duct_insulation_r=8.0), "Single-Family Detached")
    assert ins_only["duct_insulation_r"] == 8.0
