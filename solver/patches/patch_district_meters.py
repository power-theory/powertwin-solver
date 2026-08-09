"""Patch urbanopt-reporting's default_feature_reports to request the RENAMED district meters.

EnergyPlus 23.2+/OpenStudio 3.9 renamed the district-heating meter: DistrictHeating:Facility ->
DistrictHeatingWater:Facility (+ a new DistrictHeatingSteam:Facility). The reporting measure still
requests only the legacy name, so a district-heated building's report carries an empty
DistrictHeating:Facility() column with zero values and the energy silently vanishes -- the Steam
sensor (sensor_types.csv) already lists the Water/Steam column names as capture alternatives, but
the report never contains them. Add meter requests + timeseries entries for the renamed meters
(legacy name kept for older-format weather runs; absent meters report empty, which the capture's
present-any semantics tolerates).
"""

import glob
import os
import sys

MEASURES = glob.glob(
    "/usr/local/lib/ruby/gems/*/gems/urbanopt-reporting-*/lib/measures/default_feature_reports/measure.rb"
)

METER_ANCHOR = (
    'result << OpenStudio::IdfObject.load("Output:Meter:MeterFileOnly,DistrictHeating:Facility,'
    '#{reporting_frequency};").get'
)
METER_ADD = (
    METER_ANCHOR
    + '\n    result << OpenStudio::IdfObject.load("Output:Meter:MeterFileOnly,DistrictHeatingWater:Facility,'
    '#{reporting_frequency};").get'
    + '\n    result << OpenStudio::IdfObject.load("Output:Meter:MeterFileOnly,DistrictHeatingSteam:Facility,'
    '#{reporting_frequency};").get'
)

SERIES_ANCHOR = "'DistrictHeating:Facility',"
SERIES_ADD = "'DistrictHeating:Facility',\n      'DistrictHeatingWater:Facility',\n      'DistrictHeatingSteam:Facility',"

# the residential zero-out list must ALSO know the renamed meters, or the measure's deliberate
# residential district suppression is bypassed for exactly the meters this patch adds
ZERO_ANCHOR = "(['DistrictCooling:Facility', 'DistrictHeating:Facility'].include?(timeseries_name)"
ZERO_ADD = ("(['DistrictCooling:Facility', 'DistrictHeating:Facility', "
            "'DistrictHeatingWater:Facility', 'DistrictHeatingSteam:Facility'].include?(timeseries_name)")


VENDORED = "/solver/upload/measures/default_feature_reports/measure.rb"


def _check_vendored_drift(patched_gem_path):
    """The vendored copy in upload/measures/ shadows the gem via measure_paths precedence, so a gem
    upgrade would be SILENTLY ignored at runtime. Fail the image build instead: the freshly patched
    gem measure must be byte-identical to the vendored copy; a mismatch means the gem version moved
    (re-vendor deliberately) or the two copies drifted."""
    if not os.path.exists(VENDORED):
        return  # patch may run before the repo COPY in other layouts; drift check is best-effort
    if open(patched_gem_path).read() != open(VENDORED).read():
        sys.exit(
            f"VENDORED MEASURE DRIFT: {VENDORED} != patched {patched_gem_path}.\n"
            "The vendored copy shadows the gem at runtime, so this mismatch means a gem upgrade "
            "or an unsynced edit would be silently ignored. Re-vendor: copy the freshly patched "
            "gem measure over the vendored one (keep VENDORED.md), or update the patch script."
        )
    print(f"vendored copy matches patched gem: {patched_gem_path}")


def main():
    if not MEASURES:
        sys.exit("default_feature_reports/measure.rb not found -- gem layout changed?")
    for path in MEASURES:
        src = open(path).read()
        if "DistrictHeatingWater:Facility" in src:
            print(f"already patched: {path}")
            _check_vendored_drift(path)
            continue
        if METER_ANCHOR not in src or SERIES_ANCHOR not in src or ZERO_ANCHOR not in src:
            sys.exit(f"anchor not found in {path} -- measure changed, patch needs updating")
        src = src.replace(METER_ANCHOR, METER_ADD, 1)
        src = src.replace(SERIES_ANCHOR, SERIES_ADD, 1)
        src = src.replace(ZERO_ANCHOR, ZERO_ADD, 1)
        open(path, "w").write(src)
        print(f"patched district meters: {path}")
        _check_vendored_drift(path)


if __name__ == "__main__":
    main()
