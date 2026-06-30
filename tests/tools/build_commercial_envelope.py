"""Regenerate the commercial envelope DISTRIBUTIONS in cbecs2018_envelope.json from the
ComStock 2024.1 realized building-stock metadata (NREL OEDI, 346,185 sampled commercial
buildings). Replaces the single modal assembly R per (region, vintage) with a real
{mode, shares} R distribution -- symmetric to the residential ResStock work.

  wall_r_value  <- 1 / out.params.average_wall_u_value..btu_per_ft2_f_hr   (assembly R)
  roof_r_value  <- 1 / out.params.average_roof_u_value..btu_per_ft2_f_hr

Reads ONLY the needed columns over anonymous S3 (no full-file download). R is rounded to
the nearest integer (assembly R is continuous, 1/U); the resolver share-weights by
building_id hash and DERIVES the material tier from the resolved R (same path as
residential). window_to_wall_ratio stays DOE-prototype-keyed (unchanged); commercial
window type stays CBECS WINTYP (window_type_by_vintage.json).

Vintage map: Before 1946 / 1946-1959 / 1960-1969 / 1970-1979 -> pre-1980;
1980-1989 / 1990-1999 -> 1980-1999; 2000-2012 -> 2000-2009 (straddles 2009);
2013-2018 -> 2010+.

  python3 tests/tools/build_commercial_envelope.py
"""
import json
from collections import defaultdict

import pyarrow.parquet as pq
import pyarrow.fs as pafs

S3_PATH = ("oedi-data-lake/nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock/"
           "2024/comstock_amy2018_release_1/metadata/baseline.parquet")
OUT = "solver/upload/reference_data/cbecs2018_envelope.json"

REGIONS = ["Northeast", "Midwest", "South", "West"]
VINTAGES = ["pre-1980", "1980-1999", "2000-2009", "2010+"]
VBIN = {"Before 1946": "pre-1980", "1946 to 1959": "pre-1980", "1960 to 1969": "pre-1980",
        "1970 to 1979": "pre-1980", "1980 to 1989": "1980-1999", "1990 to 1999": "1980-1999",
        "2000 to 2012": "2000-2009", "2013 to 2018": "2010+"}
CZ_COL = "in.ashrae_iecc_climate_zone_2006"
WALL_U = "out.params.average_wall_u_value..btu_per_ft2_f_hr"
ROOF_U = "out.params.average_roof_u_value..btu_per_ft2_f_hr"


def _finalize(weights):
    total = sum(weights.values())
    if total <= 0:
        return None
    shares = {k: round(v / total, 4) for k, v in weights.items() if v / total >= 0.005}  # drop <0.5% noise
    if not shares:
        return None
    mode = max(shares, key=shares.get)
    shares[mode] = round(shares[mode] + (1.0 - sum(shares.values())), 4)
    return {"mode": mode, "share": shares[mode],
            "shares": dict(sorted(shares.items(), key=lambda kv: -kv[1]))}


def region_vintage_R(df, u_col):
    cells = defaultdict(lambda: defaultdict(float))
    for region, vint, u, w in zip(df["in.census_region_name"], df["in.vintage"], df[u_col], df["weight"]):
        vb = VBIN.get(vint)
        if region in REGIONS and vb and u and u > 0:
            r = str(int(round(1.0 / u)))   # assembly R = 1/U, rounded
            cells[(region, vb)][r] += float(w)
    return {r: {v: _finalize(cells.get((r, v), {})) for v in VINTAGES} for r in REGIONS}


def cz_vintage_R(df, u_col):
    """Assembly-R distribution keyed by ASHRAE/IECC climate zone x vintage -- the CORRECT
    driver (energy codes are CZ-keyed; census-region smears a 5x R range within a region)."""
    cells = defaultdict(lambda: defaultdict(float))
    czs = set()
    for cz, vint, u, w in zip(df[CZ_COL], df["in.vintage"], df[u_col], df["weight"]):
        vb = VBIN.get(vint)
        if cz and vb and u and u > 0:
            cells[(cz, vb)][str(int(round(1.0 / u)))] += float(w)
            czs.add(cz)
    return {cz: {v: _finalize(cells.get((cz, v), {})) for v in VINTAGES} for cz in sorted(czs)}


