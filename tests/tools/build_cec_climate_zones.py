"""Build solver/upload/reference_data/cec_building_climate_zones.json: California's 16 building climate zones
(Title 24 Part 6), the zones the openstudio-standards DEER templates accept ("CEC T24-CEC1".."CEC T24-CEC16").

Source: the California Energy Commission "California Building Climate Zones" layer (ArcGIS FeatureServer
BuildingClimateZones_CEC_2015, WGS84, field BZone), fetched 2026-09-30 with its checksum in
/mnt/WD2002FAEX-007BA0-4/solver/tmp_clsf/cec_climate_zones/SHA256SUMS. Zones 3 and 6 are invalid polygons as published, so make_valid.

The published geometry is kept as is (gzipped, so it is an LFS object pinned in county_tables.sha256): even a
0.0002 degree simplification moved ZIP 94925, whose center sits 8 m from the zone 2/3 line. A --tolerance is
refused if it changes the zone of any check point (the interior points of the 1,752 California ZCTAs the CEC ZIP
list covers, Census 2020 gazetteer). Against that list the polygons agree on 96.7%; the misses span two zones.

  python3 tests/tools/build_cec_climate_zones.py [--src DIR] [--tolerance DEG]
"""
import argparse
import datetime
import hashlib
import json
import os

import pandas as pd
from shapely.geometry import Point, mapping, shape
from shapely.validation import make_valid

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(REPO, "solver", "upload", "reference_data", "cec_building_climate_zones.json.gz")
SRC = "/mnt/WD2002FAEX-007BA0-4/solver/tmp_clsf/cec_climate_zones"
SERVICE = ("https://services3.arcgis.com/bWPjFyq029ChCGur/arcgis/rest/services/BuildingClimateZones_CEC_2015/"
           "FeatureServer/0/query?where=1%3D1&outFields=*&outSR=4326&f=geojson")


def _round(geom, nd=6):
    def r(c):
        return [r(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], nd), round(c[1], nd)]
    m = mapping(geom)
    return {"type": m["type"], "coordinates": r(m["coordinates"])}


def zone_of(zones, lon, lat):
    pt = Point(lon, lat)
    hits = [z for z, g in zones if g.covers(pt)]
    return hits[0] if len(hits) == 1 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC)
    ap.add_argument("--tolerance", type=float, default=0.0)      # degrees; 0 keeps the published geometry
    a = ap.parse_args()
    src = os.path.join(a.src, "bcz_wgs84.geojson")
    raw = open(src, "rb").read()
    feats = json.loads(raw)["features"]
    full = [(f["properties"]["BZone"], make_valid(shape(f["geometry"]))) for f in feats]
    assert sorted(int(z) for z, _ in full) == list(range(1, 17)), "expected the 16 CEC zones"
    simp = full if a.tolerance <= 0 else [(z, make_valid(g.simplify(a.tolerance, preserve_topology=True))) for z, g in full]

    z = pd.read_excel(os.path.join(a.src, "BuildingClimateZonesByZIPCode_ada.xlsx"))
    z.columns = ["zip", "cz"]
    z["zip"] = z["zip"].astype(str).str.zfill(5)
    g = pd.read_csv(os.path.join(a.src, "2020_Gaz_zcta_national.txt"), sep="\t", dtype={"GEOID": str})
    g.columns = [c.strip() for c in g.columns]
    pts = z.merge(g[["GEOID", "INTPTLAT", "INTPTLONG"]], left_on="zip", right_on="GEOID").dropna()
    changed = [r.zip for r in pts.itertuples()
               if zone_of(full, r.INTPTLONG, r.INTPTLAT) != zone_of(simp, r.INTPTLONG, r.INTPTLAT)]
    assert not changed, f"tolerance {a.tolerance} moves {len(changed)} check points, e.g. {changed[:5]}"
    agree = sum(zone_of(simp, r.INTPTLONG, r.INTPTLAT) == str(r.cz) for r in pts.itertuples()) / len(pts)

    doc = {
        "_source": {
            "name": "California Energy Commission, California Building Climate Zones (Title 24 Part 6)",
            "url": SERVICE,
            "catalog": "https://data.ca.gov/dataset/california-building-climate-zones",
            "terms": "https://www.energy.ca.gov/conditions-of-use",
            "fetched": "2026-09-30",
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "processing": "make_valid; " + (f"simplify {a.tolerance} deg (topology preserved); " if a.tolerance > 0 else "published geometry kept; ") + "coordinates to 6 dp",
            "zip_list": "BuildingClimateZonesByZIPCode_ada.xlsx (CEC), " + f"{len(z)} ZIPs",
            "check": f"0 of {len(pts)} ZCTA interior points change zone vs the full polygons; "
                     f"{agree:.1%} match the CEC ZIP list (misses are multi-zone ZIPs)",
            "extracted": datetime.date.today().isoformat(),
            "regen": "tests/tools/build_cec_climate_zones.py",
        },
        "zones": {zn: _round(geom) for zn, geom in sorted(simp, key=lambda t: int(t[0]))},
        # the CEC's own ZIP assignment, the fallback for a point outside every polygon (Channel Islands: Avalon 90704)
        "zip_zones": {str(r.zip): str(r.cz) for r in z.itertuples()},
    }
    import gzip
    with gzip.open(OUT, "wt") as f:
        json.dump(doc, f, separators=(",", ":"))
    print(f"wrote {os.path.relpath(OUT, REPO)} ({os.path.getsize(OUT):,} bytes); {doc['_source']['check']}")


if __name__ == "__main__":
    main()
