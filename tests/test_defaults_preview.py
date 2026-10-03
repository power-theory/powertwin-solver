"""POST /api/simulation/resolve-defaults shows each field's default as the value it takes when the user leaves it
unset (review 2026-09-28, reproduced on Teton 15973): with a saved heating_system_type in the metadata the modal
showed 'furnace' while the house's unset default is its county equipment (electric resistance), so re-picking
the shown default was an explicit edit that rebuilt the house as a gas furnace. The hint must not depend on the
field's own saved value, and re-picking it must count as confirming the default."""
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOLVER = os.path.join(REPO, "solver")

# Teton 15973's resolver inputs (Single-Family Detached, 1964, 1 floor)
HOUSE = {"county_fips": "56039", "state": "WY", "year_built": 1964, "area": 1216.0, "floor_count": 1,
         "latitude": 43.4799, "longitude": -110.7625}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.chdir(SOLVER)                       # the app resolves its data paths relative to solver/
    monkeypatch.syspath_prepend(os.path.join(SOLVER, "app"))
    monkeypatch.setenv("POWERTWIN_LOG_DIR", "/tmp")
    monkeypatch.setenv("API_SOLVER_TOKEN", "t")
    monkeypatch.setenv("URBANOPT_DYNAMIC_DEFAULTS", "true")
    monkeypatch.setenv("URBANOPT_STOCHASTIC_SAMPLING", "false")
    import flask
    import views
    app = flask.Flask("t")
    app.add_url_rule("/r", view_func=views.resolve_defaults_preview, methods=["POST"])
    c = app.test_client()
    return lambda md: c.post("/r", json={"metadata": md, "building_type": "Single-Family Detached"},
                             headers={"api-token": "t"}).get_json()["resolved"]


@pytest.mark.parametrize("saved", ["boiler", "heat_pump", "furnace", "electric_resistance"])
def test_heating_default_ignores_its_own_saved_value(client, saved):
    assert client(dict(HOUSE, heating_system_type=saved))["heating_system_type"] == client(HOUSE)["heating_system_type"]


def test_repicking_the_shown_default_confirms_it(client):
    from modules.simulation.sim_params_spec import build_asset_ctx
    shown = client(dict(HOUSE, heating_system_type="boiler"))["heating_system_type"]
    ctx = build_asset_ctx(dict(HOUSE, heating_system_type=shown), building_type="Single-Family Detached")
    assert ctx.get("heating_system_type_confirms_default") is True


def test_other_fields_still_condition_on_saved_choices(client):
    """Only the field itself is unset: the fuel default still follows a saved heating system."""
    assert client(dict(HOUSE, heating_system_type="heat_pump"))["heating_system_fuel_type"] == "electricity"


def test_saved_field_key_case_does_not_leak_into_its_default(client):
    """build_asset_ctx lowercases keys: a saved 'Heating_System_Type' must be dropped like the lowercase one."""
    assert client(dict(HOUSE, Heating_System_Type="boiler"))["heating_system_type"] == client(HOUSE)["heating_system_type"]


@pytest.mark.parametrize("building_type", ["Single-Family Detached", "Multifamily", "Office", "Mixed use", "Retail"])
def test_preview_resolves_in_the_simulations_climate_zone(client, building_type):
    """The simulation keys its ctx on the IECC zone of the coordinates (generateFeatureFile: get_location ->
    get_climate_zone); the preview had none and fell back to census region. On Teton (7A) houses showed electric
    cooling the simulation never builds and a 1964 office a Standard R-4 wall it builds as Super Insulated R-17
    (2026-09-29; before the fix every sampled Teton house and 47 of 187 commercial buildings differed)."""
    import views
    from modules.simulation.sim_params_spec import build_asset_ctx, resolve_default
    from modules.utils.weather import get_climate_zone
    md = dict(HOUSE, year_built=1964)
    sim_ctx = build_asset_ctx(md, building_type=building_type,
                              climate_zone=get_climate_zone(md["latitude"], md["longitude"]))
    assert sim_ctx["climate_zone"] == "7A"
    shown = client(md) if building_type == "Single-Family Detached" else None
    import flask
    app = flask.Flask("t2")
    app.add_url_rule("/r", view_func=views.resolve_defaults_preview, methods=["POST"])
    got = app.test_client().post("/r", json={"metadata": md, "building_type": building_type},
                                 headers={"api-token": "t"}).get_json()["resolved"]
    if shown is not None:
        assert shown == got
    for f in views.DYNAMIC_FIELDS:
        want = resolve_default(f, build_asset_ctx({k: v for k, v in md.items() if k != f}, building_type=building_type,
                                                  climate_zone="7A"))[0]
        if want is not None:
            assert got[f] == want, f


@pytest.mark.parametrize("dd", ["true", "false"])
def test_preview_shows_what_builds_for_floor_height_and_hpwh_fuel(client, monkeypatch, dd):
    """Live check 2026-09-30: with DD off the preview returned no fuel beside a saved HPWH, so the form showed the
    flat 'natural gas' while the simulation builds electricity; floor_height shows the house's 8 ft either way."""
    monkeypatch.setenv("URBANOPT_DYNAMIC_DEFAULTS", dd)
    r = client(dict(HOUSE, water_heater_type="heat pump water heater", service_water_heating_fuel_type="natural gas"))
    assert r["service_water_heating_fuel_type"] == "electricity" and r["floor_height"] == 8.0
    if dd == "false":
        assert "service_water_heating_fuel_type" not in client(HOUSE)      # no HPWH: DD off still resolves nothing


@pytest.fixture
def office_client(client, monkeypatch):
    import flask
    import views
    app = flask.Flask("t2")
    app.add_url_rule("/r", view_func=views.resolve_defaults_preview, methods=["POST"])
    c = app.test_client()
    return lambda md: c.post("/r", json={"metadata": md, "building_type": "Office"},
                             headers={"api-token": "t"}).get_json()["resolved"]


@pytest.mark.parametrize("bt_md", [{"area": 20000}])
def test_commercial_heat_pump_water_heater_previews_electricity(office_client, bt_md):
    """With dynamic defaults on the commercial fuel resolver filled the donor's natural gas first, so the preview said
    gas while the simulation builds electricity (review 2026-10-03)."""
    md = dict(HOUSE, **bt_md, water_heater_type="heat pump water heater")
    assert office_client(dict(HOUSE, **bt_md))["service_water_heating_fuel_type"] == "natural gas"   # the donor, unedited
    assert office_client(md)["service_water_heating_fuel_type"] == "electricity"


def test_a_case_variant_of_the_heat_pump_type_does_not_force_electricity():
    sys.path.insert(0, os.path.join(SOLVER, "app"))
    from modules.simulation import sim_params_spec as S
    assert S.get_param({"water_heater_type": "Heat Pump Water Heater", "service_water_heating_fuel_type": "natural gas"},
                       "service_water_heating_fuel_type") == "natural gas"
    assert S.get_param({"water_heater_type": "heat pump water heater"}, "service_water_heating_fuel_type") == "electricity"
