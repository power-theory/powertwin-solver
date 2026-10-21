"""The solver side of the fast estimate (vault Surrogate Serving Plan step 1, 2026-10-03).

asset_update with model_type_id = SURROGATE_MODEL_TYPE_ID runs this instead of URBANopt: the request's metadata and
geometry go through the simulation's own loaders (read_metadata: subtype, weather station, climate zone;
load_asset_geojson: footprint cleanup and the multi-footprint area share), each footprint becomes the collector entry
the surrogate was trained on, and the model's predictions are written as cleaned reports for pack_simulation_results.

Configuration (all optional; the fast estimate is refused, never faked, when unset):
  SURROGATE_MODEL_DIR       a model directory (surrogate.app.modules.serving: tree model, presence gate, serving_meta.json)
  SURROGATE_MODEL_TYPE_ID   the db's sensor_logs_pred_model_types id for the fast estimate (default 3; an unparsable
                            value or URBANopt's own id 1 disables the fast estimate rather than rerouting requests)
  SURROGATE_THREADS         xgboost threads per prediction (default: cores - 2)
"""
from __future__ import annotations

import os
import sys

from modules.simulation import generateFeatureFile as G
from modules.simulation.sim_params_spec import RESOLVER_VERSION
from modules.utils import weather as W

URBANOPT_MODEL_TYPE_ID = 1


def surrogate_model_type_id():
    """The fast estimate's model type id, or None when the setting is unusable: a malformed value must not turn every
    URBANopt request into a 500, and 1 must not send requests without a model_type_id to the surrogate (review)."""
    try:
        v = int(os.environ.get('SURROGATE_MODEL_TYPE_ID', '3').strip())
    except ValueError:
        return None
    return None if v == URBANOPT_MODEL_TYPE_ID else v


def _serving():
    """surrogate.app.modules.serving. In the image the package sits at /opt/powertwin/surrogate (on PYTHONPATH); in a
    checkout it is the repo's surrogate/ beside solver/."""
    try:
        from surrogate.app.modules import serving
    except ImportError:
        root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..'))
        if root not in sys.path:
            sys.path.insert(0, root)
        from surrogate.app.modules import serving
    return serving


class SurrogateUnavailable(RuntimeError):
    pass


def _model_dir() -> str:
    model_dir = os.environ.get('SURROGATE_MODEL_DIR', '').strip()
    if not model_dir:
        raise SurrogateUnavailable('SURROGATE_MODEL_DIR is not set on this solver')
    if not os.path.isdir(model_dir):
        raise SurrogateUnavailable(f'SURROGATE_MODEL_DIR {model_dir} does not exist')
    return model_dir


def precheck() -> dict:
    """The model's serving_meta, or SurrogateUnavailable: no model configured, a schema / resolver / package
    mismatch, or a model trained for another simulation year. Reads no model files (cheap enough for a request)."""
    model_dir = _model_dir()
    try:
        serving = _serving()
        meta = serving.check_meta(model_dir, RESOLVER_VERSION)
        year = os.environ.get('URBANOPT_SIMULATION_YEAR', '').strip() or '2025'   # the resolver's and feature file's default
        if int(year) != int(meta['sim_year']):
            raise SurrogateUnavailable(f"model trained for {meta['sim_year']}, this solver simulates {year}")
    except SurrogateUnavailable:
        raise
    except Exception as e:   # ModelMismatch, an unreadable meta, a bad year, an import or numba cache failure: a 503
        raise SurrogateUnavailable(f'{type(e).__name__}: {e}') from e
    return meta


def get_server():
    precheck()
    threads = int(os.environ['SURROGATE_THREADS']) if os.environ.get('SURROGATE_THREADS') else None
    return _serving().get_server(_model_dir(), RESOLVER_VERSION, threads)


# metadata keys a user or the db can set that the resolver reads (the simulation's explicit params, vacancy and the
# unit counts the collector's residential path reads)
_EXPLICIT_KEYS = tuple(G._EXPLICIT_PARAM_KEYS) + ('vacant', 'number_of_units', 'units', 'number_of_residential_units')


def _surrogate_inputs() -> frozenset:
    """The metadata keys whose explicit value the fast estimate reads (the collector's EXPLICIT_INPUTS)."""
    from surrogate.app.modules.data.collector import EXPLICIT_INPUTS
    return EXPLICIT_INPUTS


def _explicit(md: dict) -> dict:
    """The metadata's explicit simulation params; blank and whitespace-only values are unset, as get_param reads them."""
    return {k: md[k] for k in _EXPLICIT_KEYS if md.get(k) is not None and str(md.get(k)).strip() != ''}


