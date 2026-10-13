"""The fast estimate (vault Surrogate Serving Plan step 1, 2026-10-03): asset_update with the surrogate's model_type_id
predicts with a tree model instead of running URBANopt, through the simulation's own metadata and geometry loaders,
and writes the same cleaned reports, so pack_simulation_results and the results contract are unchanged.

The model here is tiny and synthetic (fitted on the real feature width), with a hand-set presence gate; the weather
station is a synthetic EPW in a temporary weather directory, so nothing is downloaded."""
import csv
import glob
import json
import math
import os
import sys

import numpy as np
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(REPO, "solver", "app")
for p in (APP, REPO):
    if p not in sys.path:
        sys.path.insert(0, p)

pytest.importorskip("xgboost")

from modules.simulation import surrogate_serving as SS  # noqa: E402
from modules.simulation.sim_params_spec import RESOLVER_VERSION  # noqa: E402
from modules.utils import pack_simulation_results  # noqa: E402
from modules.utils import weather as W  # noqa: E402
from surrogate.app.modules import schema  # noqa: E402
from surrogate.app.modules import serving  # noqa: E402
from surrogate.app.modules.models.presence import PresenceGate  # noqa: E402
from surrogate.app.modules.models.split_gbm import SplitSurrogate  # noqa: E402

VIEWS = open(os.path.join(APP, "views.py")).read()
STATION = "USA_WY_Cody.Muni.AWOS.726700_TMY3"
LAT, LON = 44.52, -109.06
SIM_NAME = "asset_7_20261003_000000_000000"


def _epw(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("LOCATION,Cody,WY,USA,TMY3,726700,44.52,-109.02,-7.0,1553.0\n")
        f.write("X\n" * 7)
        for h in range(8760):
            t = -5 + 15 * math.sin(h / 8760 * 2 * math.pi)
            ghi = max(0.0, 500 * math.sin((h % 24) / 24 * 2 * math.pi))
            row = ["2000", "1", "1", "1", "0", "x", f"{t:.1f}", "-8", "60"] + ["0"] * 4 + [f"{ghi:.0f}", "300"] \
                + ["0"] * 6 + ["3"] + ["0"] * 13
            f.write(",".join(row) + "\n")


def _square(lon0, side_m):
    dx, dy = side_m / (111320 * math.cos(math.radians(LAT))), side_m / 110540
    return {"type": "Polygon", "coordinates": [[[lon0, LAT], [lon0 + dx, LAT], [lon0 + dx, LAT + dy],
                                                [lon0, LAT + dy], [lon0, LAT]]]}


def _request(local_dir, rows):
    """The files process_asset_update writes: <name>_metadata.csv in convert_metadata_to_csv's shape and
    <name>_asset.geojson."""
    q = lambda v: '"' + json.dumps(v).replace('"', '""') + '"'
    lines = ["asset_id,asset_name,asset_subtype_id,sensors,asset_metadata,asset_geometries_properties"]
    feats = []
    for bid, sub, md, sensors, geo in rows:
        md = dict(md, latitude=LAT, longitude=LON, state="WY", county_fips="56029")
        lines.append(f'{bid},b{bid},{sub},{q(sensors)},{q(md)},{q({"id": bid})}')
        feats.append({"type": "Feature", "geometry": geo, "properties": {"id": bid, "asset_id": 7}})
    mcsv = os.path.join(local_dir, f"{SIM_NAME}_metadata.csv")
    gj = os.path.join(local_dir, f"{SIM_NAME}_asset.geojson")
    with open(mcsv, "w") as f:
        f.write("\n".join(lines))
    with open(gj, "w") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f)
    return mcsv, gj


OFFICE = next(i for i, v in SS.G.ASSET_SUBTYPES.items() if v["name"] == "Office")
ROWS = [  # building id, subtype, metadata, sensors map, footprint
    (1, 4, {"area": 2400, "floor_count": 2, "year_built": 1988}, {"1": "e1", "8": "g1", "9": "p1"}, _square(LON, 10)),
    (2, OFFICE, {"area": 12000, "floor_count": 1, "year_built": 2005}, {"1": "e2", "9": "p2"}, _square(LON + 0.01, 34)),
]
GATE = {"electricity": True, "natural_gas": True}   # every other stream absent, in both workflows


