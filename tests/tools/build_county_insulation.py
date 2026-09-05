"""CR campaign WP-R5: county wall/ceiling insulation R priors from ResStock 2024.2.

Labels parsed to nominal R: wall 'Wood Stud, R-11' -> 11 (Uninsulated -> 0,
material prefix dropped - wall_material stays DERIVED from the R draw via
_r_to_tier, the ledger invariant); ceiling 'R-30' -> 30, 'Uninsulated' -> 0,
'None' EXCLUDED (None = the home has no attic ceiling - the Phase-1 None-as-value
finding - so shares are conditional on an attic existing, the same conditioning
the TRG region table used). The five other surfaces (floor, foundation wall,
slab, rim joist, roof deck) are WP-R5b: new delivery channels, not re-keys.

Run from repo root: ./venv/bin/python tests/tools/build_county_insulation.py
"""
import os
import re
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_county_priors import RES_FLOORS, build_three_tier, emit, share_cell  # noqa: E402

PARQUET = "/mnt/h/data/resstock/baseline.parquet"
OUT = "solver/upload/reference_data/resstock2024_county_insulation"

VBIN = {"<1940": "pre-1960", "1940s": "pre-1960", "1950s": "pre-1960",
        "1960s": "1960-79", "1970s": "1960-79", "1980s": "1980-99", "1990s": "1980-99",
        "2000s": "2000-09", "2010s": "2010+"}


def parse_r(label):
    s = str(label)
    if s in ("None", "nan"):
        return None
    if "Uninsulated" in s:
        return "0"
    m = re.search(r"R-(\d+)", s)
    return m.group(1) if m else None


def insulation_cell(sub):
    cell = {}
    for vb, vsub in sub.groupby("vbin"):
        if len(vsub) < 5:
            continue
        entry = {}
        w = share_cell(vsub[vsub["wall_r"].notna()], "wall_r")
        c = share_cell(vsub[vsub["ceil_r"].notna()], "ceil_r")
        if w:
            entry["wall"] = w
        if c:
            entry["ceiling"] = c
        if entry:
            cell[vb] = entry
    entry = {}
    w = share_cell(sub[sub["wall_r"].notna()], "wall_r")
    c = share_cell(sub[sub["ceil_r"].notna()], "ceil_r")
    if w:
        entry["wall"] = w
    if c:
        entry["ceiling"] = c
    if entry:
        cell["all"] = entry
    return cell or None


def main():
    cols = ["in.county", "in.puma", "in.census_division_recs", "in.vintage",
            "in.insulation_wall", "in.insulation_ceiling", "weight"]
    df = pd.read_parquet(PARQUET, columns=cols)
    df["wall_r"] = df["in.insulation_wall"].map(parse_r)
    df["ceil_r"] = df["in.insulation_ceiling"].map(parse_r)
    df["vbin"] = df["in.vintage"].map(VBIN).fillna("2010+")
    tiers = build_three_tier(
        df, insulation_cell,
        county_col="in.county", puma_col="in.puma",
        division_col="in.census_division_recs", floors=RES_FLOORS)
    source = {
        "name": "NREL ResStock 2024.2 realized building-stock metadata",
        "url": ("https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/"
                "end-use-load-profiles-for-us-building-stock/2024/"
                "resstock_tmy3_release_2/metadata/baseline.parquet"),
        "variables": ("wall: in.insulation_wall parsed to nominal R (Uninsulated -> 0); "
                      "ceiling: in.insulation_ceiling parsed to R, 'None' (no attic) excluded "
                      "- shares conditional on an attic existing"),
        "keying": "county fips x vintage bin; puma-union and division tiers",
        "extracted": "2026-09-04",
        "regen": "tests/tools/build_county_insulation.py",
    }
    raw, gz = emit(OUT, source, RES_FLOORS, tiers)
    print(f"counties: {len(tiers['counties'])}; raw {raw/1e6:.1f} MB, gz {gz/1e6:.2f} MB")
    c = tiers["counties"].get("56039", {}).get("county")
    if c and "all" in c:
        print(f"Teton county all: wall {c['all'].get('wall', {}).get('shares')}")
        print(f"                  ceiling {c['all'].get('ceiling', {}).get('shares')}")


if __name__ == "__main__":
    main()
