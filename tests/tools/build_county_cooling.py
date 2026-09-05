"""CR campaign WP-R5: county cooling saturation from ResStock 2024.2.

NON-heat-pump homes only: ResStock ties heat-pump cooling to heat-pump heating
deterministically (86,347 + 5,358 rows, zero exceptions), and the WP-R2 heating
donor already decides heat pumps - so this table draws {Central AC, Room AC,
None} JOINTLY with the partial-conditioning fraction for everyone else.
'None' = no mechanical cooling (10.1% nationally, 27% of Teton's stock - the
None-as-value finding; the current pipeline builds central AC nearly everywhere
below CZ 7).

Short keys: ct (cooling type), pf (partial fraction label).

Run from repo root: ./venv/bin/python tests/tools/build_county_cooling.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_county_priors import RES_FLOORS, build_three_tier, donor_cell, emit  # noqa: E402

PARQUET = "/mnt/h/data/resstock/baseline.parquet"
OUT = "solver/upload/reference_data/resstock2024_county_cooling"

VBIN = {"<1940": "pre-1960", "1940s": "pre-1960", "1950s": "pre-1960",
        "1960s": "1960-79", "1970s": "1960-79", "1980s": "1980-99", "1990s": "1980-99",
        "2000s": "2000-09", "2010s": "2010+"}


def cooling_cell(sub):
    cell = {}
    for vb, vsub in sub.groupby("vbin"):
        if len(vsub) < 5:
            continue
        cell[vb] = donor_cell(vsub, ["ct", "pf"], cap=40)
    cell["all"] = donor_cell(sub, ["ct", "pf"], cap=40)
    return cell


def main():
    cols = ["in.county", "in.puma", "in.census_division_recs", "in.vintage",
            "in.hvac_cooling_type", "in.hvac_cooling_partial_space_conditioning", "weight"]
    df = pd.read_parquet(PARQUET, columns=cols)
    df = df[~df["in.hvac_cooling_type"].astype(str).str.contains("Heat Pump")].copy()
    df = df[df["in.hvac_cooling_type"].notna()]
    df = df.rename(columns={"in.hvac_cooling_type": "ct",
                            "in.hvac_cooling_partial_space_conditioning": "pf"})
    df["vbin"] = df["in.vintage"].map(VBIN).fillna("2010+")
    tiers = build_three_tier(
        df, cooling_cell,
        county_col="in.county", puma_col="in.puma",
        division_col="in.census_division_recs", floors=RES_FLOORS)
    source = {
        "name": "NREL ResStock 2024.2 realized building-stock metadata",
        "url": ("https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/"
                "end-use-load-profiles-for-us-building-stock/2024/"
                "resstock_tmy3_release_2/metadata/baseline.parquet"),
        "variables": ("JOINT donors of (in.hvac_cooling_type [ct], "
                      "in.hvac_cooling_partial_space_conditioning [pf]), heat-pump rows "
                      "EXCLUDED (HP cooling is decided by the WP-R2 heating donor)"),
        "keying": "county fips x vintage bin; puma-union and division tiers",
        "extracted": "2026-09-04",
        "regen": "tests/tools/build_county_cooling.py",
    }
    raw, gz = emit(OUT, source, RES_FLOORS, tiers)
    print(f"counties: {len(tiers['counties'])}; raw {raw/1e6:.1f} MB, gz {gz/1e6:.2f} MB")
    c = tiers["counties"].get("56039", {}).get("county")
    if c:
        top = c["all"]["donors"][:3]
        print("Teton county top donors:", [(d["ct"], d["pf"], d["w"]) for d in top])


if __name__ == "__main__":
    main()
