#!/usr/bin/env python3
"""A/B accuracy test: URBANOPT_DYNAMIC_DEFAULTS=false vs =true.

Runs two simulations back-to-back over POST /api/simulation/start, swapping
the resolver gate via the per-request `dynamic_defaults` form field (no
container restart). After both sims complete, scores predicted vs actual at
hourly grain (direct calendar overlap with sensor_logs) plus monthly and
yearly rollups aggregated from the hourly join.

Side-by-side false-vs-true RMSE + MAPE per (asset, sensor_type) at each
grain land in tests/runs/<ts>_ab_dynamic_defaults/.

Required container envs (asserted in pre-flight):
  URBANOPT_SIMULATION_YEAR=<sim_year>
  URBANOPT_RESAMPLE=H
  URBANOPT_POSTPROCESS_TRANSLATIONS=true

Sign convention: delta = true - false. Negative delta_rmse or delta_mape
means the dynamic resolver REDUCED error for that asset / sensor_type.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import math
import os
import pathlib
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import requests


# ----------------------------------------------------------------------------
# Constants. These are repository-shape facts (paths, sensor-type inner-join
# scope, DB connection) that don't vary across runs. Everything that DOES
# vary across runs lives in the Config dataclass below.
# ----------------------------------------------------------------------------
REPO = pathlib.Path(__file__).resolve().parents[1]
SIM_OUTPUT_DIR = REPO / "powertwin_data" / "user_files"
RUNS_DIR = REPO / "tests" / "runs"
DATASET_DIR = REPO / "solver" / "upload" / "demo_data"

# Inner-join scope. Only sensor types that the solver emits AND that exist
# in the DB sensor_types table get scored. The keys here match the fuel
# slug convention used by clean_report.py:
#     cleaned_predicted_<fuel_slug>.csv
# The values are (db sensor_type name, db sensor_type_id).
FUEL_TO_SENSOR: dict[str, tuple[str, int]] = {
    "electricity":   ("Electricity",   1),
    "hot_water":     ("Hot Water",     3),
    "co2_emissions": ("CO2 Emissions", 6),
    "natural_gas":   ("Natural Gas",   8),
}
WANTED_SENSOR_TYPE_IDS: set[int] = {sid for _, sid in FUEL_TO_SENSOR.values()}
SENSOR_NAME_BY_ID: dict[int, str] = {sid: name for name, sid in FUEL_TO_SENSOR.values()}

# DB connection. The verbatim points.js query needs a user_id for the
# role-permission LEFT JOINs; admin (role 6) sees everything.
ADMIN_USER_ID = 1
PG_HOST = os.environ.get("PG_HOST", "127.0.0.1")
PG_PORT = os.environ.get("PG_PORT", "5333")
PG_USER = os.environ.get("PG_USER", "admin")
PG_DB   = os.environ.get("PG_DB",   "powertwin-db")
PG_PASS = os.environ.get("PGPASSWORD", PG_USER)

GRAINS: tuple[str, ...] = ("hour", "month", "year")


# --- begin verbatim points.js SQL ----------------------------------------
# Copied byte-for-byte from powertwin-db/api/routes/context/points.js. The
# 12 $N parameters are bound via PREPARE/EXECUTE so the body itself is
# untouched -- a single source of truth shared with the API. If points.js
# changes, copy the new body in here.
POINTS_SQL = """
WITH sensor_logs_tz AS (
  SELECT
    sl.sensor_id,
    sl.ts AT TIME ZONE $8 AS ts_tz,
    sl.value,
    sl.metadata
  FROM sensor_logs sl
    INNER JOIN sensors s ON s.id = sl.sensor_id
    INNER JOIN sensor_types st ON st.id = s.sensor_type_id
  WHERE (
      $12::boolean
      OR (sl.ts AT TIME ZONE $8 >= $4::timestamp
          AND sl.ts AT TIME ZONE $8 <= $5::timestamp)
    )
    AND st.is_active = 1
    AND s.is_internal = $11
    AND sl.collection_id = $7
)
SELECT
  CASE
    WHEN $1 = -1 AND $2 = -1 THEN a.id
    ELSE NULL
  END AS id,
  s.sensor_type_id,
  CASE WHEN $10 = 'avg' THEN AVG(sl.value) ELSE SUM(sl.value) END AS value,
  CASE WHEN $10 = 'avg' THEN AVG(CAST(sl.metadata->>'cost' AS NUMERIC)) ELSE SUM(CAST(sl.metadata->>'cost' AS NUMERIC)) END AS cost,
  DATE_TRUNC($3, sl.ts_tz) AS timestamp
FROM sensor_logs_tz sl
  INNER JOIN sensors s ON sl.sensor_id = s.id
  INNER JOIN assets a ON a.id = s.asset_id
  INNER JOIN users u ON u.id = $9
  LEFT JOIN user_assets ua ON ua.asset_id = a.id AND ua.user_id = $9
  LEFT JOIN user_collections uc ON uc.collection_id = a.collection_id AND uc.user_id = $9
  LEFT JOIN user_sensors us ON us.sensor_id = s.id AND us.user_id = $9
WHERE a.is_active = 1
  AND (
    $1 = -1
    OR a.id = $1
    OR (
      a.path::text LIKE CONCAT($1, '.%')
      OR a.path::text LIKE CONCAT('%.', $1, '.%')
      OR a.path::text LIKE CONCAT('%.', $1)
    )
  )
  AND (
    $2 = -1
    OR s.head_asset_id = $2
    OR (
      a.path::text LIKE CONCAT($2, '.%')
      OR a.path::text LIKE CONCAT('%.', $2, '.%')
      OR a.path::text LIKE CONCAT('%.', $2)
    )
  )
  AND ($6 = -1 OR s.sensor_type_id = $6)
  AND s.is_internal = $11
  AND a.collection_id = $7
  AND COALESCE(us.user_role_type_id, uc.user_role_type_id, u.user_role_type_id) >= s.user_role_type_id
  AND COALESCE(ua.user_role_type_id, uc.user_role_type_id, u.user_role_type_id) >= a.user_role_type_id
GROUP BY
  CASE
    WHEN $1 = -1 AND $2 = -1 THEN a.id
    ELSE NULL
  END,
  s.sensor_type_id,
  timestamp
