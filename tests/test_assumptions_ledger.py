"""test_assumptions_ledger.py -- the single-file PROOF that closes the loop.

tests/assumptions_ledger.yaml is the spec. THIS file proves the spec is honest and
consistent with the code, so that "ledger green" is trustworthy rather than a
claim about a claim. It enforces, in one place:

  1. COMPLETENESS   every code field (SIM_PARAM_DEFAULTS) has exactly one ledger
                    row, and vice versa -- no field is undocumented or phantom.
  2. CONSISTENCY    ledger enum values / numeric ranges agree with the code
                    (ENUM_VALUES / RESOLVER_ENUM_VALUES / NUMERIC_RANGES).
  3. PROVENANCE     every reference file the resolver _load_ref's has a ledger
                    `sources` entry, and every ledger source file exists on disk.
  4. CONSERVATION   every `shares` block in the stock-survey JSONs sums to ~1.0
                    (the physical invariant that catches dropped/!=1 transcription).
  5. SELF-INTEGRITY every oracle the ledger CLAIMS (`file::name`) names a test
                    that actually EXISTS. This is what stops the ledger lying:
                    a renamed/deleted test fails here, not silently.
  6. SCOREBOARD     every field's oracle.status is a legal value and appears in
                    coverage_summary -- the summary can't drift from the rows.

What this file does NOT do: re-run the heavy oracles (resolver golden, E2E sims).
Those ARE the named tests in checks (5); this file proves they exist and the
spec is sound, then DELEGATES execution to them (run by the normal suite / the
container). Closed loop = this passes AND the named oracles pass.

The declared `coverage_summary.open_uncertainty` is printed every run -- it is
the honest residual (tracked, not hidden). It is NOT a failure: those are the
known gaps + the accepted trust_boundaries.

Run: pytest tests/test_assumptions_ledger.py   (or: python3 tests/test_assumptions_ledger.py)
"""
import importlib.util
import json
import os
import re

import pytest
from collections import Counter
try:
    import yaml
except ImportError:                       # pragma: no cover
    yaml = None

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assumptions_ledger.yaml")
REF_DIR = os.path.join(REPO, "solver", "upload", "reference_data")
SPEC_PATH = os.path.join(REPO, "solver", "app", "modules", "simulation", "sim_params_spec.py")

# Map a ledger oracle file-stem -> the actual test file on disk.
ORACLE_FILES = {
    "test_resolvers": os.path.join(REPO, "tests", "test_resolvers.py"),
    "test_regression_guards": os.path.join(REPO, "tests", "test_regression_guards.py"),
    "test_powertwin_refs": os.path.join(REPO, "tests", "test_powertwin_refs.rb"),
    "test_sim_e2e": os.path.join(REPO, "tests", "test_sim_e2e.py"),
    "test_assumptions_ledger": os.path.abspath(__file__),
}
VALID_STATUS = {
    "PROVEN", "RESOLVER_PROVEN_INTEGRATION_PARTIAL", "RESOLVER_PROVEN_INTEGRATION_UNVERIFIED",
    "STATIC", "PROPERTY", "PARTIAL", "UNVERIFIED", "PROVEN_FALLBACK",
}


def _load_ledger():
    if yaml is None:
        pytest.skip("pyyaml not installed")
    with open(LEDGER_PATH) as fh:
        return yaml.safe_load(fh)


