"""CR campaign WP-R5: county foundation + garage priors from ResStock 2024.2.

JOINT donors of (in.geometry_foundation_type [fnd], in.geometry_garage [gar]) -
one home's geometry stays coherent, and the foundation draw automatically feeds
the WP-R1 duct-location coherence remap (which reads the feature's
foundation_type). Labels are transcribed verbatim; the resolver maps them onto
the mapper's feature vocabulary ('Vented Crawlspace' -> 'crawlspace - vented').

Replaces: region-modal foundation (recs2020_envelope) and the garage_rate
hash (which always built a fixed 24 ft two-car garage - the F-01 failure class;
car counts from the donor size the garage 12 ft per car instead).

Run from repo root: ./venv/bin/python tests/tools/build_county_geometry.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_county_priors import RES_FLOORS, build_three_tier, donor_cell, emit  # noqa: E402

PARQUET = "/mnt/h/data/resstock/baseline.parquet"
OUT = "solver/upload/reference_data/resstock2024_county_geometry"


def geometry_cell(sub):
    return {"all": donor_cell(sub, ["fnd", "gar"], cap=60)}


def main():
    cols = ["in.county", "in.puma", "in.census_division_recs",
            "in.geometry_foundation_type", "in.geometry_garage", "weight"]
    df = pd.read_parquet(PARQUET, columns=cols)
    df = df.rename(columns={"in.geometry_foundation_type": "fnd", "in.geometry_garage": "gar"})
    df = df[df["fnd"].notna()]
    tiers = build_three_tier(
        df, geometry_cell,
        county_col="in.county", puma_col="in.puma",
        division_col="in.census_division_recs", floors=RES_FLOORS)
    source = {
        "name": "NREL ResStock 2024.2 realized building-stock metadata",
        "url": ("https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/"
                "end-use-load-profiles-for-us-building-stock/2024/"
                "resstock_tmy3_release_2/metadata/baseline.parquet"),
        "variables": "JOINT donors of (in.geometry_foundation_type [fnd], in.geometry_garage [gar])",
        "keying": "county fips; puma-union and division tiers",
        "extracted": "2026-09-04",
        "regen": "tests/tools/build_county_geometry.py",
    }
    raw, gz = emit(OUT, source, RES_FLOORS, tiers)
    counties = tiers["counties"]
    print(f"counties: {len(counties)}; raw {raw/1e6:.1f} MB, gz {gz/1e6:.2f} MB")
    c = counties.get("56039", {}).get("county")
    if c:
        top = c["all"]["donors"][0]
        print(f"Teton county top donor: fnd={top['fnd']} gar={top['gar']} (w={top['w']})")


if __name__ == "__main__":
    main()
