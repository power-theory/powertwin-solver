"""Footprint digitizing noise (2026-10-01): a tiny jog in a footprint became a sliver perimeter zone that EnergyPlus
could not hold (Teton 19414); generateFeatureFile drops such vertices before the fit and the zoning measure."""
import math
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))
os.environ.setdefault("POWERTWIN_LOG_DIR", "/tmp")

from modules.simulation import generateFeatureFile as G  # noqa: E402

# Teton 19414's outer ring in local metres (edges 23.48, 23.72, 3.77, 5.84, 3.47, 2.68, 1.68, 1.34, 2.62, 11.8, ...)
LAT0, LON0 = 43.48, -110.76


def _ll(xy):
    kx, ky = 111320.0 * math.cos(math.radians(LAT0)), 110540.0
    ring = [[LON0 + x / kx, LAT0 + y / ky] for x, y in xy]
    return ring + [ring[0]]


def test_a_tiny_jog_is_dropped_and_a_real_corner_kept():
    sq = [(0, 0), (20, 0), (20, 10), (10.0, 10.05), (0, 10)]          # 10.05: a 0.25 m2 jog on the top edge
    ring = _ll(sq)
    out = G._ring_noise_free(ring)
    assert len(out) == 5 and out[0] == out[-1]                         # the jog vertex is gone, 4 corners kept
    corners = _ll([(0, 0), (20, 0), (20, 10), (0, 10)])
    assert G._ring_noise_free(corners) == corners                      # nothing to drop


def test_a_dense_curve_is_simplified_not_collapsed():
    circle = [(15 * math.cos(2 * math.pi * i / 120), 15 * math.sin(2 * math.pi * i / 120)) for i in range(120)]
    ring = _ll(circle)
    out = G._ring_noise_free(ring)
    kx, ky = 111320.0 * math.cos(math.radians(LAT0)), 110540.0
    a0 = G._ring_area_unsigned([((x - LON0) * kx, (y - LAT0) * ky) for x, y in ring[:-1]])
    a1 = G._ring_area_unsigned([((x - LON0) * kx, (y - LAT0) * ky) for x, y in out[:-1]])
    assert 8 <= len(out) - 1 < 120 and a1 / a0 > 0.97


def test_tiny_footprints_keep_four_vertices_and_their_shape():
    shed = _ll([(0, 0), (1.5, 0), (1.5, 1.2), (0, 1.2)])              # 1.8 m2: every triangle is under 1 m2
    assert G._ring_noise_free(shed) == shed


def test_both_loaders_clean_before_use():
    src = open(os.path.join(REPO, "solver", "app", "modules", "simulation", "generateFeatureFile.py")).read()
    load = src.split("def load_asset_geojson", 1)[1].split("def ", 1)[0]
    assert load.index("clean_footprint(") < load.index("annotate_asset_footprints(")
    proc = src.split("def process_feature", 1)[1][:600]
    assert "clean_footprint(feature['geometry'])" in proc


def test_cleaning_twice_equals_cleaning_once():
    """load_asset_geojson cleans before the area fit measures a footprint and process_feature cleans again; a single
    Visvalingam pass is not idempotent (dropping a concave vertex raises the share-of-area tolerance), which made 8 of
    270,532 national footprints miss their fitted area by ~0.5% (review 2026-10-03)."""
    import copy
    import math
    import random
    rng = random.Random(7)
    for _ in range(300):
        n = rng.randint(6, 40)
        ring = []
        for k in range(n):
            a = 2 * math.pi * k / n
            r = 1e-4 * (1 + 0.3 * rng.random()) * (0.98 if rng.random() < 0.3 else 1.0)
            ring.append([-110.76 + r * math.cos(a), 43.48 + r * math.sin(a)])
        ring.append(list(ring[0]))
        g = {"type": "Polygon", "coordinates": [ring]}
        once = G.clean_footprint(copy.deepcopy(g))
        assert G.clean_footprint(copy.deepcopy(once)) == once


def test_shape_factor_is_measured_in_metres_not_degrees():
    """The perimeter/area ratio was taken on raw lon/lat, so a square read 1.013 at Teton's latitude and a rectangle's
    factor depended on its orientation (2026-10-03). In local metres a square is 1 and orientation does not matter."""
    import math
    from modules.utils.footprint_clean import shape_factor
    lat0, lon0 = 43.48, -110.76
    kx, ky = 111320.0 * math.cos(math.radians(lat0)), 110540.0
    ring = lambda pts: [[lon0 + x / kx, lat0 + y / ky] for x, y in pts]
    assert shape_factor(ring([(0, 0), (20, 0), (20, 20), (0, 20), (0, 0)])) == pytest.approx(1.0, abs=1e-5)
    ew = shape_factor(ring([(0, 0), (100, 0), (100, 10), (0, 10), (0, 0)]))
    ns = shape_factor(ring([(0, 0), (10, 0), (10, 100), (0, 100), (0, 0)]))
    assert ew == pytest.approx(ns, abs=1e-4) and ew == pytest.approx(22 / (4 * math.sqrt(10)), abs=1e-4)
    assert G._metric_shape_factor is shape_factor          # generateFeatureFile uses this very function
