"""generateFeatureFile emits explicit_params (programmable params the metadata supplied and get_param
accepted) and scenario_variant (stochastic_seed_id present) so PowerTwin.rb can tell a user-chosen
heat pump from a resolved or fanned-out one (asset 448523 review, 2026-09-21)."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))
os.environ.setdefault("POWERTWIN_LOG_DIR", "/tmp")

from modules.simulation import generateFeatureFile as G  # noqa: E402


def test_explicit_keys_cover_the_programmable_params():
    for k in ("heating_system_type", "heating_equipment_detail", "wall_r_value", "weekday_start_time", "system_type"):
        assert k in G._EXPLICIT_PARAM_KEYS


def test_coerce_like_matches_numbers_and_strings():
    assert G._coerce_like("19.25", 19.25) == 19.25
    assert G._coerce_like(" heat_pump ", "heat_pump") == "heat_pump"
    assert G._coerce_like("abc", 1.0) == "abc"


def test_feature_properties_carry_provenance(tmp_path, monkeypatch):
    src = open(os.path.join(REPO, "solver", "app", "modules", "simulation", "generateFeatureFile.py")).read()
    block = src.split('new_properties["explicit_params"]', 1)[1].split("\n\n", 1)[0]
    assert "_EXPLICIT_PARAM_KEYS" in block and 'new_properties["scenario_variant"]' in block
    # the mapper reads both keys and gates on the variant flag
    rb = open(os.path.join(REPO, "solver", "upload", "powertwin_refs.rb")).read()
    assert "explicit_params" in rb and "scenario_variant" in rb
    mapper = open(os.path.join(REPO, "solver", "upload", "PowerTwin.rb")).read()
    assert mapper.count("PowerTwinRefs.explicit_hvac?(res_props)") >= 3
    assert "heat_pump_heating_capacity_retention_fraction" in mapper and "'ground-to-air'" in mapper
