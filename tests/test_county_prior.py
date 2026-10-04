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
    },
    "_pumas": {"P1": {"_n": 220, "v": "puma-val"},
                "P-thin": {"_n": 5, "v": "puma-val"}},
    "_puma_unions": {"P1|P2": {"_n": 300, "v": "union-val"}},
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
        floors=bcp.RES_FLOORS)
    counties = tiers["counties"]
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
