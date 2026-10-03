"""Grind staged trash without ever enumerating a huge directory.

The failure this replaces: a bash glob at depth 4 (`$T/*/*/*/*/`) has to readdir and SORT
every match into one array before a single byte is deleted, so one pathological directory
(colorado_powertwin/tmp/node_*, each too large for `ls` to finish in 25 s) stalls the whole
job while every deleter idles. Same shape as the vault's 5h36m `find` in D state.

The fix keeps the vault's principle (enumerate shallow, let `rm -rf` recurse) but takes the
enumeration away from bash: os.scandir streams entries without sorting or materializing a
list, we descend at most `MAX_DEPTH` levels, and each path found there is handed straight to
a parallel `rm -rf` that does its own getdents+unlinkat walk in C.

Usage: grind_trash.py <root> [max_depth] [workers]
"""
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = sys.argv[1]
MAX_DEPTH = int(sys.argv[2]) if len(sys.argv) > 2 else 2
WORKERS = int(sys.argv[3]) if len(sys.argv) > 3 else 48
BATCH = 2000  # dispatch as we go; never hold a giant list


def free_gb():
    st = os.statvfs(os.environ.get("GRIND_FS", "/project/cowy-ptheory"))
    return st.f_bavail * st.f_frsize / 1e9


def rm(path):
    subprocess.run(["rm", "-rf", path], check=False)
    return path


def victims(root, max_depth):
    """Yield directories at exactly max_depth (or files/dirs shallower when a branch
    bottoms out first). Streaming: never builds a full list, never sorts."""
    stack = [(root, 0)]
    while stack:
        path, depth = stack.pop()
        if depth >= max_depth:
            yield path
            continue
        try:
            with os.scandir(path) as it:
                empty = True
                for e in it:
                    empty = False
                    if e.is_dir(follow_symlinks=False):
                        # a child that would only be yielded on its next pop is yielded NOW,
                        # so a flat directory of millions of subdirs streams into the pool
                        # instead of piling its paths onto the stack (the 67 GB colorado OOM)
                        if depth + 1 >= max_depth:
                            yield e.path
                        else:
                            stack.append((e.path, depth + 1))
                    else:
                        yield e.path
                if empty:
                    yield path
        except (NotADirectoryError, PermissionError, FileNotFoundError):
            yield path


def main():
    t0 = time.time()
    start_free = free_gb()
    print(f"[{time.strftime('%F %T')}] grinding {ROOT} depth={MAX_DEPTH} workers={WORKERS} "
          f"free={start_free:.0f} GB", flush=True)
    n = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        pending = set()
        for v in victims(ROOT, MAX_DEPTH):
            pending.add(ex.submit(rm, v))
            n += 1
            if len(pending) >= BATCH:
                done, pending = _drain(pending, n, t0, start_free)
        while pending:
            _, pending = _drain(pending, n, t0, start_free, final=True)
    # whatever shell remains (now cheap: the deep content is gone)
    shutil.rmtree(ROOT, ignore_errors=True)
    print(f"[{time.strftime('%F %T')}] DONE {ROOT}: {n} branches, "
          f"freed {free_gb()-start_free:.0f} GB in {(time.time()-t0)/60:.1f} min, "
          f"free now {free_gb():.0f} GB", flush=True)


def _drain(pending, n, t0, start_free, final=False):
    done = set()
    for f in as_completed(list(pending), timeout=None):
        done.add(f)
        if not final and len(done) >= BATCH // 2:
            break
    remaining = pending - done
    print(f"  {n} dispatched, freed {free_gb()-start_free:.0f} GB "
          f"({(time.time()-t0)/60:.1f} min)", flush=True)
    return done, remaining


if __name__ == "__main__":
    main()
