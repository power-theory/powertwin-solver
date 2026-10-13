"""CR campaign WP-00: unit tests for the three-tier county-prior lookup and the gz
reference loader. Uses a synthetic table so the tests are independent of any shipped
county data; boundary cases pin the owner-ratified floor semantics exactly."""
import gzip
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))

from modules.simulation import sim_params_spec as sps  # noqa: E402

TABLE = {
    "_source": {"name": "synthetic"},
    "_floors": {"county": 30, "puma": 30, "division": 1},
    "counties": {
        "56039": {"county": {"_n": 30, "v": "county-val"},
                   "puma_ref": "P1", "division_ref": "D"},
        "56011": {"county": {"_n": 29, "v": "county-val"},
                   "puma_ref": "P1", "division_ref": "D"},
        "56000": {"county": {"_n": 3, "v": "county-val"},
                   "puma_ref": "P-thin", "division_ref": "D"},
        "56001": {"county": {"_n": 1, "v": "county-val"},
                   "division_ref": "D"},
        "56002": {"county": {"_n": 2, "v": "county-val"},
                   "puma_union_ref": "P1|P2", "division_ref": "D"},
        "56003": {"county": {"_n": 1, "v": "county-val"},
                   "state_ref": "56", "division_ref": "D"},
    },
    "_pumas": {"P1": {"_n": 220, "v": "puma-val"},
                "P-thin": {"_n": 5, "v": "puma-val"}},
    "_puma_unions": {"P1|P2": {"_n": 300, "v": "union-val"}},
    "_states": {"56": {"_n": 900, "v": "state-val"}},
    "_divisions": {"D": {"_n": 5000, "v": "div-val"}},
}


@pytest.fixture()
def ref_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sps, "_REF_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sps, "_REF_CACHE", {})
    return tmp_path


def _write_gz(ref_dir, name, doc):
    with gzip.open(os.path.join(ref_dir, f"{name}.json.gz"), "wt") as f:
        json.dump(doc, f)


def test_tier_walk_and_floor_boundaries(ref_dir):
    _write_gz(ref_dir, "syn_table", TABLE)
    cell, tier = sps._county_prior("syn_table", "56039")
    assert (tier, cell["v"]) == ("county", "county-val"), "n == floor must pass (>=)"
    cell, tier = sps._county_prior("syn_table", "56011")
    assert (tier, cell["v"]) == ("puma", "puma-val"), "thin county -> referenced puma cell"
    cell, tier = sps._county_prior("syn_table", "56000")
    assert (tier, cell["v"]) == ("division", "div-val"), "thin puma -> referenced division"
    cell, tier = sps._county_prior("syn_table", "56001")
    assert (tier, cell["v"]) == ("division", "div-val"), "no puma ref -> division"
    cell, tier = sps._county_prior("syn_table", "56003")
    assert (tier, cell["v"]) == ("state", "state-val"), "thin county, no puma -> its state (pooled tables, 3.4)"
    tiers = [t for _, t in sps._county_prior_tiers("syn_table", "56003")]
    assert tiers == ["state", "division"]
    cell, tier = sps._county_prior("syn_table", "56002")
    assert (tier, cell["v"]) == ("puma", "union-val"), "multi-puma county -> union cell"


def test_unknown_county_and_empty_fips(ref_dir):
    _write_gz(ref_dir, "syn_table", TABLE)
    assert sps._county_prior("syn_table", "99999") == (None, None)
    assert sps._county_prior("syn_table", None) == (None, None)
    assert sps._county_prior("syn_table", "") == (None, None)


def test_load_ref_gz_and_plain_precedence(ref_dir):
    _write_gz(ref_dir, "dual", {"who": "gz"})
    assert sps._load_ref("dual")["who"] == "gz"
    sps._REF_CACHE.clear()
    with open(os.path.join(ref_dir, "dual.json"), "w") as f:
        json.dump({"who": "plain"}, f)
    assert sps._load_ref("dual")["who"] == "plain", (
        "a plain .json override must win over the .gz form"
    )


