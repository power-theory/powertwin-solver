#!/usr/bin/env python3
"""Hypothesis + regression harness for the dynamic-defaults ON/OFF Q/A audit.

Pure Python: the resolver layer (sim_params_spec) plus a static source check of
the generateFeatureFile ctx wiring. No Docker or EnergyPlus required (the full
process_feature path needs the app runtime deps, so it is exercised in the
container; this guards the same fixes on the host).

    python3 tests/test_dynamic_defaults_hypotheses.py

Findings and the two HIGH fixes this guards (see tests/prompts/accuracy_audit.md):
  H1 [HIGH, fixed]  operating-hours resolver must reach commercial feature.json
                    (the 4 time fields must be read WITH ctx, not without).
  H2 [HIGH, fixed]  build_asset_ctx must carry floor_count so multi-story Lodging
                    resolves LargeHotel (matching PowerTwin.rb), not SmallHotel.
  H3 [LATENT, fixed] Office WWR size-bands (Small 0.21 / Medium 0.33 / Large 0.40).
  H4 [LATENT, fixed] residential resolver values now consumed by BuildResidentialModel.
  H5 [OK]           dynamic OFF reproduces flat SIM_PARAM_DEFAULTS exactly.
  H6 [HIGH, fixed]  preview (views.DYNAMIC_FIELDS) and sim emission agree on time fields.
"""
import importlib.util
import os
import re
import sys
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))

# Resolver reads the switch at call time, so toggling os.environ is enough.
os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "true"
os.environ["URBANOPT_STOCHASTIC_SAMPLING"] = "true"

_spec = importlib.util.spec_from_file_location(
    "sim_params_spec",
    os.path.join(REPO, "solver", "app", "modules", "simulation", "sim_params_spec.py"),
)
sps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sps)

build_asset_ctx = sps.build_asset_ctx
resolve_default = sps.resolve_default
get_param = sps.get_param
_doe_ref_name = sps._doe_ref_name
SIM_PARAM_DEFAULTS = sps.SIM_PARAM_DEFAULTS

GFF_PATH = os.path.join(REPO, "solver", "app", "modules", "simulation", "generateFeatureFile.py")
PT_PATH = os.path.join(REPO, "solver", "upload", "PowerTwin.rb")
PT_REFS_PATH = os.path.join(REPO, "solver", "upload", "powertwin_refs.rb")

# Mirror of solver/app/views.py:1088 DYNAMIC_FIELDS (the preview contract).
DYNAMIC_FIELDS = [
    "number_of_occupants",
    "heating_system_fuel_type", "cooling_system_fuel_type",
    "service_water_heating_fuel_type",
    "heating_system_type", "water_heater_type",
    "window_type",
    "wall_material", "roof_material",
    "wall_r_value", "roof_r_value", "window_to_wall_ratio",
    "weekday_start_time", "weekday_duration",
    "weekend_start_time", "weekend_duration",
]
SIM_TIME_FIELDS = ["weekday_start_time", "weekday_duration",
                   "weekend_start_time", "weekend_duration"]


def _time_fields_get_ctx():
    """Static check that every time-field get_param call in generateFeatureFile
    passes ctx. Guards the HIGH-1 fix without the app runtime deps (rich, etc.)."""
    with open(GFF_PATH) as fh:
        src = fh.read()
    out = {}
    for f in SIM_TIME_FIELDS:
        m = re.search(r"get_param\(\s*\w+\s*,\s*'%s'\s*(,\s*ctx)?\s*\)" % f, src)
        out[f] = bool(m and m.group(1))
    return out


def _residential_wires_resolver():
    """Static check that PowerTwin.rb's residential branch feeds resolver props
    into the HPXML measure args (the residential-parity fix)."""
    with open(PT_PATH) as fh:
        src = fh.read()
    needles = ["res_props[:window_to_wall_ratio]", "window_front_wwr",
               "args[:wall_assembly_r]", "args[:ceiling_assembly_r]",
               "args[:geometry_unit_num_occupants]"]
    return all(n in src for n in needles)


def _electric_furnace_efficiency_guarded():
    """Static check that an electric furnace gets ~1.0 efficiency, not a
    combustion AFUE. VALID_FUELS_BY_SYS now allows furnace+electricity, so the
    Furnace AFUE turnover MUST be gated on non-electric fuel and the base block
    must set 1.0 for electric -- else electric furnaces are modeled at 80%
    efficiency (+25% heating electricity)."""
    with open(PT_PATH) as fh:
        src = fh.read()
    base_sets_one = "args[:heating_system_heating_efficiency] = 1.0" in src
    turnover_gated = "unless args[:heating_system_fuel].to_s.downcase == 'electricity'" in src
    return base_sets_one and turnover_gated