def _write_model(model_dir, width, sim_year=2026, **meta_over):
    rng = np.random.RandomState(0)
    X = rng.rand(40, width).astype(np.float32)
    Y = (rng.rand(40, 10, 8760) * 0.01).astype(np.float32)
    bts = np.array(["Office"] * 20 + ["Single-Family"] * 20)
    SplitSurrogate(n_components=2, backend="xgboost", reg_params={"n_estimators": 5}).fit(X, Y, bts).save(model_dir)
    g = PresenceGate()
    g.n_features = width
    g.cells = {(w, s): GATE.get(s, False) for w in ("residential", "commercial") for s in schema.target_slugs()}
    g.save(model_dir)
    meta = {"model_name": "tiny", "schema_version": schema.SCHEMA_VERSION, "resolver_version": RESOLVER_VERSION,
            "sim_year": sim_year, "stochastic_sampling": True, "dynamic_defaults": True, "packages": {},
            "accuracy": "test only"}
    meta.update(meta_over)
    with open(os.path.join(model_dir, serving.SERVING_META), "w") as f:
        json.dump(meta, f)


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "WEATHER_FILES_DIR", str(tmp_path / "wx"))
    monkeypatch.setattr(W, "get_location", lambda md: ("WY", STATION, "6B"))
    _epw(str(tmp_path / "wx" / STATION / f"{STATION}.epw"))
    local = tmp_path / "user_files" / SIM_NAME
    local.mkdir(parents=True)
    mcsv, gj = _request(str(local), ROWS)
    entries, epws, *_ = SS.build_entries(mcsv, gj)
    width = serving.SurrogateServer.features(None, entries[0], SS.G_sps(), epws[0]).shape[0]
    model_dir = tmp_path / "model"
    _write_model(str(model_dir), width)
    monkeypatch.setenv("SURROGATE_MODEL_DIR", str(model_dir))
    monkeypatch.setenv("URBANOPT_SIMULATION_YEAR", "2026")
    serving._SERVERS.clear()
    yield {"local": str(local), "mcsv": mcsv, "gj": gj, "model_dir": str(model_dir), "width": width}
    serving._SERVERS.clear()


def test_entries_follow_the_simulation_loaders(served):
    entries, epws, sens, ids, skipped, _ = SS.build_entries(served["mcsv"], served["gj"])
    assert ids == ["1", "2"] and skipped == {}
    house, office = entries
    assert house["building_type"] == "Single-Family" and office["building_type"] == "Office"   # raw subtype names
    assert house["floor_area_ft2"] == 2400.0 and house["floor_count"] == 2 and house["decade_vintage"] == "1980-1989"
    assert abs(house["shape_factor"] - 1.0) < 1e-3          # a square, measured in metres
    assert epws[0].endswith(f"{STATION}/{STATION}.epw") and sens[0] == {"1": "e1", "8": "g1", "9": "p1"}


def test_run_writes_cleaned_reports_and_packs(served):
    server = SS.get_server()
    out = SS.run(served["local"], served["mcsv"], served["gj"], server)
    # the gate keeps electricity and gas; the office has no gas sensor, and no building gets propane
    assert out["streams"] == {"1": ["electricity", "natural_gas"], "2": ["electricity"]}
    files = sorted(os.path.relpath(p, served["local"]) for p in glob.glob(f"{served['local']}/cleaned_reports/*/*.csv"))
    assert files == ["cleaned_reports/1/cleaned_predicted_electricity.csv",
                     "cleaned_reports/1/cleaned_predicted_natural_gas.csv",
                     "cleaned_reports/2/cleaned_predicted_electricity.csv"]
    rows = list(csv.DictReader(open(os.path.join(served["local"], files[0]))))
    assert len(rows) == 8760 and rows[0]["id"] == "e1"
    assert rows[0]["ts"] == "2026-01-01T01:00:00-07:00" and rows[-1]["ts"] == "2027-01-01T00:00:00-07:00"
    assert all(len(r["value"].replace(".", "").replace("-", "").lstrip("0")) <= 4 for r in rows[:200])
    packed = pack_simulation_results(served["local"], runtime_seconds=1.0, resample="M")
    assert packed["datelevel"] == "month"
    got = {(r["asset_id"], r["sensor_type_id"]) for r in packed["results"]}
    assert got == {("7", 1), ("7", 8)} and all(len(r["rows"]) >= 12 for r in packed["results"])