def test_existing_reference_loading_unchanged():
    # the gz extension must not disturb the shipped plain-JSON tables
    refs = sps._load_ref("census_regions")
    assert refs, "existing plain reference failed to load through the extended loader"


def test_builder_core_three_tier_and_cells():
    """Synthetic end-to-end: builder core -> emitted schema -> _county_prior walk."""
    import pandas as pd
    sys.path.insert(0, os.path.join(REPO, "tests", "tools"))
    import build_county_priors as bcp

    df = pd.DataFrame({
        "county": ["G5600390"]*40 + ["G5600110"]*10,
        "puma": ["P1"]*40 + ["P1"]*10,          # both counties share PUMA P1
        "division": ["Mountain North"]*50,
        "val": (["a"]*30 + ["b"]*10) + ["b"]*10,
        "weight": [1.0]*50,
    })
    tiers = bcp.build_three_tier(
        df, lambda sub: bcp.share_cell(sub, "val"),
        county_col="county", puma_col="puma", division_col="division",
        floors=bcp.RES_FLOORS, pool=False)
    counties = tiers["counties"]
    assert counties["56039"]["state_ref"] == "56" and tiers["_states"]["56"]["_n"] == 50
    # gisjoin normalization
    assert set(counties) == {"56039", "56011"}
    # county tier n and shares (residual folded into mode, sums to 1)
    c = counties["56039"]["county"]
    assert c["_n"] == 40 and abs(sum(c["shares"].values()) - 1.0) < 1e-9
    assert c["shares"]["a"] == 0.75
    # shared puma cell stored once: both counties reference P1 (n=50, b=20/50)
    assert counties["56039"]["puma_ref"] == "P1" == counties["56011"]["puma_ref"]
    p = tiers["_pumas"]["P1"]
    assert p["_n"] == 50 and p["shares"]["b"] == 0.4
    # thin county falls to puma at runtime
    import gzip as _g, json as _j, tempfile
    with tempfile.TemporaryDirectory() as td:
        bcp.emit(os.path.join(td, "syn2"), {"name": "synthetic"}, bcp.RES_FLOORS, tiers)
        doc = _j.load(_g.open(os.path.join(td, "syn2.json.gz"), "rt"))
        import importlib
        sps2 = sps
        old_dir, old_cache = sps2._REF_DATA_DIR, sps2._REF_CACHE
        try:
            sps2._REF_DATA_DIR, sps2._REF_CACHE = td, {}
            cell, tier = sps2._county_prior("syn2", "56011")
            assert tier == "puma" and cell["_n"] == 50, "thin county (n=10) must fall to PUMA"
            cell, tier = sps2._county_prior("syn2", "56039")
            assert tier == "county" and cell["_n"] == 40
        finally:
            sps2._REF_DATA_DIR, sps2._REF_CACHE = old_dir, old_cache


def test_builder_donor_cell_joint_consistency():
    import pandas as pd
    sys.path.insert(0, os.path.join(REPO, "tests", "tools"))
    import build_county_priors as bcp
    df = pd.DataFrame({
        "equip": ["furnace", "furnace", "baseboard", "baseboard"],
        "ducts": ["Yes", "Yes", "No", "Yes"],
        "weight": [6.0, 2.0, 1.0, 1.0],
    })
    cell = bcp.donor_cell(df, ["equip", "ducts"])
    donors = cell["donors"]
    assert abs(sum(d["w"] for d in donors) - 1.0) < 1e-6
    top = donors[0]
    assert (top["equip"], top["ducts"], top["w"]) == ("furnace", "Yes", 0.8), (
        "identical joint rows must collapse with summed weight"
    )
    combos = {(d["equip"], d["ducts"]) for d in donors}
    assert ("baseboard", "No") in combos and ("baseboard", "Yes") in combos, (
        "joint (not marginal) structure must survive"
    )


