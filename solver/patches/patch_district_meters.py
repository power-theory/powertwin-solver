"""Patch urbanopt-reporting's default_feature_reports to request the RENAMED district meters.

EnergyPlus 23.2+/OpenStudio 3.9 renamed the district-heating meter: DistrictHeating:Facility ->
DistrictHeatingWater:Facility (+ a new DistrictHeatingSteam:Facility). The reporting measure still
requests only the legacy name, so a district-heated building's report carries an empty
DistrictHeating:Facility() column with zero values and the energy silently vanishes -- the Steam
sensor (sensor_types.csv) already lists the Water/Steam column names as capture alternatives, but
the report never contains them. Add meter requests + timeseries entries for the renamed meters
(legacy name kept for older-format weather runs; absent meters report empty, which the capture's
present-any semantics tolerates).

Second patch (2026-09-13), same measure: the CSV timeseries loop asked EnergyPlus for a series'
key values with the frequency hardcoded to 'Zone Timestep', so any series stored only at the
configured reporting frequency got an empty key list, fell through to key '' and was written as
an empty, unit-less column. The electricity-emissions EMS series (add_ems_emissions_reporting
requests them Hourly, key 'EMS') were the casualty: under Hourly reporting the SQL held 8760
real values but the CO2 sensor received only the gas/propane terms. Ask at the configured
frequency first, keeping 'Zone Timestep' as the fallback.
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


KEYS_ANCHOR = (
    "      if timeseries_name.include?('OtherFuels')\n"
    "        key_values = sql_file.availableKeyValues('RUN PERIOD 1', 'Zone Timestep', timeseries_name.upcase)\n"
    "      else\n"
    "        key_values = sql_file.availableKeyValues('RUN PERIOD 1', 'Zone Timestep', timeseries_name)\n"
    "      end\n"
)
KEYS_ADD = (
    "      # PowerTwin patch (patch_district_meters.py): ask at the configured reporting frequency,\n"
    "      # falling back to 'Zone Timestep'. Hardcoding 'Zone Timestep' emptied every series stored\n"
    "      # only at the configured frequency (the EMS electricity-emissions series under Hourly).\n"
    "      # The environment period is the detected annual run period, not the literal 'RUN PERIOD 1':\n"
    "      # URBANopt's merged multifamily models carry the unit prefix ('UNIT1_RUN_PERIOD_1'), so the\n"
    "      # literal found no keys, the EMS series fell back to key '' and every multifamily building\n"
    "      # reported zero electricity emissions.\n"
    "      key_freqs = [reporting_frequency == 'Timestep' ? 'Zone Timestep' : reporting_frequency.to_s, 'Zone Timestep'].uniq\n"
    "      lookup_name = timeseries_name.include?('OtherFuels') ? timeseries_name.upcase : timeseries_name\n"
    "      key_values = []\n"
    "      key_freqs.each do |kf|\n"
    "        key_values = sql_file.availableKeyValues(ann_env_pd.to_s, kf, lookup_name)\n"
    "        break unless key_values.empty?\n"
    "      end\n"
)
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
        if "DistrictHeatingWater:Facility" in src and "key_freqs" in src:
            print(f"already patched: {path}")
            _check_vendored_drift(path)
            continue
        if "DistrictHeatingWater:Facility" not in src:
            if METER_ANCHOR not in src or SERIES_ANCHOR not in src or ZERO_ANCHOR not in src:
                sys.exit(f"anchor not found in {path} -- measure changed, patch needs updating")
            src = src.replace(METER_ANCHOR, METER_ADD, 1)
            src = src.replace(SERIES_ANCHOR, SERIES_ADD, 1)
            src = src.replace(ZERO_ANCHOR, ZERO_ADD, 1)
        if "key_freqs" not in src:
            if KEYS_ANCHOR not in src:
                sys.exit(f"key-values anchor not found in {path} -- measure changed, patch needs updating")
            src = src.replace(KEYS_ANCHOR, KEYS_ADD, 1)
        open(path, "w").write(src)
        print(f"patched district meters + emissions key lookup: {path}")
        _check_vendored_drift(path)


if __name__ == "__main__":
    main()
