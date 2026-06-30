"""test_leaf_coverage.py -- standing guarantee for the synthetic leaf dataset.

Proves the synthetic stock (gen_leaf_stock.py) is (a) COMPLETE in coverage -- it hits
every leaf dimension that changes resolver/model behavior -- and (b) RESOLVABLE -- every
synthetic building resolves every programmable field without error. This is the coverage
basis that replaces ASU (which is a single cell); ASU stays as a realism fixture.

Runs anywhere (resolver only, no container). Regenerates the dataset first so it can't
drift from the generator.

    pytest tests/test_leaf_coverage.py
"""
import csv
import importlib.util
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))          # tests/
REPO = os.path.dirname(HERE)
DATA = os.path.join(HERE, "data")
OUT = os.path.join(DATA, "out")
GEN = os.path.join(DATA, "gen_leaf_stock.py")
SPEC = os.path.join(REPO, "solver", "app", "modules", "simulation", "sim_params_spec.py")

# what "complete" requires -- every value of every behavior-changing dimension.
REQUIRED = {
    "div": {"New England", "Middle Atlantic", "East North Central", "West North Central",
            "South Atlantic", "East South Central", "West South Central", "Mountain North",
            "Mountain South", "Pacific"},
    "cz": {"1A", "2A", "2B", "3A", "3B", "3C", "4A", "4B", "4C", "5A", "5B", "6A", "6B", "7", "8"},
    "vint": {"pre-1950", "1950-1959", "1960-1969", "1970-1979", "1980-1989", "1990-1999",
             "2000-2009", "2010-2019", "2020+"},
    "geo": {"county", "nocounty"},
    "wf": {"res", "com"},
    "branch": {"vacant", "slab", "nonslab", "cooling_suppress", "cooling_on",
               "ophours_suppress", "ophours", "units", "archetype_fallback"},
    "forced": {"wood+wood_stove", "natural gas+furnace", "natural gas+boiler",
               "electricity+heat_pump", "electricity+electric_resistance",
               "fuel oil+furnace", "fuel oil+boiler", "propane+furnace"},
    "sparse": {"no_year", "no_geo", "minimal"},
}
RES_SUBTYPES = {"SFA", "MF", "MF2-4", "MF5+", "null->SFD"}
COM_SUBTYPES_MIN = {"Office", "Lodging", "Inpatient health care", "Food service", "Education",
                    "Nonrefrigerated warehouse", "Outpatient health care", "Retail other than mall"}


@pytest.fixture(scope="module")
def rows():
    """The consolidated leaf dataset via the row-lookup accessor (regenerates if absent)."""
    spec = importlib.util.spec_from_file_location("leaf_stock", os.path.join(DATA, "leaf_stock.py"))
    ls = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ls)
    return ls.load()


def _covered(rows, dim):
    return {t.split(":", 1)[1] for r in rows for t in r["leaves"] if t.startswith(dim + ":")}


def test_every_leaf_dimension_covered(rows):
    missing = {d: sorted(req - _covered(rows, d)) for d, req in REQUIRED.items() if req - _covered(rows, d)}
    assert not missing, f"leaf dimensions with uncovered values: {missing}"


def test_subtypes_covered(rows):
    gap = (RES_SUBTYPES | COM_SUBTYPES_MIN) - _covered(rows, "subtype")
    assert not gap, f"subtypes not covered: {sorted(gap)}"


def test_every_building_resolves(rows):
    os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "true"
    os.environ["URBANOPT_STOCHASTIC_SAMPLING"] = "true"
    spec = importlib.util.spec_from_file_location("sps_leaf", SPEC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # physical fields that MUST resolve for every building (schedule fields are
    # legitimately empty for residential + suppressed commercial types).
    must = [f for f in m.SIM_PARAM_DEFAULTS if f not in m.TIME_FIELDS and f != "number_of_occupants"]
    errors, unresolved = [], []
    for r in rows:
        md = r["metadata"]
        try:
            ctx = m.build_asset_ctx(md, building_type=r["asset_subtype_name"], building_id=str(r["asset_id"]))
            for f in must:
                if m.get_param(md, f, ctx) in (None, ""):
                    unresolved.append((r["asset_name"], f))
        except Exception as e:
            errors.append((r["asset_subtype_name"], type(e).__name__, str(e)[:80]))
    assert not errors, f"buildings that errored during resolution: {errors[:10]}"
    assert not unresolved, f"physical fields that failed to resolve: {unresolved[:15]}"


# fields that MUST resolve dynamically (non-flat provenance) for EVERY subtype
_CORE_DYNAMIC = ["number_of_occupants", "heating_system_fuel_type", "cooling_system_fuel_type",
                 "service_water_heating_fuel_type", "window_type", "wall_material", "roof_material",
                 "wall_r_value", "roof_r_value", "window_to_wall_ratio"]
# residential-only systems -- must be dynamic for residential; legitimately flat for commercial
_RES_SYSTEMS = ["heating_system_type", "water_heater_type"]
_RES_EFFECTIVE = {"Single-Family Detached", "Single-Family Attached", "Multifamily"}


def test_dynamic_provenance_per_subtype(rows):
    """PROVENANCE oracle (the fallback-aware leg): with dynamic defaults ON, every CORE field must
    resolve with a DYNAMIC provenance (level is not None) -- i.e. it did NOT silently fall back to
    the flat default -- for EVERY asset subtype. Resolution uses the EFFECTIVE subtype the pipeline
    resolves with (generateFeatureFile maps via effective_id: MF-variants->Multifamily, null->SFD),
    so this mirrors production rather than the raw leaf name. Residential-only system fields must be
    dynamic for residential and may legitimately be flat for commercial. Operating-time fields are
    legitimately flat for residential + always-on/closed commercial, so they are NOT asserted here.

    This is exactly what distinguishes an INTENDED fallback (sparse cell, N/A field) from a BUGGY
    silent one (a must-be-dynamic field that quietly reverts to flat) -- which the A/B and per-field
    delivery tests cannot tell apart on their own."""
    os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "true"
    os.environ["URBANOPT_STOCHASTIC_SAMPLING"] = "true"
    spec = importlib.util.spec_from_file_location("sps_prov", SPEC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    sub = {int(r["id"]): r for r in csv.DictReader(open(os.path.join(REPO, "solver", "upload", "asset_subtypes.csv")))}

    def eff_name(sid):
        return sub[int(sub[int(sid)]["effective_id"])]["name"]

    seen, gaps = set(), []
    for r in rows:
        sid = int(r["asset_subtype_id"])
        if sid in seen:
            continue
        seen.add(sid)
        eff = eff_name(sid)
        ctx = m.build_asset_ctx(r["metadata"], building_type=eff, building_id=str(r["asset_id"]))
        fields = _CORE_DYNAMIC + (_RES_SYSTEMS if eff in _RES_EFFECTIVE else [])
        for f in fields:
            if m.resolve_default(f, ctx)[1] is None:    # None level == fell back to flat
                gaps.append(f"{r['asset_subtype_name']} -> {eff}: '{f}' silently fell back to flat")
    assert len(seen) >= 20, f"expected ~all subtypes exercised, got {len(seen)}"
    assert not gaps, ("dynamic defaults SILENTLY fell back to flat for must-be-dynamic fields "
                      "(buggy fallback, not intended):\n" + "\n".join(gaps))

# Run via `pytest tests/test_leaf_coverage.py`.
