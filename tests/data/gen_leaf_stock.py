#!/usr/bin/env python3
"""gen_leaf_stock.py -- generate the synthetic national-stock LEAF dataset.

Replaces ASU as the COVERAGE instrument (ASU is one cell: commercial / AZ / one CZ).
This enumerates every input equivalence-class ("leaf") that changes resolver/model
behavior, so coverage is provable-by-construction (the manifest lists leaf -> buildings).
ASU is kept as a separate realism/input-contract fixture; this is the coverage basis.

A "leaf" = a terminal input class: (division x decade-vintage x county?), every climate
zone, every subtype, every branch (vacant / MF-counts / cooling-suppress / slab / op-hours),
every forced fuel x system, and sparse/contract edge cases. One representative building per
leaf; within-cell DISTRIBUTION is checked separately by running the resolver N times per
leaf (it varies on building_id), not by N dataset rows.

Outputs (tests/synthetic/out/):
  leaf_stock_metadata.csv      -- same schema the solver ingests (ASU-compatible)
  leaf_stock_geometries.geojson
  leaf_manifest.json           -- {building_id: [leaf tags...]} + coverage summary

Run anywhere (only writes files):  python3 tests/synthetic/gen_leaf_stock.py
"""
import csv, json, os, copy

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
ASU_GEO = os.path.join(REPO, "solver", "upload", "demo_data", "asu_asset_geometries.geojson")
OUT = os.path.join(HERE, "out")

# division -> (state, lat, lon, county_fips) representative point
DIV = {
    "New England":        ("MA", 42.36, -71.06, "25025"),
    "Middle Atlantic":    ("NY", 42.65, -73.75, "36001"),
    "East North Central": ("IL", 41.84, -87.68, "17031"),
    "West North Central": ("IA", 41.59, -93.62, "19153"),
    "South Atlantic":     ("GA", 33.75, -84.39, "13121"),
    "East South Central": ("AL", 33.52, -86.80, "01073"),
    "West South Central": ("TX", 29.76, -95.36, "48201"),
    "Mountain North":     ("CO", 39.74, -104.99, "08031"),
    "Mountain South":     ("AZ", 33.45, -112.07, "04013"),
    "Pacific":            ("CA", 34.05, -118.24, "06037"),
}
# decade vintage bin -> representative year
DECADE = {"pre-1950": 1945, "1950-1959": 1955, "1960-1969": 1965, "1970-1979": 1975,
          "1980-1989": 1985, "1990-1999": 1995, "2000-2009": 2005, "2010-2019": 2015, "2020+": 2022}
# climate zone label -> (state, lat, lon) spanning CZ 1-8
CZ = {
    "1A": ("FL", 25.76, -80.19), "2A": ("TX", 29.76, -95.36), "2B": ("AZ", 33.45, -112.07),
    "3A": ("GA", 33.75, -84.39), "3B": ("CA", 34.05, -118.24), "3C": ("CA", 37.77, -122.42),
    "4A": ("MD", 39.29, -76.61), "4B": ("NM", 35.08, -106.65), "4C": ("WA", 47.60, -122.33),
    "5A": ("IL", 41.84, -87.68), "5B": ("CO", 39.74, -104.99), "6A": ("MN", 44.98, -93.27),
    "6B": ("MT", 46.59, -112.04), "7":  ("MN", 46.79, -92.10), "8":  ("AK", 64.84, -147.72),
}
# commercial subtype id -> (name, area_ft2, floors)
COMMERCIAL = {
    8: ("Office", 50000, 3), 9: ("Laboratory", 60000, 2), 10: ("Nonrefrigerated warehouse", 100000, 1),
    11: ("Food sales", 8000, 1), 12: ("Public order and safety", 40000, 2), 13: ("Outpatient health care", 25000, 2),
    14: ("Refrigerated warehouse", 80000, 1), 15: ("Religious worship", 20000, 1), 16: ("Public assembly", 30000, 1),
    17: ("Education", 80000, 2), 18: ("Food service", 5000, 1), 19: ("Inpatient health care", 200000, 5),
    20: ("Nursing", 60000, 3), 21: ("Lodging", 100000, 4), 22: ("Strip shopping mall", 30000, 1),
    23: ("Enclosed mall", 150000, 1), 24: ("Retail other than mall", 30000, 1), 25: ("Service", 15000, 1),
    26: ("Mixed use", 60000, 4),
}
SFD, SFA, MF = (1, "Single-Family Detached"), (2, "Single-Family Attached"), (3, "Multifamily")

_template = json.load(open(ASU_GEO))["features"][0]
features, rows, manifest = [], [], {}
_bid = [800000]

