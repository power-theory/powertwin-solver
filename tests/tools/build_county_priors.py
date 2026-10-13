"""CR campaign WP-00: shared builder core for county-resolved prior tables.

Every county-prior reference table ships from this core so the cited-source rule is
cheap to honor: tables are always builder-emitted with a `_source` block, never
hand-typed. Field packages (ducts, heating joint, setpoints, ...) are thin wrappers
that pass a cell function; the core owns the geography.

Emitted schema (consumed by sim_params_spec._county_prior) - deduplicated: shared
tiers are stored once and referenced, so a county table never inlines its division:
{
  "_source": {...}, "_floors": {"county": 30, "puma": 30, "state": 1, "division": 1},
  "counties": {"<fips>": {"county": {cell}, "puma_ref": "<puma>" | "puma_union_ref": "<p1|p2>",
                           "state_ref": "<state fips>", "division_ref": "<division>"}},
  "_pumas": {"<puma>": {cell}}, "_puma_unions": {"<p1|p2>": {cell}},
  "_states": {"<state fips>": {cell}}, "_divisions": {"<division>": {cell}},
  "_pooling": {"county" | "puma" | "state": {"rho", "k", "cells"}, "method": ...}
}

Cell conventions (matching build_residential_envelope.py lineage):
- share dicts are rounded to 3, shares < NOISE_FLOOR dropped, the rounding residual
  folded into the modal key so shares sum to exactly 1.0;
- `donor_cell` emits a weighted donor list for COUPLED field families (the Q1 finding:
  in.hvac_has_ducts is whole-home, so ducts must be drawn jointly with equipment) -
  drawing one donor index by hash reproduces the full joint distribution for free.
- every leaf ({mode, shares} or {donors}) carries `_n`, the Kish effective sample size of its rows.

Pooling (County Prior Pooling Plan, 2026-10-11): an area's cell is blended toward its parent area's,
shares (or donor weights) = (n x cell + k x parent) / (n + k), top-down (state toward division, PUMA toward
state, county toward its PUMA or, without one, its state). k = (1 - rho) / rho per table and tier, rho the
between-area share of variance estimated from the cells themselves (method of moments under a
Dirichlet-multinomial, nested-sampling terms included). rho <= 0 (no signal beyond sampling noise) drops the
tier: the resolver then walks to the parent. Audit behind it: Validation Report 2026-09, 2026-10-11 entries.
"""
import gzip
import json
from collections import defaultdict

NOISE_FLOOR = 0.005
RES_FLOORS = {"county": 30, "puma": 30, "state": 1, "division": 1}
COM_FLOORS = {"county": 10, "puma": 10, "state": 1, "division": 1}
DONOR_CAP = 200  # max donors kept per cell; weighted sampling stays faithful


def gisjoin_to_fips(g):
    """'G5600390' -> '56039' (NHGIS GISJOIN: G SS 0 CCC 0). Used by both stocks."""
    g = str(g)
    if not g.startswith("G") or len(g) < 8:
        raise ValueError(f"not a county GISJOIN: {g!r}")
    return g[1:3] + g[4:7]


def _kish_n(w):
    """Kish effective sample size of a weight column: (sum w)^2 / sum w^2."""
    w = w.astype(float)
    total = float(w.sum())
    return round(total * total / float((w * w).sum()), 1) if total > 0 else 0.0


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
    return {"mode": mode, "shares": shares, "_n": _kish_n(s[weight_col])}


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
    return {"donors": donors, "_n": _kish_n(s[weight_col])}


NULL_RUNS = 3      # shuffled-label builds per tier for the estimator's noise baseline (fixed seeds: reproducible)


