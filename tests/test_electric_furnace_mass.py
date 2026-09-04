"""WP-08 / R-035 evidence: furnace-electric mass must survive the county-first reconciliation.

Verified from primary data (RECS 2020 fuel-by-system): in East South Central 2020+, 64% of
furnaces heat with electricity, yet the reconciliation bucketed furnace wholesale as combustion
and reassigned that electric mass to heat_pump/electric_resistance by the division hp:er ratio --
in the South that ratio is 80-100% hp, so up to ~30% of a cell simulated as COP-3 heat pumps
that are actually COP-1 electric furnaces (3x heating-energy error)."""
import os
import sys
from collections import Counter

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))
# Set (not setdefault) at import AND per-test: collection-time setdefault is
# fragile -- an earlier module that pops the var mid-session would disable the
# resolver and turn these draws into silent no-ops.
os.environ["URBANOPT_DYNAMIC_DEFAULTS"] = "true"
os.environ["URBANOPT_STOCHASTIC_SAMPLING"] = "true"


@pytest.fixture(autouse=True)
def _pin_resolver_flags(monkeypatch):
    monkeypatch.setenv("URBANOPT_DYNAMIC_DEFAULTS", "true")
    monkeypatch.setenv("URBANOPT_STOCHASTIC_SAMPLING", "true")

from modules.simulation.sim_params_spec import build_asset_ctx, get_param  # noqa: E402

# Mississippi: East South Central, no county (division path), 2022 build year -> 2020+ vintage
MD = {"state": "MS", "area": 2000, "floor_count": 1, "year_built": 2022}


def _draws(n=400):
    pairs = Counter()
    for i in range(n):
        md = dict(MD)
        ctx = build_asset_ctx(md, building_type="Single-Family Detached", climate_zone="3A",
                              building_id=str(i))
        sysv = get_param(md, "heating_system_type", ctx)
        fuel = get_param(md, "heating_system_fuel_type", ctx)
        pairs[(sysv, fuel)] += 1
    return pairs


def test_electric_furnaces_exist_where_the_data_says_they_dominate():
    pairs = _draws()
    n = sum(pairs.values())
    furn_elec = pairs.get(("furnace", "electricity"), 0) / n
    # RECS: ~59% furnaces x ~64% electric ~= 38% of the division stock (division path, no ACS
    # county reconciliation in this ctx). Loose band: the draw must at least PRODUCE the
    # combination materially, where before it was structurally impossible (always 0).
    assert furn_elec > 0.10, (
        f"furnace+electricity share {furn_elec:.1%}; the dominant real-world combination in "
        "this cell is structurally impossible in the resolver"
    )


def test_no_impossible_combustion_heat_pumps():
    pairs = _draws(200)
    for (sysv, fuel), c in pairs.items():
        if sysv in ("heat_pump", "electric_resistance"):
            assert fuel == "electricity", f"{sysv} drew {fuel}"
        if sysv == "wood_stove":
            assert fuel == "wood"
