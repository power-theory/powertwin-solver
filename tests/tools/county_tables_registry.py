#!/usr/bin/env python3
"""County reference tables: publish to / fetch from the GitLab generic package registry.

The sixteen county tables under solver/upload/reference_data/*.json.gz are gitignored
builder outputs (tests/tools/build_county_*.py, which need the local ResStock/ComStock
parquets). A fresh checkout, CI included, gets them from this project's package
registry instead, pinned by two tracked files next to the tables:

  county_tables.sha256   sha256sum-format lines, one per table (shell-verifiable with
                         `sha256sum -c` from any image, python or not)
  county_tables.version  the package version those checksums were published under

Usage (repo root):
  county_tables_registry.py fetch     download any table missing or mismatching, verify
  county_tables_registry.py verify    exit 1 unless every table is present and matches
  county_tables_registry.py publish   after re-running the builders: upload the local
                                      tables under a content-derived version and rewrite
                                      the two pin files (commit those with the change)

Auth, first match wins: CI_JOB_TOKEN (JOB-TOKEN header), GITLAB_TOKEN (PRIVATE-TOKEN),
the userinfo of the origin remote URL. Tokens are never printed. The API base is
CI_API_V4_URL/CI_PROJECT_ID in CI, else derived from the origin remote.
"""
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
REF_DIR = os.path.join(REPO, "solver", "upload", "reference_data")
SUM_FILE = os.path.join(REF_DIR, "county_tables.sha256")
VERSION_FILE = os.path.join(REF_DIR, "county_tables.version")
PACKAGE = "county-tables"


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_pins():
    pins = {}
    with open(SUM_FILE) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            digest, name = line.split(None, 1)
            pins[name.lstrip("*")] = digest
    with open(VERSION_FILE) as fh:
        version = fh.read().strip()
    return version, pins


def _origin():
    url = subprocess.run(["git", "-C", REPO, "remote", "get-url", "origin"],
                         capture_output=True, text=True, check=True).stdout.strip()
    return urllib.parse.urlsplit(url)


def _api():
    """(api_base, project_path_or_id, headers) with the token kept out of any output."""
    if os.environ.get("CI_API_V4_URL") and os.environ.get("CI_PROJECT_ID"):
        base, proj = os.environ["CI_API_V4_URL"].rstrip("/"), os.environ["CI_PROJECT_ID"]
    else:
        o = _origin()
        if not o.scheme.startswith("http"):
            sys.exit("origin remote is not an https GitLab URL; set CI_API_V4_URL and CI_PROJECT_ID")
        netloc = o.hostname + (f":{o.port}" if o.port else "")
        base = f"{o.scheme}://{netloc}/api/v4"
        proj = urllib.parse.quote(o.path.lstrip("/").removesuffix(".git"), safe="")
    if os.environ.get("CI_JOB_TOKEN"):
        headers = {"JOB-TOKEN": os.environ["CI_JOB_TOKEN"]}
    elif os.environ.get("GITLAB_TOKEN"):
        headers = {"PRIVATE-TOKEN": os.environ["GITLAB_TOKEN"]}
    else:
        pw = _origin().password
        if not pw:
            sys.exit("no GitLab credential: set CI_JOB_TOKEN, GITLAB_TOKEN, or use an origin URL with a token")
        headers = {"PRIVATE-TOKEN": pw}
    return base, proj, headers


def _package_url(base, proj, version, name):
    return f"{base}/projects/{proj}/packages/generic/{PACKAGE}/{version}/{name}"


def _ssl_context():
    """GitLab Runner exposes the server CA to jobs as CI_SERVER_TLS_CA_FILE when the
    instance certificate is not in the job image's trust store (it is not in
    python:*-slim); the git checkout trusts it through the same file."""
    import ssl
    cafile = os.environ.get("CI_SERVER_TLS_CA_FILE")
    return ssl.create_default_context(cafile=cafile if cafile and os.path.exists(cafile) else None)


def _request(url, headers, data=None, method="GET"):
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120, context=_ssl_context()) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {PACKAGE} {os.path.basename(url)} -> HTTP {e.code}")
    except urllib.error.URLError as e:
        hint = ("" if os.environ.get("CI_SERVER_TLS_CA_FILE") else
                " (CI_SERVER_TLS_CA_FILE is unset; the runner must expose the GitLab CA via tls-ca-file)")
        sys.exit(f"{method} {PACKAGE} {os.path.basename(url)} -> {e.reason}{hint}")


def verify(quiet=False):
    version, pins = read_pins()
    bad = []
    for name, digest in sorted(pins.items()):
        path = os.path.join(REF_DIR, name)
        if not os.path.exists(path):
            bad.append(f"{name}: missing")
        elif _sha256(path) != digest:
            bad.append(f"{name}: sha256 mismatch")
    if bad:
        print("county tables NOT verified (version " + version + "):\n  " + "\n  ".join(bad))
        return False
    if not quiet:
        print(f"county tables verified: {len(pins)} tables at version {version}")
    return True


def fetch():
    version, pins = read_pins()
    base, proj, headers = _api()
    got = 0
    for name, digest in sorted(pins.items()):
        path = os.path.join(REF_DIR, name)
        if os.path.exists(path) and _sha256(path) == digest:
            continue
        _, body = _request(_package_url(base, proj, version, name), headers)
        actual = hashlib.sha256(body).hexdigest()
        if actual != digest:
            sys.exit(f"{name}: downloaded sha256 {actual[:12]} != pinned {digest[:12]} "
                     f"(version {version}); the registry and the pin files disagree")
        with open(path, "wb") as fh:
            fh.write(body)
        got += 1
    print(f"county tables fetched: {got} downloaded, {len(pins) - got} already current, version {version}")
    return verify(quiet=True)


def publish():
    names = sorted(n for n in os.listdir(REF_DIR) if n.endswith(".json.gz"))
    if not names:
        sys.exit("no *.json.gz tables under reference_data; run the builders first")
    digests = {n: _sha256(os.path.join(REF_DIR, n)) for n in names}
    content = "".join(f"{digests[n]}  {n}\n" for n in names)
    version = "sha-" + hashlib.sha256(content.encode()).hexdigest()[:16]
    base, proj, headers = _api()
    for n in names:
        with open(os.path.join(REF_DIR, n), "rb") as fh:
            _request(_package_url(base, proj, version, n), headers, data=fh.read(), method="PUT")
        print(f"uploaded {n}")
    with open(SUM_FILE, "w") as fh:
        fh.write(content)
    with open(VERSION_FILE, "w") as fh:
        fh.write(version + "\n")
    print(f"published {len(names)} tables as {PACKAGE} {version}; commit "
          f"{os.path.relpath(SUM_FILE, REPO)} and {os.path.relpath(VERSION_FILE, REPO)}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "fetch":
        sys.exit(0 if fetch() else 1)
    elif cmd == "verify":
        sys.exit(0 if verify() else 1)
    elif cmd == "publish":
        publish()
    else:
        sys.exit(__doc__)
