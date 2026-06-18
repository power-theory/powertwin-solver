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

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))

# Resolver reads the switch at call time, so toggling os.environ is enough.
os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "true"

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

# Mirror of solver/app/views.py:1088 DYNAMIC_FIELDS (the preview contract).
DYNAMIC_FIELDS = [
    "number_of_occupants",
    "heating_system_fuel_type", "cooling_system_fuel_type",
    "service_water_heating_fuel_type", "window_type",
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


class V:
    def __init__(self, key, label, status, detail):
        self.key, self.label, self.status, self.detail = key, label, status, detail


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
    if fixed:  # numeric guard for Manzanita (403,931 ft^2)
        assert (occ_tall, wwr_tall, wwr_short) == (7966, 0.27, 0.11), \
            f"numeric drift: occ={occ_tall} wwr_tall={wwr_tall} wwr_short={wwr_short}"
    detail = (f"floor_count in ctx={has_fc}; 15-story={name_tall}(wwr {wwr_tall}, occ {occ_tall}) "
              f"2-story={name_short}(wwr {wwr_short})")
    return V("H2", "Lodging floor_count archetype split [HIGH]",
             "OK (fixed)" if fixed else "REGRESSED (multi-story Lodging -> SmallHotel)", detail)


def h3_office_wwr_size_split():
    """Office WWR size-bands (Small 0.21/Med 0.33/Large 0.40); Office-aliased non-Office types stay 0.33."""
    def wwr(bt, area):
        return resolve_default("window_to_wall_ratio",
                               build_asset_ctx({"state": "AZ", "year_built": 2000, "area": area}, building_type=bt))[0]
    ws, wm, wl = wwr("Office", 1442), wwr("Office", 60000), wwr("Office", 219968)
    # Public assembly aliases to Office for density/schedule but Ruby builds MediumOffice -> must NOT size-band.
    pa_small, pa_large = wwr("Public assembly", 1442), wwr("Public assembly", 219968)
    fixed = (ws, wm, wl) == (0.21, 0.33, 0.40) and (pa_small, pa_large) == (0.33, 0.33)
    detail = (f"Office S/M/L={ws}/{wm}/{wl} (want 0.21/0.33/0.40); "
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
    assert wwr == 0.15 and occ == 4, f"residential resolver drift: wwr={wwr} occ={occ}"
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


CHECKS = [h1_operating_hours_reach_sim, h2_lodging_archetype_split, h3_office_wwr_size_split,
          h4_residential_resolver_consumed, h5_off_equals_flat, h6_preview_sim_agree_on_time,
          h7_multifamily_per_unit_occupants]


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


if __name__ == "__main__":
    raise SystemExit(main())
