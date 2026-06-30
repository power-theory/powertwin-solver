#!/usr/bin/env python3
"""Compare annual energy consumption between current code and a cached baseline.

On first run (or with --save-baseline), runs all buildings and saves the
results as the baseline JSON. On subsequent runs, compares current sim
results against the cached baseline -- no need to re-run the old code.

Usage:
    # Save current results as the baseline (run after pushing):
    python3 tests/compare_consumption.py --save-baseline

    # Compare current sims against the saved baseline:
    python3 tests/compare_consumption.py

    # Compare two live arms (no baseline needed):
    python3 tests/compare_consumption.py --arms resolver flat

    # Use a specific container/port:
    python3 tests/compare_consumption.py --container powertwin-solver-flask
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO / "tests" / "baselines" / "consumption_baseline.json"
CONTAINER = os.environ.get("QA_CONTAINER", "powertwin-solver-flask")
DATA_DIR = "/solver/data"

FIXTURES = [
    # name                  label           asset_id  region       vintage
    ("office_small",        "Office",       9800,     "West",      "pre-1980"),
    ("office_medium",       "Office",       9801,     "Midwest",   "1980-1999"),
    ("office_large",        "Office",       9802,     "South",     "2000-2009"),
    ("education",           "Education",    9803,     "West",      "2010+"),
    ("lodging_small",       "Lodging",      9804,     "Northeast", "2010+"),
    ("lodging_large",       "Lodging",      9805,     "South",     "2000-2009"),
    ("food_service",        "Food service", 9806,     "South",     "1980-1999"),
    ("food_sales",          "Food sales",   9807,     "Midwest",   "2000-2009"),
    ("outpatient",          "Outpatient",   9808,     "West",      "1980-1999"),
    ("inpatient",           "Inpatient",    9809,     "Northeast", "2010+"),
    ("warehouse",           "Warehouse",    9810,     "South",     "pre-1980"),
    ("public_order",        "Public order", 9811,     "Midwest",   "2010+"),
    ("laboratory",          "Laboratory",   9812,     "Northeast", "2010+"),
    ("retail",              "Retail",       9813,     "Northeast", "1980-1999"),
    ("strip_mall",          "Strip mall",   9814,     "South",     "2000-2009"),
    ("public_assembly",     "Assembly",     9815,     "Midwest",   "pre-1980"),
    ("religious",           "Religious",    9816,     "West",      "1980-1999"),
    ("service",             "Service",      9817,     "Northeast", "2000-2009"),
    ("nursing",             "Nursing",      9818,     "South",     "2010+"),
    ("enclosed_mall",       "Enclosed mall",9819,     "Midwest",   "2000-2009"),
    ("mixed_use",           "Mixed use",    9820,     "West",      "2010+"),
    ("refrigerated_warehouse","Refrig WH",  9821,     "Northeast", "1980-1999"),
    ("vacant",              "Vacant",       9822,     "Midwest",   "pre-1980"),
    ("sfd",                 "SFD",          9823,     "West",      "2000-2009"),
    ("sfa",                 "SFA",          9824,     "South",     "1980-1999"),
    ("mf_small",            "MF small",     9825,     "Northeast", "2000-2009"),
    ("mf_large",            "MF large",     9826,     "Midwest",   "2010+"),
    ("mobile_home",         "Mobile home",  9827,     "South",     "1980-1999"),
]


def query_consumption(container, sim_name, asset_id):
    """Query total site energy from eplusout.sql inside the container."""
    geom_id = asset_id * 1000 + 1
    script = f"""
import sqlite3, json, glob, sys
pattern = '{DATA_DIR}/{sim_name}/urbanopt_simulation/run/powertwin_scenario_*/{geom_id}/eplusout.sql'
hits = glob.glob(pattern)
if not hits:
    print(json.dumps({{"error": "no eplusout.sql"}}))
    sys.exit(0)
try:
    con = sqlite3.connect(f'file:{{hits[0]}}?mode=ro', uri=True)
    cur = con.cursor()
    cur.execute("SELECT Value FROM TabularDataWithStrings "
                "WHERE TableName='Site and Source Energy' "
                "AND RowName='Total Site Energy' "
                "AND ColumnName='Total Energy' "
                "AND Units='GJ';")
    row = cur.fetchone()
    elec = None
    gas = None
    cur.execute("SELECT Value FROM TabularDataWithStrings "
                "WHERE TableName='End Uses' "
                "AND RowName='Total End Uses' "
                "AND ColumnName='Electricity' "
                "AND Units='GJ';")
    erow = cur.fetchone()
    if erow: elec = float(erow[0])
    cur.execute("SELECT Value FROM TabularDataWithStrings "
                "WHERE TableName='End Uses' "
                "AND RowName='Total End Uses' "
                "AND ColumnName='Natural Gas' "
                "AND Units='GJ';")
    grow = cur.fetchone()
    if grow: gas = float(grow[0])
    con.close()
    if row:
        print(json.dumps({{"gj": float(row[0]), "elec_gj": elec, "gas_gj": gas}}))
    else:
        print(json.dumps({{"error": "no row"}}))
except Exception as e:
    print(json.dumps({{"error": str(e)}}))
