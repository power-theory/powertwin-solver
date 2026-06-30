"""Unit tests for the energy-balance closure helper (pure logic, host-runnable).
The real-report wiring is verified in the container e2e (test_sim_e2e.py)."""
import pandas as pd

from energy_balance import balance_report, fuel_residuals


def _report(rows):
    return pd.DataFrame(rows)


def test_balanced_report_closes():
    # Electricity facility == sum of its end-uses; NaturalGas likewise.
    df = _report([
        {"Heating:Electricity(kWh)": 10, "Cooling:Electricity(kWh)": 5,
         "InteriorLights:Electricity(kWh)": 3, "Electricity:Facility(kWh)": 18,
         "Heating:NaturalGas(kBtu)": 40, "WaterSystems:NaturalGas(kBtu)": 10,
         "NaturalGas:Facility(kBtu)": 50},
        {"Heating:Electricity(kWh)": 20, "Cooling:Electricity(kWh)": 0,
         "InteriorLights:Electricity(kWh)": 4, "Electricity:Facility(kWh)": 24,
         "Heating:NaturalGas(kBtu)": 30, "WaterSystems:NaturalGas(kBtu)": 10,
         "NaturalGas:Facility(kBtu)": 40},
    ])
    verdict = balance_report(df)
    assert verdict["closes"], verdict
    assert set(verdict["fuels"]) == {"Electricity", "NaturalGas"}
    assert verdict["fuels"]["Electricity"]["residual"] == 0
    assert verdict["fuels"]["NaturalGas"]["enduse_sum"] == 90


def test_silent_capture_loss_is_caught():
    # Facility carries 100 kWh but only 60 is accounted in end-uses -> must NOT close.
    df = _report([
        {"Heating:Electricity(kWh)": 60, "Electricity:Facility(kWh)": 100},
    ])
    verdict = balance_report(df)
    assert not verdict["closes"]
    assert "Electricity" in verdict["unbalanced"]
    assert verdict["fuels"]["Electricity"]["residual"] == 40


def test_otherfuels_wood_reconciles():
    # Wood heat lands on the OtherFuels meter; its end-use must reconcile.
    df = _report([
        {"Heating:OtherFuels(kBtu)": 70, "OtherFuels:Facility(kBtu)": 70},
    ])
    r = fuel_residuals(df)
    assert r["OtherFuels"]["closes"]
    assert r["OtherFuels"]["enduse_sum"] == 70


def test_facility_with_no_enduse_is_flagged():
    # Energy on a fuel meter with zero end-use accounting is a capture gap.
    df = _report([{"Propane:Facility(kBtu)": 25}])
    r = fuel_residuals(df)
    assert not r["Propane"]["closes"]
    assert r["Propane"]["residual"] == 25
