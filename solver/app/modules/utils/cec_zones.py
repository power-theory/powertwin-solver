"""California's Title 24 building climate zones (CEC layer, cec_building_climate_zones.json.gz, fetched 2026-09-30): the
zones the openstudio-standards DEER templates accept. Standard library plus shapely, no solver imports, so the surrogate
(sampling/sim_levers) loads this same file and labels a building with the templates the simulation builds."""
import gzip
import json
import math
import os

CEC_ZONE_FALLBACK_M = 5000.0   # a point this close to a zone (coast, island, boundary slivers) takes the nearest one
_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'upload', 'reference_data',
                                      'cec_building_climate_zones.json.gz'))   # sim_params_spec._REF_DATA_DIR
_lookup = None


def _load():
    global _lookup
    if _lookup is None:
        from shapely.geometry import shape
        from shapely import STRtree
        with gzip.open(_PATH, 'rt') as f:
            zones = json.load(f)
        names = list(zones['zones'])
        geoms = [shape(zones['zones'][n]) for n in names]
        _lookup = (STRtree(geoms), geoms, names, zones.get('zip_zones', {}))
    return _lookup


def _metres(lat, lon, lat2, lon2):
    """Equirectangular distance; exact enough at the 5 km fallback scale, unlike scaling a degree distance by cos(lat),
    which under-measures north-south offsets."""
    k = math.cos(math.radians((lat + lat2) / 2.0))
    return 111320.0 * math.hypot((lon2 - lon) * k, lat2 - lat)


def get_cec_climate_zone(lat, lon, postcode=None):
    """The CEC building climate zone ('1'..'16') of a California point, or None outside California. A point in no
    polygon but within CEC_ZONE_FALLBACK_M of one (coast) takes the nearest zone; farther out (the Channel Islands,
    which the CEC layer does not cover) the CEC's own ZIP assignment for `postcode` decides. Non-finite coordinates
    (an 'inf' or 1e400 in metadata) are None, never an exception that would stop a whole run."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    from shapely.geometry import Point
    from shapely.ops import nearest_points
    tree, geoms, names, zip_zones = _load()
    pt = Point(lon, lat)
    hits = [names[i] for i in tree.query(pt) if geoms[i].covers(pt)]
    if hits:
        return hits[0]
    i = tree.nearest(pt)
    q = nearest_points(geoms[i], pt)[0]
    if _metres(lat, lon, q.y, q.x) <= CEC_ZONE_FALLBACK_M:
        return names[i]
    zip5 = str(postcode or '').strip()[:5]
    return zip_zones.get(zip5) if zip5.isdigit() else None
