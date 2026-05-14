import csv
import datetime
import json
import os
import re
from glob import glob

import pandas as pd


SENSOR_TYPES_CSV = os.path.join(
    os.path.dirname(__file__), '..', '..', '..', 'upload', 'sensor_types.csv'
)


# Map a pandas-style period letter to the canonical datelevel label used by
# sensor_logs_pred.datelevel (the convention in cron/leaderboard.js is 'hour').
_RESAMPLE_TO_DATELEVEL = {
    'H': 'hour',
    'D': 'day',
    'W': 'week',
    'M': 'month',
    'Y': 'year',
}


def _detect_native_datelevel(ts_strings):
    """Cheap, pandas-free native-frequency detection. Median consecutive gap → label.
    Mirrors the buckets used by consolidate_sensor_logs.py (>=27d → monthly)."""
    parsed = []
    for s in ts_strings:
        if not s:
            continue
        try:
            parsed.append(datetime.datetime.fromisoformat(s.replace('Z', '+00:00')))
        except (ValueError, TypeError):
            continue
    if len(parsed) < 2:
        return 'hour'
    parsed.sort()
    diffs = sorted((parsed[i + 1] - parsed[i]).total_seconds()
                   for i in range(len(parsed) - 1))
    median = diffs[len(diffs) // 2]
    if median >= 27 * 86400:
        return 'month'
    if median >= 86400:
        return 'day'
    if median >= 3600:
        return 'hour'
    return 'minute'


def _normalize_rows(rows, resample, start_date_time=None, end_date_time=None):
    """Bucket cleaned-report rows. The two custom translations (-1s shift and
    monthly noon-on-1st) only apply when URBANOPT_POSTPROCESS_TRANSLATIONS is
    truthy; otherwise this is plain pandas: tz-aware grouping, period-start
    markers, no boundary fixups.

      1. Preserve the EPW local-time tz info through bucketing so periods align
         to the simulation's local calendar — independent of the translations
         flag; this is correctness, not convention.
      2. Optionally subtract 1s from each ts (translations only) — mirrors
         consolidate_sensor_logs.py:46.
      3. If start_date_time/end_date_time provided: slice rows to that closed
         window before bucketing. Naive bounds are interpreted in the data's
         local tz; tz-aware bounds are honored as-is.
      4. If resample is set: group by period (SUM). Monthly markers land at
         noon-on-1st when translations are on, period-start otherwise.
      5. If no resample: floor to native frequency. Native monthly snaps to
         the 1st regardless of the flag.
    """
    apply_translations = os.environ.get('URBANOPT_POSTPROCESS_TRANSLATIONS') == 'true'

    df = pd.DataFrame(rows)
    df['ts'] = pd.to_datetime(df['ts'])

    # Capture the local tz (if any) and strip to naive wall-clock so all
    # bucketing/flooring operates in local time. Re-attach on emit.
    local_tz = df['ts'].dt.tz if df['ts'].dt.tz is not None else None
    if local_tz is not None:
        df['ts'] = df['ts'].dt.tz_localize(None)

    native_freq = (df['ts'].diff().median()
                   if len(df) >= 2 else pd.Timedelta(hours=1))

    # End-of-period -> start-of-period (translations only)
    if apply_translations:
        df['ts'] = df['ts'] - pd.Timedelta(seconds=1)

    # Optional window filter — applied post-shift so user-supplied bounds line
    # up with the emitted start-of-period timestamps. Boundary buckets reflect
    # only the data inside the window (partial buckets at edges are expected).
    if start_date_time:
        start_ts = pd.to_datetime(start_date_time)
        if start_ts.tzinfo is not None:
            # Convert to data's local tz then strip, so the comparison is
            # against the same naive wall clock the rows now carry.
            if local_tz is not None:
                start_ts = start_ts.tz_convert(local_tz)
            start_ts = start_ts.tz_localize(None)
        df = df[df['ts'] >= start_ts]
    if end_date_time:
        end_ts = pd.to_datetime(end_date_time)
        if end_ts.tzinfo is not None:
            if local_tz is not None:
                end_ts = end_ts.tz_convert(local_tz)
            end_ts = end_ts.tz_localize(None)
        df = df[df['ts'] <= end_ts]

    if df.empty:
        return []

    if resample:
        df = (df.groupby(df['ts'].dt.to_period(resample))['value']
                .sum()
                .reset_index())
        if resample == 'M' and apply_translations:
            df['ts'] = df['ts'].dt.to_timestamp().dt.normalize() + pd.Timedelta(hours=12)
        else:
            df['ts'] = df['ts'].dt.to_timestamp()
    else:
        if native_freq >= pd.Timedelta(days=27):
            df['ts'] = df['ts'].dt.to_period('M').dt.to_timestamp()
        else:
            df['ts'] = df['ts'].dt.floor(native_freq)

    # Convert bucket markers to naive UTC for emission. The bucketing happened
    # in local time (correctness) but the stored representation is UTC with no
    # offset, matching the rest of sensor_logs.
    if local_tz is not None:
        df['ts'] = df['ts'].dt.tz_localize(local_tz).dt.tz_convert('UTC').dt.tz_localize(None)

    return [
        {
            'ts': r.ts.isoformat() if pd.notna(r.ts) else None,
            'value': float(r.value),
            'metadata': {},
        }
        for r in df.itertuples()
    ]


def _load_sensor_type_index():
    """Build {filename_slug: (sensor_type_id, sensor_type_name)} from sensor_types.csv.

    Mirrors the file naming convention used by clean_report.py:
        cleaned_predicted_{name.lower().replace(' ', '_')}.csv
    """
    index = {}
    with open(SENSOR_TYPES_CSV, 'r') as f:
        for row in csv.DictReader(f):
            name = row.get('name', '').strip()
            if not name:
                continue
            slug = name.lower().replace(' ', '_')
            index[slug] = (int(row['id']), name)
    return index


def pack_simulation_results(local_dir, runtime_seconds=None, resample=None,
                            start_date_time=None, end_date_time=None):
    """Read cleaned_reports/<asset_id>/cleaned_predicted_*.csv into a JSON-friendly dict.

    resample:
      None | '' → native passthrough (label inferred from row spacing)
      'H' | 'D' | 'W' | 'M' | 'Y' → group by period (SUM); pandas convention,
        same as consolidate_sensor_logs.py / consolidate-state.sh's RESAMPLE arg.

    start_date_time / end_date_time:
      Optional ISO-8601 strings or anything pandas.to_datetime accepts. When set,
      the post-shift rows are sliced to the closed window [start, end] BEFORE
      any resample is applied. Either bound is independent — pass only start
      to open the upper end, only end to open the lower.

    Response always includes a 'datelevel' field so the API ingest doesn't have
    to guess. Stored as sensor_logs_pred.datelevel.
    """
    sensor_type_index = _load_sensor_type_index()
    cleaned_root = os.path.join(local_dir, 'cleaned_reports')
    results = []
    detected_native = None

    resample = (resample or '').strip().upper() or None

    if not os.path.isdir(cleaned_root):
        return {
            'results': results,
            'runtime_seconds': runtime_seconds,
            'datelevel': _RESAMPLE_TO_DATELEVEL.get(resample, 'hour'),
        }

    for asset_id in sorted(os.listdir(cleaned_root)):
        asset_dir = os.path.join(cleaned_root, asset_id)
        if not os.path.isdir(asset_dir):
            continue

        for csv_path in sorted(glob(os.path.join(asset_dir, 'cleaned_predicted_*.csv'))):
            slug = re.sub(r'^cleaned_predicted_', '', os.path.basename(csv_path))
            slug = re.sub(r'\.csv$', '', slug)
            mapping = sensor_type_index.get(slug)
            if not mapping:
                continue
            sensor_type_id, sensor_type_name = mapping

            rows = []
            with open(csv_path, 'r', newline='') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    raw_meta = row.get('metadata') or '{}'
                    try:
                        meta = json.loads(raw_meta)
                    except (json.JSONDecodeError, TypeError):
                        meta = {}
                    try:
                        value = float(row['value'])
                    except (KeyError, ValueError, TypeError):
                        continue
                    rows.append({
                        'ts': row.get('ts'),
                        'value': value,
                        'metadata': meta,
                    })

            if not rows:
                continue

            # Always normalize timestamps — EnergyPlus end-of-period -> start-of-period
            # plus optional window slice plus per-period flooring/grouping.
            # Matches consolidate_sensor_logs.py for the steps they share.
            if detected_native is None:
                detected_native = _detect_native_datelevel(r['ts'] for r in rows)
            rows = _normalize_rows(rows, resample,
                                   start_date_time=start_date_time,
                                   end_date_time=end_date_time)
            if not rows:
                continue

            results.append({
                'asset_id': asset_id,
                'sensor_type_id': sensor_type_id,
                'sensor_type_name': sensor_type_name,
                'rows': rows,
            })

    if resample:
        datelevel = _RESAMPLE_TO_DATELEVEL.get(resample, 'hour')
    else:
        datelevel = detected_native or 'hour'

    return {
        'results': results,
        'runtime_seconds': runtime_seconds,
        'datelevel': datelevel,
        'resample': resample or None,
    }


def atomic_write_json(path, payload):
    """Write JSON atomically: write to .tmp then os.rename. POSIX-atomic on same filesystem."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(payload, f)
    os.rename(tmp, path)


def write_status(local_dir, phase, **extra):
    """Write a small status.json indicating the current phase. Always atomic."""
    payload = {'phase': phase, **extra}
    atomic_write_json(os.path.join(local_dir, 'status.json'), payload)


def read_status(local_dir):
    """Read status.json if present; return None otherwise."""
    path = os.path.join(local_dir, 'status.json')
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
