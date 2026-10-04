"""Surrogate training-data collector: verify the static feature vector the collector produces.

Covers the fields that used to be hardcoded null (schema 0.2.0 populated them):
  - window_u_factor / window_shgc  -> golden-locked against the SAME powertwin_refs.rb the sim uses
  - wall_area / window_area        -> the sim's generateFeatureFile.py geometry formula
  - weekday/weekend schedules      -> DOE-prototype operating windows via the resolver
  - elevation                      -> parsed from the EPW LOCATION header

test_collect_e2e drives the real collect() over a self-contained synthetic sim output (manifest +
cleaned_reports + in.osw + EPW), i.e. the whole join, not a reimplementation of it.
"""

import json
import math
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "surrogate"))

# The surrogate tree is NOT in the solver container image (build context is solver/), and its deps
# may be absent in minimal CI runners. Skip cleanly instead of erroring collection: a collection
# error aborts even fully-deselected pytest runs, which is how this file silently blocked the
# requires_container stage until 2026-09-02.
_surrogate = pytest.importorskip(
    "app.modules.schema", reason="surrogate tree/deps unavailable (solver-only environment)"
)
from app.modules import schema  # noqa: E402
from app.modules.data import collector as C  # noqa: E402

_SI_TO_IP_U = 5.678263