def test_prediction_is_gated_intensity_times_floor_area(served):
    """The model predicts per m2; serving clips, zeroes the streams the gate says are absent and multiplies by the
    building's floor area (ft2 x 0.092903, the collector's constant)."""
    server = SS.get_server()
    entries, epws, *_ = SS.build_entries(served["mcsv"], served["gj"])
    (_, Y), = list(server.predict(entries, SS.G_sps(), epws))
    X = np.stack([server.features(e, SS.G_sps(), p) for e, p in zip(entries, epws)])
    bts = [e["building_type"] for e in entries]
    per_m2 = np.clip(server.model.predict(X, bts), 0, None) * server.gate.predict(X, bts)[:, :, None]
    area = np.array([e["floor_area_ft2"] * 0.092903 for e in entries], np.float32)
    assert np.allclose(Y, per_m2 * area[:, None, None], rtol=1e-6, atol=0)
    assert not Y[:, schema.target_slugs().index("propane")].any()        # gated off

def test_stream_ids_match_the_pack_index():
    from modules.utils.pack_results import _load_sensor_type_index
    idx = _load_sensor_type_index()
    for s in schema.TARGET_STREAMS:
        assert idx[s.slug][0] == s.sensor_type_id, s.slug


def test_precheck_refuses_what_it_cannot_serve(served, monkeypatch):
    assert SS.precheck()["model_name"] == "tiny"
    monkeypatch.setenv("URBANOPT_SIMULATION_YEAR", "2025")
    with pytest.raises(SS.SurrogateUnavailable, match="trained for 2026"):
        SS.precheck()
    monkeypatch.delenv("URBANOPT_SIMULATION_YEAR")                # unset is the solver's default year, 2025
    with pytest.raises(SS.SurrogateUnavailable, match="trained for 2026"):
        SS.precheck()
    monkeypatch.setenv("URBANOPT_SIMULATION_YEAR", "2026")
    meta_path = os.path.join(served["model_dir"], serving.SERVING_META)
    meta = json.load(open(meta_path))
    for key, bad in (("schema_version", "0.0.1"), ("resolver_version", "0.0"), ("packages", {"xgboost": "0.1"})):
        json.dump(dict(meta, **{key: bad}), open(meta_path, "w"))
        with pytest.raises(SS.SurrogateUnavailable):
            SS.precheck()
    json.dump(meta, open(meta_path, "w"))
    import shutil
    shutil.rmtree(os.path.join(served["model_dir"], "presence"))
    with pytest.raises(SS.SurrogateUnavailable, match="presence gate"):
        SS.precheck()
    monkeypatch.delenv("SURROGATE_MODEL_DIR")
    with pytest.raises(SS.SurrogateUnavailable, match="not set"):
        SS.precheck()


def test_unsimulatable_and_weatherless_buildings_are_skipped(served, monkeypatch):
    rows = ROWS + [(3, 4, {"area": 1800, "floor_count": 1, "year_built": 1999}, {"1": "e3"}, _square(LON + 0.02, 9))]
    mcsv, gj = _request(served["local"], rows)
    monkeypatch.setattr(W, "get_location", lambda md: ("WY", STATION, "6B") if md.get("area") != 1800 else (None, None, None))
    _, _, _, ids, skipped, _ = SS.build_entries(mcsv, gj)
    assert ids == ["1", "2"] and set(skipped) == {"3"}


