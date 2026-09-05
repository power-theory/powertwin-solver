"""CR campaign WP-R4: county-resolved residential appliance fuels from ResStock 2024.2.

Cells are keyed by GAS AVAILABILITY inside each tier: appliance fuels ride the gas
line (measured on 2024.2: P(gas dryer | gas-heat-or-SWH home) = 0.249 vs 0.022
without; gas range 0.530 vs 0.099), so an unconditioned county draw would
decorrelate appliances from the WP-R2/SWH fuels - the same union-inflation defect
the tetonr1 arm exposed for water heating. At resolve time the pipeline knows the
home's resolved heating and SWH fuels and picks the matching arm.

All five appliance channels come from ONE donor (dryer, range, grill, gas lighting,
gas fireplace stay mutually coherent). Short keys: dr/rg/gr/gl/gf.

Run from repo root: ./venv/bin/python tests/tools/build_county_appliances.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_county_priors import RES_FLOORS, build_three_tier, donor_cell, emit  # noqa: E402

PARQUET = "/mnt/h/data/resstock/baseline.parquet"
OUT = "solver/upload/reference_data/resstock2024_county_appliances"

RAW = {"in.clothes_dryer": "dr", "in.cooking_range": "rg", "in.misc_gas_grill": "gr",
       "in.misc_gas_lighting": "gl", "in.misc_gas_fireplace": "gf"}
JOINT_COLS = list(RAW.values())
ARM_MIN_N = 5


def appliance_cell(sub):
    cell = {}
    for arm, asub in sub.groupby("gas_arm"):
        if len(asub) < ARM_MIN_N:
            continue
        cell[str(arm)] = donor_cell(asub, JOINT_COLS, cap=100)
    return cell or None


def main():
    cols = (["in.county", "in.puma", "in.census_division_recs", "in.heating_fuel",
             "in.water_heater_fuel", "weight"] + list(RAW))
    df = pd.read_parquet(PARQUET, columns=cols)
    df = df.rename(columns=RAW)
    df["gas_arm"] = (((df["in.heating_fuel"] == "Natural Gas") |
                      (df["in.water_heater_fuel"] == "Natural Gas"))
                     .map({True: "gas", False: "nogas"}))
    tiers = build_three_tier(
        df, appliance_cell,
        county_col="in.county", puma_col="in.puma",
        division_col="in.census_division_recs", floors=RES_FLOORS)
    source = {
        "name": "NREL ResStock 2024.2 realized building-stock metadata",
        "url": ("https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/"
                "end-use-load-profiles-for-us-building-stock/2024/"
                "resstock_tmy3_release_2/metadata/baseline.parquet"),
        "variables": ("JOINT donors of clothes_dryer [dr], cooking_range [rg], "
                      "misc_gas_grill [gr], misc_gas_lighting [gl], misc_gas_fireplace [gf], "
                      "keyed by gas_arm = gas iff heating OR SWH fuel is Natural Gas"),
        "coupling": ("conditioning measured on 2024.2: P(gas dryer|gas)=0.249 vs 0.022; "
                     "P(gas range|gas)=0.530 vs 0.099 - unconditioned draws would "
                     "decorrelate appliances from the WP-R2/SWH fuels"),
        "keying": "county fips x gas_arm; precomputed puma-union and division tiers",
        "extracted": "2026-09-04",
        "regen": "tests/tools/build_county_appliances.py",
    }
    raw, gz = emit(OUT, source, RES_FLOORS, tiers)
    counties = tiers["counties"]
    print(f"counties: {len(counties)}; raw {raw/1e6:.1f} MB, gz {gz/1e6:.2f} MB")
    t = counties.get("56039", {})
    c = t.get("county")
    if c:
        for arm in ("gas", "nogas"):
            if arm in c:
                top = c[arm]["donors"][0]
                print(f"Teton county {arm}: top donor dr={top['dr']} rg={top['rg']} (w={top['w']})")


if __name__ == "__main__":
    main()