# ComStock building types -> the DOE-ref keys the resolver looks up (_doe_ref_name).
# Most match verbatim; only retail differs. Resolver keys ComStock does NOT cover
# (Office alias, SuperMarket, Mid/HighriseApartment, Laboratory) keep their existing scalar.
COMSTOCK_BT_KEY = {"RetailStandalone": "Retail", "RetailStripmall": "StripMall"}


def wwr_by_type(df):
    cells = defaultdict(lambda: defaultdict(float))
    for bt, wwr, w in zip(df["in.comstock_building_type"], df["out.params.window_to_wall_ratio"], df["weight"]):
        if bt is None or wwr is None or wwr != wwr:   # skip None / NaN
            continue
        key = COMSTOCK_BT_KEY.get(bt, bt)
        b = str(round(round(float(wwr) / 0.05) * 0.05, 2))   # 0.05 bins
        cells[key][b] += float(w)
    return {k: _finalize(v) for k, v in cells.items() if _finalize(v)}


def main():
    s3 = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    cols = ["in.census_region_name", CZ_COL, "in.vintage", "weight", WALL_U, ROOF_U,
            "in.comstock_building_type", "out.params.window_to_wall_ratio"]
    df = pq.read_table(s3.open_input_file(S3_PATH), columns=cols).to_pandas()
    # Dual keying: climate zone is the correct driver; census region is the fallback for
    # when a building has no resolved climate_zone in ctx.
    wall_r = {"by_climate_zone": cz_vintage_R(df, WALL_U), "by_region": region_vintage_R(df, WALL_U)}
    roof_r = {"by_climate_zone": cz_vintage_R(df, ROOF_U), "by_region": region_vintage_R(df, ROOF_U)}
    wwr = wwr_by_type(df)

    doc = json.load(open(OUT))
    doc["_source"] = {
        "name": "NREL ComStock 2024.1 realized building-stock metadata (OEDI baseline.parquet, 346,185 buildings)",
        "publisher": "National Renewable Energy Laboratory",
        "url": ("https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/"
                "end-use-load-profiles-for-us-building-stock/2024/comstock_amy2018_release_1/metadata/baseline.parquet"),
        "extracted": "2026-06-28",
        "regen": "tests/tools/build_commercial_envelope.py",
        "units": "wall/roof assembly R in hr-ft2-F/Btu (= 1 / out.params.average_*_u_value)",
        "methodology": (
            "Wall/roof assembly-R DISTRIBUTION {mode, shares} from the ComStock realized stock, "
            "weight-weighted, R = round(1 / average_*_u_value). Keyed BOTH by ASHRAE/IECC climate "
            "zone x vintage (by_climate_zone -- the correct code-driven key) AND by census region x "
            "vintage (by_region -- fallback when a building has no resolved climate_zone). The "
            "resolver prefers climate_zone, falls back to region. WWR per building-type {mode, shares} "
            "from out.params.window_to_wall_ratio (0.05 bins, ComStock type -> DOE-ref key); types "
            "ComStock does not cover keep their prior scalar. Material tier derived from the resolved R. "
            "Commercial window type stays CBECS WINTYP."
        ),
    }
    doc["wall_r_value"] = wall_r
    doc["roof_r_value"] = roof_r
    doc.pop("wall_material", None)
    doc.pop("roof_material", None)
    # merge ComStock WWR distributions over the DOE scalars (keep uncovered types' scalars)
    doc.setdefault("window_to_wall_ratio_by_building_type", {}).update(wwr)
    with open(OUT, "w") as f:
        json.dump(doc, f, indent=2)
    print(f"wrote {OUT}  ({len(df)} ComStock buildings)")
    for label, sec in (("wall_r_value", wall_r), ("roof_r_value", roof_r)):
        nz = len(sec["by_climate_zone"])
        print(f"\n{label}: {nz} climate zones + {len(REGIONS)} region-fallback. by_region modes:")
        for r in REGIONS:
            modes = [f"{v}:R-{sec['by_region'][r][v]['mode']}" if sec['by_region'][r][v] else f"{v}:-" for v in VINTAGES]
            print(f"  {r:10s} {'  '.join(modes)}")
    print("\nWWR by type (mode | shares):")
    for k, v in sorted(wwr.items()):
        print(f"  {k:24s} {v['mode']:5s}  {v['shares']}")


if __name__ == "__main__":
    main()
