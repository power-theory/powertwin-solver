"""Test standard for powertwin-solver.

ONE runner: `pytest tests/`. Everything is collected by pytest, including the Ruby
golden (via the wrapper in test_ruby_goldens.py).

Layout convention:
  tests/                  pytest test files ONLY (test_*.py) + the Ruby golden it wraps
  tests/fixtures/         committed golden data (resolver_golden.json, ...)
  tests/data/             the synthetic leaf dataset generator (gen_leaf_stock.py)
  tests/prompts/          the QA prompt (accuracy_audit.md)
  tests/tools/            QA / analysis SCRIPTS + their fixtures (not tests; not collected)
  tests/assumptions_ledger.yaml the single-source-of-truth ledger
  tests/runs/             gitignored sim outputs

Test files (all plain pytest, one runner, no __main__):
  test_resolvers.py        resolver field resolution (3016 checks, autouse-enforced)
  test_regression_guards.py H1-H10 guards for previously-fixed bugs
  test_assumptions_ledger.py     ledger self-integrity (spec == code, oracles exist)
  test_leaf_coverage.py    synthetic leaf dataset coverage + resolution
  test_concurrency.py      per-request env-override isolation
  test_ruby_goldens.py     wraps the PowerTwinRefs ruby golden (test_powertwin_refs.rb)

Two layers, one command:
  - host/unit (fast, no runtime): resolver, hypotheses, ground-truth, env-override,
    ruby golden, synthetic coverage. Run on the host.
  - integration (needs OpenStudio/`uo`): mark with @pytest.mark.requires_container.
    These auto-SKIP on the host and RUN when `pytest` is executed THROUGH the
    run_docker.sh container. Never build a bespoke harness -- run the real pipeline
    inside the container as normal pytest.
"""
import shutil
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_container: needs the OpenStudio/`uo` runtime; runs only inside the "
        "run_docker.sh container, skipped on the host.",
    )


def pytest_collection_modifyitems(config, items):
    if shutil.which("uo") is not None:
        return
    skip = pytest.mark.skip(reason="needs `uo` runtime -- run `pytest` via the run_docker.sh container")
    for item in items:
        if "requires_container" in item.keywords:
            item.add_marker(skip)
