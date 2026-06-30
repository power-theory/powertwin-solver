"""Reliability: the resolver is deterministic. The same (metadata, building_id)
must resolve byte-identically across repeated runs and fresh module loads -- the
share-weighted draws are a pure function of the building_id MD5 hash, with no
RNG/clock/dict-ordering nondeterminism. This locks that property so a future change
that introduces nondeterminism fails loudly.
"""
import importlib.util
import os
from collections import Counter

os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "true"
os.environ["URBANOPT_STOCHASTIC_SAMPLING"] = "true"

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(REPO, "solver", "app", "modules", "simulation", "sim_params_spec.py")

SHARE_WEIGHTED = ["heating_system_fuel_type", "heating_system_type", "water_heater_type",
                  "service_water_heating_fuel_type", "window_type",
                  "wall_r_value", "roof_r_value", "wall_material", "roof_material",
                  "window_to_wall_ratio"]

CASES = [
    {"state": "IL", "year_built": 1965, "area": 2000},
    {"state": "TX", "year_built": 1990, "area": 2400},
    {"state": "CA", "year_built": 2015, "area": 1800},
    {"state": "MA", "year_built": 1972, "area": 2200},
]


def _load():
    spec = importlib.util.spec_from_file_location("sps_det", SPEC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _resolve_all(m, md, bid):
    ctx = m.build_asset_ctx(md, building_type="Single-Family Detached", building_id=bid)
    return {f: m.resolve_default(f, ctx)[0] for f in SHARE_WEIGHTED}


def test_same_building_resolves_identically_across_loads():
    m1, m2 = _load(), _load()  # two independent module loads
    for md in CASES:
        for i in range(50):
            bid = f"det{md['state']}{i:04d}"
            r1 = _resolve_all(m1, dict(md), bid)
            r2 = _resolve_all(m2, dict(md), bid)
            assert r1 == r2, f"nondeterministic across loads for {bid}: {r1} != {r2}"


def test_repeated_resolution_is_stable():
    m = _load()
    for md in CASES:
        bid = f"stable{md['state']}"
        first = _resolve_all(m, dict(md), bid)
        for _ in range(20):
            assert _resolve_all(m, dict(md), bid) == first, f"unstable resolution for {bid}"


def test_distribution_is_reproducible():
    # The whole share-weighted distribution over a building_id sweep is fixed, not
    # just per-building: same id set -> same value counts every run.
    m = _load()
    md = {"state": "IL", "year_built": 1990, "area": 2000}

    def sweep():
        c = Counter()
        for i in range(300):
            ctx = m.build_asset_ctx(md, building_type="Single-Family Detached", building_id=f"sw{i:04d}")
            c[m.resolve_default("wall_r_value", ctx)[0]] += 1
        return c

    assert sweep() == sweep()
