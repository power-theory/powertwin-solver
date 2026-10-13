"""generateFeatureFile emits explicit_params (programmable params the metadata supplied and get_param
accepted) and scenario_variant (stochastic_seed_id present, provenance only) so PowerTwin.rb can tell
a chosen heat pump from a resolved one (asset 448523 review, 2026-09-21). Since 2026-09-24 a fan-out
variant counts like a user edit, and a heating_system_type that only confirms the resolved default is
not explicit at all: re-picking what the modal showed must leave the house as it was."""
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))
os.environ.setdefault("POWERTWIN_LOG_DIR", "/tmp")

from modules.simulation import generateFeatureFile as G  # noqa: E402

# Pilot home 15000 (Teton, 1993, 861 sq ft): the county donor is an electric wall furnace, no ducts.
# A house whose hosted-mode heating donor is a ductless wall furnace. Teton (56039) was until resolver 3.4 pooled the
# county tables: its county cell no longer overrides its PUMA, whose modal donor is a ducted furnace. McKinley County,
# NM (Gallup) keeps a wall furnace at the PUMA, state and division blend.
WALL_FURNACE_HOME = {"county_fips": "35031", "state": "NM", "year_built": 1993, "area": 860.6,
                     "floor_count": 1, "latitude": 35.528, "longitude": -108.742}


def _props(metadata, bid="15000"):
    feature = {"type": "Feature", "properties": {"id": bid, "asset_id": bid, "type": "Building"},
               "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}}
    out = G.process_feature(feature, {bid: 860.6}, {bid: "Single-Family Detached"}, {bid: "t"},
                            {bid: ("WY", "USA_WY_Jackson.Hole.725776_TMY3.epw")}, {bid: "6B"}, {bid: 1993},
                            {bid: dict(metadata)})
    assert out is not None, "process_feature dropped the building"
    return out[0]["features"][0]["properties"] if "features" in out[0] else out[0]["properties"]


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv("URBANOPT_DYNAMIC_DEFAULTS", "true")
    monkeypatch.setenv("URBANOPT_STOCHASTIC_SAMPLING", "false")


def test_explicit_keys_cover_the_programmable_params():
    for k in ("heating_system_type", "heating_equipment_detail", "wall_r_value", "weekday_start_time", "system_type"):
        assert k in G._EXPLICIT_PARAM_KEYS


def test_coerce_like_matches_numbers_and_strings():
    assert G._coerce_like("19.25", 19.25) == 19.25
    assert G._coerce_like(" heat_pump ", "heat_pump") == "heat_pump"
    assert G._coerce_like("abc", 1.0) == "abc"


def test_mapper_gates_on_explicit_params_not_on_the_variant_flag():
    rb = open(os.path.join(REPO, "solver", "upload", "powertwin_refs.rb")).read()
    assert "explicit_params" in rb and "scenario_variant" not in rb
    mapper = open(os.path.join(REPO, "solver", "upload", "PowerTwin.rb")).read()
    assert mapper.count("PowerTwinRefs.explicit_hvac?(res_props)") >= 3
    assert "heat_pump_heating_capacity_retention_fraction" in mapper and "'ground-to-air'" in mapper


def test_repicking_the_resolved_heating_type_changes_nothing(hosted):
    base = _props(WALL_FURNACE_HOME)
    # the path under test is live: the donor gives this house a detail and an electric fuel
    assert (base["heating_system_type"], base.get("heating_equipment_detail"), base["heating_system_fuel_type"]) == \
        ("furnace", "WallFurnace", "wood")
    repick = _props({**WALL_FURNACE_HOME, "heating_system_type": base["heating_system_type"]})
    assert repick == base  # detail, ducts, fuels and explicit_params all as unedited


def test_a_different_heating_type_is_still_explicit(hosted):
    p = _props({**WALL_FURNACE_HOME, "heating_system_type": "heat_pump"})
    assert "heating_system_type" in p["explicit_params"]
    assert not p.get("heating_equipment_detail")  # the mapper picks the equipment for a new system
    assert p.get("heating_has_ducts") == "No"  # the house still has no ducts


def test_fanout_variant_is_explicit_like_a_user_edit(hosted):
    user = _props({**WALL_FURNACE_HOME, "heating_system_type": "heat_pump"})
    variant = _props({**WALL_FURNACE_HOME, "heating_system_type": "heat_pump", "stochastic_seed_id": "15000"})
    assert variant["scenario_variant"] is True and user["scenario_variant"] is False
    assert {k: v for k, v in variant.items() if k != "scenario_variant"} == \
        {k: v for k, v in user.items() if k != "scenario_variant"}


def test_repicking_the_resolved_water_heater_changes_nothing(hosted):
    """Review 2026-09-28: re-picking the previewed 'storage water heater' on a Fairfield CT 1950 home redrew its SWH
    fuel from electricity to fuel oil. A water heater equal to the unset default is a confirmation, not an edit."""
    from modules.simulation.sim_params_spec import build_asset_ctx, resolve_default
    home = {"county_fips": "09001", "state": "CT", "year_built": 1950, "area": 1800, "floor_count": 2,
            "latitude": 41.2, "longitude": -73.3}
    bt = "Single-Family Detached"
    c0 = build_asset_ctx(home, building_type=bt)
    shown = resolve_default("water_heater_type", c0)[0]
    c1 = build_asset_ctx(dict(home, water_heater_type=shown), building_type=bt)
    assert c1.get("water_heater_type_confirms_default") is True
    assert resolve_default("service_water_heating_fuel_type", c1)[0] == resolve_default("service_water_heating_fuel_type", c0)[0]
    other = next(v for v in ("heat pump water heater", "instantaneous water heater") if v != shown)
    assert build_asset_ctx(dict(home, water_heater_type=other), building_type=bt).get("water_heater_type") == other
