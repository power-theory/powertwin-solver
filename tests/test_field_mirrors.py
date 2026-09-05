"""CR campaign WP-00: the resolved-field list is hand-mirrored in five places, and the Q/A
campaign proved mirror drift is a real silent-failure class. This guard makes the five sets
provably equal so any future field addition that misses a mirror fails loudly here.

Mirrors:
  1. solver/app/views.py DYNAMIC_FIELDS (the preview API contract - source of truth)
  2. tests/test_regression_guards.py DYNAMIC_FIELDS (hand copy)
  3. tests/test_resolvers.py ALL_DYNAMIC_FIELDS (+ NON_DYNAMIC_FIELDS partition)
  4. tests/tools/qa_dynamic_defaults_matrix.py embedded fields tuple (superset: adds the
     non-dynamic system_type/floor_height it also probes)
  5. powertwin-db simulationParamsSpec.js (cross-repo - checked only when the sibling repo
     is present on this machine; skipped otherwise, surfaced as a skip not a pass)
"""
import ast
import os
import re
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


def test_cross_repo_js_mirror_matches_views():
    js = "/home/missionloyd/dev/power-theory/powertwin-db/api/src/utils/simulationParamsSpec.js"
    if not os.path.exists(js):
        candidates = []
        base = "/home/missionloyd/dev/power-theory/powertwin-db"
        for root, _dirs, files in os.walk(base) if os.path.isdir(base) else []:
            if "node_modules" in root:
                continue
            candidates += [os.path.join(root, f) for f in files
                           if f == "simulationParamsSpec.js"]
        if not candidates:
            pytest.skip("powertwin-db simulationParamsSpec.js not present on this machine")
        js = candidates[0]
    src = open(js).read()
    m = re.search(r"DYNAMIC_FIELDS\s*=\s*\[(.*?)\]", src, re.S)
    if not m:
        pytest.skip("DYNAMIC_FIELDS list not found in the JS mirror (layout changed)")
    js_fields = set(re.findall(r"['\"]([a-z_0-9]+)['\"]", m.group(1)))
    assert js_fields == _views_fields(), (
        f"powertwin-db simulationParamsSpec.js DYNAMIC_FIELDS drifted from views.py: "
        f"js-only={sorted(js_fields - _views_fields())} "
        f"py-only={sorted(_views_fields() - js_fields)}"
    )