ORDER BY timestamp ASC
"""
# --- end verbatim points.js SQL ------------------------------------------


# ----------------------------------------------------------------------------
# Data classes. Everything that varies per run -- input paths, sim names,
# resolved DB facts -- flows through these.
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class MetadataIndex:
    """Parsed view of a `<prefix>_metadata.csv`."""
    geom_to_asset: dict[str, int]   # asset_geometries_properties.id -> asset_id
    asset_name: dict[int, str]
    sensor_ids: frozenset[int]      # all sensor_ids appearing in the file


@dataclass(frozen=True)
class DBContext:
    """DB-side facts resolved from the metadata, used to drive the points.js
    actuals query."""
    tz_groups: dict[str, list[int]]  # tz_name -> sensor_ids in that tz
    collection_id: int


@dataclass(frozen=True)
class Config:
    """All inputs the run pipeline needs. Built once in `main`, passed by
    value through every function. No module-level state, no mutation."""
    geojson_path: pathlib.Path
    metadata_path: pathlib.Path
    sim_name_false: str
    sim_name_true: str
    num_cores: int
    sim_year: str
    api_base: str
    db: DBContext
    metadata: MetadataIndex
    # Sensor-type IDs for which actuals zeros are treated as ingest-side NaNs.
    # Three behaviors gate off this set:
    #   1. Hourly scoring: zero-actual buckets pruned from the join (existing
    #      `if a[b] > 0` in score_pair already does this for ALL types; for
    #      affected types specifically this is the intended behavior, not
    #      collateral filtering).
    #   2. Monthly / yearly aggregation: zero hours linearly interpolated
    #      before summation, so the aggregate isn't deflated.
    #   3. Coverage filter: (asset, sensor_type) pairs whose hourly non-zero
    #      fraction is below `min_nonzero_fraction` are dropped entirely
    #      from all grains.
    # Empty set (default) preserves prior behavior. Populate via
    # `--interpolate-zeros-for "Electricity,Hot Water,CO2 Emissions"` for
    # portfolios where the ingest pipeline stored NaN as a literal 0 row
    # (e.g. ASU collection 1).
    interpolate_zeros_for_sensor_types: frozenset[int] = frozenset()
    # Coverage thresholds applied to the sensor types above. A pair (asset,
    # sensor_type) is dropped if EITHER:
    #   * fewer than `min_nonzero_fraction` of its hourly actuals are
    #     non-zero (relative threshold), OR
    #   * fewer than `min_nonzero_hours` of its hourly actuals are non-zero
    #     (absolute count). 4380 = 50% of an 8760-hour year, which is the
    #     ASHRAE-style "half a year of valid data" bar for treating a
    #     building's score as informative. Set to 0 to disable.
    min_nonzero_fraction: float = 0.0
    min_nonzero_hours: int = 0


@dataclass(frozen=True)
class PerAssetScore:
    asset_id: int
    sensor_type_id: int
    n_samples: int
    rmse_false: float
    rmse_true: float
    mape_false: float
    mape_true: float

    @property
    def delta_rmse(self) -> float:
        return self.rmse_true - self.rmse_false

    @property
    def delta_mape(self) -> float:
        return self.mape_true - self.mape_false


# ----------------------------------------------------------------------------
# Process boundaries: psql + docker. Thin wrappers that raise on non-zero
# exit instead of returning ambiguous values to ambiguous callers.
# ----------------------------------------------------------------------------
def psql(sql: str) -> list[list[str]]:
    """Run a one-off psql command, return pipe-separated rows.
    Raises RuntimeError on non-zero exit so callers can fail fast."""
    out = subprocess.run(
        ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", PG_DB,
         "-At", "-F", "|", "-c", sql],
        env={**os.environ, "PGPASSWORD": PG_PASS},
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"psql failed: {out.stderr.strip()}")
    return [line.split("|") for line in out.stdout.strip().splitlines() if line]


def docker_printenv(var: str) -> str:
    """Read an env var from the flask container's PID-1 process env."""
    out = subprocess.run(
        ["docker", "exec", "powertwin-solver-flask", "printenv", var],
        capture_output=True, text=True,
    )
    return out.stdout.strip()


# ----------------------------------------------------------------------------
# Input parsing and DB resolution.
# ----------------------------------------------------------------------------
def load_metadata(metadata_path: pathlib.Path) -> MetadataIndex:
    """Parse a `<prefix>_metadata.csv` into a typed view."""
    geom_to_asset: dict[str, int] = {}
    asset_name: dict[int, str] = {}
    sensor_ids: set[int] = set()
    with open(metadata_path, "r") as f:
        for row in csv.DictReader(f):
            sensor_ids.add(int(row["sensor_id"]))
            asset_id = int(row["asset_id"])
            asset_name[asset_id] = row["asset_name"]
            try:
                geom = json.loads(row["asset_geometries_properties"])
                geom_to_asset[str(geom["id"])] = asset_id
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    return MetadataIndex(
        geom_to_asset=geom_to_asset,
        asset_name=asset_name,
        sensor_ids=frozenset(sensor_ids),
    )


def resolve_db_context(metadata: MetadataIndex,
                       override_collection_id: int | None) -> DBContext:
    """Look up the metadata's sensors in the DB and group by time_zone +
    pick the modal collection_id. Caller can override the collection_id
    explicitly when the metadata legitimately spans collections."""
    if not metadata.sensor_ids:
        raise RuntimeError("metadata has no sensor_ids; nothing to join")
    sids = ",".join(str(s) for s in sorted(metadata.sensor_ids))

    tz_rows = psql(f"SELECT id, time_zone FROM sensors WHERE id IN ({sids});")
    tz_groups: dict[str, list[int]] = defaultdict(list)
    for row in tz_rows:
        if len(row) >= 2:
            tz_groups[row[1]].append(int(row[0]))
    if not tz_groups:
        raise RuntimeError(
            "none of the sensors in the metadata exist in the DB")

    if override_collection_id is not None:
        collection_id = override_collection_id
    else:
        collection_rows = psql(
            f"SELECT collection_id, COUNT(*) FROM sensors WHERE id IN ({sids}) "
            f"GROUP BY 1 ORDER BY 2 DESC LIMIT 1;")
        if not collection_rows:
            raise RuntimeError(
                "could not resolve collection_id from the metadata's sensors")
        collection_id = int(collection_rows[0][0])

    return DBContext(tz_groups=dict(tz_groups), collection_id=collection_id)


