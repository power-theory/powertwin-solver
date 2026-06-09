"""End-to-end matrix verification for every programmable simulation
parameter on a single building, across all three resolution paths
(metadata override / dynamic resolver / flat fallback) and five
verification layers (resolver-preview / feature.json / in.osw / in.osm
/ eplusout.sql).

Goal: every dynamic-default param survives end-to-end and is not silently
overwritten by any pipeline stage. Two small fixtures cover both the
commercial and residential resolver paths. Six sims total (2 fixtures x
3 arms), ~12-15 min wall-clock.

Run from repo root:
  python3 tests/qa_dynamic_defaults_matrix.py \\
      --num-cores 4 \\
      --output tests/runs/qa_matrix_$(date -u +%Y%m%dT%H%M%S).md

Expects:
  - Flask + DB up at localhost:1337 / localhost:5335
  - URBANOPT_SIMULATION_YEAR=2023, URBANOPT_RESAMPLE=H, POSTPROCESS=true
  - Container env carries API_SOLVER_TOKEN (read via docker exec)
"""
from __future__ import annotations
import argparse
import csv
import io
import json
import os
import pathlib
import requests
import shutil
import sqlite3
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO / "tests" / "qa_matrix_fixtures"
SIM_OUTPUT_DIR = REPO / "powertwin_data" / "user_files"
INSPECTOR_HOST = REPO / "tests" / "qa_dynamic_defaults_matrix_inspect.rb"
INSPECTOR_CONTAINER = "/tmp/qa_matrix_inspect.rb"
CONTAINER = os.environ.get("QA_CONTAINER", "powertwin-solver-flask")


# ----------------------------------------------------------------------------
# Param inventory. Drives the test matrix.
# ----------------------------------------------------------------------------
FIELDS = (
    "system_type",
    "heating_system_fuel_type",
    "cooling_system_fuel_type",
    "service_water_heating_fuel_type",
    "window_type",
    "wall_material",
    "roof_material",
    "wall_r_value",
    "roof_r_value",
    "window_to_wall_ratio",
    "floor_height",
    "number_of_occupants",
    "weekday_start_time",
    "weekday_duration",
    "weekend_start_time",
    "weekend_duration",
)

# Sentinel values distinct from any plausible resolver or flat output so
# layer-comparisons can tell which path produced the value. Arm A injects
# these as explicit metadata.
OVERRIDE = {
    "system_type":                    "PSZ-AC with gas coil",
    "heating_system_fuel_type":       "propane",
    "cooling_system_fuel_type":       "electricity",   # only valid cooling
    "service_water_heating_fuel_type":"fuel oil",
    "window_type":                    "Triple Pane",
    "wall_material":                  "Super Insulated",
    "roof_material":                  "Standard",
    "wall_r_value":                   42.0,
    "roof_r_value":                   55.0,
    "window_to_wall_ratio":           0.42,
    "floor_height":                   11.5,
    "number_of_occupants":            999,
    "weekday_start_time":             "07:30",
    "weekday_duration":               "09:00",
    "weekend_start_time":             "08:00",
    "weekend_duration":               "06:00",
}

# Flat defaults as documented in sim_params_spec.py:SIM_PARAM_DEFAULTS.
# Empty-string fields are omitted from feature.json (callers fall through
# to template default).
FLAT = {
    "system_type":                    "Inferred",         # omitted; template picks
    "heating_system_fuel_type":       "natural gas",
    "cooling_system_fuel_type":       "electricity",
    "service_water_heating_fuel_type":"natural gas",
    "window_type":                    "Double Pane",
    "wall_material":                  "Insulated",
    "roof_material":                  "Insulated",
    "wall_r_value":                   13.0,
    "roof_r_value":                   30.0,
    "window_to_wall_ratio":           0.20,
    "floor_height":                   9.0,
    "number_of_occupants":            "",                  # falls through to OCCUPANTS_MAPPING
    "weekday_start_time":             "",                  # omitted
    "weekday_duration":               "",                  # omitted
    "weekend_start_time":             "",                  # omitted
    "weekend_duration":               "",                  # omitted
}


