"""CR campaign WP-R2: county-resolved heating-equipment JOINT priors from ResStock.

Donor cells preserve the joint distribution of (equipment-and-fuel, has_ducts,
efficiency) per county x vintage bin - the Q1 finding made independent tables unsafe
(has_ducts is whole-home, wall furnaces are 31% ducted, etc.). At resolve time the
donor weights are RAKED to the ACS county fuel marginal (census marginals win where
they exist, owner-ratified), with the two correction rules: renormalize the ACS
shares (they can sum to ~0.997) and redistribute unservable marginal mass (an ACS
fuel with zero donors) proportionally across served fuels.

Run from repo root: ./venv/bin/python tests/tools/build_county_heating.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_county_priors import RES_FLOORS, build_three_tier, donor_cell, emit  # noqa: E402

PARQUET = "/mnt/h/data/resstock/baseline.parquet"
OUT = "solver/upload/reference_data/resstock2024_county_heating"

VBIN = {"<1940": "pre-1960", "1940s": "pre-1960", "1950s": "pre-1960",
        "1960s": "1960-79", "1970s": "1960-79", "1980s": "1980-99", "1990s": "1980-99",
        "2000s": "2000-09", "2010s": "2010+"}

JOINT_COLS = ["in.hvac_heating_type_and_fuel", "in.hvac_has_ducts",
              "in.hvac_heating_efficiency"]


def heating_cell(sub):
    """Vintage-binned donor lists plus an 'all' fallback."""
    cell = {}
    for vb, vsub in sub.groupby("vbin"):
        if len(vsub) < 5:
            continue
        cell[vb] = donor_cell(vsub, JOINT_COLS)
    cell["all"] = donor_cell(sub, JOINT_COLS)
    return cell


def main():
    cols = ["in.county", "in.puma", "in.census_division_recs", "in.vintage",
            "weight"] + JOINT_COLS
    df = pd.read_parquet(PARQUET, columns=cols)
    df = df[df["in.hvac_heating_type_and_fuel"].notna() &
            (df["in.hvac_heating_type_and_fuel"].astype(str) != "None")].copy()
    df["vbin"] = df["in.vintage"].map(VBIN).fillna("2010+")
    tiers = build_three_tier(
        df, heating_cell,
        county_col="in.county", puma_col="in.puma",
        division_col="in.census_division_recs", floors=RES_FLOORS)
    source = {
        "name": "NREL ResStock 2024.2 realized building-stock metadata",
        "url": ("https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/"
                "end-use-load-profiles-for-us-building-stock/2024/"
                "resstock_tmy3_release_2/metadata/baseline.parquet"),
        "variables": "JOINT donors of (in.hvac_heating_type_and_fuel, in.hvac_has_ducts, in.hvac_heating_efficiency)",
        "raking": ("applied at resolve time to the ACS county fuel marginal "
                   "(acs2022_county_fuel), with renormalization and proportional "
                   "redistribution of unservable mass (owner-ratified 2026-09-04)"),
        "keying": "county fips x vintage bin; precomputed puma-union and division tiers",
        "extracted": "2026-09-04",
        "regen": "tests/tools/build_county_heating.py",
    }
    raw, gz = emit(OUT, source, RES_FLOORS, tiers)
    counties = tiers["counties"]
    print(f"counties: {len(counties)}; raw {raw/1e6:.1f} MB, gz {gz/1e6:.2f} MB")
    t = counties.get("56039", {})
    lookup = {"county": t.get("county"),
              "puma": tiers["_pumas"].get(t.get("puma_ref", ""), tiers["_puma_unions"].get(t.get("puma_union_ref", ""))),
              "division": tiers["_divisions"].get(t.get("division_ref", ""))}
    for tier in ("county", "puma", "division"):
        c = lookup.get(tier)
        if c:
            top = c["all"]["donors"][0]
            print(f"Teton {tier}: n={c['_n']}, top donor = "
                  f"{top['in.hvac_heating_type_and_fuel']} (w={top['w']})")


if __name__ == "__main__":
    main()
