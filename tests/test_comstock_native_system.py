"""A county-drawn ComStock 'Residential forced air furnace' builds as ComStock builds it (2026-10-01).

urbanopt-geojson's system_type enum lacks the label, so COMSTOCK_HVAC_KEY maps it to 'Forced air furnace' for the
schema. That system adds a 100% outdoor-air intake downstream of the furnace; 90.1 gives it an energy recovery
ventilator whose pretreat setpoint references a mixed-air node nothing controls, and EnergyPlus stops with
checkSetpointNodesAtEnd (7 of 20 such Teton vacant buildings). The feature now carries comstock_system_type and the
mapper passes it to create_typical, whose choice list has it (openstudio-standards cbecs_hvac: unventilated furnace)."""
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))
os.environ.setdefault("POWERTWIN_LOG_DIR", "/tmp")

from modules.simulation import generateFeatureFile as G  # noqa: E402
from modules.simulation import sim_params_spec as S  # noqa: E402

TETON = {"county_fips": "56039", "state": "WY", "year_built": 1990, "area": 4770.0, "floor_count": 1,
         "latitude": 43.4799, "longitude": -110.7624}


@pytest.fixture
def stock(monkeypatch):
    monkeypatch.setenv("URBANOPT_DYNAMIC_DEFAULTS", "true")
    monkeypatch.setenv("URBANOPT_STOCHASTIC_SAMPLING", "true")


def _props(metadata, bid):
    feature = {"type": "Feature", "properties": {"id": bid, "asset_id": bid, "type": "Building"},
               "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}}
    out = G.process_feature(feature, {bid: 4770.0}, {bid: "Vacant"}, {bid: "t"},
                            {bid: ("WY", "USA_WY_Jackson.Hole.725776_TMY3.epw")}, {bid: "7"}, {bid: 1990},
                            {bid: dict(metadata, building_id=bid)})
    assert out is not None, "process_feature dropped the building"
    return out[0]["features"][0]["properties"] if "features" in out[0] else out[0]["properties"]


def _native_bid():
    for i in range(400):
        ctx = S.build_asset_ctx(dict(TETON, building_id=str(i)), building_type="Vacant", building_id=str(i))
        if S.comstock_native_system(ctx):
            return str(i)
    pytest.fail("no Teton vacant building drew a native ComStock system in 400 ids")


def test_every_native_label_is_a_remapped_comstock_label_the_measure_accepts():
    rb = open(os.path.join(REPO, "solver", "upload", "PowerTwin.rb")).read()
    for label in S.MEASURE_NATIVE_SYSTEMS:
        assert label not in S.ENUM_VALUES["system_type"] and S.COMSTOCK_HVAC_KEY[label] in S.ENUM_VALUES["system_type"]
    assert "system_type = raw_props_fh[:comstock_system_type].to_s" in rb


def test_the_drawn_furnace_carries_its_comstock_label(stock):
    bid = _native_bid()
    p = _props(TETON, bid)
    assert p["system_type"] == "Forced air furnace" and p["comstock_system_type"] == "Residential forced air furnace"
    assert "system_type" not in p["explicit_params"]


def test_a_chosen_system_type_is_built_as_chosen(stock):
    bid = _native_bid()
    p = _props(dict(TETON, system_type="PSZ-AC with gas coil"), bid)
    assert p["system_type"] == "PSZ-AC with gas coil" and "comstock_system_type" not in p


def test_repicking_the_shown_system_type_changes_nothing(stock):
    """The preview shows the enum stand-in; saving it back is a confirmation, so the furnace stays ComStock's."""
    bid = _native_bid()
    base = _props(TETON, bid)
    assert _props(dict(TETON, system_type=base["system_type"]), bid) == base


def test_dynamic_defaults_off_draws_nothing(monkeypatch):
    monkeypatch.setenv("URBANOPT_DYNAMIC_DEFAULTS", "false")
    ctx = S.build_asset_ctx(dict(TETON, building_id="1"), building_type="Vacant", building_id="1")
    assert S.comstock_native_system(ctx) is None
