"""Regenerate tests/fixtures/resolver_golden.json -- the resolver characterization
snapshot. Run ONLY on a deliberate, reviewed behavior change (e.g. a reference-data
regeneration). Mirrors the exact case matrix in test_resolvers.test_resolver_golden.

    python3 tests/tools/gen_resolver_golden.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.dirname(HERE)
sys.path.insert(0, TESTS)
import test_resolvers as t  # noqa: E402  (sets URBANOPT_* env + loads the resolver)

DIV_STATE = {'New England': 'MA', 'Middle Atlantic': 'NY', 'East North Central': 'IL',
             'West North Central': 'IA', 'South Atlantic': 'DE', 'East South Central': 'AL',
             'West South Central': 'TX', 'Mountain North': 'CO', 'Mountain South': 'AZ',
             'Pacific': 'CA'}
COUNTY = {'AZ': '04013', 'IL': '17031', 'MA': '25025', 'TX': '48201', 'CA': '06037'}


def _null_norm(rec):
    for f, dist in rec["stoch"].items():
        rec["stoch"][f] = {("null" if k is None else k): v for k, v in dist.items()}
    return rec


def main():
    golden = {}
    for div, st in DIV_STATE.items():
        for yr in (1965, 1990, 2015):
            for geo in ("nocounty", "county"):
                if geo == "county" and st not in COUNTY:
                    continue
                key = f"res|{div}|{yr}|{geo}"
                md = {"state": st, "year_built": yr, "area": 2000}
                if geo == "county":
                    md["county_fips"] = COUNTY[st]
                golden[key] = _null_norm(t._golden_case(md, "Single-Family Detached"))
    COM_CZ = {"IL": "5A", "AZ": "2B"}   # exercise the commercial climate-zone envelope path
    for bt in ("Office", "Retail", "Warehouse", "Education", "Lodging"):
        for st, yr in (("IL", 1990), ("AZ", 2010)):
            key = f"com|{bt}|{st}|{yr}"
            md = {"state": st, "year_built": yr, "area": 50000,
                  "county_fips": COUNTY.get(st), "floor_count": 3, "climate_zone": COM_CZ[st]}
            golden[key] = _null_norm(t._golden_case(md, bt))

    out = os.path.join(TESTS, "fixtures", "resolver_golden.json")
    with open(out, "w") as f:
        json.dump(golden, f, indent=2, sort_keys=True)
    print(f"wrote {out} ({len(golden)} cases)")


if __name__ == "__main__":
    main()
