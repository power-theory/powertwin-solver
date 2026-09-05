"""CR campaign WP-R3: county-resolved thermostat setpoints from ResStock 2024.2.

OCCUPIED STOCK ONLY: in.heating_setpoint == '55F' iff in.vacancy_status == 'Vacant'
(verified exact on the full 2024.2 parquet, 66,373/66,373), and this pipeline applies
its own vacancy setback downstream, so vacant rows are excluded or the 55F mass would
double-count vacancy onto occupied homes.

Heating and cooling setpoints ship as a JOINT donor list (same-household setback
behavior is correlated across seasons); offsets (in.*_setpoint_offset_magnitude and
their period columns) are DEFERRED to WP-R3b - applying them needs 24-hour schedule
strings, and the zero-offset mode is 58% of occupied stock.

Run from repo root: ./venv/bin/python tests/tools/build_county_setpoints.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_county_priors import RES_FLOORS, build_three_tier, donor_cell, emit  # noqa: E402

PARQUET = "/mnt/h/data/resstock/baseline.parquet"
OUT = "solver/upload/reference_data/resstock2024_county_setpoints"

JOINT_COLS = ["in.heating_setpoint", "in.cooling_setpoint"]


def setpoint_cell(sub):
    """One vintage-agnostic joint donor list per tier (setpoints are occupant
    behavior, not construction era; vintage binning would thin county cells for
    no signal)."""
    return {"all": donor_cell(sub, JOINT_COLS)}


def main():
    cols = ["in.county", "in.puma", "in.census_division_recs", "in.vacancy_status",
            "weight"] + JOINT_COLS
    df = pd.read_parquet(PARQUET, columns=cols)
    df = df[df["in.vacancy_status"] == "Occupied"].copy()
    tiers = build_three_tier(
        df, setpoint_cell,
        county_col="in.county", puma_col="in.puma",
        division_col="in.census_division_recs", floors=RES_FLOORS)
    source = {
        "name": "NREL ResStock 2024.2 realized building-stock metadata",
        "url": ("https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/"
                "end-use-load-profiles-for-us-building-stock/2024/"
                "resstock_tmy3_release_2/metadata/baseline.parquet"),
        "variables": "JOINT donors of (in.heating_setpoint, in.cooling_setpoint) | in.vacancy_status == Occupied",
        "vacancy_rule": ("vacant rows excluded: 55F heating setpoint is exactly the "
                         "Vacant flag and vacancy setback is applied downstream by "
                         "this pipeline (verified 66,373/66,373 on 2024.2)"),
        "offsets": "deferred to WP-R3b (24-hour schedule strings)",
        "keying": "county fips; precomputed puma-union and division tiers",
        "extracted": "2026-09-04",
        "regen": "tests/tools/build_county_setpoints.py",
    }
    raw, gz = emit(OUT, source, RES_FLOORS, tiers)
    counties = tiers["counties"]
    print(f"counties: {len(counties)}; raw {raw/1e6:.1f} MB, gz {gz/1e6:.2f} MB")
    t = counties.get("56039", {})
    c = t.get("county")
    if c:
        top = c["all"]["donors"][0]
        print(f"Teton county n={c['_n']}, top donor = "
              f"{top['in.heating_setpoint']}/{top['in.cooling_setpoint']} (w={top['w']})")


if __name__ == "__main__":
    main()
