"""Regenerate the residential envelope DISTRIBUTIONS in recs2020_envelope.json from
the ResStock 2024.2 realized building-stock metadata (NREL OEDI baseline.parquet,
549,715 sampled homes). Replaces the single modal wall/roof R per (region, vintage)
with a real {mode, shares} R-value distribution so a cell reproduces its actual
insulation spread instead of collapsing every home to one value.

  wall_r_value  <- in.insulation_wall   (e.g. "Wood Stud, R-13" -> 13; Uninsulated -> 1)
  roof_r_value  <- effective attic R: in.insulation_ceiling, or in.insulation_roof
                   when the ceiling slot is None (finished attic / cathedral)
  window_to_wall_ratio_by_building_type <- in.window_areas ("F12 B12 L12 R12" -> 0.12)

Decisions (see plan): R distributions are computed from SINGLE-FAMILY DETACHED (the
national default archetype the resolver falls back to; avoids conflating MF/mobile-home
structures into one distribution applied uniformly by region x vintage). wall_material /
roof_material are DROPPED -- the resolver derives the tier from the resolved R so a
building's tier and R always agree. WWR is kept per building-type (its existing keying),
with mobile homes folded into SFD (they archetype-fall-back to SFD).

  python3 tests/tools/build_residential_envelope.py
"""
import json
import re
from collections import defaultdict

import pandas as pd

PARQUET = "/mnt/h/data/resstock/baseline.parquet"
OUT = "solver/upload/reference_data/recs2020_envelope.json"

VBIN = {"<1940": "pre-1980", "1940s": "pre-1980", "1950s": "pre-1980", "1960s": "pre-1980",
        "1970s": "pre-1980", "1980s": "1980-1999", "1990s": "1980-1999",
        "2000s": "2000-2009", "2010s": "2010+"}
REGIONS = ["Northeast", "Midwest", "South", "West"]
VINTAGES = ["pre-1980", "1980-1999", "2000-2009", "2010+"]
BT_MAP = {"Single-Family Detached": "Single-Family Detached", "Mobile Home": "Single-Family Detached",
          "Single-Family Attached": "Single-Family Attached",
          "Multi-Family with 2 - 4 Units": "Multifamily", "Multi-Family with 5+ Units": "Multifamily"}


def parse_r(s):
    if s is None:
        return 1.0
    m = re.search(r"R-(\d+)", str(s))
    return float(m.group(1)) if m else 1.0  # Uninsulated / None -> sim-safe R-1 floor


def eff_roof_r(ceiling, roof):
    return parse_r(ceiling) if (ceiling and ceiling != "None") else parse_r(roof)


def parse_wwr(s):
    m = re.search(r"F(\d+)", str(s))
    return round(int(m.group(1)) / 100.0, 2) if m else None


def _finalize(weights):
    total = sum(weights.values())
    if total <= 0:
        return None
    shares = {k: round(v / total, 4) for k, v in weights.items() if v > 0}
    mode = max(shares, key=shares.get)
    shares[mode] = round(shares[mode] + (1.0 - sum(shares.values())), 4)
    return {"mode": mode, "share": shares[mode],
            "shares": dict(sorted(shares.items(), key=lambda kv: -kv[1]))}


def region_vintage_dist(df, value_col):
    cells = defaultdict(lambda: defaultdict(float))
    for region, vbin, val, w in zip(df["in.census_region"], df["vbin"], df[value_col], df["weight"]):
        if region in REGIONS and vbin in VINTAGES:
            cells[(region, vbin)][str(int(val))] += float(w)
    return {r: {v: _finalize(cells.get((r, v), {})) for v in VINTAGES} for r in REGIONS}