def validate_environment(geojson_path: pathlib.Path,
                         metadata_path: pathlib.Path,
                         sim_year: str,
                         api_base: str,
                         sim_names: tuple[str, str]) -> None:
    """Fail fast on anything that would only surface mid-run."""
    print("=== pre-flight ===")

    try:
        resp = requests.get(f"{api_base}/api/", timeout=5)
        if resp.status_code >= 500:
            raise RuntimeError(f"status {resp.status_code}")
        print(f"  Flask reachable at {api_base} (status {resp.status_code})")
    except Exception as e:
        sys.exit(f"  ERROR: Flask unreachable at {api_base}: {e}")

    required_envs = {
        "URBANOPT_SIMULATION_YEAR": sim_year,
        "URBANOPT_RESAMPLE": "H",
        "URBANOPT_POSTPROCESS_TRANSLATIONS": "true",
    }
    for name, expected in required_envs.items():
        actual = docker_printenv(name)
        if actual.lower() != expected.lower():
            sys.exit(f"  ERROR: container env {name}={actual!r}, "
                     f"expected {expected!r}")
        print(f"  {name}={actual}")

    for label, path in (("geojson", geojson_path), ("metadata", metadata_path)):
        if not path.exists():
            sys.exit(f"  ERROR: {label} input missing: {path}")
        print(f"  input {label}: {path}")

    for sim_name in sim_names:
        slot = SIM_OUTPUT_DIR / sim_name
        if slot.exists():
            sys.exit(
                f"  ERROR: sim slot already used: {slot}\n"
                f"    clear with: docker exec powertwin-solver-pg "
                f"rm -rf /solver/powertwin-solver-pg/user_files/{sim_name}")
        print(f"  sim slot free: {sim_name}")


# ----------------------------------------------------------------------------
# Running the two sims.
# ----------------------------------------------------------------------------
def run_sim(cfg: Config, sim_name: str, dynamic_defaults: bool) -> dict[str, Any]:
    """Multipart POST /api/simulation/start. Synchronous: blocks until the
    sim completes or fails. Returns timing metadata for the run summary."""
    print(f"\n=== run_sim({sim_name}, dynamic_defaults={dynamic_defaults}) ===")
    started_at = time.time()
    with open(cfg.geojson_path, "rb") as gf, open(cfg.metadata_path, "rb") as mf:
        files = {
            "asset_geojson_file": (cfg.geojson_path.name, gf,
                                   "application/geo+json"),
            "metadata_csv_file":  (cfg.metadata_path.name, mf, "text/csv"),
        }
        form = {
            "simulation_name": sim_name,
            "num_cores": str(cfg.num_cores),
            "dynamic_defaults": "true" if dynamic_defaults else "false",
        }
        print(f"  POST {cfg.api_base}/api/simulation/start "
              f"(blocks until sim completes)")
        resp = requests.post(f"{cfg.api_base}/api/simulation/start",
                             files=files, data=form, timeout=None)
    duration_sec = time.time() - started_at
    if resp.status_code != 200:
        raise RuntimeError(
            f"/api/simulation/start returned {resp.status_code}: {resp.text[:500]}")
    print(f"  {sim_name} done in {duration_sec/60:.1f} min")
    return {
        "name": sim_name,
        "duration_sec": duration_sec,
        "dynamic_defaults": dynamic_defaults,
    }


# ----------------------------------------------------------------------------
# Bucket keys. Predicted hourly rows and actuals from points.js are both
# bucketed to the same string keys per grain so joins are exact-string.
# ----------------------------------------------------------------------------
def bucket_from_hour_key(hour_key: str, grain: str) -> str:
    """Predicted-side: hour_key is 'YYYY-MM-DDTHH'. Truncate to the grain."""
    if grain == "hour":
        return hour_key             # 'YYYY-MM-DDTHH'
    if grain == "month":
        return hour_key[:7]         # 'YYYY-MM'
    if grain == "year":
        return hour_key[:4]         # 'YYYY'
    raise ValueError(f"unknown grain: {grain}")


def bucket_from_actuals_ts(ts_raw: str, grain: str) -> str:
    """Actuals-side: ts_raw is 'YYYY-MM-DD HH:MM:SS' from DATE_TRUNC.
    Truncate to the predicted-side convention so the join is by exact string."""
    if grain == "hour":
        return ts_raw[:13].replace(" ", "T")  # 'YYYY-MM-DDTHH'
    if grain == "month":
        return ts_raw[:7]                     # 'YYYY-MM'
    if grain == "year":
        return ts_raw[:4]                     # 'YYYY'
    raise ValueError(f"unknown grain: {grain}")


# ----------------------------------------------------------------------------
# Predicted side: read the solver's per-asset cleaned_predicted CSVs.
# ----------------------------------------------------------------------------
def load_predicted_hourly(sim_name: str, geom_to_asset: dict[str, int],
                          predicted_root: pathlib.Path | None = None,
                          ) -> dict[tuple[int, int], dict[str, float]]:
    """Walk cleaned_reports/<geom_id>/cleaned_predicted_<fuel>.csv and bin
    hourly values into wall-clock-local hour keys.

    Returns {(asset_id, sensor_type_id): {'YYYY-MM-DDTHH': sum_of_values}}.
    The hour key uses the wall-clock-local hour because the solver emits
    ts with the EPW's tz offset suffix; truncating to char-13 drops the
    offset and gives the local hour directly.

    By default reads from `SIM_OUTPUT_DIR / <sim_name> / cleaned_reports/`
    -- where the live sim wrote it. Pass `predicted_root` to override; the
    re-score path uses `tests/fixtures/_predicted_backups/<sim_name>_cleaned_reports/`.
    """
    if predicted_root is not None:
        sim_dir = predicted_root / f"{sim_name}_cleaned_reports"
    else:
        sim_dir = SIM_OUTPUT_DIR / sim_name / "cleaned_reports"
    if not sim_dir.is_dir():
        raise RuntimeError(f"cleaned_reports missing for {sim_name}: {sim_dir}")

    predicted: dict[tuple[int, int], dict[str, float]] = defaultdict(
        lambda: defaultdict(float))
    for geom_dir in sorted(sim_dir.iterdir()):
        if not geom_dir.is_dir():
            continue
        asset_id = geom_to_asset.get(geom_dir.name)
        if asset_id is None:
            continue
        for fuel_slug, (_sensor_name, sensor_type_id) in FUEL_TO_SENSOR.items():
            csv_path = geom_dir / f"cleaned_predicted_{fuel_slug}.csv"
            if not csv_path.exists():
                continue
            with open(csv_path, "r") as f:
                reader = csv.reader(f)
                next(reader, None)  # header
                for row in reader:
                    if len(row) < 3:
                        continue
                    try:
                        value = float(row[2])
                    except ValueError:
                        continue
                    hour_key = row[1][:13]
                    predicted[(asset_id, sensor_type_id)][hour_key] += value
    return predicted