def test_county_fips_is_derived_offline_from_the_shipped_boundaries(monkeypatch):
    """2026-10-03: the FCC Census Block API this used fails every request (its database rejects the service's login),
    so each building without county_fips spent ~2.4 s on retries and still resolved without a county. The derivation
    now reads urbanopt/us_counties.geojson and never touches the network."""
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    monkeypatch.setattr(sps, "_FIPS_CACHE", {})
    assert sps._derive_county_fips(43.48, -110.76) == "56039"      # Jackson, WY: Teton County
    assert sps._derive_county_fips(34.6, -78.5) == "37017"         # Bladen County, NC
    assert sps._derive_county_fips(40.0, -60.0) is None            # Atlantic
    assert sps._derive_county_fips("inf", 1) is None and sps._derive_county_fips(None, 1) is None
    ctx = sps.build_asset_ctx({"latitude": 43.48, "longitude": -110.76, "state": "WY", "area": 2000},
                              building_type="Single-Family Detached")
    assert ctx["county_fips"] == "56039"


# --- pooling (County Prior Pooling Plan, 2026-10-11) ----------------------------------------------------------------

def _bcp():
    sys.path.insert(0, os.path.join(REPO, "tests", "tools"))
    import build_county_priors as bcp
    return bcp


def test_blend_limits_and_donor_weights():
    bcp = _bcp()
    child = {"mode": "a", "shares": {"a": 0.8, "b": 0.2}, "_n": 10}
    parent = {"mode": "b", "shares": {"a": 0.3, "b": 0.6, "c": 0.1}, "_n": 1000}
    near_child = bcp._blend_leaf(child, parent, 1e-9)
    assert near_child["shares"] == {"a": 0.8, "b": 0.2} and near_child["_n"] == 10
    near_parent = bcp._blend_leaf(child, parent, 1e9)
    assert near_parent["shares"] == {"a": 0.3, "b": 0.6, "c": 0.1} and near_parent["mode"] == "b"
    half = bcp._blend_leaf(child, parent, 10)          # n = k: the midpoint
    assert half["shares"] == {"a": 0.55, "b": 0.4, "c": 0.05} and half["mode"] == "a"
    dc = {"donors": [{"w": 1.0, "x": "1"}], "_n": 5}
    dp = {"donors": [{"w": 0.5, "x": "1"}, {"w": 0.5, "x": "2"}], "_n": 500}
    out = bcp._blend_leaf(dc, dp, 5)
    w = {d["x"]: d["w"] for d in out["donors"]}
    assert w == {"1": 0.75, "2": 0.25} and abs(sum(w.values()) - 1) < 1e-9


def _leaf_from(rng, probs, n):
    counts = rng.multinomial(n, probs)
    return {"shares": {str(i): c / n for i, c in enumerate(counts) if c}, "_n": float(n)}


@pytest.mark.parametrize("conc", [None, 20.0])
def test_estimate_rho_recovers_the_between_area_share(conc):
    """No signal: children are samples of the parent mix, rho ~ 0. Signal: each child's true mix is
    Dirichlet(conc * pi), so rho = 1 / (conc + 1) ~ 0.048."""
    import numpy as np
    bcp = _bcp()
    rng = np.random.default_rng(7)
    pi = np.array([0.5, 0.3, 0.15, 0.05])
    groups = []
    for _ in range(40):
        kids, rows = [], np.zeros(4)
        for _ in range(25):
            theta = pi if conc is None else rng.dirichlet(conc * pi)
            n = int(rng.integers(10, 80))
            counts = rng.multinomial(n, theta)
            rows += counts
            kids.append({"v": {"shares": {str(i): c / n for i, c in enumerate(counts) if c}, "_n": float(n)}})
        parent = {"v": {"shares": {str(i): c / rows.sum() for i, c in enumerate(rows) if c}, "_n": float(rows.sum())}}
        groups.append((parent, kids))
    (part, (rho, m)), = bcp.estimate_rho(groups).items()
    assert part == "|shares" and m == 1000
    want = 0.0 if conc is None else 1 / (conc + 1)
    assert abs(rho - want) < 0.01, rho


def test_child_equal_to_parent_contributes_nothing():
    """A residential county that is its whole PUMA (f = 1, no siblings) must not bias rho negative."""
    bcp = _bcp()
    leaf = {"v": {"shares": {"a": 0.7, "b": 0.3}, "_n": 50.0}}
    assert bcp.estimate_rho([(leaf, [leaf])]) == {"|shares": (0.0, 1)}


