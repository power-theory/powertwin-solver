"""Build solver/upload/reference_data/comstock2024_state_code_adoption.json: the energy code a commercial building
was built to, by state and year built, from NREL ComStock 2024.1 (Energy Code Assignment Plan, 2026-10-01).

ComStock assigns `in.energy_code_followed_during_original_building_construction` by state adoption. In the 2024.1
baseline (346,185 buildings) it is a deterministic function of (in.state, in.year_built): every state-year cell
holds one code, and each state's codes only advance with time. So the whole assignment is a short list of adoption
years per state. This builder ASSERTS both properties and fails if a new release breaks them.

A code's adoption year is the first sampled year that carries it. Years between the last sample of the previous
code and the first sample of the next are unsampled; they take the previous code (the conservative side), and the
count of such gap years is recorded per state.

Reads only the needed columns over anonymous S3, or a local parquet with --parquet (same columns).

  python3 tests/tools/build_energy_code_adoption.py [--parquet PATH]
"""
import argparse
import datetime
import json
import os

import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(REPO, "solver", "upload", "reference_data", "comstock2024_state_code_adoption.json")
S3_PATH = ("oedi-data-lake/nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock/"
           "2024/comstock_amy2018_release_1/metadata/baseline.parquet")
CODE = "in.energy_code_followed_during_original_building_construction"
COLS = ["in.state", "in.year_built", CODE]
# Order within each family; a state's codes must advance along it.
ASHRAE = ["DOE Ref Pre-1980", "DOE Ref 1980-2004", "90.1-2004", "90.1-2007", "90.1-2010", "90.1-2013"]
DEER = ["DEER Pre-1975", "DEER 1985", "DEER 1996", "DEER 2003", "DEER 2007", "DEER 2011", "DEER 2014", "DEER 2015",
        "DEER 2017"]


def load(parquet):
    if parquet:
        return pd.read_parquet(parquet, columns=COLS)
    import pyarrow.fs as pafs
    import pyarrow.parquet as pq
    fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    return pq.read_table(S3_PATH, filesystem=fs, columns=COLS).to_pandas()


def build(df):
    df = df.copy()
    df["code"] = df[CODE].astype(str).str.replace("ComStock ", "", regex=False)
    df["year"] = pd.to_numeric(df["in.year_built"].astype(str), errors="coerce")
    df["state"] = df["in.state"].astype(str)
    df = df[df["year"].notna()]
    per_cell = df.groupby(["state", "year"])["code"].nunique()
    mixed = per_cell[per_cell > 1]
    assert mixed.empty, f"code is not a function of (state, year): {len(mixed)} mixed cells, e.g. {mixed.head().to_dict()}"
    states = {}
    for st, g in df.groupby("state"):
        cell = g.groupby("year")["code"].first().sort_index()
        family = DEER if cell.iloc[0].startswith("DEER") else ASHRAE
        assert set(cell) <= set(family), f"{st}: codes outside one family: {sorted(set(cell) - set(family))}"
        ranks = [family.index(c) for c in cell]
        assert ranks == sorted(ranks), f"{st}: codes go backwards in time"
        steps, gaps = [], 0
        prev_code, prev_year = None, None
        for year, code in cell.items():
            if code != prev_code:
                steps.append([0 if prev_code is None else int(year), code])
                if prev_year is not None:
                    gaps += int(year - prev_year - 1)
                prev_code = code
            prev_year = year
        states[st] = {"family": "DEER" if family is DEER else "ASHRAE", "steps": steps, "gap_years": gaps,
                      "sampled_years": int(cell.size), "last_sampled_year": int(cell.index.max())}
    return states, {"buildings": int(len(df)), "state_year_cells": int(per_cell.size)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", help="local parquet with the same columns (else anonymous S3)")
    a = ap.parse_args()
    states, stats = build(load(a.parquet))
    doc = {
        "_source": {
            "name": "NREL ComStock 2024 Release 1 realized building-stock metadata (baseline.parquet)",
            "url": "https://oedi-data-lake.s3.amazonaws.com/" + S3_PATH.split("/", 1)[1],
            "variable": CODE + " by in.state x in.year_built",
            "properties_asserted": "one code per (state, year); codes only advance within a state",
            "boundary_rule": "a code applies from the first sampled year carrying it; unsampled gap years keep the previous code",
            "after_last_year": "years after a state's last sampled year keep its last code (no sourced data past ComStock)",
            "stats": stats,
            "extracted": datetime.date.today().isoformat(),
            "regen": "tests/tools/build_energy_code_adoption.py",
        },
        "states": states,
    }
    with open(OUT, "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")
    print(f"{len(states)} states, {stats}; wrote {os.path.relpath(OUT, REPO)} ({os.path.getsize(OUT):,} bytes)")
    for st in ("WY", "CO", "TX", "AZ", "CA", "NY"):
        print(st, states[st]["steps"], "gaps", states[st]["gap_years"])


if __name__ == "__main__":
    main()
