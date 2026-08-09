"""Static pins for cross-file solver contracts whose breakage is SILENT at runtime.

Each test quotes the exact code both sides must carry. A refactor that drops one side fails here
immediately instead of producing wrong physics with no error. (The dynamic halves are covered by
tests/test_national_scale_sim.py in-container; these pins run everywhere.)"""

import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN = open(os.path.join(REPO, "solver", "app", "modules", "simulation", "generateFeatureFile.py")).read()
MAPPER = open(os.path.join(REPO, "solver", "upload", "PowerTwin.rb")).read()
VENDORED = open(os.path.join(REPO, "solver", "upload", "measures", "default_feature_reports", "measure.rb")).read()
PATCH = open(os.path.join(REPO, "solver", "patches", "patch_district_meters.py")).read()


def test_extended_fuel_contract_both_sides():
    # feature gen rehomes enum-invalid fuels (district steam) to *_extended so the file passes
    # schema validation; the mapper MUST read the extended key first, else those buildings silently
    # simulate with template-default fuel. Both sides pinned:
    assert '_fprops[_f + "_extended"] = _fprops.pop(_f)' in GEN, \
        "feature-gen side of the *_extended rehoming is gone"
    assert 'props[:"#{field}_extended"] || props[field]' in MAPPER, \
        "mapper fuel-lever no longer reads the extended key (silent default-fuel physics)"
    assert "props[:service_water_heating_fuel_type_extended] || props[:service_water_heating_fuel_type]" in MAPPER, \
        "mapper SWH path no longer reads the extended key"


def test_feature_id_scheme_guard_present():
    # the int32 modulo is only collision-free under the collection_id*10**10 + asset_id(<2e9)
    # scheme (anchored by the powertwin-db id contract); the mapper must verify reconstruction
    # and fail loud on a scheme change rather than silently colliding
    assert "% 2_000_000_000" in MAPPER
    assert "(raw_id / 10**10) * 10**10 + recovered != raw_id" in MAPPER, \
        "id-scheme reconstruction guard removed -- scheme drift would collide silently"


def test_district_meter_rename_all_three_sites():
    # the E+ 23.2 meter rename must be reflected in requests, the timeseries list, AND the
    # residential zero-out list -- in BOTH the vendored measure and the gem patch script
    for name, src in (("vendored measure", VENDORED), ("patch script", PATCH)):
        assert "DistrictHeatingWater:Facility" in src, f"{name}: renamed meter missing"
        assert "DistrictHeatingSteam:Facility" in src, f"{name}: steam meter missing"
    assert ("'DistrictCooling:Facility', 'DistrictHeating:Facility', "
            "'DistrictHeatingWater:Facility', 'DistrictHeatingSteam:Facility'") in VENDORED, \
        "vendored residential zero-out list lacks the renamed meters"
    assert "ZERO_ANCHOR" in PATCH and "ZERO_ADD" in PATCH, \
        "patch script lost the zero-out list edit"


def test_subregion_omission_still_in_place():
    # AK/HI have no entry in the CONUS-only future-emissions enum; unmapped subregions must be
    # OMITTED (absent keys validate), never emitted as null/junk
    assert 'del final_json["project"][_key]' in GEN
    assert "'AK': 'AKGDc'" not in GEN and "'HI': 'HIc'" not in GEN