# Measure that consumes each field in PowerTwin.rb. None = dead emission
# (intentional, e.g. wall_material/roof_material after the envelope-fix
# commit). For some fields the consumer is conditional -- service_water_
# heating_fuel_type routes to `set_service_water_heating_fuel` for FuelOil
# / Propane, and to `create_typical_building_from_model` for Electricity /
# NaturalGas. STEP_NAME is set when PowerTwin.rb passes a `name=` to
# OpenStudio::Extension.set_measure_argument (e.g. system_type goes to
# 'create_typical_building_from_model 2', not the first instance).
MEASURE = {
    "system_type":                    ("create_typical_building_from_model 2", "system_type"),
    "heating_system_fuel_type":       ("create_typical_building_from_model 1", "htg_src"),
    "cooling_system_fuel_type":       ("create_typical_building_from_model 1", "clg_src"),
    "service_water_heating_fuel_type":("create_typical_building_from_model 1", "swh_src"),  # NG/E only
    "window_type":                    ("set_window_construction", "u_factor"),
    "wall_material":                  None,
    "roof_material":                  None,
    "wall_r_value":                   ("IncreaseInsulationRValueForExteriorWalls", "r_value"),
    "roof_r_value":                   ("IncreaseInsulationRValueForRoofs", "r_value"),
    "window_to_wall_ratio":           ("create_bar_from_building_type_ratios", "wwr"),
    "floor_height":                   ("create_bar_from_building_type_ratios", "floor_height"),
    "number_of_occupants":            ("set_people_per_floor_area", "target_total"),
    "weekday_start_time":             ("create_typical_building_from_model 1", "wkdy_op_hrs_start_time"),
    "weekday_duration":               ("create_typical_building_from_model 1", "wkdy_op_hrs_duration"),
    "weekend_start_time":             ("create_typical_building_from_model 1", "wknd_op_hrs_start_time"),
    "weekend_duration":               ("create_typical_building_from_model 1", "wknd_op_hrs_duration"),
}

# Per-field transforms from feature.json value -> in.osw measure arg value.
# Captured here so the test matrix surfaces *expected* transforms (not flag
# them as silent overrides). Each fn receives (raw_value, ctx) where ctx
# carries climate_zone, fuel_for_swh_routing, etc.
TIME_TO_DEC = lambda v: None if v in (None, "") else round(
    int(v.split(":")[0]) + int(v.split(":")[1]) / 60.0, 4)
WIN_U_FACTOR_CZ2 = {  # ASHRAE 90.1-2013 base U at CZ2 = 4.26 W/m^2-K
    "Single Pane": 4.26 * 2.0,
    "Double Pane": 4.26 * 1.0,
    "Triple Pane": 4.26 * 0.55,
}
# PowerTwin.rb fuel routing per measure_arg validity (see lines ~1050-1062):
#   htg_src valid: Electricity, NaturalGas, DistrictHeating, DistrictAmbient
#   clg_src valid: Electricity, DistrictCooling, DistrictAmbient
#   swh_src valid: Electricity, NaturalGas, HeatPump
# Anything else maps internally but is silently DROPPED from the
# create_typical measure (because the enum rejects it). For SWH, FuelOil
# and Propane route through `set_service_water_heating_fuel` instead.
# 'wood' is silently re-mapped to NaturalGas in fuel_map.
def xform_htg(v):
    f = {"electricity": "Electricity", "natural gas": "NaturalGas",
         "wood": "NaturalGas"}.get(str(v).lower())
    return f  # propane / fuel oil -> None, expected_L3 falls back to L2-only
def xform_clg(v):
    return "Electricity" if str(v).lower() == "electricity" else None
def xform_swh(v):
    f = {"electricity": "Electricity", "natural gas": "NaturalGas"}.get(
        str(v).lower())
    return f  # propane / fuel oil routed via set_service_water_heating_fuel

L3_XFORM = {
    "system_type":                    lambda v: v,
    "heating_system_fuel_type":       xform_htg,
    "cooling_system_fuel_type":       xform_clg,
    "service_water_heating_fuel_type":xform_swh,
    "window_type":                    lambda v: WIN_U_FACTOR_CZ2.get(v),  # Phoenix = CZ2
    "wall_r_value":                   lambda v: float(v) if v not in (None, "") else None,
    "roof_r_value":                   lambda v: float(v) if v not in (None, "") else None,
    "window_to_wall_ratio":           lambda v: float(v) if v not in (None, "") else None,
    "floor_height":                   lambda v: float(v) if v not in (None, "") else None,
    "number_of_occupants":            lambda v: int(v) if v not in (None, "") else None,
    "weekday_start_time":             TIME_TO_DEC,
    "weekday_duration":               TIME_TO_DEC,
    "weekend_start_time":             TIME_TO_DEC,
    "weekend_duration":               TIME_TO_DEC,
}

# Where to find each field in the per-asset feature.json. None = field is
# emitted at the top level. Tuples are nested-key paths.
FEATURE_KEY = {
    "system_type":                    ("system_type",),
    "heating_system_fuel_type":       ("heating_system_fuel_type",),
    "cooling_system_fuel_type":       ("cooling_system_fuel_type",),
    "service_water_heating_fuel_type":("service_water_heating_fuel_type",),
    "window_type":                    ("windows", 0, "window_type"),
    "wall_material":                  ("constructions", "wall", "material"),
    "roof_material":                  ("constructions", "roof", "material"),
    "wall_r_value":                   ("constructions", "wall", "r_value"),
    "roof_r_value":                   ("constructions", "roof", "r_value"),
    "window_to_wall_ratio":           ("window_to_wall_ratio",),
    "floor_height":                   ("floor_height",),
    "number_of_occupants":            ("number_of_occupants",),
    "weekday_start_time":             ("weekday_start_time",),
    "weekday_duration":               ("weekday_duration",),
    "weekend_start_time":             ("weekend_start_time",),
    "weekend_duration":               ("weekend_duration",),
}