def build_three_tier(df, cell_fn, county_col, puma_col, division_col,
                     floors, weight_col="weight", include_puma=True, pool=True):
    """The three-tier table (plus the state tier), pooled unless pool=False; see _raw_tiers and pool_tiers."""
    tiers = _raw_tiers(df, cell_fn, county_col, puma_col, division_col, include_puma)
    if not pool:
        return tiers
    import numpy as np
    rhos = estimate_tiers(tiers, floors)
    nulls = {}
    for tier in ("state", "puma", "county"):
        if not rhos[tier]:
            nulls[tier] = {}
            continue
        runs = []
        for seed in range(NULL_RUNS):
            sdf = _shuffle_for(df, tier, county_col, puma_col, division_col, include_puma, np.random.default_rng(seed))
            runs.append(estimate_tiers(_raw_tiers(sdf, cell_fn, county_col, puma_col, division_col, include_puma),
                                       floors)[tier])
        nulls[tier] = {part: sum(r.get(part, (0.0, 0))[0] for r in runs) / len(runs) for part in rhos[tier]}
    pool_tiers(tiers, floors, rhos, nulls)
    return tiers


def _shuffle_for(df, tier, county_col, puma_col, division_col, include_puma, rng):
    """Labels of `tier` permuted among rows of the same parent area, so the tier carries no real signal: county
    within its PUMA (residential) or state (commercial), PUMA within state, state (via county) within division."""
    df = df.copy()
    state = df[county_col].map(lambda g: _fips(g)[:2]).values
    if tier == "county":
        grp, col = (df[puma_col].values if include_puma else state), county_col
    elif tier == "puma":
        grp, col = state, puma_col
    else:
        grp, col = df[division_col].values, county_col
    df[col] = df.groupby(grp)[col].transform(lambda x: rng.permutation(x.values)).values
    return df


def _raw_tiers(df, cell_fn, county_col, puma_col, division_col, include_puma=True):
    """Assemble the three-tier table WITHOUT duplication: PUMA and division cells are
    stored ONCE in shared blocks; each county entry carries its own cell plus
    REFERENCES (its puma list and division name). A county's puma tier is the union
    of rows across its pumas - for single-puma counties (the vast majority) that is
    exactly the shared puma cell; multi-puma counties get a dedicated union cell in
    `_puma_unions` keyed by the sorted puma tuple, still stored once per distinct
    combination. (First heating table shipped 239 MB raw because every county carried
    a full copy of its division's donors - this schema is the fix.)"""
    import pandas as pd
    counties, pumas, divisions, unions, states = {}, {}, {}, {}, {}
    df = df.assign(_state=df[county_col].map(lambda g: _fips(g)[:2]))
    puma_rows = {p: sub for p, sub in df.groupby(puma_col)} if include_puma else {}
    div_rows = {d: sub for d, sub in df.groupby(division_col)}
    for st, sub in df.groupby("_state"):
        cell = cell_fn(sub)
        if cell is not None:
            cell["_n"] = int(len(sub))
            states[str(st)] = cell
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
        fips = _fips(fips_raw)
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
        entry["state_ref"] = fips[:2]
        entry["division_ref"] = str(csub[division_col].iloc[0])
        if entry:
            counties[fips] = entry
    return {"counties": counties, "_pumas": pumas, "_states": states, "_divisions": divisions,
            "_puma_unions": unions}


def _fips(g):
    return gisjoin_to_fips(g) if str(g).startswith("G") else str(g)


# --- pooling ------------------------------------------------------------------------------------------------------

def _leaves(cell, path=()):
    """(path, leaf) for every {mode, shares} / {donors} leaf under a cell (sub-keys: building type, vintage, part);
    a cell that is itself a leaf yields ((), cell)."""
    if not path and ("shares" in cell or "donors" in cell):
        yield (), cell
        return
    for k, v in cell.items():
        if k.startswith("_") or not isinstance(v, dict):
            continue
        if "shares" in v or "donors" in v:
            yield path + (k,), v
        else:
            yield from _leaves(v, path + (k,))