def _unservable(explicit: dict, building_type) -> str | None:
    """Why a fast estimate would not be this building, or None (2026-10-13): an explicit param the model does not read
    (it would ignore it), or an explicit value outside what it was trained on (a tree model does not extrapolate).
    building_type None (not known yet) checks the range against both workflows' spans."""
    unread = sorted(set(explicit) - _surrogate_inputs())
    if unread:
        return f"explicit {', '.join(unread)} not an input of the fast estimate; simulate with URBANopt"
    from surrogate.app.modules.data.collector import explicit_outside_training
    from surrogate.app.modules.sampling.sim_levers import EFFECTIVE_BUILDING_TYPE
    from modules.simulation.sim_params_spec import RESIDENTIAL_BUILDING_TYPES
    if building_type is None:   # outside in every workflow the key has a training span in
        from surrogate.app.modules.data.collector import trained_spans
        per = {res: set(explicit_outside_training(explicit, res)) for res in (True, False)}
        outside = sorted(k for k in set(per[True]) | set(per[False])
                         if all(k in per[res] for res in (True, False) if k in trained_spans(res)))
    else:
        res = EFFECTIVE_BUILDING_TYPE.get(building_type, building_type) in RESIDENTIAL_BUILDING_TYPES
        outside = explicit_outside_training(explicit, res)
    if outside:
        return f"explicit {', '.join(outside)} outside the fast estimate's training range; simulate with URBANopt"
    return None


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def refusal(metadata_rows) -> str | None:
    """The request-time check for asset_update: the first building the fast estimate cannot be, with the reason, so
    the caller gets a clear 409 instead of a failed background run."""
    import json
    for row in metadata_rows or []:
        md = row.get('asset_metadata') if isinstance(row, dict) else None
        if isinstance(md, str):
            try:
                md = json.loads(md)
            except ValueError:
                continue
        if isinstance(md, dict):
            sub = G.ASSET_SUBTYPES.get(_int_or_none(row.get('asset_subtype_id')) or -1)
            reason = _unservable(_explicit(md), sub['name'] if sub else None)
            if reason:
                return reason
    return None


def build_entries(metadata_csv_path: str, geojson_path: str):
    """The collector entries of every simulatable footprint in the request, with the EPW each one uses, its sensors
    map and its building id; plus the ids skipped (no weather, unsimulatable type) with the reason."""
    import csv
    import json
    area, typ, name, weather, cz, year, meta = G.read_metadata(metadata_csv_path)
    data = G.load_asset_geojson(geojson_path, area, typ, name, weather, meta)
    csv.field_size_limit(sys.maxsize)
    sensors = {}
    with open(metadata_csv_path, newline='') as f:
        for r in csv.DictReader(f):
            gp = json.loads(r.get('asset_geometries_properties') or '{}')
            sensors[str(gp.get('id'))] = json.loads(r.get('sensors') or '{}')
    from modules.simulation.sim_params_spec import _STATE_FIPS_TO_ABBR, _normalize_state
    serving = _serving()
    from surrogate.app.modules.sampling.cell_enumerator import year_to_decade
    from surrogate.app.modules.sampling.sim_levers import sim_shape_factor
    entries, epws, sens, ids = [], [], [], []
    skipped = {bid: 'rejected by the metadata checks (no id or area, or a duplicate)' for bid in sensors if bid not in meta}
    for feat in data['features']:
        props = feat.get('properties') or {}
        bid = str(props.get('id'))
        if bid not in meta:
            continue
        if typ.get(bid) in G.UNSIMULATABLE_TYPES:
            skipped[bid] = 'unsimulatable type'
            continue
        station_state, title = weather.get(bid, (None, None))
        if not station_state or not title:
            skipped[bid] = 'no weather (coordinates missing or unresolved)'
            continue
        epw = os.path.join(W.WEATHER_FILES_DIR, title, f'{title}.epw')
        if not os.path.exists(epw):
            skipped[bid] = f'weather file {title} not available'
            continue
        md = meta[bid]
        explicit = _explicit(md)
        reason = _unservable(explicit, typ.get(bid))
        if reason:   # a fast estimate of a different building, or beyond what it learned (2026-10-13)
            skipped[bid] = reason
            continue
        if year_to_decade(md.get('year_built')) is None:
            skipped[bid] = 'no year_built (the model was trained on buildings with one)'
            continue
        # the ASSET's state, as the simulation's ctx (build_asset_ctx on the metadata) and the training entries use;
        # the station's state only picks emissions, and a border town's station is often in the next state (review)
        state = _normalize_state(md.get('state')) or _STATE_FIPS_TO_ABBR.get(str(md.get('county_fips') or '')[:2])
        floors = G._floor_count_of(md, props)     # the count process_feature builds with
        floor_area = float(area[bid])
        if '_asset_floor_area_m2' in md:   # one footprint of a multi-footprint asset: its share, as process_feature
            fp_m2, pool_m2 = md['_footprint_m2'], md['_asset_floor_area_m2']
            if fp_m2 > 0 and pool_m2 > 0:
                share = fp_m2 * floors / pool_m2
                if share < 1.0 - 1e-12:
                    floor_area = max(1, int(float(md['area']) * share))
        sub_id = int(md.get('_subtype_id', G.DEFAULT_SUBTYPE_ID))
        entries.append({
            'building_id': bid,
            'asset_subtype_id': sub_id,
            'building_type': G.ASSET_SUBTYPES[sub_id]['name'],     # the RAW name the surrogate was trained on
            'decade_vintage': year_to_decade(md.get('year_built')),
            'climate_zone': cz.get(bid) or 'UNK',
            'county_fips': md.get('county_fips'),
            'state': state,
            'year_built': md.get('year_built'),
            'latitude': md.get('latitude'),
            'longitude': md.get('longitude'),
            'floor_area_ft2': floor_area,
            'floor_count': floors,
            'shape_factor': sim_shape_factor(feat.get('geometry')),
            'explicit': explicit,
        })
        epws.append(epw)
        sens.append(sensors.get(bid, {}))
        ids.append(bid)
    return entries, epws, sens, ids, skipped, serving


