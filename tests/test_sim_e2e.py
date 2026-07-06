"""End-to-end integration gate (Step 6): run a slice of the synthetic leaf dataset
through the REAL solver pipeline inside the container and prove capture + closure.

This is NOT a bespoke harness -- it calls the exact functions start_simulation() calls
(create_table -> create_featurefiles -> initialize_uo, which runs the batches), minus
the final rmtree, so the kept run dir can be asserted on. Run it THROUGH the container:

    bash run_docker.sh            # brings up the flask container + DB
    docker compose exec flask pytest tests/test_sim_e2e.py -q

It auto-SKIPS on the host (no `uo` runtime). Asserts, for each building in the slice:
  (a) it simulated         -> a default_feature_report.csv exists
  (b) the RAW report's internal energy balance closes: sum(end-use) == fuel facility
      (energy_balance.balance_report). NOTE: this is the raw report's self-consistency,
      NOT proof that clean_report captured it -- that is (d).
  (c) forced fuels land on their meter in the raw report (e.g. wood -> OtherFuels:Facility)
  (d) capture-through-clean_report: every fuel with non-zero raw-report energy actually
      lands in the cleaned output (the check that energy_balance alone does NOT give).
"""
import csv
import glob
import importlib
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET

import pandas as pd
import pytest

pytestmark = pytest.mark.requires_container

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
APP = os.path.join(REPO, "solver", "app")
DATA_OUT = os.path.join(HERE, "data", "out")
sys.path.insert(0, HERE)          # energy_balance, leaf_stock
sys.path.insert(0, APP)           # the real pipeline package

from energy_balance import balance_report  # noqa: E402

# Cover EVERY asset subtype across BOTH workflows (one representative each) + the two forced-fuel
# capture cases. The leaf dataset enumerates all subtypes (residential SFD/SFA/MF + the CBECS
# commercial subtypes), so the e2e exercises each through the REAL pipeline rather than a hand-
# picked sample. (test_resolvers already covers every subtype on the host; THIS is the integration
# coverage: each subtype must sim, balance, capture, and -- where it has an HPXML -- value-check.)
FORCED_TAGS = [{"forced": "wood+wood_stove"}, {"forced": "natural gas+boiler"}]   # OtherFuels + gas capture