# Every sensor the cleaned-report layer can capture (id,name from the canonical
# sensor_types.csv; skip the column-less Water sensor). Real ASU metadata carries ONE ROW
# PER (building, sensor); the leaf data must too -- clean_report.clean_single_report captures
# only sensors a building DECLARES, so a single Electricity row silently drops every
# non-electric fuel (gas, wood->OtherFuels, ...). Declaring all is safe: a sensor with no
# energy yields no cleaned file (clean_report skips all-zero/NA sections).
_ST_CSV = os.path.join(REPO, "solver", "upload", "sensor_types.csv")
SENSORS = [(int(r["id"]), r["name"]) for r in csv.DictReader(open(_ST_CSV)) if r["columns"].strip()]


def emit(subtype_id, subtype_name, area, floors, leaves, *, state=None, lat=None, lon=None,
         fips=None, year=None, overrides=None, name=None):
    bid = _bid[0]; _bid[0] += 1
    label = name or f"{subtype_name}-{bid}"
    feat = copy.deepcopy(_template)
    feat["id"] = bid
    feat["properties"] = {"id": bid, "asset_id": bid, "name": label, "floor_count": floors,
                          "height": floors * 3.5, "base": 0, "latitude": lat, "longitude": lon}
    features.append(feat)
    meta = {"area": float(area), "city": label, "state": state, "latitude": lat, "longitude": lon,
            "year_built": year, "floor_count": floors, "organization": "leaf-stock",
            "footprint_area": float(area) / floors, "county_fips": fips}
    meta = {k: v for k, v in meta.items() if v is not None}      # omit None so absence is real
    if overrides:
        meta.update(overrides)
    base = {"asset_id": bid, "asset_name": label, "asset_subtype_id": subtype_id,
            "asset_subtype_name": subtype_name,
            "asset_metadata": json.dumps(meta),
            "asset_geometries_properties": json.dumps(
                {"id": bid, "base": 0, "name": label, "height": floors * 3.5,
                 "asset_id": bid, "floor_count": floors})}
    # one row per building; the declared sensors ride in the `sensors` map (sensor_id unique per
    # building x sensor type, the same ids as before: bid * 100 + sensor_type_id)
    rows.append({**base, "sensors": json.dumps({str(stid): bid * 100 + stid for stid, _ in SENSORS})})
    manifest[str(bid)] = leaves


# --- Block A: residential SFD full grid -- RECS division x vintage, county + no-county ----
for div, (st, lat, lon, fips) in DIV.items():
    for vbin, yr in DECADE.items():
        emit(*SFD, 2200, 1, [f"div:{div}", f"vint:{vbin}", "geo:county", "wf:res"],
             state=st, lat=lat, lon=lon, fips=fips, year=yr)
        emit(*SFD, 2200, 1, [f"div:{div}", f"vint:{vbin}", "geo:nocounty", "wf:res"],
             state=st, year=yr)   # no lat/lon, no fips -> division fallback leaf

# --- Block B: residential SFA + MF across divisions (subtype coverage) -------------------
for div, (st, lat, lon, fips) in DIV.items():
    for vbin in ("pre-1950", "1980-1989", "2010-2019"):
        emit(*SFA, 1600, 1, [f"div:{div}", f"vint:{vbin}", "subtype:SFA", "wf:res"],
             state=st, lat=lat, lon=lon, fips=fips, year=DECADE[vbin])
        emit(*MF, 12000, 3, [f"div:{div}", f"vint:{vbin}", "subtype:MF", "wf:res"],
             state=st, lat=lat, lon=lon, fips=fips, year=DECADE[vbin], overrides={"number_of_units": 12})

# --- Block C: climate-zone grid -- envelope/window/foundation/cooling-suppression --------
for czl, (st, lat, lon) in CZ.items():
    for yr in (1965, 2015):
        emit(*SFD, 2200, 1, [f"cz:{czl}", f"vint:{'pre-1980' if yr < 1980 else '2010+'}", "wf:res",
                             "branch:slab" if czl in ("1A", "2A", "2B", "3A", "3B", "3C") else "branch:nonslab",
                             "branch:cooling_suppress" if czl in ("7", "8") else "branch:cooling_on"],
             state=st, lat=lat, lon=lon, year=yr)

# --- Block D: commercial -- every DOE-prototype subtype x 2 regions x 2 vintages ---------
for sid, (sname, area, floors) in COMMERCIAL.items():
    for div in ("East North Central", "South Atlantic"):     # cold + warm region
        st, lat, lon, fips = DIV[div]
        for yr in (1975, 2015):
            emit(sid, sname, area, floors,
                 [f"subtype:{sname}", f"div:{div}", f"vint:{'pre-1980' if yr < 1980 else '2010+'}", "wf:com",
                  "branch:ophours_suppress" if sname in ("Lodging", "Inpatient health care", "Food service") else "branch:ophours"],
                 state=st, lat=lat, lon=lon, fips=fips, year=yr)

