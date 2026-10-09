"""The frozen benchmark image leaves out the fast estimate (surrogate package and its serving module, 2026-10-09):
views must still import and serve URBANopt requests, and resolve the serving module lazily, as None when absent."""
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOLVER = os.path.join(REPO, "solver")


@pytest.fixture
def views(monkeypatch):
    monkeypatch.chdir(SOLVER)
    monkeypatch.syspath_prepend(os.path.join(SOLVER, "app"))
    monkeypatch.setenv("POWERTWIN_LOG_DIR", "/tmp")
    import views as V
    return V


def test_serving_module_resolves_when_present(views):
    ss = views.fast_estimate_module()
    assert ss is not None and ss.URBANOPT_MODEL_TYPE_ID == views.URBANOPT_MODEL_TYPE_ID == 1


def test_a_build_without_the_fast_estimate_gets_none(views, monkeypatch):
    # None in sys.modules makes the import raise ImportError, as the missing file does in the benchmark image
    monkeypatch.setitem(sys.modules, 'modules.simulation.surrogate_serving', None)
    # and the package attribute an earlier import left behind (the import would return it without asking sys.modules)
    monkeypatch.delattr(sys.modules['modules.simulation'], 'surrogate_serving', raising=False)
    assert views.fast_estimate_module() is None


def test_asset_update_no_longer_imports_the_serving_module_unconditionally():
    src = open(os.path.join(SOLVER, 'app', 'views.py')).read()
    body = src[src.index('SS = fast_estimate_module()'):]
    body = body[:body.index("logger.info(f\"Accepted async simulation")]
    assert 'from modules.simulation import surrogate_serving' not in body
    assert "if SS is None and model_type_id != URBANOPT_MODEL_TYPE_ID:" in body
