"""Resolver 3.3 commercial interior lighting (2026-10-01): the installed LPD comes from the building's ComStock lighting
generation (county distribution) and NREL/TP-5500-86100 Table 2, replacing the flat 35% cut to the 90.1 allowance,
whose "DOE/NEEA surveys" comment had no source."""
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOLVER = os.path.join(REPO, "solver")
sys.path.insert(0, os.path.join(SOLVER, "app"))
os.environ.setdefault("POWERTWIN_LOG_DIR", "/tmp")

from modules.simulation import sim_params_spec as S  # noqa: E402

REF = os.path.join(SOLVER, "upload", "reference_data")
TETON = {"state": "WY", "county_fips": "56039", "year_built": 1990}


@pytest.fixture(autouse=True)
def _hosted(monkeypatch):
    monkeypatch.setenv("URBANOPT_DYNAMIC_DEFAULTS", "true")
    monkeypatch.setenv("URBANOPT_STOCHASTIC_SAMPLING", "false")


def light(bt, area=20000, floors=1, **md):
    return S.resolve_lighting(S.build_asset_ctx(dict(TETON, area=area, floor_count=floors, **md), building_type=bt))


def test_table_2_is_the_reports_table():
    t = json.load(open(os.path.join(REF, "comstock_lighting_lpd.json")))
    assert t["_source"]["pdf_sha256"] == "8571300be9139a57b75768d87cdcf942a7295de9419011f858f11f7526fd4e5c"
    lpd = t["lpd_w_per_ft2"]
    assert len(lpd) == 14 and lpd["SmallOffice"] == {"gen1": 1.18, "gen2": 0.79, "gen3": 0.5, "gen4": 0.52, "gen5": 0.47}
    assert lpd["Warehouse"]["gen1"] == 0.83 and lpd["RetailStripmall"]["gen4"] == 0.64


@pytest.mark.parametrize("bt,want", [("Office", (0.79, "gen2_t8_halogen", "county_division")),
                                     ("Strip shopping mall", (0.64, "gen4_led", "county_county")),
                                     ("Nonrefrigerated warehouse", (0.4, "gen2_t8_halogen", "county_division"))])
def test_teton_goldens_take_the_modal_generation_in_hosted_mode(bt, want):
    assert light(bt) == want


@pytest.mark.parametrize("case", ["dd_off", "house", "no_comstock_type"])
def test_no_lpd_leaves_the_old_path(monkeypatch, case):
    if case == "dd_off":
        monkeypatch.setenv("URBANOPT_DYNAMIC_DEFAULTS", "false")
    bt = {"dd_off": "Office", "house": "Single-Family Detached", "no_comstock_type": "Laboratory"}[case]
    assert light(bt) == (None, None, None)


def test_stochastic_draws_follow_the_county_shares(monkeypatch):
    monkeypatch.setenv("URBANOPT_STOCHASTIC_SAMPLING", "true")
    gens = {light("Strip shopping mall", building_id=str(i))[1] for i in range(200)}
    assert gens == {"gen1_t12_incandescent", "gen2_t8_halogen", "gen4_led"}        # Teton's county cell, 21 buildings


def test_workflow_and_mapper_apply_it_and_keep_the_vacant_cut():
    osw = [s["measure_dir_name"] for s in json.load(open(os.path.join(SOLVER, "upload", "base_workflow.osw")))["steps"]]
    assert osw.index("set_lighting_power_density") + 1 == osw.index("ReduceLightingLoadsByPercentage")
    rb = open(os.path.join(SOLVER, "upload", "PowerTwin.rb")).read()
    seg = rb.split("# Interior lighting (resolver 3.3)", 1)[1][:2500]
    assert "if PowerTwinRefs.present?(props[:lighting_power_density])" in seg
    assert "'set_lighting_power_density', 'target_w_per_ft2', props[:lighting_power_density].to_f" in seg
    vacant = seg.split("if props[:vacant]", 1)[1].split("elsif", 1)[0]
    assert "'ReduceLightingLoadsByPercentage', '__SKIP__', false" in vacant and "'lighting_power_reduction_percent', PowerTwinRefs::VACANT_LIGHTING_CUT_PCT" in vacant
    gff = open(os.path.join(SOLVER, "app", "modules", "simulation", "generateFeatureFile.py")).read()
    assert 'new_properties["lighting_power_density"] = lpd' in gff