def test_asset_update_routes_by_model_type_id():
    body = VIEWS.split("\ndef process_asset_update(", 1)[1].split("\ndef ", 1)[0]
    # absent is URBANopt; an unknown id is a 400; a fast estimate this solver cannot serve is a 503, never URBANopt
    assert "SS.URBANOPT_MODEL_TYPE_ID if raw_model in (None, '')" in body
    assert "unknown model_type_id" in body and "), 400" in body.split("unknown model_type_id", 1)[1][:80]
    assert "except SS.SurrogateUnavailable" in body and "), 503" in body.split("except SS.SurrogateUnavailable", 1)[1][:160]
    # the check runs before any directory is made, and the surrogate runs in its own worker
    assert body.index("SS.precheck()") < body.index("os.makedirs(SIMULATION_DIR")
    assert "target=_run_asset_update_surrogate" in body and "'model_type_id': model_type_id" in body
    worker = VIEWS.split("\ndef _run_asset_update_surrogate(", 1)[1].split("\ndef ", 1)[0]
    assert "server.env()" in worker and "_sim_env_override(env['URBANOPT_DYNAMIC_DEFAULTS']" in worker
    assert "pack_simulation_results(" in worker and "'estimate_kind': 'baseline_surrogate'" in worker
    assert "'failed'" in worker and "shutil.rmtree(simulation_dir" in worker


def test_compact_model_is_served_when_compiled(served):
    """A model directory with compact_meta.json serves the memory-mapped arrays (1.2 GB for natv3 instead of
    18.5 GB), equal to the source model."""
    from surrogate.app.modules.models.compact_trees import CompactSurrogate, compile_model
    rng = np.random.RandomState(1)
    X = rng.rand(40, served["width"]).astype(np.float32)
    Y = (rng.rand(40, 10, 8760) * 0.01).astype(np.float32)
    bts = np.array(["Office"] * 20 + ["Single-Family"] * 20)
    src = SplitSurrogate(n_components=2, backend="xgboost", reg_params={"n_estimators": 5}).fit(X, Y, bts)
    src.save(served["model_dir"])
    compile_model(src, served["model_dir"], log=lambda *_: None)
    meta_path = os.path.join(served["model_dir"], serving.SERVING_META)
    json.dump(dict(json.load(open(meta_path)), packages={"scikit-learn": "0.0.1", "xgboost": "0.0.1"}), open(meta_path, "w"))
    SS.precheck()        # arrays and JSON only: the training environment's package versions do not bind serving
    server = SS.get_server()
    assert isinstance(server.model, CompactSurrogate)
    entries, epws, *_ = SS.build_entries(served["mcsv"], served["gj"])
    Xs = np.stack([server.features(e, SS.G_sps(), p) for e, p in zip(entries, epws)])
    names = [e["building_type"] for e in entries]
    # equal to float32 rounding, not bytes: the source model itself differs by 1 ulp between a one-row and a two-row
    # batch (the PCA inverse takes another BLAS path), and serving routes one row per workflow here (2026-10-03)
    assert np.allclose(server.model.predict(Xs, names), src.set_inprocess(2).predict(Xs, names), rtol=1e-6, atol=0)


# --- review 2026-10-03 -------------------------------------------------------------------------------------------

def test_state_is_the_assets_not_the_weather_stations(served, monkeypatch):
    """A Driggs, ID building's nearest station is Jackson Hole, WY. The simulation's ctx and the training entries use
    the asset's state; serving used the station's, so a border town resolved another state's code templates."""
    monkeypatch.setattr(W, "get_location", lambda md: ("WY", STATION, "6B"))
    rows = [(1, OFFICE, {"area": 12000, "floor_count": 1, "year_built": 2005, "state": "Idaho", "county_fips": "16081"},
             {"1": "e1"}, _square(LON, 34)),
            (2, OFFICE, {"area": 12000, "floor_count": 1, "year_built": 2005, "county_fips": "16081"},   # no state: from FIPS
             {"1": "e2"}, _square(LON + 0.01, 34))]
    q = lambda v: '"' + json.dumps(v).replace('"', '""') + '"'
    lines = ["asset_id,asset_name,asset_subtype_id,sensors,asset_metadata,asset_geometries_properties"]
    feats = []
    for bid, sub, md, sensors, geo in rows:
        md = dict(md, latitude=LAT, longitude=LON)
        lines.append(f'{bid},b{bid},{sub},{q(sensors)},{q(md)},{q({"id": bid})}')
        feats.append({"type": "Feature", "geometry": geo, "properties": {"id": bid, "asset_id": 100 + bid}})
    mcsv, gj = os.path.join(served["local"], "id.csv"), os.path.join(served["local"], "id.geojson")
    open(mcsv, "w").write("\n".join(lines))
    json.dump({"type": "FeatureCollection", "features": feats}, open(gj, "w"))
    entries = SS.build_entries(mcsv, gj)[0]
    assert [e["state"] for e in entries] == ["ID", "ID"]


