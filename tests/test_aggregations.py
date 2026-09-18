"""The output aggregation table in solver/upload/reporting_frequency_map.json is what
process_asset_update runs a request at (reporting frequency plus resample) and what
pack_results labels the rows with, so the three must agree with each other."""
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))

MAP_PATH = os.path.join(REPO, "solver", "upload", "reporting_frequency_map.json")


def _table():
    with open(MAP_PATH) as f:
        return json.load(f)


def test_aggregations_agree_with_pack_results_and_urbanopt_natives():
    from modules.utils.pack_results import _RESAMPLE_TO_DATELEVEL

    fmap = _table()
    table = fmap["aggregations"]
    natives = set(fmap["urbanopt_to_user"].keys())
    assert set(table) == {"hourly", "daily", "monthly", "yearly"}
    for label, entry in table.items():
        assert entry["reporting_frequency"] in natives, label
        assert entry["resample"] in _RESAMPLE_TO_DATELEVEL, label
        assert _RESAMPLE_TO_DATELEVEL[entry["resample"]] == entry["datelevel"], label
    assert fmap["default_aggregation"] in table


def test_resample_flag_is_thread_scoped():
    from modules.utils.sim_flags import set_sim_flags, clear_sim_flags, get_sim_flag, sim_env

    set_sim_flags(resample="H")
    try:
        assert get_sim_flag("URBANOPT_RESAMPLE") == "H"
        assert sim_env({"URBANOPT_RESAMPLE": "M"})["URBANOPT_RESAMPLE"] == "H"
    finally:
        clear_sim_flags()
    assert get_sim_flag("URBANOPT_RESAMPLE") is None