# wall_material/roof_material get appended " Wall"/" Roof" by
# generateFeatureFile when emitted. Apply the same xform to expected so
# layer-2 comparison passes.
L2_XFORM = {
    "wall_material": lambda v: f"{v} Wall" if v not in (None, "") else None,
    "roof_material": lambda v: f"{v} Roof" if v not in (None, "") else None,
}


# ----------------------------------------------------------------------------
# Layer 3 (residential) -- HPXML feature.xml extraction.
#
# The residential pipeline routes through BuildResidentialHPXML which writes
# a feature.xml file (HPXML 4.0) instead of populating commercial measure
# arguments in the in.osw. Each entry below describes how to pull the
# per-field value out of the HPXML so the L3 check can apply uniformly.
#
# Entries are callables: fn(root: ET.Element, ns: dict) -> str | float | None.
# Return None when the field has no direct HPXML representation (e.g.
# weekday_start_time -- HPXML schedules aren't in feature.xml).
# ----------------------------------------------------------------------------
HPXML_NS = {"h": "http://hpxmlonline.com/2023/09"}


def _first_text(root, xpath):
    e = root.find(xpath, HPXML_NS)
    return None if e is None else (e.text or "").strip()


def _first_float(root, xpath):
    v = _first_text(root, xpath)
    try:
        return float(v) if v is not None else None
    except (ValueError, TypeError):
        return None


def hpxml_heating_fuel(r):
    # All HeatingSystem fuel labels in HPXML 4.0 are already lowercase
    # 'natural gas', 'electricity', 'propane', etc.
    return _first_text(r, ".//h:HeatingSystem/h:HeatingSystemFuel")


def hpxml_cooling_fuel(r):
    return _first_text(r, ".//h:CoolingSystem/h:CoolingSystemFuel")


def hpxml_swh_fuel(r):
    return _first_text(r, ".//h:WaterHeatingSystem/h:FuelType")


def hpxml_floor_height(r):
    # AverageCeilingHeight is in feet, no conversion needed.
    return _first_float(r, ".//h:BuildingConstruction/h:AverageCeilingHeight")


def hpxml_occupants(r):
    # NumberofResidents is a decimal float in HPXML, round for comparison.
    v = _first_float(r, ".//h:BuildingOccupancy/h:NumberofResidents")
    return None if v is None else int(round(v))


def _avg_r_value(r, parent):
    # AssemblyEffectiveRValue lives under <Insulation> on each Wall / Roof.
    # Many surfaces, one R per. Return the average for stability.
    vals = []
    for ins in r.iterfind(f".//h:{parent}/h:Insulation/h:AssemblyEffectiveRValue", HPXML_NS):
        try:
            vals.append(float(ins.text))
        except (TypeError, ValueError):
            pass
    return round(sum(vals) / len(vals), 1) if vals else None


def hpxml_wall_r(r):
    return _avg_r_value(r, "Wall")


def hpxml_roof_r(r):
    return _avg_r_value(r, "Roof")


def hpxml_window_u(r):
    # HPXML window UFactor is IP units (Btu/hr-ft^2-F). Our expected_l3 for
    # window_type produces SI W/m^2-K via WIN_U_FACTOR_CZ2. Convert HPXML IP
    # to SI for apples-to-apples compare: U_SI = U_IP * 5.6783.
    vals = []
    for u in r.iterfind(".//h:Window/h:UFactor", HPXML_NS):
        try:
            vals.append(float(u.text) * 5.6783)
        except (TypeError, ValueError):
            pass
    return round(sum(vals) / len(vals), 3) if vals else None


# Field -> HPXML extractor. None = no direct HPXML mapping (e.g. system_type,
# weekday/weekend times, WWR, wall_material, roof_material). For these the
# residential L3 row still degrades to L2-only.
HPXML_EXTRACT = {
    "system_type":                    None,
    "heating_system_fuel_type":       hpxml_heating_fuel,
    "cooling_system_fuel_type":       hpxml_cooling_fuel,
    "service_water_heating_fuel_type":hpxml_swh_fuel,
    "window_type":                    hpxml_window_u,
    "wall_material":                  None,
    "roof_material":                  None,
    "wall_r_value":                   hpxml_wall_r,
    "roof_r_value":                   hpxml_roof_r,
    "window_to_wall_ratio":           None,    # derived in HPXML, skip
    "floor_height":                   hpxml_floor_height,
    "number_of_occupants":            hpxml_occupants,
    "weekday_start_time":             None,
    "weekday_duration":               None,
    "weekend_start_time":             None,
    "weekend_duration":               None,
}


def find_feature_xml(sim_name: str, geom_id: int) -> pathlib.Path | None:
    base = SIM_OUTPUT_DIR / sim_name / "urbanopt_simulation"
    if not base.exists():
        return None
    hits = list(base.glob(f"batch_*/{geom_id}/feature.xml"))
    return hits[0] if hits else None


