"""Residential lighting (resolver 3.5, 2026-10-13): a house's interior, exterior and garage lighting is its county's
ResStock 2024.2 share-weighted LED and CFL fractions (resstock2024_county_lighting), replacing BuildResidentialHPXML's
default (interior 10% CFL, 90% incandescent; exterior and garage 100% incandescent) that PowerTwin.rb had applied to every
house without a decision; lighting_led_fraction is also the explicit LED retrofit input. Commercial buildings keep their
lighting power density path."""
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))
os.environ.setdefault("POWERTWIN_LOG_DIR", "/tmp")

from modules.simulation import generateFeatureFile as G  # noqa: E402
from modules.simulation import sim_params_spec as S  # noqa: E402

HOUSE = {"county_fips": "56039", "state": "WY", "year_built": 1993, "area": 1800.0, "floor_count": 1,
         "latitude": 43.48, "longitude": -110.76}
OFFICE = dict(HOUSE, area=20000.0, floor_count=2, year_built=1995)


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


def test_teton_house_gets_its_county_shares(hosted):
    ctx = S.build_asset_ctx(dict(HOUSE), building_type="Single-Family Detached")
    assert S.resolve_default("lighting_led_fraction", ctx) == (0.367, "county_county")
    assert S.resolve_default("lighting_cfl_fraction", ctx) == (0.362, "county_county")
    # the table's own cell, so a regenerated table that moves Teton fails here rather than silently
    cell = S._load_ref("resstock2024_county_lighting")["counties"]["56039"]["county"]["shares"]
    assert (cell["100% LED"], cell["100% CFL"]) == (0.367, 0.362)


def test_explicit_led_caps_the_cfl_share(hosted):
    ctx = S.build_asset_ctx(dict(HOUSE), building_type="Single-Family Detached")
    assert S.residential_lighting_fractions(dict(HOUSE, lighting_led_fraction=1.0), ctx) == (1.0, 0.0)
    assert S.residential_lighting_fractions(dict(HOUSE, lighting_led_fraction=0.8), ctx) == (0.8, 0.2)
    assert S.residential_lighting_fractions(dict(HOUSE, lighting_led_fraction=0.5), ctx) == (0.5, 0.362)


def test_dynamic_defaults_off_leaves_the_engine_default(monkeypatch):
    monkeypatch.setenv("URBANOPT_DYNAMIC_DEFAULTS", "false")
    ctx = S.build_asset_ctx(dict(HOUSE), building_type="Single-Family Detached")
    assert S.residential_lighting_fractions(dict(HOUSE), ctx) == ("", "")


def test_feature_file_carries_the_fractions_for_houses_only(hosted):
    house = _props(HOUSE, "Single-Family Detached")
    assert (house["lighting_led_fraction"], house["lighting_cfl_fraction"]) == (0.367, 0.362)
    office = _props(OFFICE, "Office")
    assert "lighting_led_fraction" not in office and "lighting_cfl_fraction" not in office
    retrofit = _props(dict(HOUSE, lighting_led_fraction=1.0), "Single-Family Detached")
    assert (retrofit["lighting_led_fraction"], retrofit["lighting_cfl_fraction"]) == (1.0, 0.0)
    rb = open(os.path.join(REPO, "solver", "upload", "PowerTwin.rb")).read()
    assert 'args[:"lighting_#{loc}_fraction_led"] = led' in rb and "%w[interior exterior garage].each" in rb


def test_surrogate_vector_carries_the_fractions(hosted):
    sys.path.insert(0, os.path.join(REPO, "surrogate"))
    pytest.importorskip("app.modules.schema", reason="surrogate tree/deps unavailable")
    from app.modules import schema
    from app.modules.data import collector as C
    sps = C._load_sps()
    house = {"building_id": 1, "building_type": "Single-Family Detached", "state": "WY", "county_fips": "56039",
             "climate_zone": "6B", "latitude": 43.48, "longitude": -110.76, "floor_area_ft2": 1800,
             "footprint_area_ft2": 1800, "floor_count": 1, "shape_factor": 1.0, "decade_vintage": "1990-1999",
             "year_built": 1993}
    v = C.resolve_static_vector(dict(house), sps, schema)
    assert (v["lighting_led_fraction"], v["lighting_cfl_fraction"]) == (0.367, 0.362)
    office = C.resolve_static_vector(dict(house, building_type="Office", floor_area_ft2=20000), sps, schema)
    assert office["lighting_led_fraction"] is None
    lo, hi = C.trained_spans(True)["lighting_led_fraction"]
    assert lo <= 0.367 <= hi < 1.0          # an all-LED retrofit is beyond the training span: serving sends it to URBANopt
    assert C.explicit_outside_training({"lighting_led_fraction": 1.0}, True) == ["lighting_led_fraction"]
