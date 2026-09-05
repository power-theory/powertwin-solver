"""CR campaign WP-C1: county-resolved commercial HVAC system type from ComStock 2024 R1.

Cells are share distributions of in.hvac_system_type keyed comstock_building_type
inside each tier (type needs n >= 5 rows), county -> division (no PUMA tier, same
rationale as the hours table). The resolver maps ComStock labels onto urbanopt's
system_type enum via COMSTOCK_HVAC_KEY in sim_params_spec.py (every ComStock label
mapped; a guard asserts the mapping lands inside the enum).

Run from repo root: ./venv/bin/python tests/tools/build_county_system.py
"""
import json
import os
import sys

import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_county_priors import COM_FLOORS, build_three_tier, emit, share_cell  # noqa: E402

PARQUET = "/mnt/h/data/comstock/baseline_cols.parquet"
OUT = "solver/upload/reference_data/comstock2024_county_system"
REGIONS = "solver/upload/reference_data/census_regions.json"
TYPE_MIN_N = 5


def system_cell(sub):
    cell = {}
    for bt, bsub in sub.groupby("in.comstock_building_type"):
        if len(bsub) < TYPE_MIN_N:
            continue
        s = share_cell(bsub, "in.hvac_system_type")
        if s:
            cell[str(bt)] = s
    return cell or None


def main():
    cols = ["in.nhgis_county_gisjoin", "in.nhgis_puma_gisjoin", "in.state",
            "in.comstock_building_type", "in.hvac_system_type", "weight"]
    df = pq.read_table(PARQUET, columns=cols).to_pandas(ignore_metadata=True)
    df = df[df["in.hvac_system_type"].notna()]
    s2d = json.load(open(REGIONS))["state_to_division"]
    df["division"] = df["in.state"].astype(str).map(s2d)
    df = df[df["division"].notna()]
    tiers = build_three_tier(
        df, system_cell,
        county_col="in.nhgis_county_gisjoin", puma_col="in.nhgis_puma_gisjoin",
        division_col="division", floors=COM_FLOORS, include_puma=False)
    source = {
        "name": "NREL ComStock 2024 Release 1 realized building-stock metadata",
        "url": ("https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/"
                "end-use-load-profiles-for-us-building-stock/2024/"
                "comstock_amy2018_release_1/metadata/baseline.parquet"),
        "variables": "in.hvac_system_type shares keyed comstock_building_type per tier (type n >= 5)",
        "mapping": "ComStock labels -> urbanopt system_type enum via COMSTOCK_HVAC_KEY (sim_params_spec.py)",
        "keying": "county fips x building type; division tier only as fallback (no PUMA, hours-table rationale)",
        "extracted": "2026-09-04",
        "regen": "tests/tools/build_county_system.py",
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
                print(f"  RetailStripmall mode: {cell['RetailStripmall']['mode']}")


if __name__ == "__main__":
    main()
