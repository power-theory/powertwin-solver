"""County-resolved residential lighting from ResStock 2024.2 (2026-10-13), replacing BuildResidentialHPXML's default
lighting (interior 10% CFL, 90% incandescent; exterior and garage 100% incandescent), which PowerTwin.rb applied to
every house without a decision (assumptions ledger: residential_lighting_fractions).

ResStock assigns `in.lighting` per home as one of 100% CFL / 100% Incandescent / 100% LED (weighted national shares
35.6 / 37.1 / 27.3%, Wyoming 35.4 / 25.7 / 38.9%, flat across vintages), so the table ships {mode, shares} per tier with
the county prior pooling of the other county tables (build_three_tier). The resolver turns a cell into the share-
weighted LED and CFL fractions of a house's lighting (deterministic, unbiased in aggregate), applied to its interior,
exterior and garage lighting as ResStock's options do.

  RESSTOCK_PARQUET=<baseline.parquet> ./venv/bin/python tests/tools/build_county_res_lighting.py [<out path, no ext>]
"""
import hashlib
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_county_priors import RES_FLOORS, build_three_tier, emit, share_cell  # noqa: E402

PARQUET = os.environ.get("RESSTOCK_PARQUET", "/home/missionloyd/dev/flow-ssd/data/resstock/baseline.parquet")
PARQUET_MD5 = "4535090ffb98627069f512d5d7e514f7"   # resstock_tmy3_release_2 metadata/baseline.parquet, fetched 2026-10-13
OUT = sys.argv[1] if len(sys.argv) > 1 else "solver/upload/reference_data/resstock2024_county_lighting"
COL = "in.lighting"


def lighting_cell(sub):
    return share_cell(sub, COL)


def main():
    md5 = hashlib.md5(open(PARQUET, "rb").read()).hexdigest()
    if md5 != PARQUET_MD5:
        sys.exit(f"{PARQUET} md5 {md5}, expected {PARQUET_MD5}")
    df = pd.read_parquet(PARQUET, columns=["in.county", "in.puma", "in.census_division_recs", "weight", COL])
    df = df[df[COL].notna()]
    df[COL] = df[COL].astype(str)
    tiers = build_three_tier(df, lighting_cell, county_col="in.county", puma_col="in.puma",
                             division_col="in.census_division_recs", floors=RES_FLOORS)
    source = {
        "name": "NREL ResStock 2024.2 realized building-stock metadata",
        "url": ("https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/end-use-load-profiles-for-us-building-"
                "stock/2024/resstock_tmy3_release_2/metadata/baseline.parquet"),
        "md5": PARQUET_MD5,
        "variables": f"{COL} {{mode, shares}} (100% CFL / 100% Incandescent / 100% LED), weight-weighted",
        "keying": "county fips, PUMA, state, census division (RECS), pooled as the other residential county priors",
        "use": "sim_params_spec resolves the share-weighted LED and CFL fractions; PowerTwin.rb applies them to the "
               "interior, exterior and garage lighting fractions of BuildResidentialHPXML",
        "extracted": "2026-10-13",
        "regen": "tests/tools/build_county_res_lighting.py",
    }
    raw, gz = emit(OUT, source, RES_FLOORS, tiers)
    print(f"counties: {len(tiers['counties'])}; raw {raw/1e6:.1f} MB, gz {gz/1e6:.2f} MB -> {OUT}.json.gz")
    print("Teton county cell:", json.dumps(tiers["counties"].get("56039"))[:600])


if __name__ == "__main__":
    main()
