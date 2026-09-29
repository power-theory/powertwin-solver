"""A commercial building's simulated floor area follows its metadata area (owner 2026-09-28: users enter commercial
areas that differ from footprint x floors). PowerTwin.rb rebuilds commercial geometry from the feature polygon
(urban_geometry_creation_zoning) and discards create_bar's area-based bar, so generateFeatureFile fits the polygon
to area / floor_count. Pinned from two 2026-09-28 reviews: one factor per asset over the footprints that are
actually fitted (multi-wing rows repeat the asset's area; parking, weatherless and residential rows do not count;
a null asset_id is its own asset), shares weighted by footprint x floors, only the outer ring counts (the measure
does not build holes), a fit that would not survive the measure's 1 m vertex merge (too few points or collinear)
falls back to a centred square, stock within AREA_FIT_TOL is simulated as mapped, residential is untouched."""
import math
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))
os.environ.setdefault("POWERTWIN_LOG_DIR", "/tmp")

from modules.simulation import generateFeatureFile as G  # noqa: E402

LON0, LAT0 = -110.7625, 43.4799
WX = ("WY", "USA_WY_Jackson.Hole.AP.725776_TMY3.epw")


def rect(dx, dy, w, h, rot=0.0, mid=False):
    """Closed lon/lat ring of a w x h m rectangle offset (dx, dy) m, rotated rot degrees (mid: a vertex halfway
    along the bottom edge)."""
    kx, ky = G._metres_per_degree(LAT0)
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    pts = [(0, 0)] + ([(w / 2, 0)] if mid else []) + [(w, 0), (w, h), (0, h), (0, 0)]
    return [[LON0 + (x * c - y * s + dx) / kx, LAT0 + (x * s + y * c + dy) / ky] for x, y in pts]


def feat(bid, asset, rings, fc=None):
    props = {"id": bid, "asset_id": asset, "type": "Building"}
    if fc is not None:
        props["floor_count"] = fc
    return {"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": rings}}


def run(features, area_ft2, floors, types=None, weather=None, per_floors=None):
    """read_metadata's lists for these features, the load_asset_geojson pre-pass, then process_feature each."""
    ids = [str(f["properties"]["id"]) for f in features]
    types = types or {i: "Office" for i in ids}
    meta = {i: {"county_fips": "56039", "state": "WY", "year_built": 1980, "area": area_ft2,
                "floor_count": (per_floors or {}).get(i, floors), "latitude": LAT0, "longitude": LON0} for i in ids}
    area_l, name_l = {i: int(area_ft2) for i in ids}, {i: "t" for i in ids}
    wx = weather or {i: WX for i in ids}
    G.annotate_asset_footprints(features, area_l, types, name_l, wx, meta)
    out = {}
    for f, i in zip(features, ids):
        r = G.process_feature(f, area_l, types, name_l, wx, {i: "6B"}, {i: 1980}, meta)
        out[i] = None if r is None else (r[0]["features"][0] if "features" in r[0] else r[0])
    return out


@pytest.mark.parametrize("area_ft2,floors", [(45880, 1), (11470, 1), (60000, 3), (10000, 4)])
def test_single_footprint_simulates_area_over_floors(area_ft2, floors):
    f = run([feat("1", "1", [rect(0, 0, 45, 47, rot=20)])], area_ft2, floors)["1"]
    assert G.outer_area_m2(f["geometry"]) == pytest.approx(area_ft2 / floors * G.SQFT_TO_M2, rel=1e-9)
    assert f["properties"]["floor_area"] == int(area_ft2)


def test_stock_within_tolerance_is_simulated_as_mapped():
    """A stock building's area matches its footprint to within projection and rounding: the outline is untouched."""
    ring = rect(0, 0, 45, 47, rot=20)
    area_ft2 = G.outer_area_m2({"type": "Polygon", "coordinates": [ring]}) * 1.0068 / G.SQFT_TO_M2   # the largest stock offset measured
    f = run([feat("2", "2", [ring])], area_ft2, 1)["2"]
    assert f["geometry"]["coordinates"][0] == ring


def test_shape_and_position_kept():
    g = {"type": "Polygon", "coordinates": [rect(0, 0, 40, 25, rot=33)]}
    before = [list(p) for p in g["coordinates"][0]]
    G.scale_polygon(g, 1.5)
    assert G._ring_center(g["coordinates"][0]) == pytest.approx(G._ring_center(before), abs=1e-12)
    for i in range(4):
        for j in (0, 1):
            assert g["coordinates"][0][i + 1][j] - g["coordinates"][0][i][j] == pytest.approx(1.5 * (before[i + 1][j] - before[i][j]))


