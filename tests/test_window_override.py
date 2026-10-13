"""Explicit window performance (2026-10-13): a rated U-factor (IP Btu/h*ft^2*F) or SHGC set on a building replaces
the window_type tier's number for that property, in the feature file, in both mapper paths (residential HPXML takes IP,
the commercial set_window_construction measure SI) and in the surrogate's window_u_factor / window_shgc features, so
a fast estimate sees the window the simulation builds. Unset, nothing changes: the tier decides."""
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))
os.environ.setdefault("POWERTWIN_LOG_DIR", "/tmp")

from modules.simulation import generateFeatureFile as G  # noqa: E402
from modules.simulation.sim_params_spec import NUMERIC_RANGES, SIM_PARAM_DEFAULTS, get_param  # noqa: E402

HOUSE = {"county_fips": "56039", "state": "WY", "year_built": 1993, "area": 1800.0, "floor_count": 1,
         "latitude": 43.48, "longitude": -110.76}


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv("URBANOPT_DYNAMIC_DEFAULTS", "true")
    monkeypatch.setenv("URBANOPT_STOCHASTIC_SAMPLING", "false")


def _props(metadata, subtype="Single-Family Detached", bid="15000"):
    feature = {"type": "Feature", "properties": {"id": bid, "asset_id": bid, "type": "Building"},
               "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}}
    out = G.process_feature(feature, {bid: float(metadata["area"])}, {bid: subtype}, {bid: "t"},
                            {bid: ("WY", "USA_WY_Jackson.Hole.725776_TMY3.epw")}, {bid: "6B"},
                            {bid: metadata["year_built"]}, {bid: dict(metadata)})
    assert out is not None, "process_feature dropped the building"
    return out[0]["features"][0]["properties"] if "features" in out[0] else out[0]["properties"]


def test_fields_are_optional_overrides():
    assert SIM_PARAM_DEFAULTS["window_u_factor"] == "" and SIM_PARAM_DEFAULTS["window_shgc"] == ""
    assert NUMERIC_RANGES["window_u_factor"] == (0.05, 1.5) and NUMERIC_RANGES["window_shgc"] == (0.01, 0.99)
    # explicit params flow to the mapper's explicit set and to the surrogate serving entries
    assert {"window_u_factor", "window_shgc"} <= set(G._EXPLICIT_PARAM_KEYS)


def test_get_param_takes_the_number_and_clamps_it():
    assert get_param({"window_u_factor": "0.30"}, "window_u_factor") == 0.30
    assert get_param({"window_shgc": 0.4}, "window_shgc") == 0.4
    assert get_param({"window_u_factor": 7.0}, "window_u_factor") == 1.5      # an SI value typed into the IP field
    assert get_param({"window_shgc": 1.0}, "window_shgc") == 0.99
    assert get_param({}, "window_u_factor") == ""


@pytest.mark.parametrize("subtype,meta", [("Single-Family Detached", HOUSE),
                                          ("Office", dict(HOUSE, area=20000.0, floor_count=2))])
def test_feature_file_carries_the_override_only_when_set(hosted, subtype, meta):
    plain = _props(meta, subtype)
    assert "window_u_factor" not in plain and "window_shgc" not in plain
    set_ = _props(dict(meta, window_u_factor=0.30, window_shgc=0.40), subtype)
    assert set_["window_u_factor"] == 0.30 and set_["window_shgc"] == 0.40
    assert set_["windows"][0]["window_type"] == plain["windows"][0]["window_type"]   # the tier is untouched
    only_u = _props(dict(meta, window_u_factor=0.27), subtype)
    assert only_u["window_u_factor"] == 0.27 and "window_shgc" not in only_u


def test_mapper_replaces_the_tier_number_in_both_paths():
    rb = open(os.path.join(REPO, "solver", "upload", "PowerTwin.rb")).read()
    # residential: IP straight into HPXML, after the tier's numbers
    res = rb.index("args[:window_ufactor] = (u / 5.678263).round(3)")
    assert rb.index("args[:window_ufactor] = res_props[:window_u_factor].to_f.round(3)") > res
    assert "args[:window_shgc] = res_props[:window_shgc].to_f.round(3)" in rb
    # commercial: SI for set_window_construction, the Double Pane tier when only a number is set
    assert "u = win_u_ip.to_f * 5.678263 if PowerTwinRefs.present?(win_u_ip)" in rb
    assert "shgc = win_shgc.to_f if PowerTwinRefs.present?(win_shgc)" in rb
    assert "PowerTwinRefs.present?(window_tier) ? window_tier : 'Double Pane'" in rb


def test_surrogate_features_take_the_override(hosted):
    sys.path.insert(0, os.path.join(REPO, "surrogate"))
    pytest.importorskip("app.modules.schema", reason="surrogate tree/deps unavailable")
    from app.modules import schema
    from app.modules.data import collector as C
    sps = C._load_sps()
    entry = {"building_id": 10000000001, "building_type": "Office", "state": "AZ", "county_fips": "04013",
             "climate_zone": "2B", "latitude": 33.43, "longitude": -112.02, "floor_area_ft2": 20000,
             "footprint_area_ft2": 10000, "floor_count": 2, "shape_factor": 1.0, "decade_vintage": "2000-2009",
             "year_built": 2005}
    plain = C.resolve_static_vector(dict(entry), sps, schema)
    over = C.resolve_static_vector(dict(entry, explicit={"window_u_factor": 0.30, "window_shgc": 0.40}), sps, schema)
    assert (over["window_u_factor"], over["window_shgc"]) == (0.30, 0.40)
    assert (plain["window_u_factor"], plain["window_shgc"]) != (0.30, 0.40)
    only_shgc = C.resolve_static_vector(dict(entry, explicit={"window_shgc": 0.25}), sps, schema)
    assert only_shgc["window_shgc"] == 0.25 and only_shgc["window_u_factor"] == plain["window_u_factor"]