def _residential_window_uses_resi_table():
    """Static check that the residential branch uses window_props_residential
    (IECC residential + physical glazing), NOT the commercial window_props /
    CZ_WINDOW (ASHRAE 90.1 nonres). Reusing the commercial table made residential
    Double Pane ~57% too leaky (U-0.55 vs IECC U-0.35 at CZ4). The helper +
    CZ_WINDOW_RES table live in the extracted powertwin_refs.rb; the mapper
    branch in PowerTwin.rb calls them."""
    with open(PT_REFS_PATH) as fh:
        refs = fh.read()
    with open(PT_PATH) as fh:
        src = fh.read()
    has_resi_method = "def self.window_props_residential" in refs and "CZ_WINDOW_RES" in refs
    resi_branch_uses_it = "PowerTwinRefs.window_props_residential(res_tier, climate_zone)" in src
    return has_resi_method and resi_branch_uses_it


class V:
    def __init__(self, key, label, status, detail):
        self.key, self.label, self.status, self.detail = key, label, status, detail


def _mapper_deploy_sites_copy_refs():
    """Every place that copies PowerTwin.rb into a sim-time mappers/ dir must ALSO
    bring powertwin_refs.rb (+ reference_data) -- PowerTwin.rb require_relatives it,
    so a copied mapper without it raises LoadError at sim time. All sites route
    through mapper_setup.deploy_mapper, which lists the siblings in one place."""
    with open(os.path.join(REPO, "solver/app/modules/simulation/mapper_setup.py")) as fh:
        helper = fh.read()
    helper_ok = "powertwin_refs.rb" in helper and "reference_data" in helper
    sites = ["solver/app/modules/simulation/run_UOsim.py",
             "solver/app/modules/simulation/pernode.py",
             "solver/app/modules/simulation/initialize_UOsim.py"]
    missing = []
    for s in sites:
        with open(os.path.join(REPO, s)) as fh:
            if "deploy_mapper" not in fh.read():
                missing.append(s)
    return (helper_ok and not missing, missing if not helper_ok or missing else [])


def h10_mapper_deploy_copies_refs():
    ok, missing = _mapper_deploy_sites_copy_refs()
    return V("H10", "mapper deploy copies powertwin_refs.rb [fixed]",
             "OK (fixed)" if ok else f"REGRESSED (LoadError risk: {missing})",
             f"all PowerTwin.rb deploy sites also copy powertwin_refs.rb: {ok}")


def h1_operating_hours_reach_sim():
    """Office: resolver yields hours AND the sim threads ctx so they reach feature.json."""
    ctx = build_asset_ctx({"state": "AZ", "year_built": 1995, "area": 60000}, building_type="Office")
    val, level = resolve_default("weekday_start_time", ctx)
    assert val == "08:00" and level == "building_type_only", "Office weekday resolver must yield 08:00"
    threaded = _time_fields_get_ctx()
    fixed = all(threaded.values())
    detail = f"resolver Office weekday={val}; generateFeatureFile passes ctx for time fields: {threaded}"
    return V("H1", "operating hours reach commercial sim [HIGH]",
             "OK (fixed)" if fixed else "REGRESSED (ctx dropped on time fields)", detail)


def h2_lodging_archetype_split():
    """Multi-story Lodging must resolve LargeHotel; low-rise stays SmallHotel."""
    tall = build_asset_ctx({"state": "Arizona", "year_built": 2010, "area": 403931, "floor_count": 15}, building_type="Lodging")
    short = build_asset_ctx({"state": "Arizona", "year_built": 2010, "area": 403931, "floor_count": 2}, building_type="Lodging")
    has_fc = "floor_count" in tall
    name_tall, name_short = _doe_ref_name("Lodging", tall), _doe_ref_name("Lodging", short)
    occ_tall = resolve_default("number_of_occupants", tall)[0]
    wwr_tall = resolve_default("window_to_wall_ratio", tall)[0]
    wwr_short = resolve_default("window_to_wall_ratio", short)[0]
    fixed = has_fc and name_tall == "LargeHotel" and name_short == "SmallHotel"
    if fixed:  # numeric guard for Manzanita (403,931 ft^2); WWR = ComStock realized modes
        assert (occ_tall, wwr_tall, wwr_short) == (7966, 0.2, 0.2), \
            f"numeric drift: occ={occ_tall} wwr_tall={wwr_tall} wwr_short={wwr_short}"
    detail = (f"floor_count in ctx={has_fc}; 15-story={name_tall}(wwr {wwr_tall}, occ {occ_tall}) "
              f"2-story={name_short}(wwr {wwr_short})")
    return V("H2", "Lodging floor_count archetype split [HIGH]",
             "OK (fixed)" if fixed else "REGRESSED (multi-story Lodging -> SmallHotel)", detail)