def run(sim_local_dir: str, metadata_csv_path: str, geojson_path: str, server) -> dict:
    """Predict every footprint of the request and write its cleaned reports. Returns {'buildings', 'skipped',
    'streams'}; raises when nothing could be estimated."""
    entries, epws, sens, ids, skipped, serving = build_entries(metadata_csv_path, geojson_path)
    if not entries:
        raise RuntimeError(f'no building could be estimated: {skipped or "no footprints"}')
    from modules.utils.precision import round_series   # clean_report's rounding, POWERTWIN_SIG_FIGS included
    cleaned = os.path.join(sim_local_dir, 'cleaned_reports')
    streams = {}
    for start, Y in server.predict(entries, G_sps(), epws):
        for k, y in enumerate(Y):
            bid, epw = ids[start + k], epws[start + k]
            title = os.path.splitext(os.path.basename(epw))[0]
            streams[bid] = serving.write_cleaned_reports(cleaned, bid, y, sens[start + k], server.sim_year,
                                                         W.get_epw_utc_offset(title), round_series=round_series)
    if not any(streams.values()):   # as the URBANopt worker: an empty result is a failure, not a completed run
        raise RuntimeError(f'no stream to report: no estimated building has a sensor for a stream it has '
                           f'(buildings {sorted(streams)}, skipped {skipped})')
    return {'buildings': len(ids), 'skipped': skipped, 'streams': streams}


def G_sps():
    from modules.simulation import sim_params_spec
    return sim_params_spec


LOG_FIELDS = ('sensor_id', 'collection_id', 'ts', 'value', 'metadata')


def log_rows(y, sensors: dict, year: int, utc_offset_h: float, resample=None, start_date_time=None,
             end_date_time=None):
    """One building's prediction as the rows a request at that datelevel reports, with no cleaned report on disk: the
    cleaned report's own stamps and rounding (serving.stream_series), bucketed by pack_results._normalize_rows, the
    function pack_simulation_results applies to every simulation (the EPW's local calendar, the translations flag,
    start-of-period markers in naive UTC, 4 significant figures after the sum). `resample` takes the request's codes
    (None = native hourly, H, D, W, M, Y, A); the window bounds are the request's. Yields (sensor id, ts, value)."""
    from modules.utils.pack_results import _RESAMPLE_CANON, _normalize_rows
    from modules.utils.precision import round_series
    resample = (resample or '').strip().upper() or None
    resample = _RESAMPLE_CANON.get(resample, resample)
    import pandas as pd
    serving = _serving()
    parsed = None
    for _slug, sid, stamps, values in serving.stream_series(y, sensors, year, utc_offset_h, round_series=round_series):
        if parsed is None:   # the report's stamps, parsed once per building as _normalize_rows parses them
            parsed = pd.to_datetime(pd.Series(stamps))
        rows = {'ts': parsed, 'value': values.astype(float)}   # the cleaned report's columns, as a DataFrame takes them
        for r in _normalize_rows(rows, resample, start_date_time, end_date_time):
            yield sid, r['ts'], r['value']


