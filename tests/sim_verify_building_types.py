#!/usr/bin/env python3
"""
End-to-end simulation test for building types that haven't been sim-verified.
Submits one single-building sim per type, waits for completion, then checks
for EnergyPlus output (eplusout.sql) to confirm the sim ran to completion.

Usage:
    python3 tests/sim_verify_building_types.py [--api http://localhost:1338] [--types vacant,religious,...]
"""
import argparse
import csv
import json
import math
import pathlib
import requests
import sys
import time

API_BASE = "http://localhost:1338"
SIM_OUTPUT_DIR = pathlib.Path("/home/genesis/dev/power-theory/powertwin-solver/powertwin_data/user_files")
GEN_DIR = pathlib.Path("/home/genesis/dev/power-theory/powertwin-solver/tests/qa_matrix_fixtures/_generated")

REGION_COORDS = {
    "West":      {"state": "Arizona",  "city": "Phoenix", "lat": 33.4539, "lon": -112.0729},
    "South":     {"state": "Texas",    "city": "Houston", "lat": 29.7604, "lon": -95.3698},
    "Midwest":   {"state": "Illinois", "city": "Chicago", "lat": 41.8781, "lon": -87.6298},
    "Northeast": {"state": "Massachusetts", "city": "Boston", "lat": 42.3601, "lon": -71.0589},
}

TYPES_TO_VERIFY = [
    # name                     subtype_name              subtype_id  region      year   area    floors  effective_type
    ("vacant",                 "Vacant",                 7,          "Midwest",  1978,  10000,  1,      None),
    ("refrigerated_warehouse", "Refrigerated warehouse", 14,         "South",    1999,  30000,  1,      None),
    ("enclosed_mall",          "Enclosed mall",          23,         "Midwest",  2003,  100000, 2,      None),
    ("religious",              "Religious worship",      15,         "West",     1988,  20000,  1,      None),
    ("public_assembly",        "Public assembly",        16,         "Midwest",  1970,  40000,  2,      None),
    ("service",                "Service",                25,         "South",    2002,  10000,  1,      None),
    ("mobile_home",            "Mobile Home",            4,          "South",    1992,  1000,   1,      "Single-Family Detached"),
    ("mixed_use",              "Mixed use",              26,         "West",     2010,  80000,  4,      "Office"),
    ("nursing",                "Nursing",                20,         "South",    2014,  60000,  2,      None),
    ("mf_small",               "Multifamily (2 to 4 units)", 5,     "Midwest",  2000,  4000,   2,      "Multifamily"),
    ("mf_large",               "Multifamily (5 or more units)", 6,  "Midwest",  2015,  50000,  4,      "Multifamily"),
    ("sfa",                    "Single-Family Attached", 2,          "South",    1995,  1400,   2,      None),
]


def generate_geojson(name, geom_id, asset_id, asset_name, lat, lon, area, floors, out_dir):
    h = floors * 3
    side = math.sqrt(area / floors) * 0.00001
    geo = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [lon - side/2, lat + side/2],
                    [lon + side/2, lat + side/2],
                    [lon + side/2, lat - side/2],
                    [lon - side/2, lat - side/2],
                    [lon - side/2, lat + side/2],
                ]],
            },
            "properties": {
                "id": geom_id,
                "asset_id": asset_id,
                "name": asset_name,
                "floor_count": floors,
                "height": h,
                "base": 0,
            },
            "id": asset_id,
        }],
    }
    p = out_dir / f"simtest_{name}.geojson"
    p.write_text(json.dumps(geo, indent=2))
    return p


def generate_metadata(name, asset_id, geom_id, asset_name, subtype_id, subtype_name,
                      area, floors, lat, lon, city, state, year, out_dir):
    footprint = area / floors
    meta = {
        "area": area,
        "city": city,
        "state": state,
        "latitude": lat,
        "longitude": lon,
        "year_built": year,
        "floor_count": floors,
        "footprint_area": footprint,
    }
    geom_props = {
        "id": geom_id,
        "base": 0,
        "name": asset_name,
        "height": floors * 3,
        "asset_id": asset_id,
        "floor_count": floors,
    }
    p = out_dir / f"simtest_{name}_metadata.csv"
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "sensor_id", "sensor_type_id", "sensor_type_name",
            "asset_id", "asset_name", "asset_subtype_id",
            "asset_subtype_name", "asset_metadata",
            "asset_geometries_properties",
        ])
        w.writerow([
            asset_id, 1, "Electricity",
            asset_id, asset_name,
            subtype_id, subtype_name,
            json.dumps(meta), json.dumps(geom_props),
        ])
    return p


def clean_sim(api_base, sim_name):
    """Delete a sim if it exists."""
    r = requests.delete(f"{api_base}/api/simulation/delete/{sim_name}", timeout=30)
    return r.status_code


def run_sim(api_base, sim_name, geojson_path, metadata_path, dynamic_defaults=True):
    with geojson_path.open("rb") as gf, metadata_path.open("rb") as mf:
        files = {
            "asset_geojson_file": (geojson_path.name, gf, "application/geo+json"),
            "metadata_csv_file":  (metadata_path.name, mf, "text/csv"),
        }
        form = {
            "simulation_name": sim_name,
            "num_cores": "1",
            "dynamic_defaults": "true" if dynamic_defaults else "false",
        }
        r = requests.post(f"{api_base}/api/simulation/start",
                          files=files, data=form, timeout=None)
    return r.status_code, r.text[:500]