"""
    r = subprocess.run(
        ["docker", "exec", container, "python3", "-c", script],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    try:
        d = json.loads(r.stdout.strip())
        if "error" in d:
            return None
        return d
    except (json.JSONDecodeError, ValueError):
        return None


def collect_arm(container, arm_suffix):
    """Collect consumption for all fixtures with the given arm suffix."""
    results = {}
    for name, label, asset_id, region, vintage in FIXTURES:
        sim_name = f"qa_matrix_{name}_{arm_suffix}"
        data = query_consumption(container, sim_name, asset_id)
        if data:
            results[name] = {
                "label": label,
                "region": region,
                "vintage": vintage,
                "asset_id": asset_id,
                **data,
            }
    return results


def save_baseline(container, arm):
    """Collect results and save as baseline JSON."""
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    results = collect_arm(container, arm)
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, cwd=REPO,
    ).stdout.strip()
    baseline = {
        "commit": commit,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "arm": arm,
        "buildings": results,
    }
    BASELINE_PATH.write_text(json.dumps(baseline, indent=2))
    print(f"Baseline saved: {BASELINE_PATH}")
    print(f"  commit: {commit}")
    print(f"  buildings: {len(results)}/{len(FIXTURES)}")
    return baseline


def load_baseline():
    """Load the cached baseline JSON."""
    if not BASELINE_PATH.exists():
        return None
    return json.loads(BASELINE_PATH.read_text())


def print_comparison(current, baseline_buildings, baseline_meta):
    """Print side-by-side comparison table."""
    commit = baseline_meta.get("commit", "?")
    ts = baseline_meta.get("timestamp", "?")
    print(f"Baseline: commit {commit} ({ts})")
    print()
    print(f"{'Fixture':<22} {'Type':<12} {'Region':<10} {'Vintage':<10} "
          f"{'GJ(now)':>10} {'GJ(base)':>10} {'Delta':>8} {'%':>7}  "
          f"{'Elec':>8} {'Gas':>8}")
    print("-" * 130)

    total_now = 0.0
    total_base = 0.0
    count = 0
    changed = 0

    for name, label, asset_id, region, vintage in FIXTURES:
        cur = current.get(name)
        base = baseline_buildings.get(name)

        if cur and base:
            gj_now = cur["gj"]
            gj_base = base["gj"]
            delta = gj_now - gj_base
            pct = (delta / gj_base * 100) if gj_base != 0 else 0
            total_now += gj_now
            total_base += gj_base
            count += 1

            elec_delta = ""
            gas_delta = ""
            if cur.get("elec_gj") is not None and base.get("elec_gj") is not None:
                ed = cur["elec_gj"] - base["elec_gj"]
                elec_delta = f"{ed:>+7.1f}"
            if cur.get("gas_gj") is not None and base.get("gas_gj") is not None:
                gd = cur["gas_gj"] - base["gas_gj"]
                gas_delta = f"{gd:>+7.1f}"

            flag = " ***" if abs(pct) > 1.0 else ""
            if abs(pct) > 0.1:
                changed += 1
            print(f"{name:<22} {label:<12} {region:<10} {vintage:<10} "
                  f"{gj_now:>10.1f} {gj_base:>10.1f} {delta:>+8.1f} {pct:>+6.1f}%  "
                  f"{elec_delta:>8} {gas_delta:>8}{flag}")
        else:
            status = "N/A" if not cur else "no baseline"
            print(f"{name:<22} {label:<12} {region:<10} {vintage:<10} "
                  f"{'---':>10} {'---':>10} {'---':>8} {'---':>7}  "
                  f"{'':>8} {'':>8}  {status}")

    if count > 0:
        delta_total = total_now - total_base
        pct_total = (delta_total / total_base * 100) if total_base != 0 else 0
        print("-" * 130)
        print(f"{'TOTAL':<22} {'':12} {'':10} {'':10} "
              f"{total_now:>10.1f} {total_base:>10.1f} {delta_total:>+8.1f} {pct_total:>+6.1f}%")
        print(f"\n{count} buildings compared, {changed} changed >0.1%")
        if changed == 0:
            print("No significant consumption changes.")
    return count > 0 and changed == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--save-baseline", action="store_true",
                        help="Save current sim results as the comparison baseline")
    parser.add_argument("--arm", default="resolver",
                        help="Arm suffix to query (default: resolver)")
    parser.add_argument("--baseline-arm", default=None,
                        help="If set, compare two live arms instead of using cached baseline")
    parser.add_argument("--container", default=CONTAINER)
    args = parser.parse_args()

    if args.save_baseline:
        save_baseline(args.container, args.arm)
        return 0

    if args.baseline_arm:
        print(f"Comparing live arms: {args.arm} vs {args.baseline_arm}\n")
        current = collect_arm(args.container, args.arm)
        baseline_data = collect_arm(args.container, args.baseline_arm)
        meta = {"commit": "(live)", "timestamp": "now", "arm": args.baseline_arm}
        ok = print_comparison(current, baseline_data, meta)
        return 0 if ok else 2

    baseline = load_baseline()
    if not baseline:
        print(f"No baseline found at {BASELINE_PATH}")
        print("Run with --save-baseline first to create one.")
        return 1

    print(f"Comparing current {args.arm} arm against cached baseline\n")
    current = collect_arm(args.container, args.arm)
    ok = print_comparison(current, baseline["buildings"], baseline)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