def main():
    cols = ["in.census_region", "in.vintage", "in.insulation_wall", "in.insulation_ceiling",
            "in.insulation_roof", "in.window_areas", "in.geometry_building_type_recs", "weight"]
    df = pd.read_parquet(PARQUET, columns=cols)
    df["vbin"] = df["in.vintage"].map(VBIN)

    df["res_bt"] = df["in.geometry_building_type_recs"].map(BT_MAP)
    df["wall_r"] = df["in.insulation_wall"].map(parse_r)
    df["roof_r"] = [eff_roof_r(c, r) for c, r in zip(df["in.insulation_ceiling"], df["in.insulation_roof"])]

    # wall R: SFD-representative, region-keyed (wall R is mid-pack across types, audited).
    sfd = df[df["in.geometry_building_type_recs"] == "Single-Family Detached"]
    wall_r_value = region_vintage_dist(sfd, "wall_r")
    # roof R: keyed BY BUILDING TYPE -- MF/mobile roofs run ~16% below SFD (audited), and the
    # resolver applies roof_r to ~33% of the stock, so SFD-only overstates their insulation.
    roof_r_value = {"by_building_type": {
        bt: region_vintage_dist(df[df["res_bt"] == bt], "roof_r")
        for bt in ("Single-Family Detached", "Single-Family Attached", "Multifamily")
    }}

    # WWR per resolver building-type (mobile home folded into SFD)
    df["wwr"] = df["in.window_areas"].map(parse_wwr)
    wwr_cells = defaultdict(lambda: defaultdict(float))
    for bt, wwr, w in zip(df["res_bt"], df["wwr"], df["weight"]):
        if bt and wwr is not None:
            wwr_cells[bt][str(wwr)] += float(w)
    wwr = {bt: _finalize(wwr_cells[bt]) for bt in ("Single-Family Detached", "Single-Family Attached", "Multifamily")}

    # preserve foundation_type + garage_prevalence; replace R + WWR; drop material (derived from R)
    doc = json.load(open(OUT))
    doc["_source"] = {
        "name": "NREL ResStock 2024.2 realized building-stock metadata (OEDI baseline.parquet)",
        "publisher": "National Renewable Energy Laboratory",
        "url": "https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock/2024/resstock_tmy3_release_2/metadata/baseline.parquet",
        "extracted": "2026-06-28",
        "regen": "tests/tools/build_residential_envelope.py",
        "units": "R-values in hr-ft^2-F/Btu (cavity for walls, ceiling/attic for roof); WWR fraction",
        "methodology": (
            "{mode, shares} R-value distributions from 549,715 ResStock realized homes, weighted. "
            "wall_r_value <- in.insulation_wall, SFD-representative, keyed (region x vintage) -- wall R "
            "is mid-pack across building types. roof_r_value <- in.insulation_ceiling (falling back to "
            "in.insulation_roof for finished-attic/cathedral homes), keyed BY BUILDING TYPE "
            "(SFD/SFA/Multifamily) x region x vintage because MF/mobile roofs run ~16% below SFD and "
            "the resolver applies roof_r to ~33% of the stock. Uninsulated -> R-1 (sim-safe floor). "
            "The resolver share-weights by building_id hash and DERIVES the material tier from the "
            "resolved R. WWR by building-type from in.window_areas; mobile folded into SFD."
        ),
    }
    doc["wall_r_value"] = wall_r_value
    doc["roof_r_value"] = roof_r_value
    doc["window_to_wall_ratio_by_building_type"] = wwr
    doc.pop("wall_material", None)
    doc.pop("roof_material", None)
    with open(OUT, "w") as f:
        json.dump(doc, f, indent=2)
    print(f"wrote {OUT}")
    print("\nwall_r_value (SFD, region-keyed) modes:")
    for r in REGIONS:
        print(f"  {r:10s} " + "  ".join(f"{v}:R-{wall_r_value[r][v]['mode']}" for v in VINTAGES))
    print("\nroof_r_value by building type (modes):")
    for bt, sec in roof_r_value["by_building_type"].items():
        print(f"  {bt}:")
        for r in REGIONS:
            print(f"    {r:10s} " + "  ".join(f"{v}:R-{sec[r][v]['mode']}" for v in VINTAGES))


if __name__ == "__main__":
    main()
