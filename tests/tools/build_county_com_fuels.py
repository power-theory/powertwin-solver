"""CR campaign WP-C1: county-resolved commercial fuels from ComStock 2024 R1.

Heating fuel and service-water-heating fuel ship as a JOINT donor per county x
building type - the WP-R2 lesson applied from day one: independent draws
decorrelate the two gas assignments and inflate the gas-anything union (that
defect cost Teton residential 28% -> 41% before the SWH coherence fix).

Keys are shortened (hf/wf) because donor keys repeat per row. CBECS division
tables remain the fallback below the county/division tiers (terminal tier per
the Phase-2 disposition).

Run from repo root: ./venv/bin/python tests/tools/build_county_com_fuels.py
"""
import json
import os
import sys

import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_county_priors import COM_FLOORS, build_three_tier, donor_cell, emit  # noqa: E402

PARQUET = "/mnt/h/data/comstock/baseline_cols.parquet"
OUT = "solver/upload/reference_data/comstock2024_county_fuels"
REGIONS = "solver/upload/reference_data/census_regions.json"

RAW_COLS = ["in.heating_fuel", "in.service_water_heating_fuel"]
JOINT_COLS = ["hf", "wf"]
TYPE_MIN_N = 5


def fuels_cell(sub):
    cell = {}
    for bt, bsub in sub.groupby("in.comstock_building_type"):
        if len(bsub) < TYPE_MIN_N:
            continue
        cell[str(bt)] = donor_cell(bsub, JOINT_COLS, cap=25)
    return cell or None


def main():
    cols = ["in.nhgis_county_gisjoin", "in.nhgis_puma_gisjoin", "in.state",
            "in.comstock_building_type", "weight"] + RAW_COLS
    df = pq.read_table(PARQUET, columns=cols).to_pandas(ignore_metadata=True)
    df = df.rename(columns=dict(zip(RAW_COLS, JOINT_COLS)))
    for c in JOINT_COLS:
        df = df[df[c].notna()]
    s2d = json.load(open(REGIONS))["state_to_division"]
    df["division"] = df["in.state"].astype(str).map(s2d)
    df = df[df["division"].notna()]
    tiers = build_three_tier(
        df, fuels_cell,
        county_col="in.nhgis_county_gisjoin", puma_col="in.nhgis_puma_gisjoin",
        division_col="division", floors=COM_FLOORS, include_puma=False)
    source = {
        "name": "NREL ComStock 2024 Release 1 realized building-stock metadata",
        "url": ("https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/"
                "end-use-load-profiles-for-us-building-stock/2024/"
                "comstock_amy2018_release_1/metadata/baseline.parquet"),
        "variables": "JOINT donors of (in.heating_fuel [hf], in.service_water_heating_fuel [wf]) keyed comstock_building_type per tier (type n >= 5)",
        "coherence": "joint draw preserves P(SWH fuel | heating fuel) - the WP-R2 decorrelation lesson applied at design time",
        "keying": "county fips x building type; division tier only (no PUMA); CBECS division remains the terminal fallback in the resolver",
        "extracted": "2026-09-04",
        "regen": "tests/tools/build_county_com_fuels.py",
    }
    raw, gz = emit(OUT, source, COM_FLOORS, tiers)
    counties = tiers["counties"]
    print(f"counties: {len(counties)}; raw {raw/1e6:.1f} MB, gz {gz/1e6:.2f} MB")
    t = counties.get("56039", {})
    for tier_name, cell in (("county", t.get("county")),
                            ("division", tiers["_divisions"].get(t.get("division_ref", "")))):
        if cell:
            types = [k for k in cell if not k.startswith("_")]
            print(f"Teton {tier_name}: n={cell['_n']}, types: {len(types)}")
            if "RetailStripmall" in cell:
                top = cell["RetailStripmall"]["donors"][0]
                print(f"  RetailStripmall top donor: heat={top['hf']} swh={top['wf']} (w={top['w']})")


if __name__ == "__main__":
    main()