def _at(cell, path):
    for k in path:
        cell = cell.get(k) if isinstance(cell, dict) else None
        if cell is None:
            return None
    return cell


def _part(path, leaf):
    """The quantity a leaf describes, so each gets its own k: the path below the first sub-key (building type,
    vintage bin, gas arm, ...) plus the leaf kind; e.g. tstat's ('air',) shares vs ('tstat',) donors, pv's has_pv
    shares vs sys donors, ducts' location vs leakage, insulation's wall vs ceiling."""
    return "/".join(path[1:]) + ("|shares" if "shares" in leaf else "|donors")


def _donor_key(d):
    return tuple(sorted((k, v) for k, v in d.items() if k != "w"))


def _dist(leaf):
    if "shares" in leaf:
        return dict(leaf["shares"])
    return {_donor_key(d): d["w"] for d in leaf["donors"]}


def estimate_rho(groups):
    """Between-area share of variance rho per part (see _part) from built cells, method of moments.

    `groups` is [(parent_cell, [child_cell, ...])]. For a child c of a parent pooled from children j (plus any rows
    of the parent in no child, `rest`), with f_j = n_j / N (Kish n per leaf) and per-child variance
    V_j = A (rho (1 - 1/n_j) + 1/n_j), A = 1 - sum(pi^2):
        E sum_x (p_cx - pi_x)^2 = (1 - f_c)^2 V_c + sum_{j != c} f_j^2 V_j,
    linear in rho; summed over every child leaf of a part and solved. A child equal to its parent (f = 1, no
    siblings) contributes nothing, which is why the nested terms matter (residential counties inside one PUMA).
    Returns {part: (rho, number of child leaves used)}."""
    acc = {}
    for pcell, kids in groups:
        paths = {p for kid in kids for p, _ in _leaves(kid)}
        for path in paths:
            pl = _at(pcell, path)
            if not pl or not pl.get("_n"):
                continue
            pi = _dist(pl)
            A = 1.0 - sum(v * v for v in pi.values())
            N = float(pl["_n"])
            if A <= 0 or N <= 1:
                continue
            kl = [x for x in (_at(kid, path) for kid in kids) if x and x.get("_n", 0) >= 1]
            ns = [float(x["_n"]) for x in kl]
            fs = [min(1.0, n / N) for n in ns]
            rest = N - sum(ns)
            terms = [(f, n) for f, n in zip(fs, ns)] + ([(rest / N, rest)] if rest > 1 else [])
            sib_sig = sum(f * f * (1 - 1 / n) for f, n in terms)
            sib_noise = sum(f * f / n for f, n in terms)
            a = acc.setdefault(_part(path, pl), [0.0, 0.0, 0.0, 0])
            for leaf, n, f in zip(kl, ns, fs):
                if n < 2:
                    continue
                p = _dist(leaf)
                a[0] += sum((p.get(x, 0.0) - pi.get(x, 0.0)) ** 2 for x in set(p) | set(pi))
                a[1] += A * ((1 - f) ** 2 * (1 - 1 / n) + sib_sig - f * f * (1 - 1 / n))
                a[2] += A * ((1 - f) ** 2 / n + sib_noise - f * f / n)
                a[3] += 1
    return {part: (((S - C) / B if B > 0 else 0.0), m) for part, (S, B, C, m) in acc.items()}


def _finish_shares(sh):
    shares = {k: round(v, 3) for k, v in sorted(sh.items())}
    shares = {k: v for k, v in shares.items() if v >= NOISE_FLOOR}
    if not shares:
        return None
    mode = max(shares, key=shares.get)
    shares[mode] = round(shares[mode] + (1.0 - sum(shares.values())), 3)
    return {"mode": mode, "shares": shares}


