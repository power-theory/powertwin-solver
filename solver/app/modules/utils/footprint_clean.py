"""Footprint digitizing-noise cleanup (Visvalingam), standard library only, so the surrogate (sampling/sim_levers) loads
this same file and derives the shape factor from the footprint the simulation builds. Moved out of generateFeatureFile
2026-10-03; generateFeatureFile re-exports every name."""
import math


def _ring_area_unsigned(ring):
    """Shoelace formula for the unsigned area of a coordinate ring (generateFeatureFile._ring_area_unsigned)."""
    n = len(ring)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += ring[i][0] * ring[j][1]
        area -= ring[j][0] * ring[i][1]
    return abs(area) / 2.0


FOOTPRINT_NOISE_M2 = 1.0          # a vertex whose triangle with its neighbours is smaller than this is digitizing noise
FOOTPRINT_NOISE_SHARE = 0.005     # ...or smaller than this share of the ring's area (small footprints)


def _ring_noise_free(ring):
    """Visvalingam: repeatedly drop the vertex whose triangle with its two neighbours is the smallest while that
    triangle is under min(FOOTPRINT_NOISE_M2, FOOTPRINT_NOISE_SHARE x ring area), keeping at least 4 vertices.
    Teton 19414 (2026-10-01): a 0.04 m2 jog (177.8 degrees between 1.7 m and 1.3 m edges) became a 0.41 m2 perimeter
    zone behind 22.5 m2 of wall and 7.4 m2 of windows in urban_geometry_creation_zoning, whose air ran past
    EnergyPlus's 200 C bound at 13 ft stories; dropping that one vertex builds and runs. Curves are simplified, not
    collapsed: a 105-vertex Teton footprint keeps 17 vertices and 98.6% of its area. Teton overall: 11% of footprints
    lose a vertex, 0.074% of footprint area."""
    if not ring or len(ring) < 6 or ring[0] != ring[-1]:
        return ring
    pts = [tuple(p[:2]) for p in ring[:-1]]
    lat = math.radians(pts[0][1])
    kx, ky = 111320.0 * math.cos(lat), 110540.0
    xy = [((x - pts[0][0]) * kx, (y - pts[0][1]) * ky) for x, y in pts]
    tol = min(FOOTPRINT_NOISE_M2, FOOTPRINT_NOISE_SHARE * _ring_area_unsigned(xy))
    keep = list(range(len(pts)))

    def tri(k):
        a, b, c = xy[keep[k - 1]], xy[keep[k]], xy[keep[(k + 1) % len(keep)]]
        return abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2.0

    while len(keep) > 4:
        areas = [tri(k) for k in range(len(keep))]
        k = min(range(len(keep)), key=areas.__getitem__)
        if areas[k] >= tol:
            break
        keep.pop(k)
    if len(keep) == len(pts):
        return ring
    out = [list(ring[i]) for i in keep]
    return out + [list(out[0])]


def _ring_noise_free_fixpoint(ring):
    """_ring_noise_free repeated until it changes nothing. One pass is not idempotent: dropping a concave vertex grows
    the ring, which raises the share-of-area tolerance, so a second pass could drop more; load_asset_geojson measures
    the cleaned footprint for the area fit and process_feature cleans again, so 8 of 270,532 national footprints missed
    their target area by about 0.5% (review 2026-10-03). At the fixed point a second call is a no-op."""
    while True:
        out = _ring_noise_free(ring)
        if out == ring:
            return ring
        ring = out


def clean_footprint(geom):
    """Drop digitizing-noise vertices from every ring of a Polygon or MultiPolygon in place (_ring_noise_free, run to a
    fixed point so cleaning twice equals cleaning once)."""
    if not geom or 'coordinates' not in geom:
        return geom
    if geom.get('type') == 'Polygon':
        geom['coordinates'] = [_ring_noise_free_fixpoint(r) for r in geom['coordinates']]
    elif geom.get('type') == 'MultiPolygon':
        geom['coordinates'] = [[_ring_noise_free_fixpoint(r) for r in poly] for poly in geom['coordinates']]
    return geom


def shape_factor(ring):
    """Perimeter of a lon/lat ring over the perimeter of the square of the same area, measured in local metres
    (metres per degree of longitude and latitude at the ring's mean latitude). Measured in raw degrees, a square read 1.013 at Teton's
    latitude and the factor depended on orientation (2026-10-03). 1.0 for a degenerate ring."""
    if not ring or len(ring) < 4:
        return 1.0
    lat0 = math.radians(sum(p[1] for p in ring) / len(ring))
    kx, ky = 111320.0 * math.cos(lat0), 110540.0     # metres per degree, as _ring_noise_free
    xy = [(p[0] * kx, p[1] * ky) for p in ring]
    area = _ring_area_unsigned(xy)
    if area <= 0:
        return 1.0
    perim = sum(math.dist(xy[i], xy[i + 1]) for i in range(len(xy) - 1))
    return perim / (4.0 * math.sqrt(area))