# --- Block E: forced fuel x system (residential capture proof) ---------------------------
FORCED = [("wood", "wood_stove"), ("natural gas", "furnace"), ("natural gas", "boiler"),
          ("electricity", "heat_pump"), ("electricity", "electric_resistance"),
          ("fuel oil", "furnace"), ("fuel oil", "boiler"), ("propane", "furnace")]
for fuel, system in FORCED:
    st, lat, lon, fips = DIV["East North Central"]
    emit(*SFD, 2200, 1, [f"forced:{fuel}+{system}", "wf:res", "purpose:capture"],
         state=st, lat=lat, lon=lon, fips=fips, year=1990,
         overrides={"heating_system_fuel_type": fuel, "heating_system_type": system})

# --- Block F: branch / edge cases --------------------------------------------------------
st, lat, lon, fips = DIV["Mountain South"]
emit(*SFD, 2200, 1, ["branch:vacant", "wf:res"], state=st, lat=lat, lon=lon, fips="56039", year=1985,
     overrides={"vacant": True})                                   # high-vacancy county (Teton)
emit(8, "Office", 50000, 3, ["branch:vacant", "wf:com"], state=st, lat=lat, lon=lon, fips=fips, year=1990,
     overrides={"vacant": True})
emit(5, "Multifamily (2 to 4 units)", 4000, 2, ["subtype:MF2-4", "branch:units", "wf:res"],
     state="IL", lat=41.84, lon=-87.68, fips="17031", year=1995, overrides={"number_of_units": 3})
emit(6, "Multifamily (5 or more units)", 30000, 4, ["subtype:MF5+", "branch:units", "wf:res"],
     state="IL", lat=41.84, lon=-87.68, fips="17031", year=1995, overrides={"number_of_units": 24})
emit(29, "null", 2200, 1, ["subtype:null->SFD", "branch:archetype_fallback", "wf:res"],
     state="IL", lat=41.84, lon=-87.68, fips="17031", year=1995)

# --- Block G: sparsity / input-contract leaves -------------------------------------------
emit(*SFD, 2200, 1, ["sparse:no_year", "wf:res"], state="IL", lat=41.84, lon=-87.68, fips="17031")
emit(*SFD, 2200, 1, ["sparse:no_geo", "wf:res"], state="TX", year=1990)            # state+year only
emit(*SFD, 2200, 1, ["sparse:minimal", "wf:res"], state="CA")                       # state+area only
# Real footprint-source shape (OSM/Microsoft): county_fips + lat/lon but NO state.
# Exercises build_asset_ctx's county_fips->state derivation -- division must resolve
# (Mountain North) instead of falling to None/flat as a state-less asset used to.
emit(*SFD, 2200, 1, ["sparse:footprint_nostate", "geo:county_fips_only", "wf:res"],
     lat=43.58, lon=-110.83, fips="56039", year=1990)                               # Teton, WY; no state
# Neither state nor county_fips nor coords: exercises read_metadata's metadata-quality
# warning (no_geo_key) and the no-coords Failed path.
emit(*SFD, 2200, 1, ["sparse:no_geo_key", "branch:metadata_quality_warn", "wf:res"], year=1990)

# --- write outputs -----------------------------------------------------------------------
os.makedirs(OUT, exist_ok=True)
json.dump({"type": "FeatureCollection", "features": features},
          open(os.path.join(OUT, "leaf_stock_geometries.geojson"), "w"), indent=1)
with open(os.path.join(OUT, "leaf_stock_metadata.csv"), "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

# coverage summary: which leaf dimensions are hit
from collections import Counter
tags = Counter(t.split(":")[0] for tags in manifest.values() for t in tags)
covered = {dim: sorted({t.split(":", 1)[1] for ts in manifest.values() for t in ts if t.startswith(dim + ":")})
           for dim in ("div", "vint", "cz", "subtype", "branch", "forced", "geo", "wf", "sparse")}
json.dump({"buildings": len(rows), "manifest": manifest, "covered": covered},
          open(os.path.join(OUT, "leaf_manifest.json"), "w"), indent=1)

print(f"wrote {len(rows)} synthetic leaf buildings -> {OUT}")
print("coverage:")
for dim, vals in covered.items():
    print(f"  {dim:10s} {len(vals):2d}: {vals}")
