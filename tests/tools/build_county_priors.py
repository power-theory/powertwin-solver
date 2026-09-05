"""CR campaign WP-00: shared builder core for county-resolved prior tables.

Every county-prior reference table ships from this core so the cited-source rule is
cheap to honor: tables are always builder-emitted with a `_source` block, never
hand-typed. Field packages (ducts, heating joint, setpoints, ...) are thin wrappers
that pass a cell function; the core owns the geography.

Emitted schema (consumed by sim_params_spec._county_prior) - deduplicated: shared
tiers are stored once and referenced, so a county table never inlines its division:
{
  "_source": {...}, "_floors": {"county": 30, "puma": 30, "division": 1},
  "counties": {"<fips>": {"county": {cell}, "puma_ref": "<puma>" | "puma_union_ref": "<p1|p2>",
                           "division_ref": "<division>"}},
  "_pumas": {"<puma>": {cell}}, "_puma_unions": {"<p1|p2>": {cell}},
  "_divisions": {"<division>": {cell}}
}

Cell conventions (matching build_residential_envelope.py lineage):
- share dicts are rounded to 3, shares < NOISE_FLOOR dropped, the rounding residual
  folded into the modal key so shares sum to exactly 1.0;
- `donor_cell` emits a weighted donor list for COUPLED field families (the Q1 finding:
  in.hvac_has_ducts is whole-home, so ducts must be drawn jointly with equipment) -
  drawing one donor index by hash reproduces the full joint distribution for free.
"""
import gzip
import json
from collections import defaultdict

NOISE_FLOOR = 0.005
RES_FLOORS = {"county": 30, "puma": 30, "division": 1}
COM_FLOORS = {"county": 10, "puma": 10, "division": 1}
DONOR_CAP = 200  # max donors kept per cell; weighted sampling stays faithful


def gisjoin_to_fips(g):
    """'G5600390' -> '56039' (NHGIS GISJOIN: G SS 0 CCC 0). Used by both stocks."""
    g = str(g)
    if not g.startswith("G") or len(g) < 8:
        raise ValueError(f"not a county GISJOIN: {g!r}")
    return g[1:3] + g[4:7]


def share_cell(sub, col, weight_col="weight"):
    """Weighted value shares for one column: {mode, shares{...}} with the residual
    folded into the mode and sub-noise values dropped. None/NaN rows are excluded -
    when None is a VALUE (no-AC, no-attic), the wrapper must materialize it first."""
    s = sub[sub[col].notna()]
    if len(s) == 0:
        return None
    w = s.groupby(col)[weight_col].sum()
    total = float(w.sum())
    if total <= 0:
        return None
    shares = {str(k): round(float(v) / total, 3) for k, v in w.items()}
    shares = {k: v for k, v in shares.items() if v >= NOISE_FLOOR}
    if not shares:
        return None
    mode = max(shares, key=shares.get)
    shares[mode] = round(shares[mode] + (1.0 - sum(shares.values())), 3)
    return {"mode": mode, "shares": shares}


def donor_cell(sub, cols, weight_col="weight", cap=DONOR_CAP):
    """Weighted donor list preserving the JOINT distribution of `cols`.
    Identical rows collapse; the heaviest `cap` joint rows are kept and weights
    renormalized. Consumers draw one donor by building-id hash over cumulative
    weights, so every field in `cols` stays mutually consistent."""
    s = sub[[weight_col] + cols].copy()
    for c in cols:
        s[c] = s[c].astype(object).where(s[c].notna(), None)
    g = s.groupby(cols, dropna=False)[weight_col].sum().reset_index()
    g = g.sort_values(weight_col, ascending=False).head(cap)
    total = float(g[weight_col].sum())
    donors = []
    for _, r in g.iterrows():
        donors.append({"w": round(float(r[weight_col]) / total, 6),
                       **{c: (None if r[c] is None else str(r[c])) for c in cols}})
    return {"donors": donors}


def build_three_tier(df, cell_fn, county_col, puma_col, division_col,
                     floors, weight_col="weight", include_puma=True):
    """Assemble the three-tier table WITHOUT duplication: PUMA and division cells are
    stored ONCE in shared blocks; each county entry carries its own cell plus
    REFERENCES (its puma list and division name). A county's puma tier is the union
    of rows across its pumas - for single-puma counties (the vast majority) that is
    exactly the shared puma cell; multi-puma counties get a dedicated union cell in
    `_puma_unions` keyed by the sorted puma tuple, still stored once per distinct
    combination. (First heating table shipped 239 MB raw because every county carried
    a full copy of its division's donors - this schema is the fix.)"""
    import pandas as pd
    counties, pumas, divisions, unions = {}, {}, {}, {}
    puma_rows = {p: sub for p, sub in df.groupby(puma_col)} if include_puma else {}
    div_rows = {d: sub for d, sub in df.groupby(division_col)}
    for p, sub in puma_rows.items():
        cell = cell_fn(sub)
        if cell is not None:
            cell["_n"] = int(len(sub))
            pumas[str(p)] = cell
    for d, sub in div_rows.items():
        cell = cell_fn(sub)
        if cell is not None:
            cell["_n"] = int(len(sub))
            divisions[str(d)] = cell
    for fips_raw, csub in df.groupby(county_col):
        fips = gisjoin_to_fips(fips_raw) if str(fips_raw).startswith("G") else str(fips_raw)
        entry = {}
        cell = cell_fn(csub)
        if cell is not None:
            cell["_n"] = int(len(csub))
            entry["county"] = cell
        plist = sorted(str(p) for p in csub[puma_col].dropna().unique()) if include_puma else []
        if len(plist) == 1:
            entry["puma_ref"] = plist[0]
        elif len(plist) > 1:
            key = "|".join(plist)
            if key not in unions:
                usub = pd.concat([puma_rows[p] for p in plist if p in puma_rows])
                ucell = cell_fn(usub)
                if ucell is not None:
                    ucell["_n"] = int(len(usub))
                    unions[key] = ucell
            if key in unions:
                entry["puma_union_ref"] = key
        entry["division_ref"] = str(csub[division_col].iloc[0])
        if entry:
            counties[fips] = entry
    return {"counties": counties, "_pumas": pumas, "_divisions": divisions,
            "_puma_unions": unions}


def emit(path_no_ext, source_block, floors, tiers):
    doc = {"_source": source_block, "_floors": floors}
    doc.update(tiers)
    raw = json.dumps(doc).encode()
    gz = gzip.compress(raw, 9)
    with open(f"{path_no_ext}.json.gz", "wb") as f:
        f.write(gz)
    return len(raw), len(gz)