def test_multi_footprint_asset_takes_each_footprints_share(served):
    """Every row of a multi-wing asset repeats the asset's area; each footprint simulates (and is estimated at) its
    share by footprint x floors, as generateFeatureFile.process_feature does."""
    rows = [(1, OFFICE, {"area": 30000, "floor_count": 2, "year_built": 2005}, {"1": "e"}, _square(LON, 40)),
            (2, OFFICE, {"area": 30000, "floor_count": 1, "year_built": 2005}, {"1": "e"}, _square(LON + 0.01, 20))]
    mcsv, gj = _request(served["local"], rows)          # both footprints belong to asset 7
    areas = [e["floor_area_ft2"] for e in SS.build_entries(mcsv, gj)[0]]
    assert areas == [int(30000 * 3200 / 3600), int(30000 * 400 / 3600)]   # 40 m x 40 m x 2 floors, 20 m x 20 m x 1


def test_unservable_buildings_are_skipped_with_a_reason(served, monkeypatch):
    rows = ROWS + [(3, 4, {"area": 1800, "floor_count": 1}, {"1": "e3"}, _square(LON + 0.02, 9)),               # no year
                   (4, 4, {"area": 1800, "floor_count": 1, "year_built": 1999}, {"1": "e4"}, _square(LON + 0.03, 9)),
                   (5, 4, {"floor_count": 1, "year_built": 1999}, {"1": "e5"}, _square(LON + 0.04, 9))]        # no area
    mcsv, gj = _request(served["local"], rows)
    monkeypatch.setattr(W, "get_location", lambda md: ("WY", "USA_WY_Nowhere.999999_TMY3" if md.get("area") == 1800
                                                       and md.get("year_built") else STATION, "6B"))
    _, _, _, ids, skipped, _ = SS.build_entries(mcsv, gj)
    assert ids == ["1", "2"]
    assert "year_built" in skipped["3"] and "weather file" in skipped["4"] and "metadata checks" in skipped["5"]


def test_a_run_with_nothing_to_report_fails(served):
    rows = [(1, 4, {"area": 2400, "floor_count": 2, "year_built": 1988}, {"9": "p1"}, _square(LON, 10))]  # propane only
    mcsv, gj = _request(served["local"], rows)
    with pytest.raises(RuntimeError, match="no stream to report"):
        SS.run(served["local"], mcsv, gj, SS.get_server())


def test_model_type_setting_cannot_break_or_reroute_urbanopt(monkeypatch):
    for raw, want in (("3", 3), (" 7 ", 7), ("abc", None), ("1", None), ("", None)):
        monkeypatch.setenv("SURROGATE_MODEL_TYPE_ID", raw)
        assert SS.surrogate_model_type_id() == want, raw


def test_bad_year_setting_is_a_503_not_a_500(served, monkeypatch):
    monkeypatch.setenv("URBANOPT_SIMULATION_YEAR", "twenty")
    with pytest.raises(SS.SurrogateUnavailable, match="ValueError"):
        SS.precheck()


def test_leap_year_is_refused_rather_than_ending_on_dec_31(tmp_path):
    with pytest.raises(ValueError, match="2028 has 8784"):
        serving.write_cleaned_reports(str(tmp_path), "1", np.ones((10, 8760), np.float32), {"1": "e"}, 2028, -7)


