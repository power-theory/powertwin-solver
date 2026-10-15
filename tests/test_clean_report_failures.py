"""clean_single_report (review 2026-09-28): a stream whose cleaning fails (ENOSPC or EACCES on the write) used to be
logged and skipped, after which the asset's raw report was deleted and the asset went Finished without that fuel.
Now the asset fails (run_UOsim's `cleaned_ok is False` path), the partial stream file is removed and the raw
feature report is kept for the retry; a clean run is unchanged."""
import csv
import json
import os
import sys

import pandas as pd
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOLVER = os.path.join(REPO, "solver")
EPW = "USA_WY_Jackson.Hole.AP.725776_TMY3.epw"


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.chdir(SOLVER)
    monkeypatch.syspath_prepend(os.path.join(SOLVER, "app"))
    monkeypatch.setenv("POWERTWIN_LOG_DIR", "/tmp")
    for k in ("URBANOPT_KEEP_RUN_DIR", "POWERTWIN_KEEP_DIRS"):
        monkeypatch.delenv(k, raising=False)
    from modules.simulation import clean_report as C
    monkeypatch.setattr(C, "get_epw_utc_offset", lambda title: -7)   # no EPW cache in tests; Mountain standard
    local, batch, sim, aid = tmp_path / "local", tmp_path / "batch", tmp_path / "sim", "77"
    (sim / aid / "feature_reports").mkdir(parents=True)
    (batch / aid).mkdir(parents=True)
    local.mkdir()
    pd.DataFrame({"Datetime": ["2026/01/01 01:00:00", "2026/01/01 02:00:00", "2026/01/01 03:00:00"],
                  "Electricity:Facility(kWh)": [10.0, 11.0, 12.0],
                  "NaturalGas:Facility(kBtu)": [30.0, 31.0, 32.0]}).to_csv(sim / aid / "feature_reports" / "default_feature_report.csv", index=False)
    json.dump({"steps": [{"measure_dir_name": "ChangeBuildingLocation", "arguments": {"weather_file_name": EPW}}]},
              open(sim / aid / "in.osw", "w"))
    meta = local / "t_metadata.csv"
    with open(meta, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["asset_id", "sensors", "asset_geometries_properties"])
        w.writeheader()
        w.writerow({"asset_id": aid, "sensors": json.dumps({"1": 7701, "8": 7708}), "asset_geometries_properties": json.dumps({"id": aid})})
    return C, str(local), str(batch), str(sim), str(meta), aid


def test_clean_run_writes_both_streams_and_tidies(run):
    C, local, batch, sim, meta, aid = run
    assert C.clean_single_report(local, batch, sim, meta, aid) is not False
    out = os.path.join(local, "cleaned_reports", aid)
    assert {"cleaned_predicted_electricity.csv", "cleaned_predicted_natural_gas.csv"} <= set(os.listdir(out))
    assert not os.path.exists(os.path.join(sim, aid, "feature_reports", "default_feature_report.csv"))


def test_a_failed_stream_fails_the_asset_and_keeps_the_raw_report(run, monkeypatch):
    C, local, batch, sim, meta, aid = run
    real = pd.DataFrame.to_csv

    def disk_full(self, path=None, *a, **k):
        if path and str(path).endswith("cleaned_predicted_natural_gas.csv"):
            open(path, "w").write("id,ts,val")        # a write cut short
            raise OSError(28, "No space left on device")
        return real(self, path, *a, **k)
    monkeypatch.setattr(pd.DataFrame, "to_csv", disk_full)
    assert C.clean_single_report(local, batch, sim, meta, aid) is False
    out = os.listdir(os.path.join(local, "cleaned_reports", aid))
    assert not [f for f in out if f.startswith("cleaned_predicted")]       # no truncated or partial-fuel set for ingest
    assert os.path.exists(os.path.join(sim, aid, "feature_reports", "default_feature_report.csv"))   # kept for retry
