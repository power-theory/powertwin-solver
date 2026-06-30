"""Energy-balance closure -- the capture certificate for a simulated building.

Given an URBANopt default_feature_report (per-timestep rows, columns for per-end-use
and per-fuel-facility energy), verify the per-fuel conservation identity to a tolerance:

  sum(end-use columns for that fuel)  ==  <fuel>:Facility

A residual beyond tolerance means end-use energy was produced that did NOT land on the fuel's
facility meter (or vice-versa) -- a silent capture loss. The default tolerance is generous
(~8%) because the EnergyPlus/URBANopt default_feature_report omits some end-use categories
(humidification, heat recovery, refrigeration) for complex commercial buildings, so the
itemized end-uses reconcile to the facility meter only to a few percent (a Lab is ~6%); a
larger residual is a real capture gap. This is pure (no I/O); the container test wires it in.

Column naming in the report (EnergyPlus/URBANopt):
  end-use:   "<EndUse>:<Fuel>(<unit>)"     e.g. "Heating:Electricity(kWh)"
  facility:  "<Fuel>:Facility(<unit>)"     e.g. "Electricity:Facility(kWh)"
The matcher is suffix/unit tolerant and excludes the facility column from the end-use sum.
"""
import re

# Fuels whose end-uses must reconcile to a facility meter. District heat/cool are
# carried as facility-only (no per-end-use breakdown) so they're balance-exempt.
FUELS = ["Electricity", "NaturalGas", "Propane", "FuelOilNo2", "OtherFuels"]


def _facility_col(cols, fuel):
    # Require the unit paren immediately after ':facility(' so we match the ENERGY meter
    # "<Fuel>:Facility(kWh)" and NOT "<Fuel>:Facility Power(kW)" / "Apparent Power(kVA)"
    # (which normalize to ':facilitypower(' -- no '(' right after 'facility').
    f = fuel.lower()
    for c in cols:
        n = c.replace(" ", "").lower()
        if f"{f}:facility(" in n or f":{f}:facility(" in n:
            return c
    return None


def _enduse_cols(cols, fuel):
    out = []
    for c in cols:
        norm = c.replace(" ", "").lower()
        if f":{fuel.lower()}(" not in norm and not norm.endswith(f":{fuel.lower()}"):
            continue
        if "facility" in norm:           # exclude the fuel's own facility meter
            continue
        out.append(c)
    return out


def fuel_residuals(report, rel_tol=0.08, abs_tol=1.0):
    """Return {fuel: {facility, enduse_sum, residual, closes}} for fuels present.
    `report` is a dict-of-lists / DataFrame-like with a `.columns` and column indexing
    returning summable sequences. Sums over all rows (annual closure)."""
    cols = list(report.columns)
    out = {}
    for fuel in FUELS:
        fcol = _facility_col(cols, fuel)
        if fcol is None:
            continue
        ecols = _enduse_cols(cols, fuel)
        facility = float(sum(report[fcol]))
        enduse = float(sum(sum(report[c]) for c in ecols)) if ecols else 0.0
        resid = facility - enduse
        tol = max(abs_tol, rel_tol * abs(facility))
        out[fuel] = {"facility": facility, "enduse_sum": enduse, "enduse_cols": ecols,
                     "residual": resid, "closes": abs(resid) <= tol}
    return out


def balance_report(report, rel_tol=0.08, abs_tol=1.0):
    """Whole-building verdict: {closes: bool, fuels: {...}, unbalanced: [fuel,...]}."""
    fuels = fuel_residuals(report, rel_tol, abs_tol)
    unbalanced = [f for f, r in fuels.items() if not r["closes"]]
    return {"closes": not unbalanced, "fuels": fuels, "unbalanced": unbalanced}
