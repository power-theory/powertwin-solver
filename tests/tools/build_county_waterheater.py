"""CR campaign WP-R4: county water-heater donors from ResStock 2024.2.

One donor column, in.water_heater_efficiency, encodes SWH FUEL AND HEATER TYPE
jointly ('Natural Gas Tankless' -> gas + instantaneous), so a single draw keeps
the two channels coherent by construction. Cells are keyed by the HEATING gas
arm (gas iff in.heating_fuel == 'Natural Gas'), preserving P(gas SWH | gas heat)
without a resolver cycle (heating fuel never depends on the water heater).

Short key: whe. Label parsing lives in sim_params_spec._parse_wh_label.

Run from repo root: ./venv/bin/python tests/tools/build_county_waterheater.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_county_priors import RES_FLOORS, build_three_tier, donor_cell, emit  # noqa: E402

PARQUET = "/mnt/h/data/resstock/baseline.parquet"
OUT = "solver/upload/reference_data/resstock2024_county_waterheater"
ARM_MIN_N = 5


def wh_cell(sub):
    cell = {}
    for arm, asub in sub.groupby("gas_arm"):
        if len(asub) < ARM_MIN_N:
            continue
        cell[str(arm)] = donor_cell(asub, ["whe"], cap=30)
    return cell or None


def main():
    cols = ["in.county", "in.puma", "in.census_division_recs", "in.heating_fuel",
            "in.water_heater_efficiency", "weight"]
    df = pd.read_parquet(PARQUET, columns=cols)
    df = df.rename(columns={"in.water_heater_efficiency": "whe"})
    df = df[df["whe"].notna()]
    df["gas_arm"] = (df["in.heating_fuel"] == "Natural Gas").map({True: "gas", False: "nogas"})
    tiers = build_three_tier(
        df, wh_cell,
        county_col="in.county", puma_col="in.puma",
        division_col="in.census_division_recs", floors=RES_FLOORS)
    source = {
        "name": "NREL ResStock 2024.2 realized building-stock metadata",
        "url": ("https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/"
                "end-use-load-profiles-for-us-building-stock/2024/"
                "resstock_tmy3_release_2/metadata/baseline.parquet"),
        "variables": ("in.water_heater_efficiency donors [whe] - fuel AND type in one "
                      "label; keyed gas_arm = gas iff in.heating_fuel == Natural Gas"),
        "coherence": ("single draw keeps SWH fuel and heater type coherent; arm keying "
                      "preserves P(gas SWH | gas heat) with no resolver cycle"),
        "keying": "county fips x gas_arm; precomputed puma-union and division tiers",
        "extracted": "2026-09-04",
        "regen": "tests/tools/build_county_waterheater.py",
    }
    raw, gz = emit(OUT, source, RES_FLOORS, tiers)
    counties = tiers["counties"]
    print(f"counties: {len(counties)}; raw {raw/1e6:.1f} MB, gz {gz/1e6:.2f} MB")
    c = counties.get("56039", {}).get("county")
    if c:
        for arm in ("gas", "nogas"):
            if arm in c:
                top = c[arm]["donors"][0]
                print(f"Teton county {arm}: top donor whe={top['whe']} (w={top['w']})")


if __name__ == "__main__":
    main()