def aggregate_predicted(predicted_hourly: dict[tuple[int, int], dict[str, float]],
                        grain: str,
                        ) -> dict[tuple[int, int], dict[str, float]]:
    """Roll up hour-keyed predicted values to the requested grain. Identity
    when grain == 'hour'."""
    if grain == "hour":
        return predicted_hourly
    rolled: dict[tuple[int, int], dict[str, float]] = defaultdict(
        lambda: defaultdict(float))
    for key, hours in predicted_hourly.items():
        for hour_key, value in hours.items():
            rolled[key][bucket_from_hour_key(hour_key, grain)] += value
    return rolled


# ----------------------------------------------------------------------------
# Zero-as-NaN interpolation for actuals from ingest pipelines that store
# missing readings as literal 0 rows (notably ASU collection 1). Opt in via
# Config.interpolate_zeros_for_sensor_types.
# ----------------------------------------------------------------------------
def interpolate_zero_hours(hourly: dict[str, float]) -> dict[str, float]:
    """Linearly interpolate over zero values in a sorted-by-key hourly series.

    Zero values are interior-interpolated between the nearest non-zero
    neighbors. Leading zeros (before the first non-zero) are forward-filled
    from the first non-zero value; trailing zeros are backward-filled from
    the last non-zero. Series with no non-zero values are returned unchanged.

    Hour keys are 'YYYY-MM-DDTHH' strings which sort lexically into
    chronological order, so simple sorted() is sufficient -- no datetime
    parsing required. Returned dict has the same keys as the input.
    """
    if not hourly:
        return {}
    sorted_items = sorted(hourly.items())
    keys = [k for k, _ in sorted_items]
    vals = [v for _, v in sorted_items]
    nonzero_idx = [i for i, v in enumerate(vals) if v != 0.0]
    if not nonzero_idx:
        return dict(hourly)

    out = list(vals)
    first, last = nonzero_idx[0], nonzero_idx[-1]
    for i in range(first):
        out[i] = vals[first]
    for i in range(last + 1, len(vals)):
        out[i] = vals[last]
    for left, right in zip(nonzero_idx, nonzero_idx[1:]):
        if right - left == 1:
            continue
        v_left, v_right = vals[left], vals[right]
        span = right - left
        for i in range(left + 1, right):
            t = (i - left) / span
            out[i] = v_left + t * (v_right - v_left)
    return dict(zip(keys, out))


def filter_low_coverage_pairs(hourly: dict[tuple[int, int], dict[str, float]],
                              interpolate_sensor_types: frozenset[int],
                              min_nonzero_fraction: float,
                              min_nonzero_hours: int,
                              ) -> tuple[dict[tuple[int, int], dict[str, float]],
                                         dict[int, int]]:
    """Drop (asset, sensor_type) pairs whose hourly actuals are too sparse.

    Two independent thresholds are applied; a pair must satisfy BOTH to
    survive (a pair is dropped if it fails either):
      * non_zero_count / total_buckets >= `min_nonzero_fraction`
      * non_zero_count                  >= `min_nonzero_hours`

    Only applies to pairs whose sensor_type_id is in
    `interpolate_sensor_types` -- the assumption is that for those types the
    ingest pipeline writes NaN-as-zero, so a high zero ratio (relative
    filter) or a low absolute reading count (absolute filter) is an ingest
    coverage gap rather than legitimate building behavior. Pairs in other
    sensor types pass through untouched.

    Either threshold can be disabled by passing 0. With both at 0 the
    function is a no-op.

    Returns (filtered_hourly, dropped_counts_by_sensor_type). The dropped
    counts let the caller log how much data the filter removed per type.
    """
    if (min_nonzero_fraction <= 0.0 and min_nonzero_hours <= 0) \
            or not interpolate_sensor_types:
        return hourly, {}
    kept: dict[tuple[int, int], dict[str, float]] = {}
    dropped: dict[int, int] = defaultdict(int)
    for key, hours in hourly.items():
        sensor_type_id = key[1]
        if sensor_type_id not in interpolate_sensor_types:
            kept[key] = hours
            continue
        n = len(hours)
        nonzero = sum(1 for v in hours.values() if v != 0.0)
        if n == 0 \
                or (min_nonzero_fraction > 0.0 and nonzero / n < min_nonzero_fraction) \
                or (min_nonzero_hours  > 0    and nonzero      < min_nonzero_hours):
            dropped[sensor_type_id] += 1
        else:
            kept[key] = hours
    return kept, dict(dropped)


def aggregate_actuals_hourly(hourly: dict[tuple[int, int], dict[str, float]],
                             grain: str,
                             interpolate_sensor_types: frozenset[int],
                             ) -> dict[tuple[int, int], dict[str, float]]:
    """Roll up an hour-keyed actuals dict to the requested grain. For
    (asset, sensor_type) pairs whose sensor_type_id is in
    `interpolate_sensor_types`, zero hours are linearly interpolated before
    summation so the monthly / yearly total isn't deflated by ingest-side
    NaN-as-zero rows. At the hour grain we return the dict as-is -- the
    downstream zero filter in score_pair will drop zero-actual hours from
    the join, which is the prune-not-interpolate behavior the user asked for.
    """
    if grain == "hour":
        return hourly
    rolled: dict[tuple[int, int], dict[str, float]] = defaultdict(
        lambda: defaultdict(float))
    for key, hours in hourly.items():
        series = interpolate_zero_hours(hours) if key[1] in interpolate_sensor_types else hours
        for hour_key, value in series.items():
            rolled[key][bucket_from_hour_key(hour_key, grain)] += value
    return rolled