def h3_office_wwr_size_split():
    """Office WWR size-bands to distinct DOE-ref cells (Small/Med/Large); Office-aliased
    non-Office types resolve to MediumOffice and must NOT size-band. Values are now the
    ComStock realized WWR modes (Small 0.05 / Med 0.2 / Large 0.2)."""
    def wwr(bt, area):
        return resolve_default("window_to_wall_ratio",
                               build_asset_ctx({"state": "AZ", "year_built": 2000, "area": area}, building_type=bt))[0]
    ws, wm, wl = wwr("Office", 1442), wwr("Office", 60000), wwr("Office", 219968)
    # Public assembly aliases to Office for density/schedule but Ruby builds MediumOffice -> must NOT size-band.
    pa_small, pa_large = wwr("Public assembly", 1442), wwr("Public assembly", 219968)
    # Banding works if Small differs from Medium; Office-aliased non-Office types resolve
    # the literal 'Office' alias key (kept scalar 0.33, ComStock has no literal Office) and
    # do NOT size-band -- both sizes stay 0.33.
    fixed = (ws, wm, wl) == (0.05, 0.2, 0.2) and (pa_small, pa_large) == (0.33, 0.33)
    detail = (f"Office S/M/L={ws}/{wm}/{wl} (want 0.05/0.2/0.2, Small!=Med shows banding); "
              f"Public assembly S/L={pa_small}/{pa_large} (want 0.33/0.33, NOT banded)")
    return V("H3", "Office WWR size-split + alias scoping [fixed]",
             "OK (fixed)" if fixed else "REGRESSED (banding leaked to Office-aliased types)", detail)


def h7_multifamily_per_unit_occupants():
    """Residential occupants must be PER dwelling unit; multifamily must not N-fold over-count."""
    occ_sfd = resolve_default("number_of_occupants",
                              build_asset_ctx({"state": "AZ", "year_built": 2005, "area": 1800}, building_type="Single-Family Detached"))[0]
    occ_mf = resolve_default("number_of_occupants",
                             build_asset_ctx({"state": "AZ", "year_built": 2005, "area": 8000, "number_of_units": 8}, building_type="Multifamily"))[0]
    # per-unit: round(8000/8/800)+1 = 2; pre-fix whole-building would be round(8000/800)+1 = 11
    fixed = occ_sfd == 3 and occ_mf == 2
    detail = f"SFD(1800,1u)={occ_sfd}(want 3); MF(8000,8u)={occ_mf}(want 2 per-unit; pre-fix bug=11 whole-building)"
    return V("H7", "multifamily per-unit occupants [fixed]",
             "OK (fixed)" if fixed else "REGRESSED (whole-building occupants in per-unit field)", detail)


def h4_residential_resolver_consumed():
    """Resolver produces residential values AND PowerTwin.rb now feeds them to BuildResidentialModel."""
    ctx = build_asset_ctx({"state": "AZ", "year_built": 2005, "area": 2400, "number_of_bedrooms": 3}, building_type="Single-Family Detached")
    wwr = resolve_default("window_to_wall_ratio", ctx)[0]
    occ = resolve_default("number_of_occupants", ctx)[0]
    # WWR is now the SFD modal of the ResStock realized-stock distribution (0.09).
    assert wwr == 0.09 and occ == 4, f"residential resolver drift: wwr={wwr} occ={occ}"
    wired = _residential_wires_resolver()
    detail = (f"SFD resolver wwr={wwr}(flat={SIM_PARAM_DEFAULTS['window_to_wall_ratio']}) occ={occ}; "
              f"PowerTwin.rb residential branch wires resolver props: {wired}")
    return V("H4", "residential resolver consumed by BuildResidentialModel [LATENT, fixed]",
             "OK (fixed)" if wired else "REGRESSED (residential ignores resolver)", detail)


def h5_off_equals_flat():
    """Dynamic OFF must reproduce flat SIM_PARAM_DEFAULTS for both workflows."""
    os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "false"
    try:
        for bt in ("Office", "Single-Family Detached", "Lodging"):
            ctx = build_asset_ctx({"state": "AZ", "year_built": 2005, "area": 50000, "floor_count": 10}, building_type=bt)
            for f in DYNAMIC_FIELDS:
                assert resolve_default(f, ctx) == (None, None), f"OFF leaked dynamic value for {f}/{bt}"
                assert get_param({}, f, ctx) == SIM_PARAM_DEFAULTS[f], f"OFF get_param != flat for {f}/{bt}"
    finally:
        os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "true"
    return V("H5", "OFF == flat defaults", "OK", "all DYNAMIC_FIELDS fall back to SIM_PARAM_DEFAULTS when off")