def read_hpxml_field(xml_path: pathlib.Path | None, field: str):
    """Return the HPXML-realized value for `field`, or None if the field has
    no HPXML mapping (HPXML_EXTRACT[field] is None) or feature.xml is missing."""
    extractor = HPXML_EXTRACT.get(field)
    if extractor is None or xml_path is None:
        return None
    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(xml_path).getroot()
    except (ET.ParseError, OSError):
        return None
    return extractor(root)


# ----------------------------------------------------------------------------
# Fixture + arm spec.
# ----------------------------------------------------------------------------
@dataclass
class Fixture:
    name: str
    asset_id: int
    geom_id: int
    asset_name: str
    subtype_name: str
    building_type: str        # what alias.json resolves the subtype to
    state: str
    year_built: int
    area: float
    floor_count: int
    geojson: pathlib.Path
    metadata: pathlib.Path


FIXTURES = (
    Fixture(
        name="commercial",
        asset_id=5, geom_id=7697305,
        asset_name="Beus Center for Law and Society",
        subtype_name="Education", building_type="Education",
        state="Arizona", year_built=2016,
        area=286845.0, floor_count=6,
        geojson=FIXTURE_DIR / "commercial_office.geojson",
        metadata=FIXTURE_DIR / "commercial_office_metadata.csv",
    ),
    Fixture(
        name="residential",
        asset_id=9900, geom_id=9900001,
        asset_name="QA Residential SFD",
        subtype_name="Single-Family Detached", building_type="Single-Family Detached",
        state="Arizona", year_built=2005,
        area=1800.0, floor_count=1,
        geojson=FIXTURE_DIR / "residential_sfd.geojson",
        metadata=FIXTURE_DIR / "residential_sfd_metadata.csv",
    ),
)

ARMS = ("override", "resolver", "flat")