# ----------------------------------------------------------------------------
# Actuals side: verbatim points.js SQL, one call per grain x tz group.
# ----------------------------------------------------------------------------
def fetch_actuals_for_tz_group(grain: str, tz: str,
                               sensor_ids_in_group: list[int],
                               collection_id: int,
                               sim_year: str,
                               ) -> dict[tuple[int, int], dict[str, float]]:
    """Run the verbatim points.js SQL for one (grain, tz, sensor-group)
    triple. Returns {(asset, sensor_type): {bucket_key: actual_value}}."""
    asset_id_rows = psql(
        f"SELECT DISTINCT asset_id FROM sensors WHERE id IN "
        f"({','.join(str(s) for s in sorted(sensor_ids_in_group))});"
    )
    in_group_asset_ids = {int(r[0]) for r in asset_id_rows if r and r[0]}
    if not in_group_asset_ids:
        return {}

    prepare_sql = (
        "PREPARE points_query (int, int, text, text, text, int, int, text, "
        "varchar, text, int, boolean) AS\n"
        + POINTS_SQL + ";\n"
        f"EXECUTE points_query (-1, -1, '{grain}', '{sim_year}-01-01', "
        f"'{sim_year}-12-31 23:59:59', -1, {collection_id}, '{tz}', "
        f"'{ADMIN_USER_ID}', 'sum', 1, false);"
    )
    rows = psql(prepare_sql)

    out: dict[tuple[int, int], dict[str, float]] = defaultdict(
        lambda: defaultdict(float))
    for row in rows:
        if len(row) < 5:
            continue
        asset_id_raw, sensor_type_id_raw, value_raw, _cost, ts_raw = row[:5]
        if not asset_id_raw:
            continue
        asset_id = int(asset_id_raw)
        if asset_id not in in_group_asset_ids:
            continue
        sensor_type_id = int(sensor_type_id_raw)
        if sensor_type_id not in WANTED_SENSOR_TYPE_IDS:
            continue
        value = float(value_raw) if value_raw else 0.0
        out[(asset_id, sensor_type_id)][bucket_from_actuals_ts(ts_raw, grain)] = value
    return out


def fetch_actuals(db: DBContext, sim_year: str,
                  interpolate_sensor_types: frozenset[int] = frozenset(),
                  min_nonzero_fraction: float = 0.0,
                  min_nonzero_hours: int = 0,
                  ) -> dict[str, dict[tuple[int, int], dict[str, float]]]:
    """Fetch actuals from sensor_logs and return one dict per grain.

    Time-zone-group splitting is required because points.js takes a single
    `time_zone` parameter applied to every row, so sensors in different
    zones must be queried separately or one half would be bucketed in the
    wrong wall clock.

    Behavior is dictated by `interpolate_sensor_types`:

      * If empty (default): preserves the prior per-grain SQL fetch path
        for every sensor type. Three SQL roundtrips (hour, month, year).

      * If non-empty: fetches ONLY hourly actuals via SQL (one roundtrip)
        and aggregates to monthly + yearly in Python so the zero-as-NaN
        interpolation happens pre-aggregation for the listed types. Other
        sensor types are aggregated via the same Python path but without
        interpolation -- they get the same SUM as the SQL DATE_TRUNC would
        produce, so the two paths are equivalent for non-interpolated
        types (zeros are real readings either way).

      * If `min_nonzero_fraction > 0`, pairs whose non-zero hour fraction
        is below the threshold get dropped from all grains. Only applies
        to pairs whose sensor_type_id is in `interpolate_sensor_types`.
    """
    print("\n=== fetching actuals per grain x tz_group ===")

    if not interpolate_sensor_types:
        # Legacy path: per-grain SQL fetch, no interpolation.
        actuals: dict[str, dict[tuple[int, int], dict[str, float]]] = {}
        for grain in GRAINS:
            merged: dict[tuple[int, int], dict[str, float]] = {}
            for tz, sensor_ids in db.tz_groups.items():
                chunk = fetch_actuals_for_tz_group(
                    grain, tz, sensor_ids, db.collection_id, sim_year)
                for key, buckets in chunk.items():
                    if key in merged:
                        merged[key].update(buckets)
                    else:
                        merged[key] = dict(buckets)
            actuals[grain] = merged
            print(f"  {grain}: {len(merged)} (asset, sensor_type) pairs with actuals")
        return actuals

    # Interpolation path: fetch hourly once, filter low-coverage pairs,
    # then aggregate in Python so interpolation happens before summation.
    hourly: dict[tuple[int, int], dict[str, float]] = {}
    for tz, sensor_ids in db.tz_groups.items():
        chunk = fetch_actuals_for_tz_group(
            "hour", tz, sensor_ids, db.collection_id, sim_year)
        for key, buckets in chunk.items():
            if key in hourly:
                hourly[key].update(buckets)
            else:
                hourly[key] = dict(buckets)
    print(f"  hourly (pre-filter): {len(hourly)} (asset, sensor_type) pairs")

    hourly, dropped_counts = filter_low_coverage_pairs(
        hourly, interpolate_sensor_types, min_nonzero_fraction, min_nonzero_hours)
    if dropped_counts:
        thresh_parts = []
        if min_nonzero_fraction > 0.0:
            thresh_parts.append(f"<{min_nonzero_fraction*100:.0f}% non-zero")
        if min_nonzero_hours > 0:
            thresh_parts.append(f"<{min_nonzero_hours} non-zero hours")
        thresh = " or ".join(thresh_parts)
        for stid in sorted(dropped_counts):
            name = SENSOR_NAME_BY_ID.get(stid, str(stid))
            print(f"  coverage-filter dropped {dropped_counts[stid]:>4d} "
                  f"({name}) pairs ({thresh})")
    print(f"  hourly (post-filter): {len(hourly)} pairs")

    out: dict[str, dict[tuple[int, int], dict[str, float]]] = {}
    for grain in GRAINS:
        rolled = aggregate_actuals_hourly(hourly, grain, interpolate_sensor_types)
        # aggregate_actuals_hourly returns nested defaultdicts; collapse to plain dicts
        out[grain] = {k: dict(v) for k, v in rolled.items()}
        print(f"  {grain}: {len(out[grain])} (asset, sensor_type) pairs with actuals")
    return out