def test_coarser_reporting_frequency_is_resampled_and_finer_refused():
    body = VIEWS.split("\ndef process_asset_update(", 1)[1].split("\ndef ", 1)[0]
    block = body.split("surrogate_id = SS.surrogate_model_type_id()", 1)[1].split("SS.precheck()", 1)[0]
    ns = {}
    exec(block.split("coarser = ", 1)[1].split("\n", 1)[0].join(["coarser = ", ""]), ns)
    assert ns["coarser"] == {"Hourly": None, "Timestep": None, "Daily": "D", "Monthly": "M", "Runperiod": "Y"}
    assert "is finer'}), 400" in block and "request_resample is None and request_reporting_frequency is not None" in block


def test_batches_cover_every_building_in_order(served, monkeypatch):
    server = SS.get_server()
    entries, epws, *_ = SS.build_entries(served["mcsv"], served["gj"])
    whole = np.concatenate([y for _, y in server.predict(entries * 3, SS.G_sps(), epws * 3)])
    monkeypatch.setattr(server, "BATCH", 2)
    parts = list(server.predict(entries * 3, SS.G_sps(), epws * 3))
    assert [s for s, _ in parts] == [0, 2, 4]
    assert np.allclose(np.concatenate([y for _, y in parts]), whole, rtol=1e-6, atol=0)


def test_values_use_the_solvers_rounding(tmp_path, monkeypatch):
    """clean_report rounds with precision.round_series (POWERTWIN_SIG_FIGS); the fast estimate must write the same
    text a simulation would for the same numbers."""
    import pandas as pd
    from modules.utils.precision import round_series
    y = np.zeros((10, 8760), np.float32)
    y[0] = np.random.RandomState(0).lognormal(0, 3, 8760)
    serving.write_cleaned_reports(str(tmp_path), "1", y, {"1": "e"}, 2026, -7, round_series=round_series)
    got = pd.read_csv(tmp_path / "1" / "cleaned_predicted_electricity.csv")
    assert np.array_equal(got["value"].to_numpy(), round_series(pd.Series(y[0].astype(np.float64))).to_numpy())
    monkeypatch.setenv("POWERTWIN_SIG_FIGS", "0")          # the rule switched off reaches the fast estimate too
    serving.write_cleaned_reports(str(tmp_path / "off"), "1", y, {"1": "e"}, 2026, -7, round_series=round_series)
    off = pd.read_csv(tmp_path / "off" / "1" / "cleaned_predicted_electricity.csv")["value"].to_numpy()
    assert np.allclose(off, y[0].astype(np.float64), rtol=1e-12, atol=0)   # unrounded, up to to_csv text (16 figures)
    assert np.array_equal(serving._round4(y[0]), round_series(pd.Series(y[0].astype(np.float64)), 4).to_numpy())


def test_a_replaced_model_is_reloaded_and_broken_parts_are_refused(served):
    first = SS.get_server()
    assert SS.get_server() is first
    meta_path = os.path.join(served["model_dir"], serving.SERVING_META)
    meta = json.load(open(meta_path))
    json.dump(dict(meta, model_name="tiny-2"), open(meta_path, "w"))
    second = SS.get_server()
    assert second is not first and second.meta["model_name"] == "tiny-2"
    gate = os.path.join(served["model_dir"], "presence", "presence_meta.json")
    g = json.load(open(gate))
    json.dump(dict(g, schema_version="0.0.1"), open(gate, "w"))
    with pytest.raises(SS.SurrogateUnavailable, match="presence gate schema"):
        SS.precheck()
    json.dump(g, open(gate, "w"))
    open(meta_path, "w").write("{not json")
    with pytest.raises(SS.SurrogateUnavailable, match="unreadable"):
        SS.precheck()



