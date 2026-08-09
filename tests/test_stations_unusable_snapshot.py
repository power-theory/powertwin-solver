"""stations_unusable_stat.json must stay coupled to the master_weather.geojson snapshot it was
built from: consumers (weather.py, sampling/station.py) fail loud on a count mismatch, and this
pin catches the drift at test time -- a geojson update without rerunning the builder would
otherwise default new legacy-.stat stations to usable and abort create_typical mid-campaign."""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
URBANOPT = os.path.join(HERE, "..", "solver", "app", "urbanopt")

SNAP = json.load(open(os.path.join(URBANOPT, "stations_unusable_stat.json")))
GEO = json.load(open(os.path.join(URBANOPT, "master_weather.geojson")))


def test_snapshot_counts_are_internally_consistent():
    assert SNAP["usable_count"] + len(SNAP["unusable_stat"]) == SNAP["total_stations"]
    assert len(SNAP["unusable_stat"]) == len(set(SNAP["unusable_stat"])), "duplicate titles"


def test_snapshot_matches_the_committed_geojson():
    feats = GEO["features"]
    assert SNAP["total_stations"] == len(feats), (
        "master_weather.geojson changed without rerunning "
        "solver/patches/build_stations_unusable_stat.py"
    )
    titles = {f["properties"]["title"] for f in feats}
    stray = set(SNAP["unusable_stat"]) - titles
    assert not stray, f"unusable titles absent from the geojson: {sorted(stray)[:5]}"


def test_builder_never_classifies_errors_and_emits_the_coupling():
    src = open(os.path.join(HERE, "..", "solver", "patches", "build_stations_unusable_stat.py")).read()
    assert "SystemExit" in src and '"error"' in src, "builder must abort on persistent download errors"
    assert '"total_stations"' in src, "builder must emit the snapshot-coupling count"
    assert "no_url" in src, "stations without a .stat URL must be classified, not usable-by-omission"
