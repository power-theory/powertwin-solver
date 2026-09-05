"""CR campaign WP-R1: county-resolved duct priors from ResStock 2024.2.

Scope (register): duct LOCATION and LEAKAGE/INSULATION conditioned on the home being
ducted (`in.hvac_has_ducts == 'Yes'`). Which homes are ducted at all stays with the
heating-equipment joint (WP-R2); until then these priors replace HPXML's worst-case
defaults for every system the mapper already builds as ducted (gas furnaces included).

Leakage label semantics are cited to ResStock's own options_lookup.tsv (v3.3.0):
"N% Leakage to Outside" = supply 2N/3 + return N/3 (e.g. 10% -> 0.067/0.033), with the
R value applied to BOTH supply and return insulation. Do not re-derive; parse the label
into total N and R here, split in the mapper by the cited 2/3:1/3 rule.

Run from repo root: ./venv/bin/python tests/tools/build_county_ducts.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_county_priors import RES_FLOORS, build_three_tier, emit, share_cell  # noqa: E402

PARQUET = "/mnt/h/data/resstock/baseline.parquet"
OUT = "solver/upload/reference_data/resstock2024_county_ducts"

VBIN = {"<1940": "pre-1960", "1940s": "pre-1960", "1950s": "pre-1960",
        "1960s": "1960-79", "1970s": "1960-79", "1980s": "1980-99", "1990s": "1980-99",
        "2000s": "2000-09", "2010s": "2010+"}
VINTAGES = ["pre-1960", "1960-79", "1980-99", "2000-09", "2010+"]


def duct_cell(sub):
    """Per-tier cell: vintage-binned location + leakage distributions, plus an 'all'
    vintage-agnostic fallback (mirrors build_water_heater_type.py's 'all' convention)."""
    cell = {}
    for vb, vsub in sub.groupby("vbin"):
        if len(vsub) < 5:
            continue
        loc = share_cell(vsub, "in.duct_location")
        leak = share_cell(vsub, "in.duct_leakage_and_insulation")
        if loc and leak:
            cell[vb] = {"location": loc, "leakage": leak}
    loc = share_cell(sub, "in.duct_location")
    leak = share_cell(sub, "in.duct_leakage_and_insulation")
    if loc and leak:
        cell["all"] = {"location": loc, "leakage": leak}
    return cell or None


def main():
    cols = ["in.county", "in.puma", "in.census_division_recs", "in.vintage",
            "in.hvac_has_ducts", "in.duct_location", "in.duct_leakage_and_insulation",
            "weight"]
    df = pd.read_parquet(PARQUET, columns=cols)
    df = df[df["in.hvac_has_ducts"] == "Yes"].copy()
    df["vbin"] = df["in.vintage"].map(VBIN).fillna("2010+")
    tiers = build_three_tier(
        df, duct_cell,
        county_col="in.county", puma_col="in.puma",
        division_col="in.census_division_recs", floors=RES_FLOORS)
    source = {
        "name": "NREL ResStock 2024.2 realized building-stock metadata",
        "url": ("https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/"
                "end-use-load-profiles-for-us-building-stock/2024/"
                "resstock_tmy3_release_2/metadata/baseline.parquet"),
        "variables": "in.duct_location, in.duct_leakage_and_insulation | in.hvac_has_ducts == Yes",
        "leakage_split": ("options_lookup.tsv v3.3.0: N% total = supply 2N/3 + return N/3, "
                          "insulation R on both sides"),
        "keying": "county fips x vintage bin, with precomputed puma-union and division tiers",
        "extracted": "2026-09-04",
        "regen": "tests/tools/build_county_ducts.py",
    }
    raw, gz = emit(OUT, source, RES_FLOORS, tiers)
    counties = tiers["counties"]
    print(f"counties: {len(counties)}; raw {raw/1e6:.1f} MB, gz {gz/1e6:.2f} MB -> {OUT}.json.gz")
    t = counties.get("56039", {})
    lookup = {"county": t.get("county"),
              "puma": tiers["_pumas"].get(t.get("puma_ref", ""), tiers["_puma_unions"].get(t.get("puma_union_ref", ""))),
              "division": tiers["_divisions"].get(t.get("division_ref", ""))}
    for tier in ("county", "puma", "division"):
        c = lookup.get(tier)
        if c:
            print(f"Teton {tier}: n={c['_n']}, all-vintage top location = "
                  f"{c['all']['location']['mode']}")


if __name__ == "__main__":
    main()
