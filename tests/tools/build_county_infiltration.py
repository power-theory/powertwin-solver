"""CR campaign WP-R3: county-resolved envelope infiltration from ResStock 2024.2.

in.infiltration labels are 'N ACH50' (air changes per hour at 50 Pa, whole-dwelling).
Phase-1 Q4 verified the delivery semantics: BuildResidentialHPXML's air_leakage args
accept ACH at 50 Pa directly (air_leakage_units='ACH', air_leakage_house_pressure=50)
and util.rb's 'unit total' convention matches ResStock's whole-unit basis, so labels
transcribe with no unit conversion - parse N and pass it through.

Vintage-binned (infiltration is a construction-era property; leakier stock is older),
mirroring build_county_ducts.py's binning.

Run from repo root: ./venv/bin/python tests/tools/build_county_infiltration.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_county_priors import RES_FLOORS, build_three_tier, emit, share_cell  # noqa: E402

PARQUET = "/mnt/h/data/resstock/baseline.parquet"
OUT = "solver/upload/reference_data/resstock2024_county_infiltration"

VBIN = {"<1940": "pre-1960", "1940s": "pre-1960", "1950s": "pre-1960",
        "1960s": "1960-79", "1970s": "1960-79", "1980s": "1980-99", "1990s": "1980-99",
        "2000s": "2000-09", "2010s": "2010+"}


def infiltration_cell(sub):
    cell = {}
    for vb, vsub in sub.groupby("vbin"):
        if len(vsub) < 5:
            continue
        s = share_cell(vsub, "in.infiltration")
        if s:
            cell[vb] = s
    s = share_cell(sub, "in.infiltration")
    if s:
        cell["all"] = s
    return cell or None


def main():
    cols = ["in.county", "in.puma", "in.census_division_recs", "in.vintage",
            "in.infiltration", "in.sqft", "in.geometry_building_type_recs", "weight"]
    df = pd.read_parquet(PARQUET, columns=cols)
    df = df[df["in.infiltration"].notna()].copy()
    df["vbin"] = df["in.vintage"].map(VBIN).fillna("2010+")

    # Domain stamp (WP-R3b): the ACH50 labels are only defined on the unit floor
    # areas ResStock actually modeled. Whole-dwelling ACH50 on a larger volume is
    # an extrapolation with energy scaling as value x volume (the tetonall
    # regression: lodges classed SFD drew home-scale ACH50). The resolver refuses
    # to serve a unit larger than the largest donor of its building type; the
    # bounds are COMPUTED from the donor rows here, never hand-chosen.
    sqft = pd.to_numeric(df["in.sqft"], errors="coerce")
    domain = {"max_unit_sqft": {str(bt): int(v) for bt, v in
                                sqft.groupby(df["in.geometry_building_type_recs"]).max().items()}}
    domain["max_unit_sqft"]["_overall"] = int(sqft.max())
    tiers = build_three_tier(
        df, infiltration_cell,
        county_col="in.county", puma_col="in.puma",
        division_col="in.census_division_recs", floors=RES_FLOORS)
    source = {
        "name": "NREL ResStock 2024.2 realized building-stock metadata",
        "url": ("https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/"
                "end-use-load-profiles-for-us-building-stock/2024/"
                "resstock_tmy3_release_2/metadata/baseline.parquet"),
        "variables": "in.infiltration ('N ACH50', whole-dwelling ACH at 50 Pa)",
        "unit_rule": ("pass-through: HPXML air_leakage_units='ACH' at "
                      "air_leakage_house_pressure=50; no conversion (Phase-1 Q4)"),
        "keying": "county fips x vintage bin; precomputed puma-union and division tiers",
        "domain_rule": ("_domain.max_unit_sqft holds the LARGEST donor unit floor "
                        "area per in.geometry_building_type_recs (computed from "
                        "in.sqft, WP-R3b); the resolver only serves units within it"),
        "extracted": "2026-09-04",
        "regen": "tests/tools/build_county_infiltration.py",
    }
    tiers["_domain"] = domain
    raw, gz = emit(OUT, source, RES_FLOORS, tiers)
    print("domain:", domain["max_unit_sqft"])
    counties = tiers["counties"]
    print(f"counties: {len(counties)}; raw {raw/1e6:.1f} MB, gz {gz/1e6:.2f} MB")
    t = counties.get("56039", {})
    c = t.get("county")
    if c:
        print(f"Teton county n={c['_n']}, all-vintage mode = {c['all']['mode']}")


if __name__ == "__main__":
    main()