def check_cleaned_reports(sim_name, geom_id):
    """Check if the sim produced cleaned report CSVs (the final output).
    URBANOPT_KEEP_RUN_DIR=false deletes eplusout.sql, so we check cleaned_reports."""
    report_dir = SIM_OUTPUT_DIR / sim_name / "cleaned_reports" / str(geom_id)
    if not report_dir.exists():
        return None
    csvs = list(report_dir.glob("cleaned_predicted_*.csv"))
    if not csvs:
        return None
    total_rows = 0
    for csv_path in csvs:
        lines = csv_path.read_text().strip().splitlines()
        total_rows += max(0, len(lines) - 1)
    return {"files": len(csvs), "rows": total_rows, "names": [c.name for c in csvs]}


def check_failure_log(sim_name, geom_id):
    """Check for error indicators when cleaned reports are missing."""
    sim_dir = SIM_OUTPUT_DIR / sim_name
    if not sim_dir.exists():
        return "sim directory does not exist"
    uo_dir = sim_dir / "urbanopt_simulation"
    if not uo_dir.exists():
        contents = [f.name for f in sim_dir.iterdir()]
        return f"no urbanopt_simulation dir; sim_dir has: {contents}"
    run_dirs = list(uo_dir.glob(f"batch_*/{geom_id}"))
    if not run_dirs:
        return "no batch run dir for this geom_id"
    rd = run_dirs[0]
    contents = [f.name for f in rd.iterdir()] if rd.exists() else []
    err_files = list(rd.glob("eplusout.err"))
    if err_files:
        err = err_files[0].read_text(errors="replace")
        fatals = [l.strip() for l in err.splitlines() if "Fatal" in l or "Severe" in l]
        return "\n".join(fatals[-5:]) if fatals else f"run dir contents: {contents}"
    return f"run dir contents: {contents}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default=API_BASE)
    parser.add_argument("--types", default=None, help="comma-separated list of type names to test")
    args = parser.parse_args()

    GEN_DIR.mkdir(parents=True, exist_ok=True)

    types = TYPES_TO_VERIFY
    if args.types:
        wanted = set(args.types.split(","))
        types = [t for t in types if t[0] in wanted]

    results = []
    total_t0 = time.time()
    arms = [("dyn", True), ("flat", False)]

    for i, (name, subtype, sid, region, year, area, floors, eff) in enumerate(types):
        rc = REGION_COORDS[region]
        asset_id = 7700 + i
        geom_id = asset_id * 1000 + 1
        asset_name = f"SimTest {name.replace('_', ' ').title()}"
        lat = rc["lat"] + i * 0.001
        lon = rc["lon"] + i * 0.001

        # Generate files once per type
        geojson = generate_geojson(name, geom_id, asset_id, asset_name,
                                   lat, lon, area, floors, GEN_DIR)
        metadata = generate_metadata(name, asset_id, geom_id, asset_name,
                                     sid, subtype, area, floors,
                                     lat, lon, rc["city"], rc["state"], year, GEN_DIR)

        for arm_label, dyn_flag in arms:
            sim_name = f"simtest_{name}_{arm_label}"

            print(f"\n{'='*60}")
            print(f"[{i+1}/{len(types)}] {name} ({subtype}) - {region} [{arm_label}]")
            print(f"{'='*60}")

            # Clean existing sim
            clean_sim(args.api, sim_name)
            import subprocess
            subprocess.run(["docker", "exec", "powertwin-solver-flask",
                           "rm", "-rf", f"/solver/powertwin-solver-pg/user_files/{sim_name}"],
                          capture_output=True)

            # Run sim
            t0 = time.time()
            status, text = run_sim(args.api, sim_name, geojson, metadata, dynamic_defaults=dyn_flag)
            dur = time.time() - t0

            # Check result
            if status != 200:
                print(f"  FAIL: HTTP {status} - {text[:200]}")
                results.append((name, arm_label, subtype, "HTTP_ERROR", status, dur, text[:200]))
                continue

            print(f"  HTTP 200 in {dur/60:.1f} min")

            # Check for cleaned report CSVs (final pipeline output)
            report = check_cleaned_reports(sim_name, geom_id)
            if report and report["rows"] > 0:
                print(f"  PASS: {report['files']} cleaned reports, {report['rows']} data rows")
                results.append((name, arm_label, subtype, "PASS", report["rows"], dur, ""))
            else:
                fail_info = check_failure_log(sim_name, geom_id)
                print(f"  FAIL: no cleaned reports - {fail_info}")
                results.append((name, arm_label, subtype, "SIM_FAIL", 0, dur, fail_info))

    total_dur = time.time() - total_t0

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY ({total_dur/60:.1f} min total)")
    print(f"{'='*60}")
    passed = sum(1 for r in results if r[3] == "PASS")
    print(f"{passed}/{len(results)} passed\n")
    print(f"{'name':<25} {'arm':<5} {'subtype':<30} {'result':<12} {'detail':<10} {'time':>8}")
    print("-" * 95)
    for name, arm, subtype, result, detail, dur, info in results:
        mark = "PASS" if result == "PASS" else "FAIL"
        print(f"{name:<25} {arm:<5} {subtype:<30} {mark:<12} {str(detail):<10} {dur/60:>7.1f}m")
        if info and result != "PASS":
            for line in info.split("\n")[:3]:
                print(f"  >> {line}")

    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
