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
