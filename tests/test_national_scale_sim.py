"""Real-pipeline regression test at NATIONAL id magnitude + AK coverage.

The nat40 campaign proved the e2e leaf fixture (ids ~800k) structurally cannot catch scale-dependent
bugs: national ids are collection_id * 10**10 + asset_id (~10**10-10**11), which overflowed the
int32 urbanopt_feature_id measure argument and failed 100% of residential; and Alaska/Hawaii
buildings failed site_properties validation because the future-emissions enum is CONUS-only.

This test runs the REAL pipeline (same driver as test_sim_e2e) on a 5-building slice with:
  - a Single-Family Detached at national id magnitude   (int32 overflow regression)
  - a Multifamily at national id magnitude              (the other residential workflow path)
  - a commercial building relocated to Anchorage, AK    (emissions-enum omission regression)
  - a commercial building forced to district steam      (fuel-enum rehoming + steam capture)
  - a commercial CONTROL at national id magnitude, untouched -- it passed even pre-fix, so it
    distinguishes environment breakage (all five fail) from a genuine regression (probes fail,
    control passes)

All five must simulate to a default_feature_report. On pre-fix code, the four probes fail.
"""

import importlib
import importlib.util
import glob
import json
import os
import sys

import pandas as pd
import pytest

pytestmark = pytest.mark.requires_container

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
APP = os.path.join(REPO, "solver", "app")
DATA_OUT = os.path.join(HERE, "data", "out")
sys.path.insert(0, HERE)
sys.path.insert(0, APP)

NATIONAL_BASE = 51 * 10**10  # WY-style collection prefix; anything > 2**31-1 exercises the class
ANCHORAGE = {"latitude": 61.19, "longitude": -149.87}


