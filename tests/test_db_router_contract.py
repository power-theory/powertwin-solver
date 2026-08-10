"""The diagnostics DB router must only call functions its backends actually define.

`db.get_weather` guarded a call to `sqlite_ops.copy_master_to_local(...)` for a distributed
mode that was never implemented. It stayed invisible for as long as every caller passed
`get_weather(asset_id)` with no simulation_name, because the guard `if simulation_name:` was
always false. The moment weather lookups were scoped to a run, the branch became reachable
and every HPC asset died with AttributeError -- a whole class of bug that no unit test of
either backend can catch, since both backends are individually fine.

Everything here is AST-level so it runs on the host: postgres_operations imports psycopg,
which only exists inside the solver image.
"""

import ast
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROUTER = os.path.join(REPO, "solver", "app", "modules", "diagnostics", "db.py")
BACKENDS = {
    "sqlite_ops": os.path.join(REPO, "solver", "app", "modules", "database", "sqlite_operations.py"),
    "postgres_ops": os.path.join(REPO, "solver", "app", "modules", "database", "postgres_operations.py"),
}


def _tree(path):
    return ast.parse(open(path).read())


def _referenced_attrs():
    """{alias: {attr, ...}} for every `alias.attr` the router mentions."""
    found = {k: set() for k in BACKENDS}
    for node in ast.walk(_tree(ROUTER)):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in found:
                found[node.value.id].add(node.attr)
    return found


def _defined_names(path):
    """Top-level defs and assignments the module exposes."""
    names = set()
    for node in _tree(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
    return names


@pytest.mark.parametrize("alias", sorted(BACKENDS))
def test_router_only_calls_functions_the_backend_defines(alias):
    referenced = _referenced_attrs()[alias]
    assert referenced, f"no {alias}.* references found -- the AST walk is broken, not the code"
    missing = sorted(a for a in referenced if a not in _defined_names(BACKENDS[alias]))
    assert not missing, (
        f"solver/app/modules/diagnostics/db.py calls {alias}.{{{', '.join(missing)}}} "
        f"which {os.path.basename(BACKENDS[alias])} does not define"
    )


def test_both_backends_define_every_name_the_router_dispatches_to_both():
    """A name the router dispatches to on both paths must exist on both, or one environment
    breaks while the other stays green (the router is the only thing choosing between them)."""
    refs = _referenced_attrs()
    shared = refs["sqlite_ops"] & refs["postgres_ops"]
    assert shared, "expected the router to dispatch some name to both backends"
    sq, pg = _defined_names(BACKENDS["sqlite_ops"]), _defined_names(BACKENDS["postgres_ops"])
    assert not sorted(shared - sq), f"sqlite_operations missing {sorted(shared - sq)}"
    assert not sorted(shared - pg), f"postgres_operations missing {sorted(shared - pg)}"