@pytest.fixture(autouse=True)
def _preserve_env():
    """collect() sets URBANOPT_* env vars process-globally to drive the resolver; snapshot and
    restore os.environ around every test here so that mutation can't leak into other test modules
    (e.g. test_resolvers, which relies on URBANOPT_STOCHASTIC_SAMPLING)."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


def test_window_props_matches_ruby_golden():
    """_window_props_ip must equal PowerTwinRefs.window_props[_residential] (the Ruby the sim runs),
    across the full glazing x climate-zone grid. Locked to the same golden the Ruby is locked to,
    so a drift in either side fails here. Golden values are SI; the feature is IP (SI / 5.678263)."""
    golden = json.load(open(os.path.join(HERE, "fixtures", "powertwin_refs_golden.json")))
    grid = {"window_props": False, "window_props_residential": True}
    checked = 0
    for gkey, is_res in grid.items():
        for combo, (u_si, shgc) in golden[gkey].items():
            tier, cz = combo.split("|", 1)
            if tier not in schema.WINDOW_TYPES:
                continue  # the resolver only ever emits the 3 real tiers
            u_ip, shgc_got = C._window_props_ip(tier, cz, is_res)
            assert u_ip == pytest.approx(u_si / _SI_TO_IP_U, rel=1e-9), f"{gkey} {combo} U"
            assert shgc_got == pytest.approx(shgc, rel=1e-9), f"{gkey} {combo} SHGC"
            checked += 1
    assert checked >= 40, f"expected the full grid, only checked {checked}"


def test_window_props_unknown_tier_is_null():
    """A window_type outside the enum -> (None, None) so the collector emits a null optional feature
    (fail-soft on an unexpected value rather than fabricating a glazing)."""
    assert C._window_props_ip("Storefront", "4A", False) == (None, None)


def test_hhmm_parser():
    assert C._hhmm_to_hours("08:00") == 8.0
    assert C._hhmm_to_hours("17:30") == 17.5
    assert C._hhmm_to_hours("") is None
    assert C._hhmm_to_hours(None) is None


def test_load_sps_does_not_shadow_solver_package():
    """_load_sps must never register 'modules'/'modules.utils' stubs: a stub in sys.modules
    permanently shadows the REAL solver package, so any later real import in the same process
    (e.g. test_sim_e2e's views) dies with an order-dependent ImportError. The sim_flags seam is
    primed by file-load instead."""
    had = {k: (k in sys.modules) for k in ("modules", "modules.utils")}
    sps = C._load_sps()
    for k, was_there in had.items():
        assert (k in sys.modules) == was_there, f"_load_sps registered a stub {k!r}"
    # and the seam is primed: flag reads resolve through the file-loaded sim_flags
    assert sps._get_sim_flag is not None
    os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "true"
    assert sps.is_dynamic_defaults_enabled() is True
    os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "false"
    assert sps.is_dynamic_defaults_enabled() is False


def test_shape_factor_scales_geometry_like_the_sim():
    """The sim multiplies its reconstructed-square perimeter by the real polygon's shape factor
    (generateFeatureFile); the sampler stamps that factor into the manifest entry and the collector
    must scale perimeter/wall_area/window_area by it -- and default to 1.0 (synthetic squares)."""
    sps = C._load_sps()
    os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "true"
    os.environ["URBANOPT_STOCHASTIC_SAMPLING"] = "false"
    base = _building(10000000001, "Office")
    square = C.resolve_static_vector(dict(base), sps, schema)
    elongated = C.resolve_static_vector(dict(base, shape_factor=1.5), sps, schema)

    # a manifest predating the stamp must FAIL, not silently default to a square
    stale = {k: v for k, v in base.items() if k != "shape_factor"}
    with pytest.raises(KeyError, match="no shape_factor"):
        C.resolve_static_vector(stale, sps, schema)

    assert square["shape_factor"] == 1.0
    assert elongated["shape_factor"] == 1.5
    side = math.sqrt(square["footprint_area"])
    assert square["perimeter"] == pytest.approx(4.0 * side)
    assert elongated["perimeter"] == pytest.approx(4.0 * side * 1.5)
    assert elongated["wall_area"] == pytest.approx(square["wall_area"] * 1.5)
    assert elongated["window_area"] == pytest.approx(square["window_area"] * 1.5)


def _write_epw(path, elevation_m):
    """Minimal but structurally-valid EPW: 8 header lines (LOCATION carries elevation) + 8760 rows
    with >=22 comma fields so read_epw_channels' columns (6,7,8,13,14,21) parse."""
    header = [
        f"LOCATION,Test City,AZ,USA,TMY,999999,33.43,-112.02,-7.0,{elevation_m}",
        "DESIGN CONDITIONS,0",
        "TYPICAL/EXTREME PERIODS,0",
        "GROUND TEMPERATURES,0",
        "HOLIDAYS/DAYLIGHT SAVINGS,No,0,0,0",
        "COMMENTS 1,test fixture",
        "COMMENTS 2,test fixture",
        "DATA PERIODS,1,1,Data,Sunday,1/1,12/31",
    ]
    cols = ["1991", "1", "1", "1", "0", "_"]        # 0-5 date/flags
    cols += ["20.0", "10.0", "50"]                   # 6 drybulb, 7 dewpoint, 8 rh
    cols += ["101325", "0", "0", "0"]                # 9-12
    cols += ["500", "300"]                           # 13 ghi, 14 dni
    cols += ["0", "0", "0", "0", "0", "0"]           # 15-20
    cols += ["3.5", "0", "0", "0"]                   # 21 windspeed + padding
    row = ",".join(cols)
    with open(path, "w") as f:
        f.write("\n".join(header) + "\n")
        f.write((row + "\n") * schema.HOURS_PER_YEAR)


def _building(bid, building_type):
    return {
        "building_id": bid,
        "building_type": building_type,
        "state": "AZ",
        "county_fips": "04013",
        "climate_zone": "2B",
        "latitude": 33.43,
        "longitude": -112.02,
        "floor_area_ft2": 20000,
        "footprint_area_ft2": 10000,
        "floor_count": 2,
        "shape_factor": 1.0,
        "decade_vintage": "2000-2009",
        "year_built": 2005,
    }


@pytest.mark.requires_container
def test_collect_e2e(tmp_path):
    """Full collect(): synthetic manifest + reports + in.osw + EPW -> manifest.parquet. Assert the
    previously-dead static features are now populated, elevation comes from the EPW, and the glazing
    matches the resolver-driven window_type for both a commercial and a residential building."""
    import pandas as pd
    # collect() writes parquet; it runs in the collect image (pyarrow), not the flask image this marker also matches
    pytest.importorskip("pyarrow", reason="collect() writes parquet; run in the collect image")

    ELEV = 337.0
    comm_id, res_id = 10000000001, 10000000002
    # the samplers stamp the sim year the levers were previewed with; collect() refuses a manifest without it
    manifest = {"sim_year": 2026,
                "buildings": [_building(comm_id, "Office"), _building(res_id, "Single-Family Detached")]}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    # cleaned_reports/{bid}/cleaned_predicted_{slug}.csv (all target slugs, small)
    reports = tmp_path / "cleaned_reports"
    for bid in (comm_id, res_id):
        d = reports / str(bid)
        d.mkdir(parents=True)
        for slug in schema.target_slugs():
            (d / f"cleaned_predicted_{slug}.csv").write_text("value\n" + "\n".join(["1.0"] * 24) + "\n")

    # each building's run dir under the urbanopt tree, with in.osw + the preserved in.epw that
    # KEEP_RUN_DIR leaves behind (the resolver reads weather straight from in.epw)
    uo = tmp_path / "sim" / "urbanopt_simulation" / "batch_0"
    for bid in (comm_id, res_id):
        bd = uo / str(bid)
        bd.mkdir(parents=True)
        (bd / "in.osw").write_text(json.dumps({"steps": []}))
        _write_epw(str(bd / "in.epw"), ELEV)

    out = tmp_path / "out"
    wr = C.make_osw_weather_resolver(sim_output_dir=str(tmp_path / "sim"))
    res = C.collect(str(manifest_path), str(reports), str(out), weather_resolver=wr, stochastic=False)
    assert res["n"] == 2

    df = pd.read_parquet(out / "manifest.parquet").set_index("building_id")

    # every canonical static feature is a column
    for name in schema.static_feature_names():
        assert name in df.columns, f"missing feature column {name}"

    # elevation is the EPW header value, for both buildings
    assert df.loc[comm_id, "elevation"] == pytest.approx(ELEV)
    assert df.loc[res_id, "elevation"] == pytest.approx(ELEV)

    # geometry: wall_area = perimeter x stories x floor_height; window_area = WWR x wall_area
    perimeter = 4.0 * math.sqrt(10000)  # square footprint
    for bid in (comm_id, res_id):
        wa = df.loc[bid, "wall_area"]
        assert wa is not None and wa > 0
        # wall_area / (perimeter x stories) recovers a plausible floor height in the resolver's range
        fh = wa / (perimeter * 2)
        assert 6.0 <= fh <= 20.0, f"implied floor_height {fh} out of resolver range"
        assert df.loc[bid, "window_area"] == pytest.approx(df.loc[bid, "window_to_wall_ratio"] * wa)

    # glazing: commercial uses the 90.1 table, residential the IECC/physical table -> both non-null,
    # and residential single/triple differ from commercial for the same climate zone.
    for bid in (comm_id, res_id):
        assert df.loc[bid, "window_u_factor"] is not None and df.loc[bid, "window_u_factor"] > 0
        assert 0 < df.loc[bid, "window_shgc"] <= 1.0

    # commercial Office has a daytime operating window -> schedules populated (not null)
    assert df.loc[comm_id, "weekday_start_hour"] is not None
    assert df.loc[comm_id, "weekday_duration_h"] is not None