def test_explicit_params_the_model_does_not_read_route_to_urbanopt(served):
    """A fast estimate must not silently ignore a user's explicit value (2026-10-13): a building whose metadata sets a
    simulation param the surrogate does not read is skipped with a reason; params it reads are served as before."""
    rows = ROWS + [(3, 4, {"area": 1800, "floor_count": 1, "year_built": 1999, "infiltration_ach50": 3.0},
                    {"1": "e3"}, _square(LON + 0.02, 9)),
                   (4, OFFICE, {"area": 9000, "floor_count": 1, "year_built": 2001, "system_type": "VAV chiller with gas boiler reheat"},
                    {"1": "e4"}, _square(LON + 0.03, 30)),
                   (5, 4, {"area": 1800, "floor_count": 1, "year_built": 1999, "window_u_factor": 0.3, "wall_r_value": 21},
                    {"1": "e5"}, _square(LON + 0.04, 9))]
    mcsv, gj = _request(served["local"], rows)
    entries, _, _, ids, skipped, _ = SS.build_entries(mcsv, gj)
    assert ids == ["1", "2", "5"]
    assert "infiltration_ach50" in skipped["3"] and "URBANopt" in skipped["3"]
    assert "system_type" in skipped["4"]
    assert entries[2]["explicit"] == {"window_u_factor": 0.3, "wall_r_value": 21}


def test_surrogate_explicit_inputs_match_what_the_collector_reads():
    """EXPLICIT_INPUTS must name every metadata key resolve_static_vector reads explicitly, and only those."""
    import re
    from surrogate.app.modules.data import collector as C
    src = open(C.__file__).read()
    body = src[src.index("def resolve_static_vector"):src.index("def load_targets")]
    reads = set(re.findall(r'gp\("([a-z_0-9]+)"\)', body)) | set(re.findall(r'meta\.get\("([a-z_0-9]+)"\)', body))
    reads |= set(C._GET_PARAM_KEY.values())
    assert reads == set(C.EXPLICIT_INPUTS), (sorted(reads - C.EXPLICIT_INPUTS), sorted(C.EXPLICIT_INPUTS - reads))


def test_values_beyond_training_and_unread_params_are_refused_up_front(served):
    """2026-10-13 review: a value outside what the model saw goes to URBANopt (trees do not extrapolate), per
    building in build_entries and at request time (refusal -> asset_update answers 409, not a failed background run)."""
    from surrogate.app.modules.data import collector as C
    res_u = C.trained_spans(True)["window_u_factor"]
    com_lpd = C.trained_spans(False)["lighting_power_density"]
    assert res_u[0] <= 0.30 <= res_u[1] and com_lpd == (0.21, 1.73)
    rows = ROWS + [(3, 4, {"area": 1800, "floor_count": 1, "year_built": 1999, "window_u_factor": 0.05},
                    {"1": "e3"}, _square(LON + 0.02, 9)),
                   (4, OFFICE, {"area": 9000, "floor_count": 1, "year_built": 2001, "lighting_power_density": 3.0},
                    {"1": "e4"}, _square(LON + 0.03, 30)),
                   (5, OFFICE, {"area": 9000, "floor_count": 1, "year_built": 2001, "lighting_power_density": 0.45,
                                "door_r_value": " "}, {"1": "e5"}, _square(LON + 0.04, 30))]
    mcsv, gj = _request(served["local"], rows)
    _, _, _, ids, skipped, _ = SS.build_entries(mcsv, gj)
    assert ids == ["1", "2", "5"]                     # a whitespace-only value is unset, as get_param reads it
    assert "window_u_factor" in skipped["3"] and "training range" in skipped["3"]
    assert "lighting_power_density" in skipped["4"]
    assert SS.refusal([{"asset_metadata": {"infiltration_ach50": 3}}]).startswith("explicit infiltration_ach50")
    assert "training range" in SS.refusal([{"asset_metadata": json.dumps({"lighting_power_density": 3.0})}])
    assert "training range" in SS.refusal([{"asset_subtype_id": OFFICE, "asset_metadata": {"lighting_power_density": 3.0}}])
    assert SS.refusal([{"asset_subtype_id": 4, "asset_metadata": {"window_u_factor": 1.3}}])     # beyond a house's tiers
    assert SS.refusal([{"asset_metadata": {"window_u_factor": 0.3, "wall_r_value": 21}}]) is None
    views = open(os.path.join(APP, "views.py")).read()
    assert "refused = SS.refusal(metadata_json)" in views and "'route': 'urbanopt'" in views
