"""CR campaign WP-C1: county-resolved commercial operating windows from ComStock 2024 R1.

Cells are keyed BY COMSTOCK BUILDING TYPE inside each geographic tier (ComStock's
type names ARE the DOE reference names the schedule resolver already speaks), with
opening time and operating hours drawn as a JOINT donor (one real building's window,
weekday and weekend together). County commercial samples are small (Teton n=21, and
per-type slices smaller), so a type appears in a tier's cell only with n >= 5 rows;
the resolver walks _county_prior_tiers until a tier carries the type.

Unlike the DOE-prototype fallback (which suppresses 24/7 and bimodal types because
its window is an occupancy-PEAK artifact), ComStock windows are actual open-to-close
operating hours, so ALL types are servable from this table.

Run from repo root: ./venv/bin/python tests/tools/build_county_hours.py
"""
import json
import os
import sys

import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_county_priors import COM_FLOORS, build_three_tier, donor_cell, emit  # noqa: E402

PARQUET = "/mnt/h/data/comstock/baseline_cols.parquet"
OUT = "solver/upload/reference_data/comstock2024_county_hours"
REGIONS = "solver/upload/reference_data/census_regions.json"

RAW_COLS = ["in.weekday_opening_time..hr", "in.weekday_operating_hours..hr",
            "in.weekend_opening_time..hr", "in.weekend_operating_hours..hr"]
# donor keys are stored VERBATIM per donor row, so short names keep the table small
# (the raw-name version weighed 110 MB raw; wo/wh/eo/eh = weekday/weekend open/hours)
JOINT_COLS = ["wo", "wh", "eo", "eh"]
TYPE_MIN_N = 5
HOURS_DONOR_CAP = 30  # quarter-hour joint windows converge fast; 200 donors x 14 types
                      # x 3 tiers x 3k counties made a 145 MB raw table (60 -> ~50 MB)


def hours_cell(sub):
    cell = {}
    for bt, bsub in sub.groupby("in.comstock_building_type"):
        if len(bsub) < TYPE_MIN_N:
            continue
        cell[str(bt)] = donor_cell(bsub, JOINT_COLS, cap=HOURS_DONOR_CAP)
    return cell or None


def main():
    cols = (["in.nhgis_county_gisjoin", "in.nhgis_puma_gisjoin", "in.state",
             "in.comstock_building_type", "weight"] + RAW_COLS)
    df = pq.read_table(PARQUET, columns=cols).to_pandas(ignore_metadata=True)
    df = df.rename(columns=dict(zip(RAW_COLS, JOINT_COLS)))
    for c in JOINT_COLS:
        df = df[df[c].notna()]
    s2d = json.load(open(REGIONS))["state_to_division"]
    df["division"] = df["in.state"].astype(str).map(s2d)
    df = df[df["division"].notna()]
    tiers = build_three_tier(
        df, hours_cell,
        county_col="in.nhgis_county_gisjoin", puma_col="in.nhgis_puma_gisjoin",
        division_col="division", floors=COM_FLOORS, include_puma=False)
    source = {
        "name": "NREL ComStock 2024 Release 1 realized building-stock metadata",
        "url": ("https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/"
                "end-use-load-profiles-for-us-building-stock/2024/"
                "comstock_amy2018_release_1/metadata/baseline.parquet"),
        "variables": ("JOINT donors of (weekday/weekend opening time, weekday/weekend "
                      "operating hours), decimal hours, keyed comstock_building_type "
                      "inside each tier (type needs n >= 5 rows per tier)"),
        "semantics": ("actual open-to-close windows (NOT occupancy-peak artifacts), "
                      "so all 14 types are servable including 24/7 and bimodal ones"),
        "keying": "county fips x building type; division tier only as fallback (PUMA omitted: hours are type-driven with weak sub-state geography, and the puma block tripled the table)",
        "extracted": "2026-09-04",
        "regen": "tests/tools/build_county_hours.py",
    }
    raw, gz = emit(OUT, source, COM_FLOORS, tiers)
    counties = tiers["counties"]
    print(f"counties: {len(counties)}; raw {raw/1e6:.1f} MB, gz {gz/1e6:.2f} MB")
    t = counties.get("56039", {})
    for tier_name, cell in (("county", t.get("county")),
                            ("division", tiers["_divisions"].get(t.get("division_ref", "")))):
        if cell:
            types = [k for k in cell if not k.startswith("_")]
            print(f"Teton {tier_name}: n={cell['_n']}, types served: {len(types)}")
            if "RetailStripmall" in cell:
                top = cell["RetailStripmall"]["donors"][0]
                print(f"  RetailStripmall top donor: open {top['wo']}h "
                      f"for {top['wh']}h (w={top['w']})")


if __name__ == "__main__":
    main()