# ----------------------------------------------------------------------------
# Scoring.
# ----------------------------------------------------------------------------
def score_pair(pred_false: dict[tuple[int, int], dict[str, float]],
               pred_true: dict[tuple[int, int], dict[str, float]],
               actuals: dict[tuple[int, int], dict[str, float]],
               ) -> list[PerAssetScore]:
    """Compute RMSE and MAPE for each (asset, sensor_type) that has
    overlapping buckets in pred_false, pred_true, AND actuals. Buckets
    where actual <= 0 are dropped before MAPE is computed."""
    scored: list[PerAssetScore] = []
    common_keys = set(pred_false.keys()) & set(pred_true.keys()) & set(actuals.keys())
    for key in sorted(common_keys):
        pf, pt, a = pred_false[key], pred_true[key], actuals[key]
        buckets = sorted(set(pf.keys()) & set(pt.keys()) & set(a.keys()))
        triples = [(pf[b], pt[b], a[b]) for b in buckets if a[b] > 0]
        if not triples:
            continue
        n = len(triples)
        rmse_false = math.sqrt(sum((p - x) ** 2 for p, _t, x in triples) / n)
        rmse_true = math.sqrt(sum((t - x) ** 2 for _p, t, x in triples) / n)
        mape_false = (sum(abs(p - x) / x for p, _t, x in triples) / n) * 100.0
        mape_true = (sum(abs(t - x) / x for _p, t, x in triples) / n) * 100.0
        scored.append(PerAssetScore(
            asset_id=key[0],
            sensor_type_id=key[1],
            n_samples=n,
            rmse_false=rmse_false,
            rmse_true=rmse_true,
            mape_false=mape_false,
            mape_true=mape_true,
        ))
    return scored


def aggregate_overall(scores: list[PerAssetScore]) -> list[dict[str, Any]]:
    """Mean each metric across assets within each sensor_type. The mean of
    per-asset deltas equals the delta of per-asset means, so we compute
    each independently for the CSV consumer's convenience."""
    by_sensor: dict[int, list[PerAssetScore]] = defaultdict(list)
    for s in scores:
        by_sensor[s.sensor_type_id].append(s)
    out: list[dict[str, Any]] = []
    for sensor_type_id in sorted(by_sensor):
        group = by_sensor[sensor_type_id]
        n = len(group)
        out.append({
            "sensor_type_id": sensor_type_id,
            "sensor_type_name": SENSOR_NAME_BY_ID.get(sensor_type_id, ""),
            "n_assets": n,
            "mean_rmse_false": sum(s.rmse_false for s in group) / n,
            "mean_rmse_true":  sum(s.rmse_true  for s in group) / n,
            "delta_rmse":      sum(s.delta_rmse for s in group) / n,
            "mean_mape_false": sum(s.mape_false for s in group) / n,
            "mean_mape_true":  sum(s.mape_true  for s in group) / n,
            "delta_mape":      sum(s.delta_mape for s in group) / n,
        })
    return out


# ----------------------------------------------------------------------------
# Output. CSVs + summary.json into a timestamped run dir.
# ----------------------------------------------------------------------------
PER_ASSET_FIELDS = [
    "asset_id", "asset_name", "sensor_type_id", "sensor_type_name",
    "n_samples", "rmse_false", "rmse_true", "delta_rmse",
    "mape_false", "mape_true", "delta_mape",
]
OVERALL_FIELDS = [
    "sensor_type_id", "sensor_type_name", "n_assets",
    "mean_rmse_false", "mean_rmse_true", "delta_rmse",
    "mean_mape_false", "mean_mape_true", "delta_mape",
]


def write_per_asset_csv(path: pathlib.Path, scores: list[PerAssetScore],
                        asset_name: dict[int, str]) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PER_ASSET_FIELDS)
        writer.writeheader()
        for s in scores:
            writer.writerow({
                "asset_id": s.asset_id,
                "asset_name": asset_name.get(s.asset_id, ""),
                "sensor_type_id": s.sensor_type_id,
                "sensor_type_name": SENSOR_NAME_BY_ID.get(s.sensor_type_id, ""),
                "n_samples": s.n_samples,
                "rmse_false": s.rmse_false,
                "rmse_true": s.rmse_true,
                "delta_rmse": s.delta_rmse,
                "mape_false": s.mape_false,
                "mape_true": s.mape_true,
                "delta_mape": s.delta_mape,
            })


def write_overall_csv(path: pathlib.Path, overall: list[dict[str, Any]]) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OVERALL_FIELDS)
        writer.writeheader()
        writer.writerows(overall)


def write_summary_json(path: pathlib.Path, cfg: Config,
                       runs: list[dict[str, Any]],
                       scores_by_grain: dict[str, list[PerAssetScore]],
                       overalls_by_grain: dict[str, list[dict[str, Any]]],
                       ts_label: str) -> None:
    summary = {
        "timestamp": ts_label,
        "geojson_path": str(cfg.geojson_path),
        "metadata_path": str(cfg.metadata_path),
        "sim_year": cfg.sim_year,
        "collection_id": cfg.db.collection_id,
        "num_cores": cfg.num_cores,
        "tz_groups": {tz: len(ids) for tz, ids in cfg.db.tz_groups.items()},
        "interpolate_zeros_for_sensor_types": sorted(
            cfg.interpolate_zeros_for_sensor_types),
        "interpolate_zeros_for_sensor_names": [
            SENSOR_NAME_BY_ID[sid]
            for sid in sorted(cfg.interpolate_zeros_for_sensor_types)
        ],
        "min_nonzero_fraction": cfg.min_nonzero_fraction,
        "min_nonzero_hours": cfg.min_nonzero_hours,
        "runs": runs,
        "grains": {
            grain: {
                "n_scored_pairs": len(scores_by_grain[grain]),
                "overall": overalls_by_grain[grain],
            }
            for grain in scores_by_grain
        },
    }
    with open(path, "w") as f:
        json.dump(summary, f, indent=2, default=str)