def _synthetic_stock(signal, seed=3):
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(seed)
    rows = []
    for st in ("56", "16", "30", "49"):
        for c in range(30):
            probs = rng.dirichlet([3, 2, 1]) if signal else [0.5, 0.3, 0.2]
            vals = rng.choice(["a", "b", "c"], size=60, p=probs)
            rows += [{"county": f"G{st}0{c:03d}0", "puma": f"P{st}", "division": "Mountain North", "val": v,
                      "weight": 1.0} for v in vals]
    return pd.DataFrame(rows)


def test_pooling_drops_or_keeps_the_county_tier_by_signal():
    bcp = _bcp()
    floors = dict(bcp.COM_FLOORS)
    fn = lambda sub: bcp.share_cell(sub, "val")
    noise = bcp.build_three_tier(_synthetic_stock(False), fn, "county", "puma", "division", floors, include_puma=False)
    pk = noise["_pooling"]["county"]["|shares"]
    # counties are noise: a 60-building county keeps at most a small weight against its state's mix
    assert pk["k"] is None or 60 / (60 + pk["k"]) < 0.25, pk
    real = bcp.build_three_tier(_synthetic_stock(True), fn, "county", "puma", "division", floors, include_puma=False)
    pk = real["_pooling"]["county"]["|shares"]
    assert pk["k"] is not None and pk["k"] < 20, pk
    raw = bcp.build_three_tier(_synthetic_stock(True), fn, "county", "puma", "division", floors, include_puma=False,
                               pool=False)
    for fips, e in real["counties"].items():
        a, b = e["county"]["shares"], raw["counties"][fips]["county"]["shares"]
        st = raw["_states"][e["state_ref"]]["shares"]
        for x in b:                                         # every share moved toward the state's, never past it
            lo, hi = sorted((b[x], st.get(x, 0.0)))
            assert lo - 0.002 <= a.get(x, 0.0) <= hi + 0.002


def test_pooled_build_is_reproducible():
    bcp = _bcp()
    fn = lambda sub: bcp.share_cell(sub, "val")
    one = bcp.build_three_tier(_synthetic_stock(True), fn, "county", "puma", "division", bcp.COM_FLOORS,
                               include_puma=False)
    two = bcp.build_three_tier(_synthetic_stock(True), fn, "county", "puma", "division", bcp.COM_FLOORS,
                               include_puma=False)
    assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)


def test_parts_get_their_own_k_and_a_dropped_part_walks_up():
    """pv-like cell: a has_pv share with real state signal and a donor bundle with none. One k for the table let the
    bundle swamp the share (resolver 3.4 review, 2026-10-11); each part is estimated and pooled on its own."""
    import numpy as np
    import pandas as pd
    bcp = _bcp()
    rng = np.random.default_rng(5)
    rows = []
    for st, p_yes in zip((f"{i:02d}" for i in range(10, 30)), np.linspace(0.02, 0.30, 20)):
        for c in range(6):
            for _ in range(60):
                rows.append({"county": f"G{st}0{c:03d}0", "puma": f"P{st}", "division": "Mountain North",
                             "has": "Yes" if rng.random() < p_yes else "No", "sz": str(rng.integers(1, 4)),
                             "weight": 1.0})
    df = pd.DataFrame(rows)

    def fn(sub):
        yes = sub[sub["has"] == "Yes"]
        cell = {"has_pv": bcp.share_cell(sub, "has")}
        if len(yes) >= 5:
            cell["sys"] = bcp.donor_cell(yes, ["sz"])
        return cell

    t = bcp.build_three_tier(df, fn, "county", "puma", "division", bcp.COM_FLOORS, include_puma=False)
    st = t["_pooling"]["state"]
    assert st["|shares"]["k"] is not None and st["|shares"]["k"] < 50, st
    assert st["|donors"]["k"] is None or st["|donors"]["k"] > st["|shares"]["k"] * 3, st
    if st["|donors"]["k"] is None:                       # dropped: the state cell keeps has_pv only
        assert all("sys" not in c for c in t["_states"].values())
        assert all("has_pv" in c for c in t["_states"].values())