def _blend_leaf(child, parent, k):
    n = float(child.get("_n", 0))
    w = n / (n + k)
    if "shares" in child and "shares" in parent:
        keys = set(child["shares"]) | set(parent["shares"])
        out = _finish_shares({x: w * child["shares"].get(x, 0.0) + (1 - w) * parent["shares"].get(x, 0.0)
                              for x in keys})
    elif "donors" in child and "donors" in parent:
        merged = {}
        for src, wt in ((child["donors"], w), (parent["donors"], 1 - w)):
            for d in src:
                key = _donor_key(d)
                merged.setdefault(key, [dict(d), 0.0])[1] += wt * d["w"]
        top = sorted(merged.values(), key=lambda t: (-t[1], str(_donor_key(t[0]))))[:DONOR_CAP]
        total = sum(t[1] for t in top)
        out = {"donors": [{**{k2: v2 for k2, v2 in d.items() if k2 != "w"}, "w": round(x / total, 6)}
                          for d, x in top]}
    else:
        return child
    if out is None:
        return child
    out["_n"] = child.get("_n", 0)
    return out


def _blend_cell(child, ancestors, ks):
    """The child cell with every leaf blended toward the nearest ancestor cell that has it, with its part's k
    (ks: part -> k; None drops the leaf, so the resolver walks up to the ancestor). A part with no estimate keeps
    the leaf as built. Returns None when no leaf is left."""
    import copy
    whole = "shares" in child or "donors" in child
    out = copy.deepcopy(child)
    for path, leaf in list(_leaves(child)):
        part = _part(path, leaf)
        if part in ks and ks[part] is None:
            if whole:
                return None
            del (_at(out, path[:-1]) if len(path) > 1 else out)[path[-1]]
            continue
        pl = next((x for x in (_at(a, path) if path else a for a in ancestors)
                   if x is not None and ("shares" in x or "donors" in x)), None)
        if pl is not None and ks.get(part) is not None:
            new = _blend_leaf(leaf, pl, ks[part])
            if whole:
                return {**new, **{k2: v2 for k2, v2 in child.items() if k2.startswith("_")}}
            (_at(out, path[:-1]) if len(path) > 1 else out)[path[-1]] = new
    if whole:
        return out
    _prune(out)
    return out if any(True for _ in _leaves(out)) else None


def _prune(cell):
    for k in [k for k, v in cell.items() if isinstance(v, dict) and not k.startswith("_")
              and "shares" not in v and "donors" not in v]:
        _prune(cell[k])
        if not any(True for _ in _leaves(cell[k])):
            del cell[k]


def _k(rho):
    return None if rho <= 0 else (1.0 - rho) / rho


def _relations(tiers, floors):
    counties, pumas, unions, states = tiers["counties"], tiers["_pumas"], tiers["_puma_unions"], tiers["_states"]
    ok = lambda cell, tier: cell is not None and cell.get("_n", 0) >= floors.get(tier, 1)
    st_div, puma_st = {}, {}
    for e in counties.values():
        st_div[e["state_ref"]] = e["division_ref"]
        for key in ("puma_union_ref", "puma_ref"):
            if key in e:
                puma_st[e[key]] = e["state_ref"]

    def parent_of(e):
        if "puma_union_ref" in e and ok(unions.get(e["puma_union_ref"]), "puma"):
            return ("u", e["puma_union_ref"])
        if "puma_ref" in e and ok(pumas.get(e["puma_ref"]), "puma"):
            return ("p", e["puma_ref"])
        return ("s", e["state_ref"])
    return ok, st_div, puma_st, parent_of, {"u": unions, "p": pumas, "s": states}


