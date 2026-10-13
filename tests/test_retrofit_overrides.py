"""Retrofit overrides, phase 1 (vault Dev/powertwin-solver/Retrofit Overrides Plan, 2026-10-13): explicit commercial
lighting power density, residential door R-value, and the explicit residential air sealing and setpoints the product
now exposes. An empty field leaves the solver's default; a value replaces it for that property only. The window
U-factor / SHGC override has its own file (test_window_override.py)."""
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))
os.environ.setdefault("POWERTWIN_LOG_DIR", "/tmp")

from modules.simulation import generateFeatureFile as G  # noqa: E402
from modules.simulation.sim_params_spec import NUMERIC_RANGES, SIM_PARAM_DEFAULTS  # noqa: E402

HOUSE = {"county_fips": "56039", "state": "WY", "year_built": 1993, "area": 1800.0, "floor_count": 1,
         "latitude": 43.48, "longitude": -110.76}
OFFICE = dict(HOUSE, area=20000.0, floor_count=2, year_built=1995)
RB = open(os.path.join(REPO, "solver", "upload", "PowerTwin.rb")).read()


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
    assert out is not None, "process_feature dropped the building"
    return out[0]["features"][0]["properties"] if "features" in out[0] else out[0]["properties"]


def test_new_fields_are_optional_with_ranges():
    for f, rng in (("lighting_power_density", (0.1, 3.5)), ("door_r_value", (1.0, 15.0))):
        assert SIM_PARAM_DEFAULTS[f] == "" and NUMERIC_RANGES[f] == rng and f in G._EXPLICIT_PARAM_KEYS


def test_commercial_lpd_override_reaches_feature_file(hosted):
    plain = _props(OFFICE, "Office")
    over = _props(dict(OFFICE, lighting_power_density=0.45), "Office")
    assert over["lighting_power_density"] == 0.45 and over["lighting_generation"] == "explicit"
    assert plain.get("lighting_generation") != "explicit"
    house = _props(dict(HOUSE, lighting_power_density=0.45), "Single-Family Detached")
    assert "lighting_power_density" not in house          # a house ignores it
    # the mapper's existing hook takes the property as the target LPD
    assert "'set_lighting_power_density', 'target_w_per_ft2', props[:lighting_power_density].to_f" in RB


def test_door_r_value_reaches_feature_file_and_mapper(hosted):
    assert "door_r_value" not in _props(HOUSE, "Single-Family Detached")
    assert _props(dict(HOUSE, door_r_value=7.0), "Single-Family Detached")["door_r_value"] == 7.0
    assert "args[:door_rvalue] = res_props[:door_r_value].to_f if PowerTwinRefs.present?(res_props[:door_r_value])" in RB


@pytest.mark.parametrize("field,value", [("infiltration_ach50", 3.0), ("heating_setpoint_f", 66.0),
                                         ("cooling_setpoint_f", 78.0)])
def test_explicit_air_sealing_and_setpoints_reach_feature_file(hosted, field, value):
    """The product now offers these solver fields; an explicit value is delivered even while the infiltration prior
    is held off (the resolver's domain gate applies to its own draws, not to a user's value)."""
    assert _props(dict(HOUSE, **{field: value}), "Single-Family Detached")[field] == value


def test_surrogate_lpd_follows_the_override(hosted):
    sys.path.insert(0, os.path.join(REPO, "surrogate"))
    pytest.importorskip("app.modules.schema", reason="surrogate tree/deps unavailable")
    from app.modules import schema
    from app.modules.data import collector as C
    sps = C._load_sps()
    entry = {"building_id": 10000000001, "building_type": "Office", "state": "AZ", "county_fips": "04013",
             "climate_zone": "2B", "latitude": 33.43, "longitude": -112.02, "floor_area_ft2": 20000,
             "footprint_area_ft2": 10000, "floor_count": 2, "shape_factor": 1.0, "decade_vintage": "2000-2009",
             "year_built": 2005}
    assert C.resolve_static_vector(dict(entry, explicit={"lighting_power_density": 0.45}), sps, schema)[
        "lighting_power_density"] == 0.45
    house = dict(entry, building_type="Single-Family Detached")
    assert C.resolve_static_vector(dict(house, explicit={"lighting_power_density": 0.45}), sps, schema)[
        "lighting_power_density"] == C.resolve_static_vector(dict(house), sps, schema)["lighting_power_density"]
    # door R, air sealing and setpoints are not surrogate inputs: serving routes them to URBANopt
    assert {"door_r_value", "infiltration_ach50", "heating_setpoint_f"}.isdisjoint(C.EXPLICIT_INPUTS)
    assert "lighting_power_density" in C.EXPLICIT_INPUTS