def run_logs(out_csv: str, metadata_csv_path: str, geojson_path: str, server, collection_id, resample=None,
             start_date_time=None, end_date_time=None) -> dict:
    """The fast estimate for a whole collection written straight to one sensor-log CSV at the requested datelevel, in
    consolidate_sensor_logs' layout (sensor_id, collection_id, ts 'YYYY-MM-DD HH:MM:SS' naive UTC, value, metadata), so
    a state needs no hourly files on disk; the rows equal what a request at that datelevel would report (log_rows).
    Returns {'buildings', 'rows', 'skipped', 'datelevel'}."""
    import csv
    from modules.utils.pack_results import _RESAMPLE_CANON, _RESAMPLE_TO_DATELEVEL
    entries, epws, sens, ids, skipped, _ = build_entries(metadata_csv_path, geojson_path)
    if not entries:
        raise RuntimeError(f'no building could be estimated: {skipped or "no footprints"}')
    code = _RESAMPLE_CANON.get((resample or '').strip().upper(), (resample or '').strip().upper()) or None
    n = 0
    offsets = {}
    streams = {}   # sensor_type_id -> buildings with a non-zero stream of it (settings.sensorTypeOptions, 2026-10-20)
    with open(out_csv, 'w', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(LOG_FIELDS)
        for start, Y in server.predict(entries, G_sps(), epws):
            for k, y in enumerate(Y):
                title = os.path.splitext(os.path.basename(epws[start + k]))[0]
                if title not in offsets:
                    offsets[title] = W.get_epw_utc_offset(title)
                type_of = {str(sid): str(t) for t, sid in sens[start + k].items()}
                written = set()
                for sid, ts, value in log_rows(y, sens[start + k], server.sim_year, offsets[title], code,
                                               start_date_time, end_date_time):
                    w.writerow((sid, collection_id, ts.replace('T', ' '), value, '{}'))
                    n += 1
                    if value:
                        written.add(type_of.get(str(sid)))
                for t in written:
                    streams[t] = streams.get(t, 0) + 1
    return {'buildings': len(ids), 'rows': n, 'skipped': skipped,
            'datelevel': _RESAMPLE_TO_DATELEVEL.get(code, 'hour') if code else 'hour',
            'streams_by_sensor_type': {t: streams[t] for t in sorted(streams, key=lambda x: int(x))}}


def main(argv=None):
    """python -m modules.simulation.surrogate_serving <metadata.csv> <asset_geometries.geojson> <out.csv>
    --collection-id N [--resample M] [--start ...] [--end ...]: the fast estimate for a whole collection (a state, in
    chunks) as one sensor-log CSV at a datelevel, in the model's training mode, as the asset_update worker runs it.
    Writes <out.csv>.json beside it: the model, the settings, the input md5s, the counts and the skipped buildings."""
    import argparse
    import hashlib
    import json
    import time
    from modules.utils.sim_flags import clear_sim_flags, set_sim_flags
    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument('metadata_csv'); ap.add_argument('geojson'); ap.add_argument('out_csv')
    ap.add_argument('--collection-id', required=True)
    ap.add_argument('--resample', default=None, help='H, D, W, M or Y (default: native hourly)')
    ap.add_argument('--start', default=None); ap.add_argument('--end', default=None)
    a = ap.parse_args(argv)
    t0 = time.time()
    server = get_server()
    env = server.env()
    os.environ.update(env)            # the resolver and the packing read the process env as well as the flags
    set_sim_flags(env['URBANOPT_DYNAMIC_DEFAULTS'], env['URBANOPT_STOCHASTIC_SAMPLING'], None, None, None)
    try:
        res = run_logs(a.out_csv, a.metadata_csv, a.geojson, server, a.collection_id, a.resample, a.start, a.end)
    finally:
        clear_sim_flags()

    def md5(p):
        h = hashlib.md5()
        with open(p, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
        return h.hexdigest()
    run = {'model': server.meta, 'model_dir': server.model_dir, 'resolver_env': env, 'resample': a.resample,
           'window': [a.start, a.end], 'collection_id': a.collection_id,
           'translations': os.environ.get('URBANOPT_POSTPROCESS_TRANSLATIONS', ''),
           'inputs': {os.path.basename(p): md5(p) for p in (a.metadata_csv, a.geojson)},
           'output_md5': md5(a.out_csv), 'seconds': round(time.time() - t0, 1), **res}
    with open(a.out_csv + '.json', 'w') as f:
        json.dump(run, f, indent=1)
    print(json.dumps({k: run[k] for k in ('buildings', 'rows', 'datelevel', 'seconds')}), flush=True)
    return run


if __name__ == '__main__':   # from the solver root with app on the path (cd /solver; PYTHONPATH=app python -m ...), as direct_runner runs
    main()
