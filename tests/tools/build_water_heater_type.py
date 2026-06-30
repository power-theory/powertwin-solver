"""Regenerate recs2020_water_heater_type.json from ResStock 2024.2 realized metadata.

WHY THIS EXISTS: the previous file cited RECS `TYPERFR1` -- the REFRIGERATOR-type variable.
RECS 2020 public microdata has no water-heater-TYPE variable at all, and the old values
(storage ~76% / tankless ~21% / HPWH ~5%) matched no source and were implausible (tankless
~3x, HPWH ~15x reality). This re-derives type from ResStock `in.water_heater_efficiency`,
weighted, keyed `census_division_recs` x decade vintage -- the same realized-stock source
already used for the envelope (recs2020_envelope.json), so the name keeps its convention.

Output schema (drop-in for sim_params_spec._resolve_water_heater_type):
  {_source, _notes, _division: {division: {<decade>|all: {mode, shares{3 types}}}}}
Lookup is division -> ctx.decade_vintage -> else 'all'.
"""
import json
import pandas as pd

PARQUET = "/mnt/h/data/resstock/baseline.parquet"
OUT = "solver/upload/reference_data/recs2020_water_heater_type.json"
TYPES = ["storage water heater", "instantaneous water heater", "heat pump water heater"]
VMAP = {"<1940": "pre-1950", "1940s": "pre-1950", "1950s": "1950-1959", "1960s": "1960-1969",
        "1970s": "1970-1979", "1980s": "1980-1989", "1990s": "1990-1999",
        "2000s": "2000-2009", "2010s": "2010-2019"}
DECADES = ["pre-1950", "1950-1959", "1960-1969", "1970-1979", "1980-1989",
           "1990-1999", "2000-2009", "2010-2019"]


def wtype(e):
    e = str(e)
    if "Tankless" in e:
        return "instantaneous water heater"
    if "Heat Pump" in e:
        return "heat pump water heater"
    return "storage water heater"


def shares_of(sub):
    g = sub.groupby("type").w.sum()
    tot = g.sum()
    sh = {t: round(float(g.get(t, 0.0)) / tot, 3) for t in TYPES}
    resid = round(1.0 - sum(sh.values()), 3)          # absorb rounding into the modal share
    if resid:
        top = max(sh, key=sh.get)
        sh[top] = round(sh[top] + resid, 3)
    return {"mode": max(sh, key=sh.get), "shares": sh}


df = pd.read_parquet(PARQUET, columns=["in.water_heater_efficiency", "in.census_division_recs",
                                       "in.vintage", "weight"])
df["type"] = df["in.water_heater_efficiency"].map(wtype)
df["dec"] = df["in.vintage"].map(VMAP)
df["w"] = df["weight"]

division = {}
for div, dsub in df.groupby("in.census_division_recs"):
    cell = {}
    for dec in DECADES:
        s = dsub[dsub.dec == dec]
        if s.w.sum() > 0:
            cell[dec] = shares_of(s)
    if "2010-2019" in cell:                            # ResStock vintage tops at 2010s
        cell["2020+"] = json.loads(json.dumps(cell["2010-2019"]))
    cell["all"] = shares_of(dsub)
    division[div] = cell

doc = {
    "_source": {
        "name": "NREL ResStock 2024.2 realized building-stock metadata (OEDI baseline.parquet, 549,715 homes)",
        "url": "https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock/2024/resstock_tmy3_release_2/metadata/baseline.parquet",
        "variable": "type from in.water_heater_efficiency x in.census_division_recs x in.vintage, weighted by `weight`",
        "extracted": "2026-06-30",
        "regen": "tests/tools/build_water_heater_type.py",
    },
    "_notes": ("Water heater TYPE from ResStock realized stock. Type from efficiency string: "
               "'Tankless'->instantaneous, 'Heat Pump'->heat pump water heater, else storage. "
               "Keyed division x decade vintage; '2020+' carried forward from 2010-2019 "
               "(ResStock vintage tops at 2010s); 'all' = vintage-agnostic division fallback. "
               "REPLACES a prior file that wrongly cited RECS TYPERFR1 (the refrigerator-type "
               "variable; RECS has no water-heater-type variable) and overstated tankless/HPWH ~3-15x."),
    "_division": division,
}

with open(OUT, "w") as f:
    json.dump(doc, f, indent=2)

g = df.groupby("type").w.sum()
print(f"wrote {OUT}: {len(division)} divisions")
print("national:", {t: round(float(g.get(t, 0)) / g.sum(), 3) for t in TYPES})