def h6_preview_sim_agree_on_time():
    """After the HIGH-1 fix the sim emits the time fields the preview advertises."""
    threaded = _time_fields_get_ctx()
    agree = all(threaded.values())
    detail = f"sim threads ctx for time fields: {threaded}; preview (DYNAMIC_FIELDS) always resolved them"
    return V("H6", "preview vs sim agree on time fields [HIGH]",
             "OK (fixed)" if agree else "REGRESSED (preview advertises hours the sim drops)", detail)


def h8_electric_furnace_efficiency():
    """All electric heating is now carried by the heat_pump/electric_resistance
    system types (reconciled to the electricity marginal in BOTH the county and
    no-county paths), so furnaces are combustion-only and the resolver no longer
    emits furnace+electricity. The PowerTwin.rb AFUE guard (set 1.0 + skip
    combustion AFUE for electric fuel) is kept as defensive code in case an
    electric furnace ever arrives via a metadata override -- verify it remains.
    Also confirm the resolver does NOT produce furnace+electricity."""
    fuel_for_furnace = Counter()
    for i in range(1500):
        ctx = build_asset_ctx({"state": "TX", "year_built": 2005, "area": 1800},
                              building_type="Single-Family Detached", building_id=f"ef{i:05d}")
        st = resolve_default("heating_system_type", ctx)[0]
        if st == "furnace":
            fuel_for_furnace[resolve_default("heating_system_fuel_type", ctx)[0]] += 1
    elec_furnaces = fuel_for_furnace.get("electricity", 0)
    guarded = _electric_furnace_efficiency_guarded()
    fixed = elec_furnaces == 0 and guarded
    detail = (f"furnace+electricity emitted={elec_furnaces} (want 0; furnaces are "
              f"combustion-only); PowerTwin.rb AFUE guard present: {guarded}")
    return V("H8", "electric furnace guard + furnaces combustion-only [fixed]",
             "OK (fixed)" if fixed else "REGRESSED", detail)


def h9_residential_window_table():
    """Residential windows must use the IECC residential / physical-glazing table
    (window_props_residential), not the commercial ASHRAE 90.1 CZ_WINDOW."""
    ok = _residential_window_uses_resi_table()
    return V("H9", "residential window U uses IECC resi table [fixed]",
             "OK (fixed)" if ok else "REGRESSED (residential uses commercial 90.1 window U)",
             f"PowerTwin.rb residential branch -> window_props_residential + CZ_WINDOW_RES: {ok}")


CHECKS = [h1_operating_hours_reach_sim, h2_lodging_archetype_split, h3_office_wwr_size_split,
          h4_residential_resolver_consumed, h5_off_equals_flat, h6_preview_sim_agree_on_time,
          h7_multifamily_per_unit_occupants, h8_electric_furnace_efficiency,
          h9_residential_window_table, h10_mapper_deploy_copies_refs]


# pytest entry points: the HIGH fixes are hard-gated, LATENT/info just run.
def test_h1_high_fixed():
    assert h1_operating_hours_reach_sim().status.startswith("OK")
def test_h2_high_fixed():
    assert h2_lodging_archetype_split().status.startswith("OK")
def test_h3_office_wwr_fixed():
    assert h3_office_wwr_size_split().status.startswith("OK")
def test_h4_residential_fixed():
    assert h4_residential_resolver_consumed().status.startswith("OK")
def test_h5_off_equals_flat():
    h5_off_equals_flat()
def test_h6_high_fixed():
    assert h6_preview_sim_agree_on_time().status.startswith("OK")
def test_h7_mf_occupants_fixed():
    assert h7_multifamily_per_unit_occupants().status.startswith("OK")
def test_h8_electric_furnace_efficiency():
    assert h8_electric_furnace_efficiency().status.startswith("OK")
def test_h9_residential_window_table():
    assert h9_residential_window_table().status.startswith("OK")
def test_h10_mapper_deploy_copies_refs():
    assert h10_mapper_deploy_copies_refs().status.startswith("OK")


def main():
    print(f"dynamic-defaults hypotheses (resolver v{sps.RESOLVER_VERSION}, switch=ON)\n")
    failures = 0
    for fn in CHECKS:
        try:
            v = fn()
        except AssertionError as exc:
            failures += 1
            print(f"  INVARIANT FAIL  {fn.__name__}: {exc}\n")
            continue
        if v.status.startswith("REGRESSED"):
            failures += 1
        print(f"  {v.status:<26} {v.key} {v.label}")
        print(f"  {'':<26} {v.detail}\n")
    print("Legend: OK=fixed/expected, OPEN=known LATENT not fixed, REGRESSED/FAIL=action needed.")
    return 1 if failures else 0


# Run via `pytest tests/test_regression_guards.py` (each H is a test_h* pytest fn).
