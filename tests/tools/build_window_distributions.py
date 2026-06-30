"""Regenerate window_type_by_vintage.json with per-(region,vintage) DISTRIBUTIONS
(not just the mode), transcribed directly from the survey microdata:

  residential  <- RECS 2020 TYPEGLASS  (1=Single, 2=Double, 3=Triple pane), NWEIGHT-weighted
  commercial   <- CBECS 2018 WINTYP    (1=Single-layer, 2=Multi-layer,
                                         3=Combination, 4=No windows), FINALWT-weighted

Output cell structure matches the fuel-mix records that _select_fuel_by_share already
consumes: {"mode": <str>, "share": <float>, "shares": {<type>: <float>, ...}}.

Documented assumptions (no fabrication):
  - Commercial has no triple-pane code; CBECS "Multi-layer" -> "Double Pane".
  - CBECS "Combination of both" (WINTYP=3) is split 50/50 Single/Double (neutral prior
    for a genuine mix); "No windows" (WINTYP=4) is excluded from the glazing distribution.
  - Vintage bins: RECS YEARMADERANGE 1-4->pre-1980, 5-6->1980-1999, 7->2000-2009, 8-9->2010+.
    CBECS YRCONC 2-5->pre-1980, 6-7->1980-1999, 8(2000-2012)->2000-2009, 9(2013-2018)->2010+.
    The CBECS 2000-2012 bin straddles 2009; assigned to 2000-2009 (its larger share).
"""
import json
from collections import defaultdict

import pandas as pd

RECS_CSV = "tmp/recs2020_public_v7.csv"
CBECS_CSV = "/mnt/h/data/cbecs/cbecs2018_final_public.csv"
OUT = "solver/upload/reference_data/window_type_by_vintage.json"

REGIONS = ["Northeast", "Midwest", "South", "West"]
VINTAGES = ["pre-1980", "1980-1999", "2000-2009", "2010+"]

RECS_REGION = {"NORTHEAST": "Northeast", "MIDWEST": "Midwest", "SOUTH": "South", "WEST": "West"}
RECS_VINTAGE = {1: "pre-1980", 2: "pre-1980", 3: "pre-1980", 4: "pre-1980",
                5: "1980-1999", 6: "1980-1999", 7: "2000-2009", 8: "2010+", 9: "2010+"}
RECS_GLASS = {1: "Single Pane", 2: "Double Pane", 3: "Triple Pane"}

CBECS_REGION = {1: "Northeast", 2: "Midwest", 3: "South", 4: "West"}
CBECS_VINTAGE = {2: "pre-1980", 3: "pre-1980", 4: "pre-1980", 5: "pre-1980",
                 6: "1980-1999", 7: "1980-1999", 8: "2000-2009", 9: "2010+"}


def _finalize(weights: dict) -> dict:
    total = sum(weights.values())
    if total <= 0:
        return None
    shares = {k: round(v / total, 4) for k, v in weights.items() if v > 0}
    # fix rounding drift so shares sum to exactly 1.0
    drift = round(1.0 - sum(shares.values()), 4)
    mode = max(shares, key=shares.get)
    shares[mode] = round(shares[mode] + drift, 4)
    return {"mode": mode, "share": shares[mode], "shares": dict(sorted(shares.items(), key=lambda kv: -kv[1]))}


def residential():
    df = pd.read_csv(RECS_CSV, usecols=["REGIONC", "YEARMADERANGE", "TYPEGLASS", "NWEIGHT"])
    cells = defaultdict(lambda: defaultdict(float))
    for _, row in df.iterrows():
        region = RECS_REGION.get(str(row["REGIONC"]).upper())
        vint = RECS_VINTAGE.get(int(row["YEARMADERANGE"]))
        glass = RECS_GLASS.get(int(row["TYPEGLASS"]))
        if region and vint and glass:
            cells[(region, vint)][glass] += float(row["NWEIGHT"])
    return cells


def commercial():
    df = pd.read_csv(CBECS_CSV, usecols=["REGION", "YRCONC", "WINTYP", "FINALWT"])
    cells = defaultdict(lambda: defaultdict(float))
    for _, row in df.iterrows():
        region = CBECS_REGION.get(int(row["REGION"]))
        vint = CBECS_VINTAGE.get(int(row["YRCONC"]))
        wt = float(row["FINALWT"])
        if not (region and vint):
            continue
        code = int(row["WINTYP"])
        if code == 1:
            cells[(region, vint)]["Single Pane"] += wt
        elif code == 2:
            cells[(region, vint)]["Double Pane"] += wt
        elif code == 3:  # combination of both -> neutral 50/50
            cells[(region, vint)]["Single Pane"] += wt / 2
            cells[(region, vint)]["Double Pane"] += wt / 2
        # code == 4 (no windows) excluded
    return cells


def build(cells):
    out = {}
    for region in REGIONS:
        out[region] = {}
        for vint in VINTAGES:
            rec = _finalize(cells.get((region, vint), {}))
            out[region][vint] = rec
    return out


def main():
    res = build(residential())
    com = build(commercial())
    doc = {
        "_source": {
            "name": "EIA RECS 2020 (residential, TYPEGLASS) + EIA CBECS 2018 (commercial, WINTYP)",
            "publisher": "U.S. Energy Information Administration",
            "urls": {
                "recs": "https://www.eia.gov/consumption/residential/data/2020/",
                "cbecs": "https://www.eia.gov/consumption/commercial/data/2018/",
            },
            "extracted": "2026-06-28",
            "methodology": (
                "Per-(census_region, vintage_bin) glazing DISTRIBUTION transcribed directly from "
                "survey microdata, weighted by NWEIGHT (RECS) / FINALWT (CBECS). residential=RECS "
                "TYPEGLASS (1 Single/2 Double/3 Triple). commercial=CBECS WINTYP (1 Single-layer-> "
                "Single Pane, 2 Multi-layer-> Double Pane, 3 Combination-> 50/50 Single/Double, "
                "4 No windows-> excluded). Cells carry {mode, share, shares}; the resolver mode-picks "
                "(deterministic default) or share-weights by building_id hash when stochastic sampling is on."
            ),
            "vintage_bins": {
                "recs_YEARMADERANGE": "1-4=pre-1980, 5-6=1980-1999, 7=2000-2009, 8-9=2010+",
                "cbecs_YRCONC": "2-5=pre-1980, 6-7=1980-1999, 8(2000-2012)=2000-2009, 9(2013-2018)=2010+",
            },
        },
        "residential": res,
        "commercial": com,
    }
    with open(OUT, "w") as f:
        json.dump(doc, f, indent=2)
    print(f"wrote {OUT}")
    for label, sec in (("residential", res), ("commercial", com)):
        print(f"\n{label}:")
        for region in REGIONS:
            for vint in VINTAGES:
                rec = sec[region][vint]
                if rec:
                    print(f"  {region:10s} {vint:11s} mode={rec['mode']:12s} shares={rec['shares']}")
                else:
                    print(f"  {region:10s} {vint:11s} (no data)")


if __name__ == "__main__":
    main()
