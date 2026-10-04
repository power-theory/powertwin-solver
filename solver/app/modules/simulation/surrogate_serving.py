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
            'explicit': {k: md[k] for k in _EXPLICIT_KEYS if md.get(k) not in (None, '')},
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
