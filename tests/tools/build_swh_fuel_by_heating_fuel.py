"""Derive P(water-heater fuel | heating fuel) from RECS 2020 (FUELH2O | FUELHEAT),
NWEIGHT-weighted. Fixes the verified coherence gap: the resolver conditioned SWH fuel on
the heating SYSTEM TYPE (furnace/boiler), which averages over gas+propane+oil furnaces and
collapses to ~the marginal -- so a gas-heated home got gas water heating only ~48% of the
time vs ~82% in reality. Conditioning on the resolved heating FUEL closes that.

Writes recs2020_swh_fuel_by_heating_fuel.json = {heating_fuel: {mode, share, shares}}.
  python3 tests/tools/build_swh_fuel_by_heating_fuel.py
"""
import json
from collections import defaultdict

import pandas as pd

RECS = "tmp/recs2020_public_v7.csv"
OUT = "solver/upload/reference_data/recs2020_swh_fuel_by_heating_fuel.json"
FUEL = {1: "natural gas", 2: "propane", 3: "fuel oil", 5: "electricity", 7: "wood"}


def main():
    df = pd.read_csv(RECS, usecols=["FUELHEAT", "FUELH2O", "REGIONC", "NWEIGHT"])
    df["hf"] = df["FUELHEAT"].map(FUEL)
    df["wf"] = df["FUELH2O"].map(FUEL)
    df = df[df["hf"].notna() & df["wf"].notna()]

    def cond(sub):
        w = defaultdict(float)
        for wf, n in zip(sub["wf"], sub["NWEIGHT"]):
            w[wf] += float(n)
        tot = sum(w.values())
        if tot <= 0:
            return None
        shares = {k: round(v / tot, 4) for k, v in w.items() if v / tot >= 0.005}
        mode = max(shares, key=shares.get)
        shares[mode] = round(shares[mode] + (1.0 - sum(shares.values())), 4)
        return {"mode": mode, "share": shares[mode],
                "shares": dict(sorted(shares.items(), key=lambda kv: -kv[1]))}

    out = {hf: cond(df[df["hf"] == hf]) for hf in FUEL.values() if (df["hf"] == hf).any()}
    doc = {
        "_source": {
            "name": "EIA RECS 2020 public microdata v7, P(FUELH2O | FUELHEAT), NWEIGHT-weighted",
            "url": "https://www.eia.gov/consumption/residential/data/2020/",
            "extracted": "2026-06-29",
            "regen": "tests/tools/build_swh_fuel_by_heating_fuel.py",
            "methodology": ("Water-heater fuel conditioned on the building's HEATING fuel (not system "
                            "type). The resolver resolves heating fuel first, then share-weights the "
                            "SWH fuel from this table by the building_id hash; a heat-pump water heater "
                            "is forced electric upstream. National (heating-fuel signal dominates region)."),
        },
        **out,
    }
    with open(OUT, "w") as f:
        json.dump(doc, f, indent=2)
    print(f"wrote {OUT}")
    for hf, rec in out.items():
        print(f"  heat={hf:12s} -> SWH {rec['shares']}")
    # region-variance sanity: does P(SWH|heat=gas) move much across regions?
    print("\nregion check P(SWH | heat=natural gas):")
    for r in df["REGIONC"].unique():
        sub = df[(df["hf"] == "natural gas") & (df["REGIONC"] == r)]
        c = cond(sub)
        if c:
            print(f"  {r:10s} {c['shares']}")


if __name__ == "__main__":
    main()
