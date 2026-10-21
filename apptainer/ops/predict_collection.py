#!/usr/bin/env python3
"""Fast-estimate predictions for a whole collection (a national state), in parallel chunks, inside the solver image
(owner 2026-10-20: one repeatable process instead of hand-run scripts). predict-collection.sh runs it under docker on
exodus or apptainer on the cluster.

    predict_collection.py <collection_dir> <cid> <out_dir> --collection-id ID [--mode logs|gate] [--resample M]
                          [--chunk 20000] [--workers 12 (0: in this process)] [--threads 2]

- split: <collection_dir>/<cid>_metadata.csv rows in order, `--chunk` buildings each, every footprint of
  <cid>_asset_geometries.geojson routed to its building's chunk by geometry id (streamed). Written once into
  <out_dir>/chunks/; a rerun reuses them.
- predict, `--workers` processes of `--threads` threads, each chunk on its own (resumable: a chunk with a .done marker
  is skipped):
  - logs: surrogate_serving.run_logs, the hosted fast estimate's own rows at the datelevel (`--resample`, default M),
    stamped `--collection-id`;
  - gate: the presence gate only (no energy model), counting the streams a building would get (a sensor for the type
    and the gate saying present; the full run also drops a stream whose energy rounds to zero, so this is the upper
    set). For sensor-type lists before a full run.
- merge: one CSV <cid>_<model>_<datelevel>.csv (logs) and one run record .json: model and serving_meta, resolver env,
  translations, chunk input and output md5s, output md5, rows, buildings, skipped (ids and reasons),
  streams_by_sensor_type. The merge refuses a missing chunk.

The run's mode is the model's training mode (server.env()), as the asset_update worker runs it; timestamps follow
URBANOPT_POSTPROCESS_TRANSLATIONS, recorded in the run record.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import multiprocessing
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

csv.field_size_limit(sys.maxsize)
SOLVER_APP = os.environ.get("SOLVER_APP", "/solver/app")


def md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def split(collection_dir: str, cid: str, chunk_dir: str, n: int) -> list[str]:
    """Chunk names c0000.. in metadata order; idempotent (an existing complete split is reused)."""
    import ijson
    done = os.path.join(chunk_dir, "SPLIT_DONE")
    if os.path.exists(done):
        return json.load(open(done))["chunks"]
    os.makedirs(chunk_dir, exist_ok=True)
    chunk_of, files, writers = {}, {}, {}
    with open(os.path.join(collection_dir, f"{cid}_metadata.csv"), newline="") as f:
        r = csv.DictReader(f)
        for i, row in enumerate(r):
            k = f"c{i // n:04d}"
            chunk_of[str(json.loads(row["asset_geometries_properties"] or "{}").get("id"))] = k
            if k not in files:
                files[k] = open(os.path.join(chunk_dir, f"{k}_metadata.csv"), "w", newline="")
                writers[k] = csv.DictWriter(files[k], fieldnames=r.fieldnames)
                writers[k].writeheader()
            writers[k].writerow(row)
    for fh in files.values():
        fh.close()
    geo = {k: open(os.path.join(chunk_dir, f"{k}_asset_geometries.geojson"), "w") for k in files}
    first = dict.fromkeys(files, True)
    for g in geo.values():
        g.write('{"type":"FeatureCollection","features":[')
    footprints = 0
    with open(os.path.join(collection_dir, f"{cid}_asset_geometries.geojson"), "rb") as f:
        for feat in ijson.items(f, "features.item", use_float=True):
            k = chunk_of.get(str((feat.get("properties") or {}).get("id")))
            if k is None:
                continue
            geo[k].write(("" if first[k] else ",") + json.dumps(feat, separators=(",", ":")))
            first[k] = False
            footprints += 1
    for g in geo.values():
        g.write("]}")
        g.close()
    chunks = sorted(files)
    json.dump({"chunks": chunks, "buildings": len(chunk_of), "footprints": footprints, "chunk_size": n}, open(done, "w"))
    return chunks


def _server(threads: int):
    if SOLVER_APP not in sys.path:
        sys.path.insert(0, SOLVER_APP)
    os.environ["SURROGATE_THREADS"] = str(threads)
    from modules.simulation import surrogate_serving as SS
    from modules.utils.sim_flags import set_sim_flags
    server = SS.get_server()
    env = server.env()
    os.environ.update(env)
    set_sim_flags(env["URBANOPT_DYNAMIC_DEFAULTS"], env["URBANOPT_STOCHASTIC_SAMPLING"], None, None, None)
    return SS, server, env


def run_chunk(chunk_dir: str, out_dir: str, name: str, mode: str, collection_id: str, resample: str | None,
              threads: int) -> str:
    """One chunk in this process; writes <name>.csv + .json (logs) or <name>.gate.json, then <name>.done."""
    if os.path.exists(os.path.join(out_dir, f"{name}.done")):
        return name
    if os.getcwd() != os.path.dirname(SOLVER_APP):
        os.chdir(os.path.dirname(SOLVER_APP))   # the solver's relative paths (weather, master geojson) are from its root
    SS, server, env = _server(threads)
    meta = os.path.join(chunk_dir, f"{name}_metadata.csv")
    geo = os.path.join(chunk_dir, f"{name}_asset_geometries.geojson")
    t0 = time.time()
    if mode == "logs":
        out = os.path.join(out_dir, f"{name}.csv")
        res = SS.run_logs(out, meta, geo, server, collection_id, resample)
        rec = {"model": server.meta, "resolver_env": env, "resample": resample, "collection_id": collection_id,
               "translations": os.environ.get("URBANOPT_POSTPROCESS_TRANSLATIONS", "").strip().lower() in ("1", "true", "yes", "on"),
               "inputs": {os.path.basename(p): md5(p) for p in (meta, geo)}, "output_md5": md5(out),
               "seconds": round(time.time() - t0, 1), **res}
        json.dump(rec, open(out + ".json", "w"), indent=1)
    else:
        import numpy as np
        from surrogate.app.modules import schema
        entries, epws, sens, ids, skipped, _ = SS.build_entries(meta, geo)
        types = [str(s.sensor_type_id) for s in schema.TARGET_STREAMS]
        counts, wc = {}, {}
        for s in range(0, len(entries), 256):
            ent, ep = entries[s:s + 256], epws[s:s + 256]
            X = np.stack([server.features(e, SS.G_sps(), p, wc) for e, p in zip(ent, ep)])
            for i, row in enumerate(server.gate.predict(X, [e["building_type"] for e in ent])):
                for j, t in enumerate(types):
                    if row[j] and sens[s + i].get(t) not in (None, ""):
                        counts[t] = counts.get(t, 0) + 1
        json.dump({"buildings": len(ids), "skipped": skipped, "streams_by_sensor_type": counts, "model": server.meta,
                   "resolver_env": env, "inputs": {os.path.basename(p): md5(p) for p in (meta, geo)},
                   "seconds": round(time.time() - t0, 1)}, open(os.path.join(out_dir, f"{name}.gate.json"), "w"), indent=1)
    open(os.path.join(out_dir, f"{name}.done"), "w").write(time.strftime("%F %T\n"))
    return name


def merge(out_dir: str, chunks: list[str], mode: str, cid: str, dest: str) -> dict:
    missing = [c for c in chunks if not os.path.exists(os.path.join(out_dir, f"{c}.done"))]
    if missing:
        raise RuntimeError(f"missing chunks: {missing}")
    os.makedirs(dest, exist_ok=True)
    tot = {"buildings": 0, "streams_by_sensor_type": {}, "skipped_ids": {}}
    recs = []
    for c in chunks:
        rec = json.load(open(os.path.join(out_dir, f"{c}.csv.json" if mode == "logs" else f"{c}.gate.json")))
        recs.append((c, rec))
        tot["buildings"] += rec["buildings"]
        tot["skipped_ids"].update(rec["skipped"])
        for t, n in rec["streams_by_sensor_type"].items():
            tot["streams_by_sensor_type"][t] = tot["streams_by_sensor_type"].get(t, 0) + n
    tot["streams_by_sensor_type"] = dict(sorted(tot["streams_by_sensor_type"].items(), key=lambda kv: int(kv[0])))
    reasons = {}
    for v in tot["skipped_ids"].values():
        reasons[v[:80]] = reasons.get(v[:80], 0) + 1
    first = recs[0][1]
    model = first["model"].get("model_name", "model")
    run = {"collection_source_cid": cid, "mode": mode, "model": first["model"], "resolver_env": first["resolver_env"],
           "chunks": len(recs), "chunk_inputs": {c: r["inputs"] for c, r in recs}, "skipped": len(tot["skipped_ids"]),
           "skip_reasons": reasons, **tot}
    if mode == "logs":
        level = first.get("datelevel", "month")
        out_csv = os.path.join(dest, f"{cid}_{model}_{level}.csv")
        with open(out_csv, "w", newline="") as fo:
            for i, (c, _r) in enumerate(recs):
                with open(os.path.join(out_dir, f"{c}.csv"), newline="") as fi:
                    head = fi.readline()
                    if i == 0:
                        fo.write(head)
                    for line in fi:
                        fo.write(line)
        run.update({"run_id": f"{cid}_{model}_{level}_{time.strftime('%Y%m%d')}", "collection_id_stamped": first["collection_id"],
                    "resample": first["resample"], "datelevel": level, "translations": first["translations"],
                    "chunk_outputs": {c: r["output_md5"] for c, r in recs}, "rows": sum(r["rows"] for _, r in recs),
                    "output": os.path.basename(out_csv), "output_md5": md5(out_csv)})
        path = out_csv[:-4] + ".json"
    else:
        run["note"] = "gate only: a stream counts when the building has its sensor and the presence gate says present"
        path = os.path.join(dest, f"{cid}_{model}_gate.json")
    json.dump(run, open(path, "w"), indent=1)
    return run


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("collection_dir"); ap.add_argument("cid"); ap.add_argument("out_dir")
    ap.add_argument("--collection-id", required=True)
    ap.add_argument("--mode", choices=("logs", "gate"), default="logs")
    ap.add_argument("--resample", default="M")
    ap.add_argument("--chunk", type=int, default=20000)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--threads", type=int, default=2)
    a = ap.parse_args(argv)
    chunk_dir, work = os.path.join(a.out_dir, "chunks"), os.path.join(a.out_dir, f"work_{a.mode}")
    os.makedirs(work, exist_ok=True)
    chunks = split(a.collection_dir, a.cid, chunk_dir, a.chunk)
    print(f"[{time.strftime('%T')}] {a.cid}: {len(chunks)} chunks, mode {a.mode}, {a.workers} workers", flush=True)
    if a.workers == 0:   # in this process, one chunk after another (tests, debugging)
        for c in chunks:
            print(f"[{time.strftime('%T')}] {run_chunk(chunk_dir, work, c, a.mode, a.collection_id, a.resample or None, a.threads)} done", flush=True)
    else:
        # fork, before any model is loaded here (a fork after xgboost or numba threads start can deadlock): workers
        # inherit the modules and settings; the image's Python forks by default, 3.14 does not
        ctx = multiprocessing.get_context("fork")
        with ProcessPoolExecutor(max_workers=a.workers, mp_context=ctx) as ex:
            futs = [ex.submit(run_chunk, chunk_dir, work, c, a.mode, a.collection_id, a.resample or None, a.threads)
                    for c in chunks]
            for f in as_completed(futs):
                print(f"[{time.strftime('%T')}] {f.result()} done", flush=True)
    run = merge(work, chunks, a.mode, a.cid, a.out_dir)
    print(json.dumps({k: run[k] for k in ("buildings", "skipped", "streams_by_sensor_type")}), flush=True)


if __name__ == "__main__":
    main()
