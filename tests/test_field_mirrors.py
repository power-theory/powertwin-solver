"""CR campaign WP-00: the resolved-field list is hand-mirrored in five places, and the Q/A
campaign proved mirror drift is a real silent-failure class. This guard makes the five sets
provably equal so any future field addition that misses a mirror fails loudly here.

Mirrors:
  1. solver/app/views.py DYNAMIC_FIELDS (the preview API contract - source of truth)
  2. tests/test_regression_guards.py DYNAMIC_FIELDS (hand copy)
  3. tests/test_resolvers.py ALL_DYNAMIC_FIELDS (+ NON_DYNAMIC_FIELDS partition)
  4. tests/tools/qa_dynamic_defaults_matrix.py embedded fields tuple (superset: adds the
     non-dynamic system_type/floor_height it also probes)
  5. powertwin-db api/lib/simulationParamsSpec.ts and its dropdown seeds (cross-repo): the db
     keeps no dynamic list since the solver owns that, so the check is spec to spec (fields,
     ranges, defaults, seed values). Checked only when the sibling repo and its tsx are present;
     skipped otherwise, surfaced as a skip not a pass)
"""
import ast
import csv
import functools
import json
import os
import re
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))


def _list_literal(path, name):
    """Extract a top-level list-of-strings assignment via AST (comment-proof)."""
    tree = ast.parse(open(path).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return {ast.literal_eval(e) for e in node.value.elts}
    raise AssertionError(f"{name} not found in {path}")


def _views_fields():
    return _list_literal(os.path.join(REPO, "solver", "app", "views.py"), "DYNAMIC_FIELDS")


def test_regression_guard_mirror_matches_views():
    guard = _list_literal(os.path.join(REPO, "tests", "test_regression_guards.py"),
                          "DYNAMIC_FIELDS")
    assert guard == _views_fields(), (
        "tests/test_regression_guards.py DYNAMIC_FIELDS drifted from views.py"
    )


def test_resolver_suite_partition_matches_views():
    p = os.path.join(REPO, "tests", "test_resolvers.py")
    dyn = _list_literal(p, "ALL_DYNAMIC_FIELDS")
    assert dyn == _views_fields(), (
        "tests/test_resolvers.py ALL_DYNAMIC_FIELDS drifted from views.py"
    )
    non = _list_literal(p, "NON_DYNAMIC_FIELDS")
    from modules.simulation.sim_params_spec import SIM_PARAM_DEFAULTS
    assert dyn | non == set(SIM_PARAM_DEFAULTS), (
        "ALL_DYNAMIC + NON_DYNAMIC no longer partition SIM_PARAM_DEFAULTS"
    )
    assert not (dyn & non), "a field appears in both halves of the partition"


def test_qa_matrix_module_inventory_covers_views_fields():
    """The matrix tool ALSO keeps a module-level FIELDS/FLAT inventory (found while
    adding the duct fields - a sixth mirror). Both must cover the dynamic set."""
    p = os.path.join(REPO, "tests", "tools", "qa_dynamic_defaults_matrix.py")
    fields = _list_literal(p, "FIELDS") if False else None
    # FIELDS is a tuple; reuse the AST helper on tuples too
    tree = ast.parse(open(p).read())
    got = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "FIELDS":
                    got = {ast.literal_eval(e) for e in node.value.elts}
    assert got, "module-level FIELDS not found"
    missing = _views_fields() - got
    assert not missing, f"matrix module FIELDS missing dynamic fields: {sorted(missing)}"
    flat = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "FLAT":
                    flat = {ast.literal_eval(k) for k in node.value.keys}
    assert flat and not (_views_fields() - flat), "matrix FLAT missing dynamic fields"


def test_qa_matrix_probe_covers_views_fields():
    src = open(os.path.join(REPO, "tests", "tools", "qa_dynamic_defaults_matrix.py")).read()
    m = re.search(r'"fields=\((.*?)\); "', src, re.S)
    assert m, "embedded fields tuple not found in qa_dynamic_defaults_matrix.py"
    probed = set(re.findall(r"'([a-z_0-9]+)'", m.group(1)))
    missing = _views_fields() - probed
    assert not missing, (
        f"qa_dynamic_defaults_matrix.py probe tuple is missing dynamic fields: {sorted(missing)}"
    )


DB = os.environ.get("POWERTWIN_DB_REPO", os.path.join(os.path.dirname(REPO), "powertwin-db"))
DB_SPEC = os.path.join(DB, "api", "lib", "simulationParamsSpec.ts")
DB_SEEDS = os.path.join(DB, "db", "data", "records", "simulations")
# Where the db deliberately differs from the solver. The solver range bounds resolver draws as well as user input;
# the db range bounds only what a user may type.
DB_STRICTER_RANGES = {
    # R-0 is a county "Uninsulated" draw; a typed value needs R-1, the least the commercial roof measure takes
    'wall_r_value': (1.0, 80.0),
    'roof_r_value': (1.0, 80.0),
}
# User fields the solver reads outside SIM_PARAM_DEFAULTS
DB_FIELDS_READ_ELSEWHERE = {'vacant'}      # sim_params_spec.is_vacant
# Defaults the solver owns: the db spec leaves them blank and shows the preview's resolved value (a failed preview
# shows no number). floor_height's unset value is what URBANopt builds by building type (house 8 ft, commercial the
# prototype story or 3.6 m), which no single spec number can state (2026-09-30).
DB_DEFAULT_SOLVER_OWNED = {'floor_height'}


@functools.lru_cache(maxsize=1)
def _db_spec():
    """The db's SIMULATION_PARAMS_SPEC as a dict, evaluated by the db's own tsx (the db moved to typescript on
    2026-09-27, and a stale compiled .js without the lists left the old regex check skipping for three days)."""
    if not os.path.exists(DB_SPEC):
        pytest.skip(f"powertwin-db spec not present on this machine ({DB_SPEC})")
    tsx = os.path.join(DB, "api", "node_modules", ".bin", "tsx")
    if not os.path.exists(tsx):
        pytest.skip("powertwin-db api/node_modules not installed (tsx)")
    code = (f"import {{ SIMULATION_PARAMS_SPEC as s }} from {json.dumps(DB_SPEC)}; "
            "console.log(JSON.stringify(s));")
    out = subprocess.run([tsx, "-e", code], capture_output=True, text=True, timeout=120, cwd=os.path.join(DB, "api"))
    assert out.returncode == 0, f"tsx could not load {DB_SPEC}: {out.stderr[-500:]}"
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_db_spec_fields_are_solver_fields():
    from modules.simulation.sim_params_spec import SIM_PARAM_DEFAULTS, TIME_FIELDS, RETIRED_INPUT_FIELDS
    db = set(_db_spec())
    unknown = db - set(SIM_PARAM_DEFAULTS) - set(TIME_FIELDS) - DB_FIELDS_READ_ELSEWHERE
    assert not unknown, f"db offers fields the solver does not read: {sorted(unknown)}"
    retired = db & set(RETIRED_INPUT_FIELDS)
    assert not retired, f"db still offers retired fields: {sorted(retired)}"


def test_every_db_spec_field_is_a_fan_out_lever():
    """The delta surrogate answers only edits to levers its fan-out varied (Surrogate Serving Plan, delta handshake rule
    4), so a field the product offers and the sampler leaves out routes every edit of it to a full URBANopt run."""
    sys.path.insert(0, REPO)
    from surrogate.app.modules.sampling.scenario_fanout import LEVER_SECTOR
    db = set(_db_spec())
    assert db == set(LEVER_SECTOR), (f"db only: {sorted(db - set(LEVER_SECTOR))}; "
                                     f"sampler only: {sorted(set(LEVER_SECTOR) - db)}")


def test_db_spec_ranges_and_defaults_match_solver():
    from modules.simulation.sim_params_spec import NUMERIC_RANGES, SIM_PARAM_DEFAULTS
    drift = []
    for key, entry in _db_spec().items():
        if key in NUMERIC_RANGES:
            want = DB_STRICTER_RANGES.get(key, NUMERIC_RANGES[key])
            got = (entry.get("min"), entry.get("max"))
            # no maximum: the db spec omits max, the solver bound is inf (floor_height, 2026-10-01)
            if (float(got[0]), float("inf") if got[1] in (None, "") else float(got[1])) != tuple(map(float, want)):
                drift.append(f"{key} range db {got} vs solver {want}")
        if key in SIM_PARAM_DEFAULTS and not (key in DB_DEFAULT_SOLVER_OWNED and entry.get('default') in (None, '')):
            a, b = entry.get("default"), SIM_PARAM_DEFAULTS[key]
            blank = lambda v: v in (None, "")
            same = (blank(a) and blank(b)) or (not blank(a) and not blank(b) and (
                float(a) == float(b) if isinstance(b, (int, float)) else str(a) == str(b)))
            if not same:
                drift.append(f"{key} default db {a!r} vs solver {b!r}")
    assert not drift, "powertwin-db simulationParamsSpec.ts drifted from sim_params_spec.py: " + "; ".join(drift)


def test_db_dropdown_seeds_are_values_the_solver_builds():
    """Every dropdown value the modal can offer must pass the solver's enum gate; a value the gate rejects is
    dropped for the default with only a log line. Only is_active = 1 rows are offered (the sim-types route filters
    on it), so the 15 inactive 'Residential - *' system types (ids 86-100) are not drift."""
    from modules.simulation.sim_params_spec import ENUM_VALUES, RESOLVER_ENUM_VALUES
    rejected = {}
    for key, entry in _db_spec().items():
        table = entry.get("enum_table")
        if not table:
            continue
        path = os.path.join(DB_SEEDS, f"{table}.csv")
        assert os.path.exists(path), f"{key}: seed {path} missing"
        with open(path, newline="") as f:
            offered = {row["name"] for row in csv.DictReader(f) if str(row.get("is_active", "1")).strip() == "1"}
        allowed = set(RESOLVER_ENUM_VALUES.get(key, ENUM_VALUES.get(key, ())))
        assert allowed, f"{key}: the db offers a dropdown the solver has no enum for"
        bad = sorted(offered - allowed)
        if bad:
            rejected[key] = bad
    assert not rejected, f"db dropdowns offer values the solver ignores: {rejected}"
