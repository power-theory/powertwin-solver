"""CR campaign WP-C2: county commercial thermostat + airtightness from ComStock 2024 R1.

Two cells per tier, both keyed comstock_building_type (type n >= 5):
- tstat: JOINT donors of (heating setpoint [th], cooling setpoint [tc]) deg F,
  SENTINEL-FILTERED (ComStock uses 999 and other out-of-band values for spaces
  with no mechanical setpoint: raw p90 cooling = 999, mean 353): keep 40 <= th
  <= 90 and 60 <= tc <= 95 only.
- air: shares of in.airtightness..m3_per_m2_s. NOTE the column name lies about
  units: values are 5-75 in steps of 5 = m3/(h*m2) at 75 Pa (the standard
  commercial pressurization metric, and exactly what the vendored
  set_nist_infiltration_correlations measure takes verbatim - zero conversion).

Run from repo root: ./venv/bin/python tests/tools/build_county_com_tstat.py
"""
import json
import os
import sys

import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_county_priors import COM_FLOORS, build_three_tier, donor_cell, emit, share_cell  # noqa: E402

PARQUET = "/mnt/h/data/comstock/baseline_cols.parquet"
OUT = "solver/upload/reference_data/comstock2024_county_tstat"
REGIONS = "solver/upload/reference_data/census_regions.json"
TYPE_MIN_N = 5


def tstat_cell(sub):
    cell = {}
    for bt, bsub in sub.groupby("in.comstock_building_type"):
        valid = bsub[(bsub["th"] >= 40) & (bsub["th"] <= 90) &
                     (bsub["tc"] >= 60) & (bsub["tc"] <= 95)]
        entry = {}
        if len(valid) >= TYPE_MIN_N:
            v = valid.copy()
            v["th"] = v["th"].astype(int).astype(str)
            v["tc"] = v["tc"].astype(int).astype(str)
            entry["tstat"] = donor_cell(v, ["th", "tc"], cap=40)
        if len(bsub) >= TYPE_MIN_N:
            a = bsub.copy()
            a["air"] = a["air"].astype(int).astype(str)
            s = share_cell(a, "air")
            if s:
                entry["air"] = s
        if entry:
            cell[str(bt)] = entry
    return cell or None


def main():
    cols = ["in.nhgis_county_gisjoin", "in.nhgis_puma_gisjoin", "in.state",
            "in.comstock_building_type", "in.tstat_htg_sp_f..f", "in.tstat_clg_sp_f..f",
            "in.airtightness..m3_per_m2_s", "weight"]
    df = pq.read_table(PARQUET, columns=cols).to_pandas(ignore_metadata=True)
    df = df.rename(columns={"in.tstat_htg_sp_f..f": "th", "in.tstat_clg_sp_f..f": "tc",
                            "in.airtightness..m3_per_m2_s": "air"})
    df["th"] = df["th"].astype(float)
    df["tc"] = df["tc"].astype(float)
    df = df[df["air"].notna()]
    s2d = json.load(open(REGIONS))["state_to_division"]
    df["division"] = df["in.state"].astype(str).map(s2d)
    df = df[df["division"].notna()]
    tiers = build_three_tier(
        df, tstat_cell,
        county_col="in.nhgis_county_gisjoin", puma_col="in.nhgis_puma_gisjoin",
        division_col="division", floors=COM_FLOORS, include_puma=False)
    source = {
        "name": "NREL ComStock 2024 Release 1 realized building-stock metadata",
        "url": ("https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/"
                "end-use-load-profiles-for-us-building-stock/2024/"
                "comstock_amy2018_release_1/metadata/baseline.parquet"),
        "variables": ("tstat: JOINT donors of (in.tstat_htg_sp_f [th], in.tstat_clg_sp_f [tc]), "
                      "sentinel-filtered to th in [40,90], tc in [60,95]; "
                      "air: in.airtightness shares, m3/(h*m2) at 75 Pa despite the column suffix"),
        "delivery": ("tstat as DELTAS vs the DOE prototype 70F htg / 75F clg occupied baseline via "
                     "AdjustThermostatSetpointsByDegrees; airtightness verbatim via "
                     "set_nist_infiltration_correlations (airtightness_pressure=75)"),
        "keying": "county fips x building type; division tier only",
        "extracted": "2026-09-04",
        "regen": "tests/tools/build_county_com_tstat.py",
    }
    raw, gz = emit(OUT, source, COM_FLOORS, tiers)
    counties = tiers["counties"]
    print(f"counties: {len(counties)}; raw {raw/1e6:.1f} MB, gz {gz/1e6:.2f} MB")
    t = counties.get("56039", {})
    for tier_name, cell in (("county", t.get("county")),
                            ("division", tiers["_divisions"].get(t.get("division_ref", "")))):
        if cell and "RetailStripmall" in cell:
            e = cell["RetailStripmall"]
            top = e.get("tstat", {}).get("donors", [{}])[0]
            print(f"Teton {tier_name} RetailStripmall: tstat top {top.get('th')}/{top.get('tc')}"
                  f" air mode {e.get('air', {}).get('mode')}")


if __name__ == "__main__":
    main()