def _load(modname, path):
    spec = importlib.util.spec_from_file_location(modname, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _report_for(report_root, asset_id):
    hits = glob.glob(os.path.join(report_root, "**", str(asset_id), "**", "default_feature_report*.csv"),
                     recursive=True)
    return hits[0] if hits else None


@pytest.fixture(scope="module")
def national_outputs(tmp_path_factory):
    leaf_stock = _load("leaf_stock", os.path.join(HERE, "data", "leaf_stock.py"))
    rows = leaf_stock.load()

    def _first(subtype_name):
        return next(r for r in rows if r["asset_subtype_name"] == subtype_name
                    and r["metadata"].get("latitude") and r["metadata"].get("longitude"))

    picks = {
        "sfd_big": _first("Single-Family Detached"),
        "mf_big": _first("Multifamily"),
        "office_ak": _first("Office"),
        "district_steam": _first("Retail other than mall"),
        "control": _first("Education"),  # untouched baseline -- passed even pre-fix
    }
    id_map = {str(r["asset_id"]): str(NATIONAL_BASE + int(r["asset_id"])) for r in picks.values()}
    assert all(int(v) > 2**31 - 1 for v in id_map.values()), "ids must overflow int32 or the test is vacuous"
    ak_old_id = str(picks["office_ak"]["asset_id"])

    geo = json.load(open(os.path.join(DATA_OUT, "leaf_stock_geometries.geojson")))
    feats = []
    for f in geo["features"]:
        p = f.get("properties", {})
        old = str(p.get("id") or p.get("asset_id"))
        if old in id_map:
            p["id"] = id_map[old]
            p["asset_id"] = id_map[old]
            feats.append(f)
    geo["features"] = feats
    assert len(feats) == 5

    meta = pd.read_csv(os.path.join(DATA_OUT, "leaf_stock_metadata.csv"))
    meta = meta[meta["asset_id"].astype(str).isin(id_map)].copy()
    # relocate the AK building + remap every id, incl. inside the embedded asset_metadata JSON
    new_meta_rows = []
    for _, row in meta.iterrows():
        old = str(row["asset_id"])
        row = row.copy()
        row["asset_id"] = id_map[old]
        am = json.loads(row["asset_metadata"]) if isinstance(row.get("asset_metadata"), str) else {}
        if old == ak_old_id:
            am.update(ANCHORAGE)
        if old == str(picks["district_steam"]["asset_id"]):
            # force the CBECS district-heat fuel: explicit metadata returns verbatim from get_param,
            # exercising the *_extended rehoming + the mapper's htg_src=DistrictHeating lever
            am["heating_system_fuel_type"] = "district steam"
        row["asset_metadata"] = json.dumps(am)
        # the pipeline keys building_id off the EMBEDDED geometries-properties id, not the asset_id
        # column -- remap it too or metadata and geojson disagree and every building is dropped
        gp = json.loads(row["asset_geometries_properties"]) if isinstance(row.get("asset_geometries_properties"), str) else {}
        for k in ("id", "asset_id"):
            if k in gp:
                gp[k] = id_map[old]
        row["asset_geometries_properties"] = json.dumps(gp)
        new_meta_rows.append(row)
    meta = pd.DataFrame(new_meta_rows)

    os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "true"
    os.environ["URBANOPT_STOCHASTIC_SAMPLING"] = "true"
    os.environ["URBANOPT_KEEP_RUN_DIR"] = "true"

    sim_name = "national_scale"
    sim_dir = str(tmp_path_factory.mktemp("sim") / sim_name)
    local_dir = str(tmp_path_factory.mktemp("local") / sim_name)
    os.makedirs(sim_dir, exist_ok=True)
    os.makedirs(local_dir, exist_ok=True)
    geo_path = os.path.join(local_dir, f"{sim_name}_asset.geojson")
    meta_path = os.path.join(local_dir, f"{sim_name}_metadata.csv")
    json.dump(geo, open(geo_path, "w"))
    meta.to_csv(meta_path, index=False)

    views = importlib.import_module("views")
    gff = importlib.import_module("modules.simulation.generateFeatureFile")
    init = importlib.import_module("modules.simulation.initialize_UOsim")
    views.create_table()
    gff.create_featurefiles(sim_dir, local_dir, geo_path, meta_path, 1, sim_name)
    init.initialize_uo(sim_dir, local_dir, sim_name)
    return local_dir, sim_dir, picks, id_map, ak_old_id


def test_residential_simulates_at_national_id_magnitude(national_outputs):
    local_dir, _sim, picks, id_map, _ak = national_outputs
    for key in ("sfd_big", "mf_big"):
        nid = id_map[str(picks[key]["asset_id"])]
        assert _report_for(local_dir, nid), \
            f"{key} (id {nid}) produced no report -- int32 feature-id regression"


def test_alaska_building_simulates(national_outputs):
    local_dir, _sim_dir, picks, id_map, ak_old = national_outputs
    nid = id_map[ak_old]
    # pre-fix, the AK future-subregion enum violation failed site_properties validation at `uo run`
    # LOAD time -- no measure ever executed. A report existing therefore proves the feature file
    # passed schema validation with the subregion omitted. (Feature jsons are cleaned post-run, so
    # assert on the surviving run artifacts.)
    assert _report_for(local_dir, nid), f"AK building {nid} produced no report -- emissions-enum regression"
    osws = glob.glob(os.path.join(local_dir, "**", str(nid), "in.osw"), recursive=True)
    assert osws, f"no in.osw for AK building {nid} (KEEP_RUN_DIR off?)"
    osw = json.load(open(osws[0]))
    epw = json.dumps(osw)
    assert "USA_AK_" in epw, "AK building did not get an Alaska weather station"


def test_emissions_omission_unit():
    # the unit-level half of the AK fix: unmapped states must OMIT the future subregion key, and
    # the mapping tables must not contain the CONUS-only-enum violations
    src = open(os.path.join(REPO, "solver", "app", "modules", "simulation", "generateFeatureFile.py")).read()
    assert "'AK': 'AKGDc'" not in src and "'HI': 'HIc'" not in src, \
        "AK/HI future-subregion mappings must stay absent (schema enum is CONUS-only)"
    assert "del final_json[\"project\"][_key]" in src, "unmapped subregions must be omitted, not emitted as null"


def test_district_steam_building_simulates_with_district_heat(national_outputs):
    # pre-fix, 'district steam' failed building_properties validation at load (fuel enum lacks it).
    # The fix rehomes it to *_extended; the mapper must then drive create_typical
    # htg_src=DistrictHeating, and EnergyPlus's DistrictHeating output is captured as steam.
    local_dir, _sim, picks, id_map, _ak = national_outputs
    nid = id_map[str(picks["district_steam"]["asset_id"])]
    assert _report_for(local_dir, nid), f"district-steam building {nid} produced no report"
    osws = glob.glob(os.path.join(local_dir, "**", str(nid), "in.osw"), recursive=True)
    assert osws, f"no in.osw for district-steam building {nid}"
    osw = json.load(open(osws[0]))
    args = {}
    for step in osw.get("steps", []):
        if step.get("measure_dir_name") == "create_typical_building_from_model":
            args.update(step.get("arguments", {}))
    assert args.get("htg_src") == "DistrictHeating", \
        f"htg_src not DistrictHeating (got {args.get('htg_src')!r}) -- extended-key lever regression"
    steam = glob.glob(os.path.join(local_dir, "cleaned_reports", str(nid), "cleaned_predicted_steam.csv"))
    assert steam, "district-heat building produced no steam stream (Steam sensor capture regression)"


def test_untouched_control_simulates(national_outputs):
    # the CONTROL: no id-magnitude probe semantics beyond the big id, no relocation, no fuel forcing.
    # It passed even on pre-fix code, so an environment-level breakage (weather download, bundler,
    # DB) fails it too, while a genuine regression fails only its probe -- diagnostic isolation.
    local_dir, _sim, picks, id_map, _ak = national_outputs
    nid = id_map[str(picks["control"]["asset_id"])]
    assert _report_for(local_dir, nid), f"CONTROL building {nid} failed -- suspect environment, not a regression"
