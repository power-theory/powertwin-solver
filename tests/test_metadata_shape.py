"""The metadata csv shape (one row per building, `sensors` map) is enforced at ingestion, before any
weather lookup or urbanopt run, and every in-repo producer of a metadata csv writes that shape
(review 2026-09-20: clean_report was the only guard and it fired after each asset's full run, while
three tools still wrote or read the legacy per-sensor header)."""
import csv
import json
import os
import pathlib
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))
sys.path.insert(0, os.path.join(REPO, "tests", "tools"))

LEGACY_HEADER = "sensor_id,sensor_type_id,sensor_type_name,asset_id,asset_name,asset_subtype_id,asset_subtype_name,asset_metadata,asset_geometries_properties"


def test_read_metadata_refuses_the_legacy_shape_before_any_lookup(tmp_path, monkeypatch):
    from modules.simulation import generateFeatureFile as G
    p = tmp_path / "legacy_metadata.csv"
    p.write_text(LEGACY_HEADER + "\n1,1,Electricity,5,Name,17,Education,\"{}\",\"{}\"\n")
    import modules.utils.weather as W
    monkeypatch.setattr(W, "get_location", lambda *a, **k: pytest.fail("weather lookup ran on a rejected file"))
    with pytest.raises(ValueError, match="compact_metadata"):
        G.read_metadata(str(p))


def test_repo_metadata_csvs_carry_the_sensors_column():
    for rel in ("solver/upload/demo_data/asu_metadata.csv",
                "tests/tools/qa_matrix_fixtures/commercial_office_metadata.csv",
                "tests/tools/qa_matrix_fixtures/residential_sfd_metadata.csv"):
        with open(os.path.join(REPO, rel)) as f:
            assert "sensors" in (csv.DictReader(f).fieldnames or []), rel


def test_qa_matrix_and_simtest_writers_emit_the_sensors_shape(tmp_path):
    import qa_dynamic_defaults_matrix as Q
    fx = Q.build_fixtures()[0]
    p = Q._generate_metadata(fx, tmp_path)
    rows = list(csv.DictReader(open(p)))
    assert len(rows) == 1 and json.loads(rows[0]["sensors"]) == {"1": fx.asset_id}
    assert "sensor_id" not in rows[0]
    import sim_verify_building_types as V
    src = pathlib.Path(V.__file__).read_text()
    assert '"sensors", "asset_metadata", "asset_geometries_properties"' in src and '"sensor_id", "sensor_type_id"' not in src


def test_ab_scorer_reads_sensor_ids_from_the_map():
    import score_dynamic_defaults_ab as A
    idx = A.load_metadata(pathlib.Path(REPO) / "solver" / "upload" / "demo_data" / "asu_metadata.csv")
    assert len(idx.sensor_ids) > 0
