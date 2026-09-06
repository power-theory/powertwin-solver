"""Row-lookup accessor for the synthetic leaf dataset -- the ONE consolidated
fixture (one CSV of rows + one manifest of tag->row-ids), replacing piles of
one-building CSVs. Reference what you need BY ROW:

    from leaf_stock import load, select
    load()                          -> every asset (dict; asset_metadata parsed; .leaves tags)
    select(subtype="Lodging")       -> rows whose manifest tags include "subtype:Lodging"
    select(div="Pacific", wf="res") -> AND of leaf tags  ("div:Pacific" AND "wf:res")
    select("branch:vacant")         -> rows for a raw tag
    select(forced="wood+wood_stove")

Regenerates the dataset via gen_leaf_stock.py if it's absent. The manifest tag
namespaces are: div, vint, cz, subtype, branch, forced, geo, wf, sparse.
"""
import csv
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
_CSV = os.path.join(OUT, "leaf_stock_metadata.csv")
_MAN = os.path.join(OUT, "leaf_manifest.json")


def _ensure():
    if not (os.path.exists(_CSV) and os.path.exists(_MAN)):
        subprocess.run([sys.executable, os.path.join(HERE, "gen_leaf_stock.py")],
                       check=True, stdout=subprocess.DEVNULL)


def load():
    """One dict per BUILDING (the metadata CSV now carries multiple sensor rows per
    building, like real ASU data; this is the coverage/select view, so dedup by asset_id).
    Each row gets `metadata` (parsed asset_metadata) and `leaves` (its manifest tag list).
    The e2e fixture reads the raw CSV separately when it needs the full per-sensor rows."""
    _ensure()
    manifest = json.load(open(_MAN))["manifest"]
    seen, out = set(), []
    for r in csv.DictReader(open(_CSV)):
        aid = str(r["asset_id"])
        if aid in seen:
            continue
        seen.add(aid)
        r["metadata"] = json.loads(r["asset_metadata"])
        r["leaves"] = manifest.get(aid, [])
        out.append(r)
    return out


def select(*tags, **filters):
    """Rows matching ALL given leaf tags. Positional tags are raw ("branch:vacant");
    keyword filters are namespace=value (subtype="Lodging" -> tag "subtype:Lodging")."""
    want = list(tags) + [f"{k}:{v}" for k, v in filters.items()]
    return [r for r in load() if all(t in r["leaves"] for t in want)]