def print_overall_table(grain: str, overall: list[dict[str, Any]]) -> None:
    print(f"\n=== overall {grain} ===")
    print(f"{'sensor_type':14s} {'n':>5s}  "
          f"{'rmse_false':>14s}  {'rmse_true':>14s}  {'d_rmse':>14s}  "
          f"{'mape_false':>10s}  {'mape_true':>10s}  {'d_mape':>10s}")
    for r in overall:
        print(f"{r['sensor_type_name']:14s} {r['n_assets']:>5d}  "
              f"{r['mean_rmse_false']:>14,.2f}  {r['mean_rmse_true']:>14,.2f}  "
              f"{r['delta_rmse']:>+14,.2f}  "
              f"{r['mean_mape_false']:>9.2f}%  {r['mean_mape_true']:>9.2f}%  "
              f"{r['delta_mape']:>+9.2f}%")


# ----------------------------------------------------------------------------
# CLI.
# ----------------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sources = parser.add_argument_group(
        "inputs (provide --dataset OR both --geojson and --metadata)")
    sources.add_argument("--dataset",
                         help=f"dataset name resolved to "
                              f"{DATASET_DIR}/<dataset>_asset_geometries.geojson "
                              f"and {DATASET_DIR}/<dataset>_metadata.csv. "
                              f"Overridden by --geojson/--metadata.")
    sources.add_argument("--geojson", type=pathlib.Path,
                         help="explicit asset_geometries.geojson path")
    sources.add_argument("--metadata", type=pathlib.Path,
                         help="explicit metadata.csv path")

    parser.add_argument("--name",
                        help="sim slot prefix; sims will be named "
                             "<name>_false and <name>_true. "
                             "Defaults to --dataset.")
    parser.add_argument("--num-cores", type=int, default=22,
                        help="joblib worker count (default 22)")
    parser.add_argument("--sim-year", default="2024",
                        help="URBANOPT_SIMULATION_YEAR the container must "
                             "be running (default 2024)")
    parser.add_argument("--collection-id", type=int,
                        help="DB collection_id override (default: modal "
                             "collection across the metadata's sensors)")
    parser.add_argument("--flask-port", type=int,
                        default=int(os.environ.get("FLASK_PORT", "1337")),
                        help="flask container port (default $FLASK_PORT or 1337)")

    nan_group = parser.add_argument_group(
        "zero-as-NaN handling (ASU and similar ingest pipelines)")
    nan_group.add_argument("--interpolate-zeros-for", default="",
                           help="comma-separated sensor type NAMES whose "
                                "actuals zeros should be treated as ingest-side "
                                "NaNs (e.g. 'Electricity,Hot Water,CO2 "
                                "Emissions'). For those types: hourly scoring "
                                "prunes zeros (existing behavior); monthly and "
                                "yearly aggregations linearly interpolate zero "
                                "hours pre-sum; pairs below "
                                "--min-nonzero-fraction get dropped. Default: "
                                "empty (zeros honored as real readings).")
    nan_group.add_argument("--min-nonzero-fraction", type=float, default=0.0,
                           help="for sensor types in --interpolate-zeros-for: "
                                "drop (asset, sensor_type) pairs whose hourly "
                                "non-zero fraction is below this threshold. "
                                "0.0 (default) disables the relative coverage "
                                "filter; 0.5 means 'require non-zero readings "
                                "for at least half of the actuals window'.")
    nan_group.add_argument("--min-nonzero-hours", type=int, default=0,
                           help="for sensor types in --interpolate-zeros-for: "
                                "drop (asset, sensor_type) pairs with fewer "
                                "than this many non-zero hourly readings "
                                "(absolute count). 0 (default) disables; "
                                "4380 = half a year, the ASHRAE-style 'at "
                                "least 6 months of valid data' bar. For "
                                "narrower actuals windows pick a lower value "
                                "(e.g. 372 = 50%% of one month).")

    reuse_group = parser.add_argument_group("re-score from existing predicted CSVs")
    reuse_group.add_argument("--reuse-from", type=pathlib.Path,
                             help="skip simulation entirely. Read predicted "
                                  "hourly CSVs from this directory instead of "
                                  "running new sims. Expected layout: "
                                  "<reuse-from>/<sim_name>_cleaned_reports/<geom_id>/"
                                  "cleaned_predicted_*.csv (matches the "
                                  "backup-watcher output in "
                                  "tests/fixtures/_predicted_backups/). "
                                  "Useful for iterating on filter thresholds "
                                  "without re-simulating.")
    return parser.parse_args(argv)


def resolve_interpolation_set(spec: str) -> frozenset[int]:
    """Parse --interpolate-zeros-for into a frozenset of sensor_type_ids.

    Accepts comma-separated names matching the keys of FUEL_TO_SENSOR or the
    DB-side names ('Electricity', 'Hot Water', 'CO2 Emissions', 'Natural
    Gas'). Whitespace tolerant; case-insensitive on the name match. Unknown
    names cause sys.exit with a helpful message rather than silently
    becoming an empty filter."""
    if not spec.strip():
        return frozenset()
    name_to_id = {name.lower(): sid for name, sid in FUEL_TO_SENSOR.values()}
    out: set[int] = set()
    for raw in spec.split(","):
        name = raw.strip()
        if not name:
            continue
        sid = name_to_id.get(name.lower())
        if sid is None:
            valid = ", ".join(sorted(n for n, _ in FUEL_TO_SENSOR.values()))
            sys.exit(f"ERROR: --interpolate-zeros-for: unknown sensor type "
                     f"{name!r}; expected one of {{{valid}}}")
        out.add(sid)
    return frozenset(out)