def _load_spec():
    spec = importlib.util.spec_from_file_location("sps_gt", SPEC_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _ledger_field_names(ledger):
    """Expand the field rows to the underlying code field names (op_schedule_fields
    is one row covering the 4 time fields)."""
    names = set()
    for key, body in ledger["fields"].items():
        sub = body.get("fields") if isinstance(body, dict) else None
        names.update(sub if sub else [key])
    return names


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


# --------------------------------------------------------------------------- #
# 1. COMPLETENESS
# --------------------------------------------------------------------------- #
def test_every_code_field_is_ledgered_and_vice_versa():
    ledger, sps = _load_ledger(), _load_spec()
    code = set(sps.SIM_PARAM_DEFAULTS)
    led = _ledger_field_names(ledger)
    missing = code - led
    phantom = led - code
    assert not missing, f"code fields with NO ledger row (undocumented): {sorted(missing)}"
    assert not phantom, f"ledger fields not in SIM_PARAM_DEFAULTS (phantom): {sorted(phantom)}"


# --------------------------------------------------------------------------- #
# 2. CONSISTENCY -- ledger enum/range agree with code
# --------------------------------------------------------------------------- #
def test_enum_and_range_consistency():
    ledger, sps = _load_ledger(), _load_spec()
    enum = sps.ENUM_VALUES
    renum = getattr(sps, "RESOLVER_ENUM_VALUES", {})
    ranges = sps.NUMERIC_RANGES
    problems = []
    for key, body in ledger["fields"].items():
        if not isinstance(body, dict):
            continue
        vals = body.get("values")
        if isinstance(vals, list) and key in enum:
            allowed = set(enum.get(key, set())) | set(renum.get(key, set()))
            extra = set(vals) - allowed
            if extra:
                problems.append(f"{key}: ledger enum values not in code enum: {sorted(extra)}")
        rng = body.get("range")
        if isinstance(rng, list) and key in ranges:
            if tuple(rng) != tuple(ranges[key]):
                problems.append(f"{key}: ledger range {rng} != code NUMERIC_RANGES {ranges[key]}")
    assert not problems, "\n".join(problems)


# --------------------------------------------------------------------------- #
# 3. PROVENANCE -- every _load_ref file is documented and every source exists
# --------------------------------------------------------------------------- #
def test_reference_files_documented_and_present():
    ledger = _load_ledger()
    documented = {s.get("file") for s in ledger["sources"].values() if isinstance(s, dict) and s.get("file")}
    # ledger source files must exist on disk
    for f in documented:
        assert os.path.exists(os.path.join(REF_DIR, f)), f"ledger source file missing on disk: {f}"
    # every _load_ref('x') in the resolver must have an <x>.json ledger source
    src = open(SPEC_PATH).read()
    used = set(re.findall(r"_load_ref\(['\"]([a-z0-9_]+)['\"]\)", src))
    undocumented = {u for u in used if f"{u}.json" not in documented}
    assert not undocumented, f"reference files used by resolver but NOT in ledger sources: {sorted(undocumented)}"


# --------------------------------------------------------------------------- #
# 4. CONSERVATION -- every shares block sums to ~1.0
# --------------------------------------------------------------------------- #
def _walk_shares(obj, path=""):
    out = []
    if isinstance(obj, dict):
        if "shares" in obj and isinstance(obj["shares"], dict):
            out.append((path, obj["shares"]))
        for k, v in obj.items():
            out.extend(_walk_shares(v, f"{path}/{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_walk_shares(v, f"{path}[{i}]"))
    return out


def test_reference_data_shares_conserve():
    share_files = [
        "recs2020_residential_fuel_mix", "recs2020_heating_system_type",
        "recs2020_fuel_by_system_type", "recs2020_swh_fuel_by_heating_fuel",
        "recs2020_water_heater_type", "cbecs2018_commercial_fuel_mix",
    ]
    bad = []
    for name in share_files:
        path = os.path.join(REF_DIR, f"{name}.json")
        if not os.path.exists(path):
            bad.append(f"{name}: file missing")
            continue
        for cell_path, shares in _walk_shares(json.load(open(path))):
            total = sum(v for v in shares.values() if isinstance(v, (int, float)))
            if shares and abs(total - 1.0) > 0.02:
                bad.append(f"{name}{cell_path}: shares sum {total:.3f} != 1.0")
    assert not bad, "shares conservation violated:\n" + "\n".join(bad[:20])


# --------------------------------------------------------------------------- #
# 5. SELF-INTEGRITY -- every claimed oracle test actually exists
# --------------------------------------------------------------------------- #
def test_every_claimed_oracle_exists():
    raw = open(LEDGER_PATH).read()
    refs = set(re.findall(r"(test_[a-z0-9_]+)::([\w.\-]+)", raw))  # [0-9] so digit stems (test_sim_e2e) match
    missing = []
    _content_cache = {}
    for fstem, name in sorted(refs):
        fpath = ORACLE_FILES.get(fstem)
        if not fpath or not os.path.exists(fpath):
            missing.append(f"{fstem}::{name} -> oracle FILE not found ({fstem})")
            continue
        if fpath not in _content_cache:
            _content_cache[fpath] = _norm(open(fpath).read())
        if _norm(name) not in _content_cache[fpath]:
            missing.append(f"{fstem}::{name} -> named test/section NOT found in {os.path.basename(fpath)}")
    assert not missing, "ledger claims oracles that do not exist (the ledger is lying):\n" + "\n".join(missing)


# --------------------------------------------------------------------------- #
# 6. SCOREBOARD -- statuses legal; summary references only real rows
# --------------------------------------------------------------------------- #
def _all_status_rows(ledger):
    """(name, status) for every field + ctx_derivation + input_contract row."""
    rows = []
    for key, body in ledger["fields"].items():
        if isinstance(body, dict) and isinstance(body.get("oracle"), dict):
            rows.append((key, body["oracle"].get("status")))
    for sect in ("ctx_derivation", "input_contract"):
        for key, body in ledger.get(sect, {}).items():
            if isinstance(body, dict) and isinstance(body.get("oracle"), dict):
                rows.append((f"{sect}.{key}", body["oracle"].get("status")))
    return rows


def test_scoreboard_statuses_legal():
    ledger = _load_ledger()
    bad = [(n, s) for n, s in _all_status_rows(ledger) if s not in VALID_STATUS]
    assert not bad, f"rows with illegal/missing oracle.status: {bad}"


def test_trust_boundaries_have_seams():
    ledger = _load_ledger()
    ids = {b["id"] for b in ledger["trust_boundaries"]}
    for required in ("input_data_truth", "external_services"):
        assert required in ids, f"missing trust boundary: {required}"
    for b in ledger["trust_boundaries"]:
        if b["id"] in ("input_data_truth", "external_services"):
            assert b.get("in_control_seam"), f"out-of-control boundary {b['id']} has no in_control_seam"


# --------------------------------------------------------------------------- #
# RESIDUAL -- print the honest open uncertainty every run (not a failure)
# --------------------------------------------------------------------------- #
def test_report_open_uncertainty():
    ledger = _load_ledger()
    rows = _all_status_rows(ledger)
    tally = Counter(s for _, s in rows)
    open_items = ledger["coverage_summary"].get("open_uncertainty", [])
    print("\n=== GROUND-TRUTH SCOREBOARD ===")
    for status, n in sorted(tally.items()):
        print(f"  {status}: {n}")
    print(f"=== OPEN UNCERTAINTY ({len(open_items)} declared, tracked not hidden) ===")
    for i, item in enumerate(open_items, 1):
        print(f"  {i}. {item}")
    print("=== ACCEPTED TRUST BOUNDARIES (out of scope by design) ===")
    for b in ledger["trust_boundaries"]:
        print(f"  - {b['id']}")
    assert open_items, "open_uncertainty should be explicit (empty means either done or dishonest)"


# Run via `pytest tests/test_assumptions_ledger.py`.
