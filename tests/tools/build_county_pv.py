"""CR campaign WP-R6: county rooftop-PV priors from ResStock 2024.2.

Two parts per tier cell:
- has_pv: share of {Yes, No} (national Yes ~0.9%; Teton county 0/55);
- sys: JOINT donors of (in.pv_system_size [sz], in.pv_orientation [orient])
  among has_pv == Yes rows (n >= 5 per tier or the part is omitted and the
  resolver walks down).

Retires the register's phantom-renewables row: PV becomes physically modeled
(pv_system_present + max power + azimuth) instead of not-modeled.

Run from repo root: ./venv/bin/python tests/tools/build_county_pv.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_county_priors import RES_FLOORS, build_three_tier, donor_cell, emit, share_cell  # noqa: E402

PARQUET = "/mnt/h/data/resstock/baseline.parquet"
OUT = "solver/upload/reference_data/resstock2024_county_pv"
SYS_MIN_N = 5


def pv_cell(sub):
    cell = {}
    s = share_cell(sub, "in.has_pv")
    if s:
        cell["has_pv"] = s
    yes = sub[sub["in.has_pv"] == "Yes"]
    if len(yes) >= SYS_MIN_N:
        cell["sys"] = donor_cell(yes, ["sz", "orient"], cap=40)
    return cell or None


def main():
    cols = ["in.county", "in.puma", "in.census_division_recs", "in.has_pv",
            "in.pv_system_size", "in.pv_orientation", "weight"]
    df = pd.read_parquet(PARQUET, columns=cols)
    df = df.rename(columns={"in.pv_system_size": "sz", "in.pv_orientation": "orient"})
    tiers = build_three_tier(
        df, pv_cell,
        county_col="in.county", puma_col="in.puma",
        division_col="in.census_division_recs", floors=RES_FLOORS)
    source = {
        "name": "NREL ResStock 2024.2 realized building-stock metadata",
        "url": ("https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/"
                "end-use-load-profiles-for-us-building-stock/2024/"
                "resstock_tmy3_release_2/metadata/baseline.parquet"),
        "variables": ("has_pv shares (in.has_pv) + JOINT donors of "
                      "(in.pv_system_size [sz], in.pv_orientation [orient]) | has_pv == Yes"),
        "keying": "county fips; precomputed puma-union and division tiers",
        "extracted": "2026-09-04",
        "regen": "tests/tools/build_county_pv.py",
    }
    raw, gz = emit(OUT, source, RES_FLOORS, tiers)
    counties = tiers["counties"]
    print(f"counties: {len(counties)}; raw {raw/1e6:.1f} MB, gz {gz/1e6:.2f} MB")
    t = counties.get("56039", {})
    c = t.get("county")
    if c:
        print(f"Teton county has_pv: {c.get('has_pv', {}).get('shares')}, sys: {'sys' in c}")
    d = tiers["_divisions"].get(t.get("division_ref", ""))
    if d and "sys" in d:
        top = d["sys"]["donors"][0]
        print(f"division sys top donor: {top['sz']} {top['orient']} (w={top['w']})")


if __name__ == "__main__":
    main()