def test_multi_wing_asset_shares_one_factor():
    """Every footprint row repeats the asset's area; the wings together build area / floors, not n times it."""
    wings = [feat("11", "7", [rect(0, 0, 60, 30)]), feat("12", "7", [rect(100, 0, 30, 30)]), feat("13", "7", [rect(0, 80, 20, 45)])]
    before = [G.outer_area_m2(w["geometry"]) for w in wings]
    out = run(wings, 30000, 3)
    after = [G.outer_area_m2(out[i]["geometry"]) for i in ("11", "12", "13")]
    assert sum(after) == pytest.approx(30000 / 3 * G.SQFT_TO_M2, rel=1e-9)
    assert [a / sum(after) for a in after] == pytest.approx([b / sum(before) for b in before], rel=1e-9)
    assert sum(out[i]["properties"]["floor_area"] for i in out) == pytest.approx(30000, abs=3)


def test_shares_weighted_by_floors():
    """Equal footprints, 1 floor and 10 floors: the tower carries 10/11 of the area, both keep one factor."""
    wings = [feat("21", "9", [rect(0, 0, 30, 30)]), feat("22", "9", [rect(100, 0, 30, 30)])]
    out = run(wings, 11000, None, per_floors={"21": 1, "22": 10})
    assert out["21"]["properties"]["floor_area"] == pytest.approx(1000, abs=1)
    assert out["22"]["properties"]["floor_area"] == pytest.approx(10000, abs=1)
    a1, a2 = (G.outer_area_m2(out[i]["geometry"]) for i in ("21", "22"))
    assert a1 == pytest.approx(a2, rel=1e-9) and a1 + 10 * a2 == pytest.approx(11000 * G.SQFT_TO_M2, rel=1e-9)


def test_null_asset_id_is_its_own_asset():
    two = [feat("31", None, [rect(0, 0, 30, 30)]), feat("32", None, [rect(200, 0, 30, 30)])]
    out = run(two, 19374, 1)   # 1,800 m2 each; pooled as one asset each would stay at its 900 m2 share
    for i in ("31", "32"):
        assert G.outer_area_m2(out[i]["geometry"]) == pytest.approx(19374 * G.SQFT_TO_M2, rel=1e-9)
        assert out[i]["properties"]["floor_area"] == 19374


def test_rows_that_are_not_simulated_do_not_take_a_share():
    """A parking row and a weatherless row of the same asset are dropped by process_feature, so the office wing
    carries the whole area."""
    rows = [feat("41", "4", [rect(0, 0, 30, 30)]), feat("42", "4", [rect(100, 0, 30, 30)]), feat("43", "4", [rect(0, 100, 30, 30)])]
    out = run(rows, 20000, 1, types={"41": "Office", "42": "Covered Parking", "43": "Office"},
              weather={"41": WX, "42": WX, "43": (None, None)})
    assert out["42"] is None and out["43"] is None
    assert G.outer_area_m2(out["41"]["geometry"]) == pytest.approx(20000 * G.SQFT_TO_M2, rel=1e-9)
    assert out["41"]["properties"]["floor_area"] == 20000


def test_holes_do_not_count():
    """urban_geometry_creation_zoning builds the outer ring only: a courtyard building is fitted on its outer area."""
    f = run([feat("51", "5", [rect(0, 0, 80, 80), rect(20, 20, 40, 40)])], 40000, 2)["51"]
    assert G.outer_area_m2(f["geometry"]) == pytest.approx(40000 / 2 * G.SQFT_TO_M2, rel=1e-9)


@pytest.mark.parametrize("mid", [False, True])
def test_collapse_falls_back_to_a_centred_square(mid):
    """A narrow outline shrunk under the 1 m merge, with or without a collinear midpoint (3 points that remain are
    collinear and not a floor print), falls back to a square centred on the outline."""
    ring = rect(0, 0, 40, 1.2, mid=mid)
    g = {"type": "Polygon", "coordinates": [ring]}
    assert G.fit_footprint_to_area(g, 0.4, 7.68, "narrow") == "square"
    assert G.outer_area_m2(g) == pytest.approx(7.68, rel=1e-9)
    assert G._ring_center(g["coordinates"][0]) == pytest.approx(G._ring_center(ring), abs=1e-12)
    assert G._floor_print_area_m2(g["coordinates"][0]) > 0


def test_below_resolution_keeps_the_outline():
    ring = rect(0, 0, 1.0, 1.0)
    g = {"type": "Polygon", "coordinates": [ring]}
    assert G.fit_footprint_to_area(g, 0.5, 0.25, "well") == "kept"
    assert g["coordinates"] == [ring]


def test_residential_polygon_untouched():
    ring = rect(0, 0, 12, 10)
    f = run([feat("61", "6", [ring])], 5000, 1, types={"61": "Single-Family Detached"})["61"]
    assert f["geometry"]["coordinates"][0] == ring


def test_ledger_constants():
    assert (G.SQFT_TO_M2, G.WGS84_A_M, G.WGS84_INV_F, G.ZONING_MERGE_TOL_M, G.AREA_FIT_TOL) == \
        (0.09290304, 6378137.0, 298.257223563, 1.0, 0.01)