def estimate_tiers(tiers, floors):
    """{tier: {part: (rho, cells)}} for state (within division), PUMA (within state) and county (within its PUMA, or its
    state when it has none), on the cells the resolver would use (floors applied)."""
    counties, pumas, states, divisions = tiers["counties"], tiers["_pumas"], tiers["_states"], tiers["_divisions"]
    ok, st_div, puma_st, parent_of, lookup = _relations(tiers, floors)
    g = {}
    for st, cell in states.items():
        if ok(cell, "state") and st_div.get(st) in divisions:
            g.setdefault(st_div[st], []).append(cell)
    out = {"state": estimate_rho([(divisions[d], kids) for d, kids in g.items()])}
    g = {}
    for pid, cell in pumas.items():
        if ok(cell, "puma") and puma_st.get(pid) in states:
            g.setdefault(puma_st[pid], []).append(cell)
    out["puma"] = estimate_rho([(states[st], kids) for st, kids in g.items()]) if g else {}
    g = {}
    for e in counties.values():
        if ok(e.get("county"), "county"):
            g.setdefault(parent_of(e), []).append(e["county"])
    out["county"] = estimate_rho([(lookup[t][i], kids) for (t, i), kids in g.items() if lookup[t].get(i)])
    return out


def pool_tiers(tiers, floors, rhos, nulls):
    """Blend every tier toward its parent, top-down, per part with k from rho net of its shuffled-label baseline
    (rho - null; <= 0 drops that part's cells at that tier, so the resolver walks up). A leaf blends toward the
    nearest ancestor that has it (PUMA or union, state, division). Records everything in tiers['_pooling']."""
    counties, pumas, unions = tiers["counties"], tiers["_pumas"], tiers["_puma_unions"]
    states, divisions = tiers["_states"], tiers["_divisions"]
    ok, st_div, puma_st, parent_of, lookup = _relations(tiers, floors)
    ks = {t: {part: _k(r - nulls[t].get(part, 0.0)) for part, (r, m) in rhos[t].items()}
          for t in ("state", "puma", "county")}
    tiers["_pooling"] = {
        "method": "per tier and part: cell = (n cell + k parent) / (n + k), top-down toward the nearest ancestor "
                  "with the leaf; k = (1 - r) / r with r = rho - null, rho the between-area share of variance "
                  "estimated from the cells (Dirichlet-multinomial method of moments, nested terms), null its mean "
                  f"over {NULL_RUNS} shuffled-label builds; r <= 0 drops that part at that tier",
        **{t: {part: {"rho": round(r, 5), "null": round(nulls[t].get(part, 0.0), 5),
                      "k": None if ks[t][part] is None else round(ks[t][part], 1), "cells": m}
               for part, (r, m) in sorted(rhos[t].items())}
           for t in ("state", "puma", "county")},
    }

    def blend(cell, ancestors, kmap):
        anc = [a for a in ancestors if a is not None]
        return _blend_cell(cell, anc, kmap)

    for st in list(states):
        out = blend(states[st], [divisions.get(st_div.get(st, ""))], ks["state"])
        if out is None:
            del states[st]
        else:
            states[st] = out
    for block in (pumas, unions):
        for pid in list(block):
            st = puma_st.get(pid, "")
            out = blend(block[pid], [states.get(st), divisions.get(st_div.get(st, ""))], ks["puma"])
            if out is None:
                del block[pid]
            else:
                block[pid] = out
    for e in counties.values():
        for key, block in (("puma_union_ref", unions), ("puma_ref", pumas)):
            if key in e and e[key] not in block:
                del e[key]
        if "county" not in e:
            continue
        t, i = parent_of(e)
        anc = ([lookup[t].get(i)] if t != "s" else []) + [states.get(e["state_ref"]), divisions.get(e["division_ref"])]
        out = blend(e["county"], anc, ks["county"])
        if out is None:
            del e["county"]
        else:
            out["_n"] = e["county"]["_n"]
            e["county"] = out
    return tiers


def emit(path_no_ext, source_block, floors, tiers):
    doc = {"_source": source_block, "_floors": floors}
    doc.update(tiers)
    raw = json.dumps(doc).encode()
    gz = gzip.compress(raw, 9)
    with open(f"{path_no_ext}.json.gz", "wb") as f:
        f.write(gz)
    return len(raw), len(gz)
