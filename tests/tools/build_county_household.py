"""CR campaign WP-R5: county household size priors (bedrooms + occupants) from
ResStock 2024.2, replacing the area/700 bedrooms formula and the bedrooms+1
occupants heuristic with realized joint draws.

Cell = {area_bin: donor list of (bedrooms [br], occupants [oc])} - bedrooms
condition on dwelling-unit floor area (ResStock's own bin edges), occupants stay
JOINT with bedrooms (a 4br/1-person retiree home and a 2br/4-person home are
both real and the formula produces neither). OCCUPIED stock only: in.occupants
== '0' is exactly the Vacant flag (66,373 rows, same as the setpoint finding);
'10+' is stored as 10.

Run from repo root: ./venv/bin/python tests/tools/build_county_household.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_county_priors import RES_FLOORS, build_three_tier, donor_cell, emit  # noqa: E402

PARQUET = "/mnt/h/data/resstock/baseline.parquet"
OUT = "solver/upload/reference_data/resstock2024_county_household"
BIN_MIN_N = 5


def household_cell(sub):
    cell = {}
    for ab, bsub in sub.groupby("in.geometry_floor_area"):
        if len(bsub) < BIN_MIN_N:
            continue
        cell[str(ab)] = donor_cell(bsub, ["br", "oc"], cap=60)
    return cell or None


def main():
    cols = ["in.county", "in.puma", "in.census_division_recs", "in.geometry_floor_area",
            "in.bedrooms", "in.occupants", "in.vacancy_status", "weight"]
    df = pd.read_parquet(PARQUET, columns=cols)
    df = df[df["in.vacancy_status"] == "Occupied"].copy()
    df["br"] = df["in.bedrooms"].astype(str)
    df["oc"] = df["in.occupants"].astype(str).str.replace("10+", "10", regex=False)
    tiers = build_three_tier(
        df, household_cell,
        county_col="in.county", puma_col="in.puma",
        division_col="in.census_division_recs", floors=RES_FLOORS)
    source = {
        "name": "NREL ResStock 2024.2 realized building-stock metadata",
        "url": ("https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/"
                "end-use-load-profiles-for-us-building-stock/2024/"
                "resstock_tmy3_release_2/metadata/baseline.parquet"),
        "variables": ("JOINT donors of (in.bedrooms [br], in.occupants [oc]) keyed by "
                      "in.geometry_floor_area bin | in.vacancy_status == Occupied"),
        "vacancy_rule": "occupants '0' is exactly the Vacant flag; vacancy is owned downstream",
        "keying": "county fips x dwelling-unit area bin; puma-union and division tiers",
        "extracted": "2026-09-04",
        "regen": "tests/tools/build_county_household.py",
    }
    raw, gz = emit(OUT, source, RES_FLOORS, tiers)
    counties = tiers["counties"]
    print(f"counties: {len(counties)}; raw {raw/1e6:.1f} MB, gz {gz/1e6:.2f} MB")
    c = counties.get("56039", {}).get("county")
    if c:
        b = c.get("1500-1999")
        if b:
            top = b["donors"][0]
            print(f"Teton county 1500-1999: n donors={len(b['donors'])}, top br={top['br']} oc={top['oc']} (w={top['w']})")


if __name__ == "__main__":
    main()
