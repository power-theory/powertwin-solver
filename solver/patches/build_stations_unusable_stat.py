"""Regenerate solver/app/urbanopt/stations_unusable_stat.json -- the committed builder for that
reference artifact (see the reference-data provenance rule: every shipped JSON needs a builder).

Downloads every station's .stat from master_weather.geojson and classifies it: the modern ASHRAE
design-conditions table (header contains 'DB996') parses in openstudio-standards; the legacy
abbreviated table ('HDB 99.6%') makes create_typical_building_from_model abort ('Expected to find
15 Heating Design Temperatures but found 3'), failing every building assigned to that station.
The solver excludes listed stations from nearest-station selection (weather.py) and the sampler
mirrors it (sampling/station.py). Validate ALL stations, not just USA_*: the nearest-station search
is global, so border/California buildings can match CAN/MEX/Title-24 entries.

Snapshot semantics: the S3 files can change upstream. Rerun this (network required, ~2 min) and
diff the JSON whenever master_weather.geojson changes or a campaign shows the
'Heating Design Temperatures' failure class again.

  python3 solver/patches/build_stations_unusable_stat.py   # writes the JSON in place
"""

import concurrent.futures as cf
import json
import os
import re
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
GEOJSON = os.path.join(HERE, "..", "app", "urbanopt", "master_weather.geojson")
OUT = os.path.join(HERE, "..", "app", "urbanopt", "stations_unusable_stat.json")
RETRIES = 3


def _url(raw):
    m = re.search(r"href=([^ >]+)", raw or "")  # the 'stat' property is an HTML anchor, not a URL
    return m.group(1).strip("'\"") if m else None


def check(item):
    """(title, 'good'|'bad'|'error'). Transient download errors are retried and, if persistent,
    reported as 'error' -- NEVER classified unusable: a builder run during a network blip must
    abort loudly, not silently bake good stations into the blocklist (every affected building
    would be rerouted to a farther station with no failure signal)."""
    title, url = item
    last = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=45) as r:
                return title, ("good" if "DB996" in r.read(200_000).decode("utf-8", "ignore") else "bad")
        except Exception as e:
            last = e
            time.sleep(2**attempt)
    print(f"  ERROR {title}: {last}", flush=True)
    return title, "error"


def main():
    feats = json.load(open(GEOJSON))["features"]
    all_stations = [(f["properties"]["title"], _url(f["properties"].get("stat"))) for f in feats]
    # a station with NO .stat URL cannot supply design conditions to create_typical either --
    # unusable by construction, not silently usable-by-omission
    no_url = sorted(t for t, u in all_stations if not u)
    stations = [(t, u) for t, u in all_stations if u]
    print(f"stations: {len(all_stations)} total, {len(stations)} with .stat URLs, {len(no_url)} without", flush=True)

    good, bad, errors = [], [], []
    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        for i, (title, res) in enumerate(ex.map(check, stations), 1):
            {"good": good, "bad": bad, "error": errors}[res].append(title)
            if i % 500 == 0:
                print(f"  {i}/{len(stations)} good={len(good)} bad={len(bad)} err={len(errors)}", flush=True)

    if errors:
        raise SystemExit(
            f"{len(errors)} stations unreachable after {RETRIES} attempts (first: {errors[0]}). "
            f"NOT writing {OUT} -- rerun when the network/S3 is healthy."
        )

    json.dump(
        {
            "unusable_stat": sorted(bad + no_url),
            "usable_count": len(good),
            # snapshot coupling: consumers (weather.py, sampling/station.py) verify this against
            # the geojson they load, so a geojson update without a rebuild fails loud
            "total_stations": len(all_stations),
            "n_no_stat_url": len(no_url),
        },
        open(OUT, "w"),
        indent=1,
    )
    print(f"GOOD={len(good)} BAD={len(bad)} NO_URL={len(no_url)} -> {OUT}")


if __name__ == "__main__":
    main()