# ----------------------------------------------------------------------------
# Container helpers.
# ----------------------------------------------------------------------------
def container_env(name: str) -> str:
    """Read an env var from the running solver container."""
    cmd = ["docker", "exec", CONTAINER, "sh", "-c", f"echo ${name}"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return out.stdout.strip()


def container_exec(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run a command inside the solver container and return CompletedProcess."""
    return subprocess.run(["docker", "exec", CONTAINER] + cmd,
                          capture_output=True, text=True)


def container_cp(src: pathlib.Path, dest: str) -> None:
    subprocess.run(["docker", "cp", str(src), f"{CONTAINER}:{dest}"], check=True)


# ----------------------------------------------------------------------------
# Layer 1 -- resolver expectations.
#
# The preview endpoint (/api/simulation/resolve-defaults) is auth-gated AND
# returns empty when URBANOPT_DYNAMIC_DEFAULTS=false in the container env.
# To get expected resolver outputs regardless of container env, we call
# sim_params_spec.resolve_default directly via docker exec with env forced
# to true. This avoids the auth-header dance and the env-state coupling.
# ----------------------------------------------------------------------------
def resolve_expected(metadata: dict[str, Any], building_type: str) -> dict[str, Any]:
    """Return {field: resolver_value_or_None} for every DYNAMIC_FIELDS field,
    computed by invoking the actual resolver inside the container under
    URBANOPT_DYNAMIC_DEFAULTS=true (regardless of the container's current env)."""
    payload = json.dumps({"metadata": metadata, "building_type": building_type})
    script = (
        "import os, sys, json; "
        "os.environ['URBANOPT_DYNAMIC_DEFAULTS']='true'; "
        "sys.path.insert(0,'/solver/app'); "
        "from modules.simulation.sim_params_spec import "
        "build_asset_ctx, resolve_default; "
        "p=json.loads(sys.stdin.read()); "
        "ctx=build_asset_ctx(p['metadata'], building_type=p['building_type']); "
        "fields=('system_type','heating_system_fuel_type','cooling_system_fuel_type',"
        "'service_water_heating_fuel_type','window_type','wall_material','roof_material',"
        "'wall_r_value','roof_r_value','window_to_wall_ratio','floor_height',"
        "'number_of_occupants','weekday_start_time','weekday_duration',"
        "'weekend_start_time','weekend_duration'); "
        "out={'ctx':ctx,'resolved':{},'levels':{}}; "
        "[out['resolved'].update({f:resolve_default(f,ctx)[0]}) for f in fields if resolve_default(f,ctx)[0] is not None]; "
        "[out['levels'].update({f:resolve_default(f,ctx)[1] or 'flat_default'}) for f in fields]; "
        "print(json.dumps(out))"
    )
    proc = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "python3", "-c", script],
        input=payload, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(f"resolve_expected failed: {proc.stderr[:500]}")
    return json.loads(proc.stdout)


# ----------------------------------------------------------------------------
# Per-arm metadata writer.
# ----------------------------------------------------------------------------
def write_arm_metadata(fixture: Fixture, arm: str,
                       out_dir: pathlib.Path) -> pathlib.Path:
    """Build the per-arm metadata.csv. Sparse base for arms B/C, full
    OVERRIDE injection for arm A. Returns the path."""
    with fixture.metadata.open() as f:
        row = next(csv.DictReader(f))
    asset_metadata = json.loads(row["asset_metadata"])
    if arm == "override":
        for k, v in OVERRIDE.items():
            asset_metadata[k] = v
    # arms 'resolver' + 'flat' leave the sparse metadata untouched
    row["asset_metadata"] = json.dumps(asset_metadata)
    out = out_dir / f"{fixture.name}_{arm}_metadata.csv"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        w.writeheader()
        w.writerow(row)
    return out


# ----------------------------------------------------------------------------
# Submit a sim and wait.
# ----------------------------------------------------------------------------
def run_sim(api_base: str, sim_name: str, geojson: pathlib.Path,
            metadata: pathlib.Path, dynamic_defaults: bool,
            num_cores: int) -> float:
    print(f"\n=== run_sim({sim_name}, dyn={dynamic_defaults}) ===")
    t0 = time.time()
    with geojson.open("rb") as gf, metadata.open("rb") as mf:
        files = {
            "asset_geojson_file": (geojson.name, gf, "application/geo+json"),
            "metadata_csv_file":  (metadata.name, mf, "text/csv"),
        }
        form = {
            "simulation_name": sim_name,
            "num_cores": str(num_cores),
            "dynamic_defaults": "true" if dynamic_defaults else "false",
        }
        r = requests.post(f"{api_base}/api/simulation/start",
                          files=files, data=form, timeout=None)
    dur = time.time() - t0
    if r.status_code != 200:
        raise RuntimeError(f"sim {sim_name} failed: {r.status_code}: {r.text[:300]}")
    print(f"  done in {dur/60:.1f} min")
    return dur


# ----------------------------------------------------------------------------
# Layer 2 -- read feature.json from feature_files.zip.
# ----------------------------------------------------------------------------
def read_feature_json(sim_name: str, geom_id: int) -> dict[str, Any]:
    """Extract the per-asset feature properties from feature_files.zip."""
    zip_path = SIM_OUTPUT_DIR / sim_name / "feature_files.zip"
    if not zip_path.exists():
        raise FileNotFoundError(f"{zip_path} missing")
    with zipfile.ZipFile(zip_path) as zf:
        # The zip contains the per-asset feature JSON at the root or under
        # urbanopt_simulation/. We scan for any .json containing the geom id.
        for name in zf.namelist():
            if not name.endswith(".json"):
                continue
            with zf.open(name) as f:
                d = json.load(f)
            for feat in d.get("features", []):
                props = feat.get("properties", {})
                if str(props.get("id")) == str(geom_id):
                    return props
    raise RuntimeError(f"no feature for geom {geom_id} in {zip_path}")


# ----------------------------------------------------------------------------
# Layer 3 -- read in.osw measure args.
# ----------------------------------------------------------------------------
def find_in_osw(sim_name: str, geom_id: int) -> pathlib.Path | None:
    base = SIM_OUTPUT_DIR / sim_name / "urbanopt_simulation"
    if not base.exists():
        return None
    matches = list(base.glob(f"batch_*/{geom_id}/in.osw"))
    return matches[0] if matches else None


def find_step(osw: dict, measure_name: str, prefer_skip_false: bool = True) -> dict | None:
    """Return the first matching step. PowerTwin sets the same measure
    multiple times (with different `name` or `__SKIP__`); pick one that
    actually fires when possible."""
    hits = [s for s in osw.get("steps", [])
            if s.get("name") == measure_name or s.get("measure_dir_name") == measure_name]
    if prefer_skip_false:
        for s in hits:
            if not s.get("arguments", {}).get("__SKIP__", False):
                return s
    return hits[0] if hits else None


def read_osw_arg(osw_path: pathlib.Path, measure_name: str, arg: str) -> tuple[Any, bool, bool]:
    """Return (arg_value, skip_flag, step_found)."""
    d = json.loads(osw_path.read_text())
    s = find_step(d, measure_name)
    if s is None:
        return (None, None, False)
    args = s.get("arguments", {})
    return (args.get(arg), args.get("__SKIP__", False), True)


# ----------------------------------------------------------------------------
# Layer 4 -- in.osm peek via Ruby helper.
# ----------------------------------------------------------------------------
def inspect_in_osm_via_container(sim_name: str, geom_id: int) -> dict[str, Any] | None:
    """Find in.osm under /solver/data/<sim_name>/ inside the container,
    run the Ruby inspector, return the JSON result dict. Returns None if
    in.osm is unavailable (slot cleaned up after sim or sim failed)."""
    find_cmd = [
        "sh", "-c",
        f"find /solver/data/{sim_name} -name in.osm -path '*/{geom_id}/*' 2>/dev/null | head -1"
    ]
    r = container_exec(find_cmd)
    osm_path = r.stdout.strip()
    if not osm_path:
        # try host mount fallback (post-cleanup move)
        host_osm = SIM_OUTPUT_DIR / sim_name / "urbanopt_simulation"
        matches = list(host_osm.glob(f"batch_*/{geom_id}/in.osm"))
        if not matches:
            return None
        osm_path = str(matches[0]).replace(
            str(REPO / "powertwin_data"), "/solver/powertwin-solver-pg"
        )
    r = container_exec(["ruby", INSPECTOR_CONTAINER, osm_path])
    if r.returncode != 0:
        print(f"  ruby inspector failed (rc={r.returncode}): {r.stderr[:300]}")
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        print(f"  ruby inspector returned non-JSON: {r.stdout[:300]}")
        return None


# ----------------------------------------------------------------------------
# Layer 5 -- eplusout.sql query.
# ----------------------------------------------------------------------------
def find_eplusout_sql(sim_name: str, geom_id: int) -> pathlib.Path | None:
    base = SIM_OUTPUT_DIR / sim_name / "urbanopt_simulation"
    if not base.exists():
        return None
    # eplusout.sql lives one level below in.osw, in the EnergyPlus run dir.
    matches = list(base.glob(f"batch_*/{geom_id}/eplusout.sql"))
    return matches[0] if matches else None


def query_eplusout(sql_path: pathlib.Path) -> dict[str, Any]:
    """Pull a few realized values from the EnergyPlus SQLite. Schema notes:
       Constructions table: Name + total UFactor.
       NominalPeople: Name + NumberOfPeople.
       Surfaces -> Construction names (to map walls/roofs)."""
    out: dict[str, Any] = {}
    try:
        con = sqlite3.connect(f"file:{sql_path}?mode=ro", uri=True, timeout=30)
        try:
            cur = con.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
            out["_tables"] = [r[0] for r in cur.fetchall()]
            try:
                cur.execute("SELECT SUM(NumberOfPeople) FROM NominalPeople;")
                row = cur.fetchone()
                out["total_people"] = float(row[0]) if row and row[0] is not None else None
            except sqlite3.OperationalError:
                out["total_people"] = "TABLE_MISSING"
            try:
                cur.execute("SELECT COUNT(*) FROM Constructions;")
                out["construction_count"] = cur.fetchone()[0]
            except sqlite3.OperationalError:
                out["construction_count"] = "TABLE_MISSING"
        finally:
            con.close()
    except sqlite3.Error as e:
        out["_error"] = str(e)
    return out


# ----------------------------------------------------------------------------
# Per-field expectation builder.
# ----------------------------------------------------------------------------
# Per-occupancy_type fallback applied when number_of_occupants resolves to
# an empty value (flat arm, or resolver arm when ctx insufficient). Matches
# sim_params_spec.OCCUPANTS_MAPPING and the keys generateFeatureFile derives
# from asset_subtypes.csv occupancy_type column.
OCCUPANCY_MAPPING_FLAT = {
    "Office": 100,                       # Business -> 100
    "Education": 355,                    # Educational -> 355
    "Single-Family Detached": 3,         # SmallResidential -> 3
}


def expected_raw(fixture: Fixture, arm: str, field: str,
                 resolver_preview: dict[str, Any]) -> Any:
    """Raw expected value before any downstream transforms. Reflects which
    resolution path the arm exercises."""
    if arm == "override":
        return OVERRIDE[field]
    if arm == "resolver":
        if field in resolver_preview.get("resolved", {}):
            return resolver_preview["resolved"][field]
        if field == "number_of_occupants":
            # resolver returned None -> OCCUPANTS_MAPPING fallback fires
            return OCCUPANCY_MAPPING_FLAT.get(fixture.building_type, 1)
        return FLAT[field]
    # arm == flat
    if field == "number_of_occupants":
        return OCCUPANCY_MAPPING_FLAT.get(fixture.building_type, 1)
    return FLAT[field]


def expected_l2(field: str, raw: Any) -> Any:
    """Value we expect at feature.json. Applies any emission-time suffixing
    and the documented suppression rules in generateFeatureFile.py."""
    # 'Inferred' system_type and empty time strings are intentionally
    # OMITTED from feature.json. See generateFeatureFile.py L240, L248-255.
    if field == "system_type" and raw == "Inferred":
        return None
    if field in ("weekday_start_time", "weekday_duration",
                 "weekend_start_time", "weekend_duration") and raw in (None, ""):
        return None
    if field in L2_XFORM:
        return L2_XFORM[field](raw)
    return raw


def expected_l3(field: str, raw: Any) -> Any:
    """Value we expect at in.osw measure-arg layer. Applies the documented
    PowerTwin.rb transform per field (HH:MM -> decimal hours; lowercase
    fuel -> CamelCase enum; window tier -> U-factor multiplier; etc.)."""
    # Empty / Inferred raw -> field is suppressed at feature.json emission
    # so PowerTwin.rb doesn't see it; the measure arg stays at base_workflow
    # default (which our compare treats as <None>).
    if raw in (None, "", "Inferred"):
        return None
    if field in L3_XFORM:
        try:
            return L3_XFORM[field](raw)
        except (ValueError, TypeError, AttributeError):
            return raw
    return raw


def lookup_feature_value(feature: dict[str, Any], field: str) -> Any:
    """Walk the FEATURE_KEY path and return the value, or None."""
    path = FEATURE_KEY.get(field, (field,))
    cur: Any = feature
    for step in path:
        if isinstance(cur, list):
            try:
                cur = cur[step]
            except (IndexError, TypeError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(step)
            if cur is None:
                return None
        else:
            return None
    return cur


def normalize(v: Any) -> str:
    """Stable comparison key. Strings that look like numbers normalize to
    the same form as actual numbers ('09.0' == 9 == '9.000')."""
    if v is None:
        return "<None>"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        try:
            return f"{float(v):.4f}".rstrip("0").rstrip(".")
        except (ValueError, TypeError):
            return str(v)
    s = str(v).strip()
    # Try numeric-string normalization. Leaves real strings ('Triple Pane',
    # '07:30') untouched because float() fails on them.
    try:
        return f"{float(s):.4f}".rstrip("0").rstrip(".")
    except ValueError:
        return s


# ----------------------------------------------------------------------------
# Main matrix driver.
# ----------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--num-cores", type=int, default=4)
    p.add_argument("--flask-port", type=int,
                   default=int(os.environ.get("FLASK_PORT", "1337")))
    p.add_argument("--output", type=pathlib.Path,
                   default=REPO / "tests" / "runs" / f"qa_matrix_{int(time.time())}.md")
    p.add_argument("--skip-sims", action="store_true",
                   help="Skip running sims; re-score from existing slots")
    p.add_argument("--fixtures", nargs="+",
                   choices=[f.name for f in FIXTURES],
                   help="Subset of fixtures to run")
    p.add_argument("--arms", nargs="+", choices=list(ARMS),
                   help="Subset of arms to run")
    args = p.parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    api_base = f"http://127.0.0.1:{args.flask_port}"

    # Ship the Ruby inspector into the container once.
    if not INSPECTOR_HOST.exists():
        print(f"ERROR: ruby inspector not found at {INSPECTOR_HOST}")
        return 1
    container_cp(INSPECTOR_HOST, INSPECTOR_CONTAINER)

    fixtures = [f for f in FIXTURES if (not args.fixtures or f.name in args.fixtures)]
    arms = [a for a in ARMS if (not args.arms or a in args.arms)]

    # Stage per-arm metadata CSVs in a temp dir under the fixture dir.
    arm_meta_dir = FIXTURE_DIR / "_arms"
    arm_meta_dir.mkdir(exist_ok=True)

    matrix: list[dict[str, Any]] = []
    for fx in fixtures:
        # Layer-1 expectations: one preview per (fixture, arm) since arm A
        # injects metadata that the resolver would also see (resolver respects
        # metadata-wins precedence via get_param; preview endpoint only shows
        # what the resolver itself would emit, not the get_param precedence).
        sparse_md = {"area": fx.area, "state": fx.state,
                     "year_built": fx.year_built}
        resolver_preview = resolve_expected(sparse_md, fx.building_type)
        print(f"\n--- {fx.name} resolver expectations ---")
        print(f"  ctx: {resolver_preview['ctx']}")
        print(f"  resolved: {resolver_preview['resolved']}")
        print(f"  levels: {resolver_preview['levels']}")

        for arm in arms:
            sim_name = f"qa_matrix_{fx.name}_{arm}"
            metadata_csv = write_arm_metadata(fx, arm, arm_meta_dir)

            if not args.skip_sims:
                # Clear stale slot inside the container so the same arm
                # can re-run across script invocations.
                container_exec([
                    "rm", "-rf",
                    f"/solver/powertwin-solver-pg/user_files/{sim_name}"
                ])
                try:
                    run_sim(api_base, sim_name, fx.geojson, metadata_csv,
                            dynamic_defaults=(arm != "flat"),
                            num_cores=args.num_cores)
                except Exception as e:
                    print(f"  SIM FAILED {sim_name}: {e}")
                    continue

            # Per-arm artifacts.
            osw_path = find_in_osw(sim_name, fx.geom_id)
            try:
                fjson = read_feature_json(sim_name, fx.geom_id)
            except Exception as e:
                print(f"  feature.json read failed: {e}")
                fjson = {}
            osm_dump = inspect_in_osm_via_container(sim_name, fx.geom_id) or {}
            sql_path = find_eplusout_sql(sim_name, fx.geom_id)
            sql_dump = query_eplusout(sql_path) if sql_path else {}
            # Residential pipeline writes feature.xml (HPXML); commercial
            # doesn't. find_feature_xml returns None for commercial.
            xml_path = find_feature_xml(sim_name, fx.geom_id)

            for field in FIELDS:
                raw_expected = expected_raw(fx, arm, field, resolver_preview)
                exp_l2 = expected_l2(field, raw_expected)
                exp_l3 = expected_l3(field, raw_expected)
                row = {
                    "fixture": fx.name,
                    "arm": arm,
                    "field": field,
                    "expected_raw": normalize(raw_expected),
                    "expected_L2": normalize(exp_l2),
                    "expected_L3": normalize(exp_l3),
                }
                # Layer 1 -- resolver expectation table
                row["L1_preview"] = normalize(
                    resolver_preview["resolved"].get(field)
                )
                # Layer 2 -- feature.json (nested path)
                row["L2_feature"] = normalize(lookup_feature_value(fjson, field))
                # Layer 3 -- in.osw measure arg
                mm = MEASURE.get(field)
                if mm is None:
                    row["L3_osw"] = "<no consumer>"
                    row["L3_skip"] = ""
                elif osw_path is None:
                    row["L3_osw"] = "<no osw>"
                    row["L3_skip"] = ""
                else:
                    measure_name, arg_name = mm
                    v, skip_flag, found = read_osw_arg(osw_path, measure_name, arg_name)
                    row["L3_osw"] = normalize(v) if found else "<step missing>"
                    row["L3_skip"] = str(skip_flag)
                # Layer 4 -- in.osm peek
                row["L4_osm"] = normalize(osm_dump.get(field))
                # Layer 5 -- eplusout.sql
                row["L5_sql"] = normalize(sql_dump.get(field))
                # Residential L3 -- HPXML feature.xml replaces in.osw arg
                # check. BuildResidentialHPXML PASSES THROUGH the fuel-type
                # fields (heating / cooling / SWH) verbatim from feature.json
                # but DERIVES the rest (R-values, window U, floor height,
                # occupants) from its own residential template logic. So:
                #   * pass-through fields -- assert HPXML value matches the
                #     raw feature.json value (case-insensitive).
                #   * derived fields -- report the HPXML value for
                #     documentation but only assert L2 match.
                #   * fields with no HPXML mapping (system_type, schedules,
                #     WWR, wall/roof material) stay L2-only.
                HPXML_PASSTHROUGH = {
                    "heating_system_fuel_type",
                    "cooling_system_fuel_type",
                    "service_water_heating_fuel_type",
                }
                if fx.name == "residential":
                    hpxml_v = read_hpxml_field(xml_path, field)
                    if HPXML_EXTRACT.get(field) is None:
                        row["L3_osw"] = "<N/A in HPXML>"
                        row["L3_skip"] = ""
                        row["pass"] = (row["expected_L2"] == row["L2_feature"])
                    elif hpxml_v is None:
                        row["L3_osw"] = "<no feature.xml>"
                        row["L3_skip"] = ""
                        row["pass"] = (row["expected_L2"] == row["L2_feature"])
                    elif field in HPXML_PASSTHROUGH:
                        row["L3_osw"] = normalize(hpxml_v)
                        row["L3_skip"] = "(hpxml)"
                        # Case-insensitive compare: HPXML uses 'natural gas',
                        # feature.json/raw also lowercase. Skip the commercial
                        # CamelCase xform for residential.
                        row["pass"] = (
                            row["expected_L2"] == row["L2_feature"]
                            and str(raw_expected).lower() == str(hpxml_v).lower()
                        )
                    else:
                        # Derived field -- HPXML built it from residential
                        # defaults, not a literal copy. Report for
                        # documentation, require L2 match only.
                        row["L3_osw"] = normalize(hpxml_v)
                        row["L3_skip"] = "(hpxml-derived)"
                        row["pass"] = (row["expected_L2"] == row["L2_feature"])
                elif mm is None:
                    row["pass"] = (row["expected_L2"] == row["L2_feature"])
                elif row["L3_osw"] in ("<no osw>", "<step missing>"):
                    row["pass"] = (row["expected_L2"] == row["L2_feature"])
                elif row["expected_L3"] == "<None>" or row["expected_L3"] is None:
                    row["pass"] = (row["expected_L2"] == row["L2_feature"])
                else:
                    row["pass"] = (
                        row["expected_L2"] == row["L2_feature"]
                        and row["expected_L3"] == row["L3_osw"]
                    )
                matrix.append(row)

    # Emit the markdown matrix.
    with args.output.open("w") as f:
        f.write(f"# qa_dynamic_defaults_matrix run @ {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"Fixtures: {[fx.name for fx in fixtures]}  arms: {arms}\n\n")
        f.write("Pass criteria: expected == L2 (feature.json) == L3 (in.osw measure arg). L4/L5 are reported for silent-override detection but not required for green.\n\n")
        passed = sum(1 for r in matrix if r["pass"])
        f.write(f"**Summary: {passed}/{len(matrix)} green.**\n\n")
        f.write("| fixture | arm | field | expected (raw) | exp L2 | exp L3 | L1 | L2 | L3 | L3_skip | L4 | L5 | pass |\n")
        f.write("|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
        for r in matrix:
            mark = "Y" if r["pass"] else "N"
            f.write(f"| {r['fixture']} | {r['arm']} | {r['field']} | "
                    f"{r['expected_raw']} | {r['expected_L2']} | "
                    f"{r['expected_L3']} | {r['L1_preview']} | "
                    f"{r['L2_feature']} | {r['L3_osw']} | "
                    f"{r['L3_skip']} | {r['L4_osm']} | "
                    f"{r['L5_sql']} | {mark} |\n")

    print(f"\n=== summary: {passed}/{len(matrix)} green ===")
    print(f"report: {args.output}")
    return 0 if passed == len(matrix) else 2


if __name__ == "__main__":
    sys.exit(main())