def _load(modname, path):
    spec = importlib.util.spec_from_file_location(modname, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def ab_outputs(tmp_path_factory):
    leaf_stock = _load("leaf_stock", os.path.join(HERE, "data", "leaf_stock.py"))
    rows = leaf_stock.load()
    want_ids, rows_by_id = [], {}

    def _add(r):
        aid = str(r["asset_id"])
        if aid not in rows_by_id:
            want_ids.append(aid)
            rows_by_id[aid] = r

    # one representative per asset subtype -- ALL residential + commercial subtypes, both workflows
    for sub in sorted({r["asset_subtype_name"] for r in rows}):
        _add(next(r for r in rows if r["asset_subtype_name"] == sub))
    # plus the forced-fuel capture cases (wood->OtherFuels, gas) for the fuel/capture assertions
    for tag in FORCED_TAGS:
        sel = leaf_stock.select(**tag)
        if sel:
            _add(sel[0])
    assert len(want_ids) >= 20, f"expected ~all asset subtypes, got only {len(want_ids)}: {sorted(rows_by_id)}"

    # Include one no-coords building -- processed, but expected to be marked FAILED (not simulated) --
    # to regression-guard the silent-drop fix. Kept OUT of want_ids so the must-simulate tests skip it.
    no_coords_id = next((str(r["asset_id"]) for r in rows
                         if not (r["metadata"].get("latitude") and r["metadata"].get("longitude"))), None)
    process_ids = set(want_ids) | ({no_coords_id} if no_coords_id else set())

    # build the sliced geojson + metadata.csv (subset of the generated dataset) -- shared by both modes
    geo = json.load(open(os.path.join(DATA_OUT, "leaf_stock_geometries.geojson")))
    geo["features"] = [f for f in geo["features"]
                       if str(f.get("properties", {}).get("id") or f.get("properties", {}).get("asset_id")) in process_ids]
    meta = pd.read_csv(os.path.join(DATA_OUT, "leaf_stock_metadata.csv"))
    meta = meta[meta["asset_id"].astype(str).isin(process_ids)]

    os.environ["URBANOPT_STOCHASTIC_SAMPLING"] = "true"
    os.environ["URBANOPT_KEEP_RUN_DIR"] = "true"   # read by clean_report in the workers -- set via -e too

    # the REAL pipeline (same calls as views.start_simulation, without the rmtree). import_module
    # (proper PACKAGE import, not spec_from_file_location) because initialize_UOsim uses relative
    # imports that only resolve as modules.simulation.* with the app root on sys.path -- and `modules`
    # is only importable inside the container, so the import stays runtime-guarded by the fixture.
    views = importlib.import_module("views")
    gff = importlib.import_module("modules.simulation.generateFeatureFile")
    init = importlib.import_module("modules.simulation.initialize_UOsim")

    out = {"want_ids": want_ids, "rows_by_id": rows_by_id, "no_coords_id": no_coords_id}
    # Run the SAME slice twice: dynamic defaults ON and OFF. The resolver reads
    # URBANOPT_DYNAMIC_DEFAULTS at call time and runs in THIS (main) process during
    # create_featurefiles, so the os.environ toggle controls each mode (unlike KEEP_RUN_DIR,
    # which the worker processes read -> needs -e).
    for mode, flag in (("on", "true"), ("off", "false")):
        os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = flag
        sim_name = f"leaf_ab_{mode}"
        sim_dir = str(tmp_path_factory.mktemp(f"sim_{mode}") / sim_name)
        local_dir = str(tmp_path_factory.mktemp(f"local_{mode}") / sim_name)
        os.makedirs(sim_dir, exist_ok=True)
        os.makedirs(local_dir, exist_ok=True)
        geo_path = os.path.join(local_dir, f"{sim_name}_asset.geojson")
        meta_path = os.path.join(local_dir, f"{sim_name}_metadata.csv")
        json.dump(geo, open(geo_path, "w"))
        meta.to_csv(meta_path, index=False)
        views.create_table()
        gff.create_featurefiles(sim_dir, local_dir, geo_path, meta_path, 1, sim_name)   # 1 batch == 1 core (serial)
        init.initialize_uo(sim_dir, local_dir, sim_name)
        found = [a for a in want_ids if _report_for(local_dir, a)]
        assert found, (f"[{mode}] no default_feature_report under {local_dir} for any of {want_ids} -- "
                       "KEEP_RUN_DIR not honored or sims failed; nothing to verify e2e")
        out[mode] = (sim_dir, local_dir)
    return out


@pytest.fixture(scope="module")
def sim_outputs(ab_outputs):
    """The dynamic-ON run, in the (sim_dir, local_dir, want_ids, rows_by_id) shape the
    per-field / capture / balance / IDF tests expect. ON is run once (shared with the A/B)."""
    sim_dir, local_dir = ab_outputs["on"]
    return sim_dir, local_dir, ab_outputs["want_ids"], ab_outputs["rows_by_id"]


def _report_for(report_root, asset_id):
    # per-building reports + in.idf live under LOCAL_DIR (.../urbanopt_simulation/batch_N/<id>/),
    # NOT SIMULATION_DIR (which holds only the scenario-level rollups).
    hits = glob.glob(os.path.join(report_root, "**", str(asset_id), "**", "default_feature_report*.csv"), recursive=True)
    return hits[0] if hits else None


def _idf_for(report_root, asset_id):
    # the building's final merged EnergyPlus model: .../batch_N/<id>/in.idf
    hits = glob.glob(os.path.join(report_root, "**", str(asset_id), "in.idf"), recursive=True)
    return hits[0] if hits else None


def _window_ufactors(idf_text):
    # WindowMaterial:SimpleGlazingSystem, <name>, <U-Factor {W/m2-K}>, <SHGC>;
    # Strip IDF inline comments (!- FieldName) first -- EnergyPlus writes each field with a
    # trailing "!- ..." comment, which otherwise breaks the value match.
    clean = re.sub(r"!.*", "", idf_text)
    return [float(m.group(1)) for m in re.finditer(
        r"WindowMaterial:SimpleGlazingSystem,\s*[^,]+,\s*([0-9.]+)", clean)]


def _hpxml_for(report_root, asset_id):
    # residential buildings emit an HPXML (in.xml) next to in.idf; commercial do not
    hits = glob.glob(os.path.join(report_root, "**", str(asset_id), "in.xml"), recursive=True)
    return hits[0] if hits else None


def _hpxml_envelope(path):
    """Structured envelope read from an HPXML: wall assembly Rs, the insulated horizontal
    (roof/ceiling) max R, window U-factors (IP), occupants. (Namespace-agnostic by local-name.)"""
    def ln(e):
        return e.tag.split("}")[-1]

    def assembly_r(el):
        for c in el.iter():
            if ln(c) == "AssemblyEffectiveRValue" and c.text:
                return float(c.text)
        return None

    root = ET.parse(path).getroot()
    walls, horiz, win_u, occ, heat = [], [], [], None, None
    for e in root.iter():
        t = ln(e)
        if t == "Wall":
            r = assembly_r(e)
            if r is not None:
                walls.append(r)
        elif t in ("Roof", "Ceiling", "Floor", "FrameFloor"):
            r = assembly_r(e)
            if r is not None:
                horiz.append(r)
        elif t == "Window":
            for c in e.iter():
                if ln(c) == "UFactor" and c.text:
                    win_u.append(float(c.text))
        elif t == "NumberofResidents" and e.text:
            occ = float(e.text)
        elif t in ("SetpointTempHeatingSeason", "HeatingSetpointTemp") and e.text:
            heat = float(e.text)
    # PowerTwin models a VACANT building with a 55F pipe-freeze heating setback + 0 occupants
    # (+ cooling off). That heating setpoint is the reliable vacancy signature in the built model.
    return {"walls": walls, "ceiling_max": max(horiz) if horiz else None, "win_u": win_u,
            "occupants": occ, "vacant": heat is not None and abs(heat - 55.0) < 1.0}


# resolved window type -> physically-correct HPXML U-factor band (IP, Btu/hr-ft2-F)
_WINDOW_U_BAND = {"Single Pane": (0.50, 1.50), "Double Pane": (0.20, 0.70), "Triple Pane": (0.10, 0.40)}


def test_every_slice_building_simulated(sim_outputs):
    _, local_dir, want_ids, _ = sim_outputs
    missing = [a for a in want_ids if _report_for(local_dir, a) is None]
    assert not missing, f"buildings with no feature report (did not simulate): {missing}"


def test_energy_balance_closes(sim_outputs):
    _, local_dir, want_ids, _ = sim_outputs
    unbalanced = {}
    for a in want_ids:
        rpt = _report_for(local_dir, a)
        if rpt is None:
            continue
        verdict = balance_report(pd.read_csv(rpt))
        if not verdict["closes"]:
            unbalanced[a] = verdict["unbalanced"]
    assert not unbalanced, f"energy balance did not close (silent capture loss): {unbalanced}"


def test_forced_wood_lands_on_otherfuels(sim_outputs):
    _, local_dir, _, rows_by_id = sim_outputs
    wood = [a for a, r in rows_by_id.items() if "wood" in str(r["metadata"]).lower()]
    for a in wood:
        rpt = _report_for(local_dir, a)
        assert rpt, f"wood building {a} produced no report"
        df = pd.read_csv(rpt)
        of = [c for c in df.columns if "otherfuels:facility" in c.replace(" ", "").lower()]
        assert of and df[of[0]].sum() > 0, f"wood building {a} produced no OtherFuels energy"


# raw fuel-facility column prefix -> the cleaned_predicted_<sensor>.csv it MUST land in
# (clean_report names files by sensor_types.csv name, lowercased, spaces->underscores).
_FUEL_TO_CLEANED = {
    "Electricity": "electricity", "NaturalGas": "natural_gas", "Propane": "propane",
    "FuelOilNo2": "fuel_oil", "OtherFuels": "other_fuel",
}


def test_capture_through_clean_report(sim_outputs):
    """PER-FUEL capture proof: every fuel with non-zero energy in the RAW report must land in
    ITS OWN cleaned file -- not merely 'some' fuel captured (the weak version passed even when
    a gas/wood-heated home kept only its electricity file). energy_balance checks only the raw
    report's internal closure; this is the separate proof clean_report captured EACH fuel,
    catching the undeclared-sensor drop (wood->OtherFuels lost when only Electricity is
    declared) the re-audit found. Relies on the leaf metadata declaring all fuel sensors."""
    _, local_dir, want_ids, _ = sim_outputs
    gaps = {}
    for a in want_ids:
        rpt = _report_for(local_dir, a)
        if rpt is None:
            continue
        df = pd.read_csv(rpt)
        for fuel, cleaned_name in _FUEL_TO_CLEANED.items():
            raw = sum(df[c].sum() for c in df.columns
                      if f"{fuel.lower()}:facility(" in c.replace(" ", "").lower())
            if raw <= 0:
                continue
            hits = glob.glob(os.path.join(local_dir, "cleaned_reports", str(a),
                                          f"cleaned_predicted_{cleaned_name}.csv"))
            captured = sum(pd.read_csv(h)["value"].abs().sum() for h in hits) if hits else 0
            if captured <= 0:
                gaps[f"{a}/{fuel}"] = f"raw={raw:.0f} but cleaned '{cleaned_name}' {'missing' if not hits else 'zero'}"
    assert not gaps, f"per-fuel capture gaps (raw fuel has energy, its cleaned file does not): {gaps}"


def test_declared_sensors_reach_consolidated_logs(sim_outputs):
    """DB-file survival gate -- the arrow AFTER clean_report. Every sensor clean_report
    captured (a non-zero cleaned_predicted_*.csv) must survive into the consolidated,
    DB-loadable sensor-logs file that consolidate_sensor_logs.py produces, with its value
    preserved (raw passthrough, no resample) and the [sensor_id, collection_id, ts, value,
    metadata] schema the DB loader expects. test_capture_through_clean_report proves cleaning
    captured each fuel; THIS proves consolidation does not drop what cleaning kept. Water
    (sensor_type 4) has no EnergyPlus meter column and is not declared -> must never appear.
    (Leaf sensor_ids are globally unique per (building, type), so consolidation's sensor_id+ts
    dedup cannot collide -> per-sensor value conservation is exact.)"""
    _, local_dir, want_ids, _ = sim_outputs
    cleaned_root = os.path.join(local_dir, "cleaned_reports")
    if not os.path.isdir(cleaned_root):
        pytest.skip("no cleaned_reports produced (KEEP_RUN_DIR off or clean_report did not run)")

    # EXPECTED = what clean_report emitted: sensor_id -> total |value| across the slice.
    expected = {}
    for f in glob.glob(os.path.join(cleaned_root, "*", "cleaned_predicted_*.csv")):
        d = pd.read_csv(f)
        if d.empty or "value" not in d.columns or "id" not in d.columns:
            continue
        v = float(d["value"].abs().sum())
        if v <= 0:
            continue
        sid = str(d["id"].iloc[0])
        expected[sid] = expected.get(sid, 0.0) + v
    assert expected, "clean_report produced no non-zero cleaned sensor files to consolidate"

    # RUN the real consolidation tool the way HPC does (raw passthrough -> values conserved).
    consolidate = importlib.import_module("modules.utils.consolidate_sensor_logs")
    out_csv = os.path.join(local_dir, "consolidated_sensor_logs.csv")
    r = subprocess.run([sys.executable, consolidate.__file__,
                        "--input-dir", cleaned_root, "--output", out_csv,
                        "--collection-id", "1", "--workers", "1"],
                       capture_output=True, text=True, timeout=600)
    assert os.path.exists(out_csv), f"consolidation produced no DB file:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"

    con = pd.read_csv(out_csv)
    assert list(con.columns) == ["sensor_id", "collection_id", "ts", "value", "metadata"], \
        f"DB-incompatible schema: {list(con.columns)}"

    # (a) SURVIVAL: every sensor_id clean_report captured reaches the DB file.
    con_ids = set(con["sensor_id"].astype(str))
    dropped = sorted(set(expected) - con_ids)
    assert not dropped, f"sensors captured by clean_report but DROPPED in consolidation: {dropped}"

    # (b) VALUE conserved cleaned -> consolidated (no resample; unique ids -> exact).
    got = (con.assign(_sid=con["sensor_id"].astype(str))
              .groupby("_sid")["value"].apply(lambda s: float(s.abs().sum())))
    bad = {sid: (round(exp, 1), round(float(got.get(sid, 0.0)), 1))
           for sid, exp in expected.items()
           if abs(float(got.get(sid, 0.0)) - exp) > max(1.0, 0.01 * exp)}
    assert not bad, f"value not conserved cleaned->consolidated {{sensor_id:(cleaned,consolidated)}}: {bad}"

    # (c) Water (sensor_type 4: no meter column, not declared) must never appear.
    assert not glob.glob(os.path.join(cleaned_root, "*", "cleaned_predicted_water.csv")), \
        "Water (sensor_type 4) has no EnergyPlus meter -> must never produce a cleaned/consolidated log"


def test_resolved_values_reach_idf(sim_outputs):
    """Automated IDF-value gate (not a manual spot-check): the resolved ENVELOPE and forced
    FUEL must be physically present in the built EnergyPlus model. (1) Each building has a
    window/fenestration object; glazing U-factors are physical (0.3-7 W/m2K) and VARY across
    the slice -- proving the resolved window TYPE propagates into the model, not a constant.
    (2) Forced wood/gas heating reaches the model (wood->OtherFuel, gas->NaturalGas). Wall-R /
    WWR are wired + the sims balance, but reliably parsing them from IDF material layers /
    geometry is fragile, so they are left to the resolver+balance checks rather than asserted here."""
    _, local_dir, want_ids, rows_by_id = sim_outputs
    glazing_us, missing = [], []
    for a in want_ids:
        idf = _idf_for(local_dir, a)
        if idf is None:
            missing.append(a)
            continue
        text = open(idf).read()
        low = text.lower()
        assert "fenestrationsurface:detailed" in low or "windowmaterial" in low, \
            f"building {a}: no window/fenestration object in in.idf"
        for u in _window_ufactors(text):
            assert 0.3 <= u <= 7.0, f"building {a}: window U-factor {u} W/m2K outside physical range"
            glazing_us.append(u)
        meta = str(rows_by_id[a]["metadata"]).lower()
        if "wood" in meta:
            assert "otherfuel" in low, f"wood building {a}: heating fuel (OtherFuel) absent from in.idf"
        if "natural gas" in meta:
            assert "naturalgas" in low, f"gas building {a}: NaturalGas absent from in.idf"
    assert not missing, f"no in.idf found for: {missing}"
    distinct = {round(u, 2) for u in glazing_us}
    assert len(distinct) >= 2, \
        f"window U-factors do not vary across the slice (resolved window type not reaching the IDF?): {sorted(distinct)}"


def test_resolved_envelope_values_in_hpxml(sim_outputs):
    """PER-FIELD value assertion (residential): each resolved envelope field lands in the built
    HPXML (in.xml). The resolver is re-run with the same metadata+building_id -- the draw is a
    pure id-hash, so it reproduces the pipeline's resolved value exactly.

      occupants   EXACT pass-through         -> NumberofResidents == resolved
      roof_r      EXACT documented mapping    -> max horizontal assembly == ceiling_assembly_r (roof_r + 1.0)
      window_type physically-correct U band   -> every window UFactor in the type's IP band
      wall_r      EXACT documented mapping    -> a HPXML wall == wall_assembly_r (cavity*0.75 + 2.5),
                  e.g. R-1->3.25, R-11->10.75. HPXML also carries a minor secondary surface, so it is
                  matched-by-presence; FAILS on a dropped/wrong wall R (R-11 can't read as only ~R-4).

    NOT asserted verbatim: WWR (the measure redistributes it by geometry: 0.18 resolved -> ~0.09
    window/wall-area in HPXML); commercial buildings (no HPXML -- OSM-construction parse is fragile,
    left to the IDF window-U + balance checks)."""
    _, local_dir, want_ids, rows_by_id = sim_outputs
    sps = importlib.import_module("modules.simulation.sim_params_spec")
    get_location = importlib.import_module("modules.utils.weather").get_location
    os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "true"      # the HPXML being read is the dynamic-ON build
    os.environ["URBANOPT_STOCHASTIC_SAMPLING"] = "true"   # match the pipeline run's sampling
    checked = 0
    for a in want_ids:
        hp = _hpxml_for(local_dir, a)
        if hp is None:
            continue
        row = rows_by_id[a]
        # Resolve EXACTLY as the pipeline did: effective subtype (raw MF-variant names don't resolve),
        # pipeline CZ via get_location, get_param (override-aware, flat-default fallback -> never None).
        ctx = sps.build_asset_ctx(row["metadata"], building_type=_effective_subtype(row["asset_subtype_id"]),
                                  building_id=str(a), climate_zone=get_location(row["metadata"])[2])
        r_wall = sps.get_param(row["metadata"], "wall_r_value", ctx)
        r_roof = sps.get_param(row["metadata"], "roof_r_value", ctx)
        r_win = sps.get_param(row["metadata"], "window_type", ctx)
        r_occ = sps.get_param(row["metadata"], "number_of_occupants", ctx)
        hv = _hpxml_envelope(hp)

        if hv["vacant"]:
            # vacant buildings are correctly modeled with 0 occupants (ACS B25004 vacancy); the
            # resolver's would-be count does NOT apply -- asserting it here was the 800181 false red.
            assert hv["occupants"] in (0, 0.0), \
                f"{a}: modeled vacant (55F setback) but HPXML occupants={hv['occupants']} (expect 0)"
        else:
            assert hv["occupants"] is not None and abs(hv["occupants"] - float(r_occ)) < 0.5, \
                f"{a}: occupants resolved {r_occ} != HPXML NumberofResidents {hv['occupants']}"
        # roof_r -> HPXML ceiling assembly via the documented PowerTwinRefs.ceiling_assembly_r = roof_r + 1.0
        exp_ceil = r_roof + 1.0
        assert hv["ceiling_max"] is not None and abs(hv["ceiling_max"] - exp_ceil) <= 0.6, \
            f"{a}: roof_r resolved {r_roof} -> expected ceiling assembly {exp_ceil} != HPXML {hv['ceiling_max']}"
        assert hv["walls"], f"{a}: no wall AssemblyEffectiveRValue in HPXML"
        # residential cavity R -> HPXML assembly R via the documented PowerTwinRefs.wall_assembly_r
        # = cavity*0.75 + 2.5. The resolved value must land at that EXACT assembly among the HPXML
        # walls (HPXML also carries a minor secondary surface, hence `any`). FAILS on a dropped/wrong
        # wall R (a resolved R-11 -> expected assembly 10.75 cannot read as only the ~R-4 secondary).
        exp_wall = r_wall * 0.75 + 2.5
        assert any(abs(wr - exp_wall) <= 0.25 for wr in hv["walls"]), \
            f"{a}: resolved wall R-{r_wall} -> expected assembly {exp_wall:.2f} absent from HPXML walls {sorted(set(hv['walls']))}"
        lo, hi = _WINDOW_U_BAND[r_win]
        assert hv["win_u"], f"{a}: no window UFactor in HPXML"
        for u in hv["win_u"]:
            assert lo <= u <= hi, f"{a}: resolved window '{r_win}' but HPXML U {u} outside band [{lo}, {hi}]"
        checked += 1
    assert checked >= 2, f"expected >=2 residential buildings with HPXML to value-check, got {checked}"


def _effective_subtype(sid):
    # mirror generateFeatureFile: raw subtype_id -> effective subtype NAME (MF-variants->Multifamily,
    # null->SFD). The resolver must be re-run with the EFFECTIVE name the pipeline used.
    sub = {int(r["id"]): r for r in csv.DictReader(open(os.path.join(REPO, "solver", "upload", "asset_subtypes.csv")))}
    return sub[int(sub[int(sid)]["effective_id"])]["name"]


_RES_TYPES = {"Single-Family Detached", "Single-Family Attached", "Multifamily"}
# resolved (lowercase) fuel name -> the EnergyPlus token PowerTwin passes to the commercial measures
_COM_FUEL = {"natural gas": "NaturalGas", "electricity": "Electricity", "fuel oil": "FuelOil",
             "propane": "Propane", "wood": "OtherFuel", "district steam": "DistrictHeating"}
# resolved residential heating system type -> BuildResidentialModel heating_system_type token
_RES_HEATTYPE = {"furnace": "Furnace", "boiler": "Boiler",
                 "electric_resistance": "ElectricResistance", "wood_stove": "Stove"}


def _osw_by_measure(report_root, asset_id):
    """The building's in.osw measure steps with arguments grouped by measure_dir_name -- the
    integration ground truth: exactly what PowerTwin passed to each model-building measure."""
    hits = glob.glob(os.path.join(report_root, "**", str(asset_id), "in.osw"), recursive=True)
    if not hits:
        return None
    by = {}
    for st in json.load(open(hits[0])).get("steps", []):
        by.setdefault(st["measure_dir_name"], {}).update(st.get("arguments", {}))
    return by


def _hours(t):
    # op-hour args are decimal hours ("08.0"); resolved values are "HH:MM" -> hours float
    if not t:
        return None
    p = str(t).split(":")
    return int(p[0]) + (int(p[1]) / 60.0 if len(p) > 1 else 0.0)


def _sql_for(report_root, asset_id):
    # the building's EnergyPlus SQL output -- the BUILT model's tabular reports (kept by KEEP_RUN_DIR)
    hits = glob.glob(os.path.join(report_root, "**", str(asset_id), "eplusout.sql"), recursive=True)
    return hits[0] if hits else None


def _opaque_R_set(sql):
    """Multiset of the BUILT exterior opaque-surface assembly R (IP) from the EnergyPlus Envelope
    Summary ('U-Factor no Film'): R_IP = 1/(U[W/m2-K]*0.17611). Skips near-adiabatic internal
    surfaces (U~0). This is what EnergyPlus actually SIMULATED, not the IDF input."""
    con = sqlite3.connect(f"file:{sql}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT Value FROM TabularDataWithStrings WHERE TableName='Opaque Exterior' "
                           "AND ColumnName='U-Factor no Film'").fetchall()
    finally:
        con.close()
    out = []
    for (v,) in rows:
        try:
            u = float(v)
        except (ValueError, TypeError):
            continue
        if 0.001 < u <= 5.0:
            out.append(round(1.0 / (u * 0.17611), 1))
    return tuple(sorted(out))


# resolved field -> a comparable model summary read from the HPXML envelope dict
_AB_FIELD_MODEL = {
    "number_of_occupants": lambda v: v["occupants"],
    "wall_r_value": lambda v: tuple(sorted(round(w, 2) for w in v["walls"])),
    "roof_r_value": lambda v: v["ceiling_max"],
    "window_type": lambda v: tuple(sorted(round(u, 3) for u in v["win_u"])),
}


def test_dynamic_defaults_ab(ab_outputs):
    """A/B e2e -- the per-subtype DELIVERY proof. Run every subtype through the REAL pipeline with
    dynamic defaults ON and OFF; assert, IN THE BUILT MODEL, that ON differs from OFF for each field
    WHERE the resolved dynamic value differs from the flat default. FALLBACK-AWARE: where dynamic
    legitimately equals flat (sparse cell / N/A), ON==OFF is expected and not flagged -- only an
    expected-differential that fails to materialize is a delivery gap (dynamic resolved but did not
    reach the model). Residential envelope via HPXML; commercial has no HPXML (its window-U/fuels are
    covered by the IDF test; envelope-value is the known commercial gap). Complements the provenance
    oracle (dynamic ENGAGED at the resolver) and the per-field test (delivered the RIGHT value)."""
    on_local, off_local = ab_outputs["on"][1], ab_outputs["off"][1]
    rows_by_id = ab_outputs["rows_by_id"]
    sps = importlib.import_module("modules.simulation.sim_params_spec")
    gaps, checked = [], 0
    for a in ab_outputs["want_ids"]:
        hp_on, hp_off = _hpxml_for(on_local, a), _hpxml_for(off_local, a)
        if hp_on is None or hp_off is None:
            continue
        row = rows_by_id[a]
        ctx = sps.build_asset_ctx(row["metadata"], building_type=_effective_subtype(row["asset_subtype_id"]),
                                  building_id=str(a))
        os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "true"
        d_on = {f: sps.resolve_default(f, ctx)[0] for f in _AB_FIELD_MODEL}
        os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "false"
        d_off = {f: sps.resolve_default(f, ctx)[0] for f in _AB_FIELD_MODEL}
        v_on, v_off = _hpxml_envelope(hp_on), _hpxml_envelope(hp_off)
        for f, read in _AB_FIELD_MODEL.items():
            if d_on[f] == d_off[f]:
                continue   # dynamic == flat: no model differential expected (fallback-aware)
            if read(v_on) == read(v_off):
                gaps.append(f"{row['asset_subtype_name']} ({a}) {f}: resolver dynamic={d_on[f]} != flat={d_off[f]}, "
                            f"but BUILT MODEL identical ON==OFF ({read(v_on)}) -- dynamic not delivered")
        checked += 1
    os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "true"   # restore for any later test
    assert checked >= 3, f"expected >=3 residential subtypes to A/B, got {checked}"
    assert not gaps, ("dynamic defaults did NOT move the built model where the resolver says they "
                      "should (per-subtype delivery gap):\n" + "\n".join(gaps))


def test_energy_use_intensity_plausible(sim_outputs):
    """Magnitude sanity -- the check the structural tests can't give: every building's annual SITE
    EUI (total site energy / floor area) lands in a plausible band. Catches balanced-but-wrong sims
    (m2/ft2 area bug, zero-load, runaway consumption) that close + capture cleanly yet are physically
    nonsense. GENEROUS per-workflow bounds (residential vs commercial by HPXML presence) -- a gross-
    error tripwire, NOT a tight accuracy check. The commercial band is WIDE on purpose: it spans
    warehouses (~5 kBtu/ft2, lighting-dominated) to full-service restaurants (~1300, kitchen-dominated).
    KNOWN caveat: a 'Refrigerated warehouse' maps to the plain DOE Warehouse prototype (no refrigeration
    modeled), so it reads warehouse-low -- a prototype-fidelity gap (tracked in assumptions_ledger open
    items), not a sim error, so it stays within the warehouse range rather than being separately flagged."""
    _, local_dir, want_ids, rows_by_id = sim_outputs
    bad = []
    for a in want_ids:
        rpt = _report_for(local_dir, a)
        if rpt is None:
            continue
        df = pd.read_csv(rpt)
        kbtu = 0.0   # total annual site energy in kBtu (electricity kWh->kBtu + the kBtu fuels)
        for c in df.columns:
            n = c.replace(" ", "").lower()
            if n == "electricity:facility(kwh)":
                kbtu += df[c].sum() * 3.412
            elif n.endswith(":facility(kbtu)") and "district" not in n:
                kbtu += df[c].sum()
        area = float(rows_by_id[a]["metadata"].get("area") or 0)
        if area <= 0:
            continue
        eui = kbtu / area
        lo, hi = (8.0, 160.0) if _hpxml_for(local_dir, a) else (4.0, 1600.0)
        if not (lo <= eui <= hi):
            bad.append(f"{rows_by_id[a]['asset_subtype_name']} ({a}): site EUI {eui:.1f} kBtu/ft2 outside [{lo}, {hi}]")
    assert not bad, "implausible site-EUI (balanced + captured, but physically wrong magnitude):\n" + "\n".join(bad)


def test_all_dynamic_defaults_delivered_to_model(ab_outputs):
    """THE 100% delivery gate. For EVERY building (all subtypes, BOTH workflows), assert that EVERY
    dynamic default's value as PASSED to the model-building measures (in.osw) equals the resolver's
    value -- get_param (override-aware, like the pipeline), the pipeline's climate zone (get_location,
    NOT lat/lon->CZ), dynamic + stochastic ON. The in.osw measure args ARE what OpenStudio builds the
    model from, so this is the uniform integration delivery proof across both workflows:
      residential (BuildResidentialModel): heating_system_fuel, water_heater_fuel_type, water_heater_type,
        heating_system_type, wall_assembly_r (=cavity*0.75+2.5), ceiling_assembly_r (=roof_r+1),
        window_front_wwr (=resolved), geometry_unit_num_occupants (vacancy-aware), window_ufactor (band);
      commercial: set_heating_fuel/set_swh_fuel via the create_typical htg_src/swh_src lever (the
        __SKIP__'d custom measure only applies for fuels the toolchain enum can't take),
        IncreaseInsulationRValueFor{Walls,Roofs}.r_value, create_bar wwr, set_people target_total,
        create_typical wkdy/wknd op-hour start/duration.
    Complements the provenance oracle (dynamic ENGAGED at the resolver) + the A/B (dynamic MOVES the
    model) + the HPXML per-field test (residential measure OUTPUT) with the per-field measure INPUT for
    every field, both workflows. Re-resolving shares the resolver code, so this proves DELIVERY of the
    resolved value, not its source-correctness (that is the host resolver/distribution suite's job)."""
    local_dir = ab_outputs["on"][1]
    rows_by_id = ab_outputs["rows_by_id"]
    sps = importlib.import_module("modules.simulation.sim_params_spec")
    get_location = importlib.import_module("modules.utils.weather").get_location
    os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "true"
    os.environ["URBANOPT_STOCHASTIC_SAMPLING"] = "true"   # match the pipeline run's sampling
    gaps, checked = [], 0
    for a in ab_outputs["want_ids"]:
        A = _osw_by_measure(local_dir, a)
        if A is None:
            continue
        row = rows_by_id[a]
        eff = _effective_subtype(row["asset_subtype_id"])
        ctx = sps.build_asset_ctx(row["metadata"], building_type=eff, building_id=str(a),
                                  climate_zone=get_location(row["metadata"])[2])
        G = lambda f: sps.get_param(row["metadata"], f, ctx)   # noqa: E731 -- override-aware, pipeline parity

        def gap(field, exp, got):
            gaps.append(f"{row['asset_subtype_name']} ({a}) {field}: resolved {exp!r} NOT delivered (model arg {got!r})")

        if eff in _RES_TYPES:
            b = A.get("BuildResidentialModel", {})
            hp = _hpxml_for(local_dir, a)
            vacant = bool(hp and _hpxml_envelope(hp)["vacant"])
            if str(b.get("heating_system_fuel")).lower() != str(G("heating_system_fuel_type")).lower():
                gap("heating_fuel", G("heating_system_fuel_type"), b.get("heating_system_fuel"))
            if str(b.get("water_heater_fuel_type")).lower() != str(G("service_water_heating_fuel_type")).lower():
                gap("swh_fuel", G("service_water_heating_fuel_type"), b.get("water_heater_fuel_type"))
            if b.get("water_heater_type") != G("water_heater_type"):
                gap("water_heater_type", G("water_heater_type"), b.get("water_heater_type"))
            hst = G("heating_system_type")
            type_ok = (b.get("heat_pump_type") not in (None, "none")) if hst == "heat_pump" \
                else (b.get("heating_system_type") == _RES_HEATTYPE.get(hst, hst))
            if not type_ok:
                gap("heating_system_type", hst, (b.get("heating_system_type"), b.get("heat_pump_type")))
            if abs(float(b.get("wall_assembly_r")) - (G("wall_r_value") * 0.75 + 2.5)) > 0.25:
                gap("wall_r", round(G("wall_r_value") * 0.75 + 2.5, 2), b.get("wall_assembly_r"))
            if abs(float(b.get("ceiling_assembly_r")) - (G("roof_r_value") + 1.0)) > 0.6:
                gap("roof_r", G("roof_r_value") + 1.0, b.get("ceiling_assembly_r"))
            if abs(float(b.get("window_front_wwr")) - G("window_to_wall_ratio")) > 0.001:
                gap("wwr", G("window_to_wall_ratio"), b.get("window_front_wwr"))
            exp_occ = 0 if vacant else G("number_of_occupants")
            if abs(float(b.get("geometry_unit_num_occupants")) - float(exp_occ)) >= 0.5:
                gap("occupants", f"{exp_occ} (vacant={vacant})", b.get("geometry_unit_num_occupants"))
            lo, hi = _WINDOW_U_BAND[G("window_type")]
            if b.get("window_ufactor") is None or not (lo <= float(b["window_ufactor"]) <= hi):
                gap("window_ufactor", f"{G('window_type')} in [{lo},{hi}]", b.get("window_ufactor"))
        else:
            ct = A.get("create_typical_building_from_model", {})
            bar = A.get("create_bar_from_building_type_ratios", {})
            iw = A.get("IncreaseInsulationRValueForExteriorWalls", {})
            ir = A.get("IncreaseInsulationRValueForRoofs", {})

            def eff_fuel(setm, src):
                # the custom set_*_fuel measure applies only when NOT __SKIP__'d; otherwise the
                # toolchain's create_typical htg_src/swh_src lever carries the fuel.
                return setm.get("fuel") if not setm.get("__SKIP__", False) else src
            ehf = eff_fuel(A.get("set_heating_fuel", {}), ct.get("htg_src"))
            if ehf != _COM_FUEL.get(G("heating_system_fuel_type")):
                gap("heating_fuel", G("heating_system_fuel_type"), ehf)
            esf = eff_fuel(A.get("set_service_water_heating_fuel", {}), ct.get("swh_src"))
            if esf != _COM_FUEL.get(G("service_water_heating_fuel_type")):
                gap("swh_fuel", G("service_water_heating_fuel_type"), esf)
            if iw.get("r_value") is None or abs(float(iw["r_value"]) - G("wall_r_value")) > 0.5:
                gap("wall_r", G("wall_r_value"), iw.get("r_value"))
            if ir.get("r_value") is None or abs(float(ir["r_value"]) - G("roof_r_value")) > 0.5:
                gap("roof_r", G("roof_r_value"), ir.get("r_value"))
            if bar.get("wwr") is None or abs(float(bar["wwr"]) - G("window_to_wall_ratio")) > 0.001:
                gap("wwr", G("window_to_wall_ratio"), bar.get("wwr"))
            tt = A.get("set_people_per_floor_area", {}).get("target_total")
            if tt is None or abs(float(tt) - float(G("number_of_occupants"))) > 1.0:
                gap("occupants", G("number_of_occupants"), tt)
            for fld, arg in (("weekday_start_time", "wkdy_op_hrs_start_time"),
                             ("weekday_duration", "wkdy_op_hrs_duration"),
                             ("weekend_start_time", "wknd_op_hrs_start_time"),
                             ("weekend_duration", "wknd_op_hrs_duration")):
                rv = G(fld)
                if rv in (None, ""):
                    continue   # no op-hours resolved (flat / residential) -- not applicable
                av = ct.get(arg)
                if av is None or abs(float(av) - _hours(rv)) >= 0.1:
                    gap(f"op_hours:{fld}", rv, av)
        checked += 1
    os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "true"   # restore for any later test
    assert checked >= 20, f"expected ~all asset subtypes delivery-checked, got {checked}"
    assert not gaps, ("dynamic defaults NOT delivered to the built model "
                      "(in.osw measure arg != resolved value):\n" + "\n".join(gaps))


def test_commercial_envelope_reaches_built_model(ab_outputs):
    """Commercial measure-OUTPUT gate -- the residential-HPXML parallel for commercial, via the BUILT
    EnergyPlus model (eplusout.sql Envelope Summary). Residential envelope is value-checked in the built
    HPXML; commercial has no HPXML, and the IncreaseInsulation arg is an INSULATION target that the
    measure adds to the base assembly (so the built opaque R = resolved + base, not a clean equality --
    a roof reads ~14.3 for resolved 14, a wall ~7.5 for resolved 6). So prove it reached the model by
    A/B: the built opaque-surface assembly-R DISTRIBUTION (Envelope Summary 'U-Factor no Film' -> R, IP)
    under dynamic-ON differs from dynamic-OFF for every commercial building whose resolved wall/roof R
    differs from the flat default. FALLBACK-AWARE (no differential expected where dynamic==flat). This
    closes the last gap: the resolved commercial envelope not only reaches the measure (osw arg -- the
    delivery test) but CHANGES what EnergyPlus actually simulated. (Window-U + fuels are covered by
    test_resolved_values_reach_idf; the opaque envelope was the known commercial output gap.)"""
    on_local, off_local = ab_outputs["on"][1], ab_outputs["off"][1]
    rows_by_id = ab_outputs["rows_by_id"]
    sps = importlib.import_module("modules.simulation.sim_params_spec")
    get_location = importlib.import_module("modules.utils.weather").get_location
    os.environ["URBANOPT_STOCHASTIC_SAMPLING"] = "true"
    gaps, checked = [], 0
    for a in ab_outputs["want_ids"]:
        if _hpxml_for(on_local, a):    # residential -> envelope OUTPUT already value-checked via HPXML
            continue
        son, soff = _sql_for(on_local, a), _sql_for(off_local, a)
        if son is None or soff is None:
            continue
        row = rows_by_id[a]
        ctx = sps.build_asset_ctx(row["metadata"], building_type=_effective_subtype(row["asset_subtype_id"]),
                                  building_id=str(a), climate_zone=get_location(row["metadata"])[2])
        os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "true"
        on_env = {f: sps.get_param(row["metadata"], f, ctx) for f in ("wall_r_value", "roof_r_value")}
        os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "false"
        off_env = {f: sps.get_param(row["metadata"], f, ctx) for f in ("wall_r_value", "roof_r_value")}
        built_on, built_off = _opaque_R_set(son), _opaque_R_set(soff)
        if on_env != off_env and built_on == built_off:   # dynamic envelope resolved but didn't move the model
            gaps.append(f"{row['asset_subtype_name']} ({a}): resolved envelope dynamic={on_env} != flat={off_env}, "
                        f"but BUILT opaque-R identical ON==OFF -- dynamic envelope did not reach the model")
        checked += 1
    os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "true"   # restore for any later test
    assert checked >= 10, f"expected ~all commercial subtypes, got {checked}"
    assert not gaps, "commercial envelope did NOT reach the built EnergyPlus model:\n" + "\n".join(gaps)


def test_no_coords_building_marked_failed(ab_outputs):
    """Regression guard for the silent-drop fix: a building with no lat/lon can't be simulated, so it
    must be recorded as a FAILED asset (with a coordinates reason) and produce no report -- never a
    silent drop. The slice's normal reps all have coords; this one is included solely to exercise the
    drop path (it is deliberately kept out of want_ids)."""
    nc = ab_outputs["no_coords_id"]
    assert nc, "no no-coords building in the leaf stock to guard the drop path"
    assert _report_for(ab_outputs["on"][1], nc) is None, f"no-coords building {nc} was unexpectedly simulated"
    pg = importlib.import_module("modules.database.postgres_operations")
    cur = pg.get_db_connection().cursor()
    # asset_id is the global PK (one row per building, last run wins -- the A/B reuses ids across the
    # on/off sims), so query by asset_id alone rather than a specific simulation_name.
    cur.execute("SELECT status, failure_reason FROM powertwin WHERE asset_id = %s", (int(nc),))
    row = cur.fetchone()
    assert row is not None, f"no-coords building {nc} absent from the DB -- should be recorded as Failed, not dropped"
    status, reason = row
    assert status == "Failed", f"no-coords {nc}: status={status!r}, expected 'Failed'"
    assert reason and "coordinates" in reason.lower(), f"no-coords {nc}: failure_reason={reason!r} (no coordinates note)"
