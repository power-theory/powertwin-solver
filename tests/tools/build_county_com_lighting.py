"""County-resolved commercial interior lighting generation from ComStock 2024.1 (2026-10-01), replacing the flat 35%
cut to the 90.1 lighting allowance that had no source.

ComStock assigns `in.interior_lighting_generation` (gen1_t12_incandescent .. gen5_led) by the year of the last
interior lighting replacement and the code in force then. Unlike the construction code it is a real distribution
(6,966 of 8,104 state-year cells mixed; WY 43% gen1), so it ships as {mode, shares} per county x ComStock building type,
with division fallback (the county-table schema). The resolver draws a generation per building and reads its LPD from
comstock_lighting_lpd.json (NREL/TP-5500-86100 Table 2).

Reads only the needed columns over anonymous S3.

  ./venv/bin/python tests/tools/build_county_com_lighting.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_county_priors import COM_FLOORS, build_three_tier, emit, share_cell  # noqa: E402

S3_PATH = ("oedi-data-lake/nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock/"
           "2024/comstock_amy2018_release_1/metadata/baseline.parquet")
OUT = "solver/upload/reference_data/comstock2024_county_lighting"
REGIONS = "solver/upload/reference_data/census_regions.json"
GEN = "in.interior_lighting_generation"
TYPE_MIN_N = 5


def lighting_cell(sub):
    cell = {}
    for bt, bsub in sub.groupby("in.comstock_building_type"):
        if len(bsub) < TYPE_MIN_N:
            continue
        c = share_cell(bsub, GEN)
        if c:
            cell[str(bt)] = c
    return cell or None


def main():
    import pyarrow.fs as pafs
    import pyarrow.parquet as pq
    cols = ["in.nhgis_county_gisjoin", "in.nhgis_puma_gisjoin", "in.state", "in.comstock_building_type", "weight", GEN]
    fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    df = pq.read_table(S3_PATH, filesystem=fs, columns=cols).to_pandas(ignore_metadata=True)
    df = df[df[GEN].notna()]
    df[GEN] = df[GEN].astype(str)
    s2d = json.load(open(REGIONS))["state_to_division"]
    df["division"] = df["in.state"].astype(str).map(s2d)
    df = df[df["division"].notna()]
    tiers = build_three_tier(df, lighting_cell, county_col="in.nhgis_county_gisjoin",
                             puma_col="in.nhgis_puma_gisjoin", division_col="division", floors=COM_FLOORS,
                             include_puma=False)
    source = {
        "name": "NREL ComStock 2024 Release 1 realized building-stock metadata",
        "url": "https://oedi-data-lake.s3.amazonaws.com/" + S3_PATH.split("/", 1)[1],
        "variables": f"{GEN} {{mode, shares}} keyed comstock_building_type per tier (type n >= {TYPE_MIN_N}), weight-weighted",
        "keying": "county fips x building type; division tier (no PUMA), as the commercial fuels table",
        "lpd": "comstock_lighting_lpd.json (NREL/TP-5500-86100 Table 2)",
        "extracted": "2026-10-01",
        "regen": "tests/tools/build_county_com_lighting.py",
    }
    raw, gz = emit(OUT, source, COM_FLOORS, tiers)
    print(f"counties: {len(tiers['counties'])}; raw {raw/1e6:.1f} MB, gz {gz/1e6:.2f} MB")
    t = tiers["counties"].get("56039", {})
    print("Teton county cell:", json.dumps(t.get("county"))[:400])


if __name__ == "__main__":
    main()
