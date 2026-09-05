"""CR campaign WP-R5: county window-pane priors from ResStock 2024.2.

in.windows labels start with the pane class (Single/Double/Triple) - collapsed
onto the existing window_type enum (Single/Double/Triple Pane) so the resolver
and downstream U-factor/SHGC mapping stay unchanged; only the KEYING moves from
region x vintage to county-first. Vintage-binned (windows track era/retrofit).

Run from repo root: ./venv/bin/python tests/tools/build_county_windows.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_county_priors import RES_FLOORS, build_three_tier, emit, share_cell  # noqa: E402

PARQUET = "/mnt/h/data/resstock/baseline.parquet"
OUT = "solver/upload/reference_data/resstock2024_county_windows"

VBIN = {"<1940": "pre-1960", "1940s": "pre-1960", "1950s": "pre-1960",
        "1960s": "1960-79", "1970s": "1960-79", "1980s": "1980-99", "1990s": "1980-99",
        "2000s": "2000-09", "2010s": "2010+"}
PANE = {"Single": "Single Pane", "Double": "Double Pane", "Triple": "Triple Pane"}


def window_cell(sub):
    cell = {}
    for vb, vsub in sub.groupby("vbin"):
        if len(vsub) < 5:
            continue
        s = share_cell(vsub, "pane")
        if s:
            cell[vb] = s
    s = share_cell(sub, "pane")
    if s:
        cell["all"] = s
    return cell or None


def main():
    cols = ["in.county", "in.puma", "in.census_division_recs", "in.vintage",
            "in.windows", "weight"]
    df = pd.read_parquet(PARQUET, columns=cols)
    df = df[df["in.windows"].notna()].copy()
    df["pane"] = df["in.windows"].astype(str).str.split(",").str[0].str.strip().map(PANE)
    df = df[df["pane"].notna()]
    df["vbin"] = df["in.vintage"].map(VBIN).fillna("2010+")
    tiers = build_three_tier(
        df, window_cell,
        county_col="in.county", puma_col="in.puma",
        division_col="in.census_division_recs", floors=RES_FLOORS)
    source = {
        "name": "NREL ResStock 2024.2 realized building-stock metadata",
        "url": ("https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/"
                "end-use-load-profiles-for-us-building-stock/2024/"
                "resstock_tmy3_release_2/metadata/baseline.parquet"),
        "variables": "in.windows collapsed to pane class (Single/Double/Triple Pane)",
        "keying": "county fips x vintage bin; puma-union and division tiers",
        "extracted": "2026-09-04",
        "regen": "tests/tools/build_county_windows.py",
    }
    raw, gz = emit(OUT, source, RES_FLOORS, tiers)
    print(f"counties: {len(tiers['counties'])}; raw {raw/1e6:.1f} MB, gz {gz/1e6:.2f} MB")
    c = tiers["counties"].get("56039", {}).get("county")
    if c:
        print(f"Teton county all-vintage: {c['all']['shares']}")


if __name__ == "__main__":
    main()