def resolve_input_paths(args: argparse.Namespace,
                        ) -> tuple[pathlib.Path, pathlib.Path, str]:
    """Decide which input pair to use and what name prefix to give the
    sims, based on whichever subset of CLI flags the caller supplied."""
    if args.geojson and args.metadata:
        name = args.name or args.dataset
        if not name:
            sys.exit("ERROR: --name is required when using --geojson/--metadata "
                     "(unless --dataset is also passed)")
        return args.geojson, args.metadata, name
    if args.dataset:
        geojson = DATASET_DIR / f"{args.dataset}_asset_geometries.geojson"
        metadata = DATASET_DIR / f"{args.dataset}_metadata.csv"
        return geojson, metadata, (args.name or args.dataset)
    sys.exit("ERROR: pass --dataset NAME, or both --geojson PATH and "
             "--metadata PATH (with --name)")


# ----------------------------------------------------------------------------
# Top-level pipeline.
# ----------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    geojson_path, metadata_path, name = resolve_input_paths(args)
    sim_names = (f"{name}_false", f"{name}_true")
    api_base = f"http://127.0.0.1:{args.flask_port}"

    if args.reuse_from:
        # Re-score mode: skip Flask + container env checks, skip sim
        # collision detection. Only validate that the predicted backups
        # exist and the metadata + geojson are still readable.
        print("=== re-score mode (--reuse-from) ===")
        for label, path in (("geojson", geojson_path),
                            ("metadata", metadata_path)):
            if not path.exists():
                sys.exit(f"  ERROR: {label} input missing: {path}")
            print(f"  input {label}: {path}")
        for sim_name in sim_names:
            backup_dir = args.reuse_from / f"{sim_name}_cleaned_reports"
            if not backup_dir.is_dir():
                sys.exit(f"  ERROR: predicted backup missing: {backup_dir}")
            print(f"  predicted backup: {backup_dir}")
    else:
        validate_environment(geojson_path, metadata_path,
                             args.sim_year, api_base, sim_names)

    metadata = load_metadata(metadata_path)
    db = resolve_db_context(metadata, args.collection_id)
    tz_summary = ", ".join(f"{tz}:{len(v)}" for tz, v in db.tz_groups.items())
    print(f"  sensor tz groups: {{ {tz_summary} }}")
    print(f"  collection_id: {db.collection_id}"
          + ("" if args.collection_id is None else " (overridden)"))

    interpolate_set = resolve_interpolation_set(args.interpolate_zeros_for)
    if not 0.0 <= args.min_nonzero_fraction <= 1.0:
        sys.exit(f"ERROR: --min-nonzero-fraction must be in [0, 1]; "
                 f"got {args.min_nonzero_fraction}")
    if args.min_nonzero_hours < 0:
        sys.exit(f"ERROR: --min-nonzero-hours must be >= 0; "
                 f"got {args.min_nonzero_hours}")
    if interpolate_set:
        names = ", ".join(SENSOR_NAME_BY_ID[sid] for sid in sorted(interpolate_set))
        print(f"  interpolate-zeros-for: {{{names}}}")
        print(f"  min-nonzero-fraction:  {args.min_nonzero_fraction}")
        print(f"  min-nonzero-hours:     {args.min_nonzero_hours}")
    elif args.min_nonzero_fraction > 0.0 or args.min_nonzero_hours > 0:
        print(f"  WARNING: coverage thresholds set but "
              f"--interpolate-zeros-for is empty; filter inert.")

    cfg = Config(
        geojson_path=geojson_path,
        metadata_path=metadata_path,
        sim_name_false=sim_names[0],
        sim_name_true=sim_names[1],
        num_cores=args.num_cores,
        sim_year=args.sim_year,
        api_base=api_base,
        db=db,
        metadata=metadata,
        interpolate_zeros_for_sensor_types=interpolate_set,
        min_nonzero_fraction=args.min_nonzero_fraction,
        min_nonzero_hours=args.min_nonzero_hours,
    )

    if args.reuse_from:
        runs = [
            {"name": cfg.sim_name_false, "duration_sec": 0.0,
             "dynamic_defaults": False, "reused_from": str(args.reuse_from)},
            {"name": cfg.sim_name_true,  "duration_sec": 0.0,
             "dynamic_defaults": True,  "reused_from": str(args.reuse_from)},
        ]
    else:
        runs = [
            run_sim(cfg, cfg.sim_name_false, dynamic_defaults=False),
            run_sim(cfg, cfg.sim_name_true,  dynamic_defaults=True),
        ]

    print("\n=== loading predicted hourly ===")
    pred_false_hour = load_predicted_hourly(
        cfg.sim_name_false, metadata.geom_to_asset,
        predicted_root=args.reuse_from)
    pred_true_hour  = load_predicted_hourly(
        cfg.sim_name_true, metadata.geom_to_asset,
        predicted_root=args.reuse_from)
    print(f"  pred_false: {len(pred_false_hour)} (asset, sensor_type) pairs")
    print(f"  pred_true : {len(pred_true_hour)} (asset, sensor_type) pairs")

    actuals_by_grain = fetch_actuals(
        cfg.db, cfg.sim_year,
        interpolate_sensor_types=cfg.interpolate_zeros_for_sensor_types,
        min_nonzero_fraction=cfg.min_nonzero_fraction,
        min_nonzero_hours=cfg.min_nonzero_hours,
    )

    scores_by_grain: dict[str, list[PerAssetScore]] = {}
    overalls_by_grain: dict[str, list[dict[str, Any]]] = {}
    for grain in GRAINS:
        scores = score_pair(
            aggregate_predicted(pred_false_hour, grain),
            aggregate_predicted(pred_true_hour, grain),
            actuals_by_grain[grain],
        )
        overall = aggregate_overall(scores)
        scores_by_grain[grain] = scores
        overalls_by_grain[grain] = overall
        print_overall_table(grain, overall)

    ts_label = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir = RUNS_DIR / f"{ts_label}_ab_dynamic_defaults"
    out_dir.mkdir(parents=True, exist_ok=True)
    for grain in GRAINS:
        write_per_asset_csv(out_dir / f"per_asset_{grain}.csv",
                            scores_by_grain[grain], metadata.asset_name)
        write_overall_csv(out_dir / f"overall_{grain}.csv",
                          overalls_by_grain[grain])
    write_summary_json(out_dir / "summary.json", cfg, runs,
                       scores_by_grain, overalls_by_grain, ts_label)
    print(f"\nresults saved to: {out_dir}")


if __name__ == "__main__":
    main()
