"""Resolver 3.3 (vault Energy Code Assignment Plan, 2026-10-01): a commercial building's construction and HVAC
templates come from its state's energy-code adoption years (ComStock 2024.1) instead of URBANopt's year-only ladder,
which assumed every 90.1 edition governed everywhere from the year after publication."""
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOLVER = os.path.join(REPO, "solver")
sys.path.insert(0, os.path.join(SOLVER, "app"))
os.environ.setdefault("POWERTWIN_LOG_DIR", "/tmp")

from modules.simulation import sim_params_spec as S  # noqa: E402

TABLE = os.path.join(SOLVER, "upload", "reference_data", "comstock2024_state_code_adoption.json")
ASHRAE = ["DOE Ref Pre-1980", "DOE Ref 1980-2004", "90.1-2004", "90.1-2007", "90.1-2010", "90.1-2013"]
DEER = ["DEER Pre-1975", "DEER 1985", "DEER 1996", "DEER 2003", "DEER 2007", "DEER 2011", "DEER 2014", "DEER 2015",
        "DEER 2017", "DEER 2020"]     # chronological (the order check compared the DEER codes with themselves before)


@pytest.fixture(autouse=True)
def _dd_on(monkeypatch):
    monkeypatch.setenv("URBANOPT_DYNAMIC_DEFAULTS", "true")
    monkeypatch.setenv("URBANOPT_SIMULATION_YEAR", "2026")


def ctx(state, year, bt="Office", area=20000, floors=1):
    md = {"state": state, "year_built": year, "area": area, "floor_count": floors}
    return S.build_asset_ctx(md, building_type=bt)


def test_table_covers_every_state_with_one_advancing_family():
    states = json.load(open(TABLE))["states"]
    assert len(states) == 51                      # 50 states + DC
    for st, e in states.items():
        codes = [c for _, c in e["steps"]]
        order = ASHRAE if e["family"] == "ASHRAE" else DEER
        assert [order.index(c) for c in codes] == sorted(order.index(c) for c in codes), st
        assert e["steps"][0][0] == 0 and e["last_sampled_year"] == 2018, st
    assert states["CA"]["family"] == "DEER" and all(e["family"] == "ASHRAE" for s, e in states.items() if s != "CA")


@pytest.mark.parametrize("state,year,env,hvac", [
    ("WY", 1990, "DOE Ref 1980-2004", "DOE Ref 1980-2004"),   # no statewide code: 1980-2004 reference through 2018
    ("WY", 2015, "DOE Ref 1980-2004", "DOE Ref 1980-2004"),
    ("CO", 2012, "90.1-2007", "90.1-2007"),                   # CO 90.1-2007 from 2009, 90.1-2010 from 2014
    ("CO", 2016, "90.1-2010", "90.1-2010"),
    ("CO", 1985, "DOE Ref 1980-2004", "DOE Ref 1980-2004"),   # HVAC at max(2005, 2006) = 2006, still the reference
    ("TX", 2018, "90.1-2013", "90.1-2013"),
    ("NY", 1970, "DOE Ref Pre-1980", "DOE Ref 1980-2004"),    # HVAC at 2006
    ("NY", 1995, "DOE Ref 1980-2004", "90.1-2010"),           # HVAC at max(2015, 2006) = 2015: NY 90.1-2010 from 2015
    ("AZ", 2022, "90.1-2007", "90.1-2007"),                   # after 2018: the state's last code
])
def test_state_goldens(state, year, env, hvac):
    assert S.resolved_templates(ctx(state, year)) == (env, hvac)


@pytest.mark.parametrize("case", ["dd_off", "house", "no_year", "no_state", "california"])
def test_the_ladder_decides_where_the_table_does_not(monkeypatch, case):
    c = {"dd_off": ("WY", 1990, "Office"), "house": ("WY", 1990, "Single-Family Detached"),
         "no_year": ("WY", None, "Office"), "no_state": (None, 1990, "Office"), "california": ("CA", 2010, "Office")}[case]
    if case == "dd_off":
        monkeypatch.setenv("URBANOPT_DYNAMIC_DEFAULTS", "false")
    assert S.resolved_templates(ctx(c[0], c[1], c[2])) == (None, None)


def test_doe_ref_incompatible_prototypes_keep_90_1_2004():
    # a 3-story Lodging is SmallHotel, which cannot build on a DOE Ref template (PowerTwin.rb DOE_REF_INCOMPATIBLE)
    c = ctx("WY", 1990, "Lodging", area=30000, floors=3)
    assert S.resolved_templates(c)[0] == "DOE Ref 1980-2004"
    assert S._preset_template(1990, "SmallHotel", c) == "90.1-2004"


def test_presets_use_the_state_template():
    c = ctx("CO", 2012)
    assert S._preset_template(2012, "SmallOffice", c) == "90.1-2007"     # ladder alone said 90.1-2010
    assert S._preset_template(2012, "SmallOffice") == "90.1-2010"


def test_feature_file_and_mapper_carry_the_resolved_templates():
    gff = open(os.path.join(SOLVER, "app", "modules", "simulation", "generateFeatureFile.py")).read()
    assert 'new_properties["template"] = env_template' in gff and 'new_properties["hvac_template"] = hvac_template' in gff
    rb = open(os.path.join(SOLVER, "upload", "PowerTwin.rb")).read()
    seg = rb.split("# Resolver 3.3: generateFeatureFile resolved the construction and HVAC templates", 1)[1][:3000]
    assert "new_template = doe_ref_guard.call(feature.template)" in seg
    assert "doe_ref_guard.call(state_hvac_template), 'create_typical_building_from_model 2'" in seg


def test_surrogate_mirror_matches_the_resolver():
    """The surrogate labels every training record's templates with sampling/sim_levers; it must equal the sim."""
    sys.path.insert(0, os.path.join(REPO, "surrogate"))
    from app.modules.sampling import sim_levers as L
    for st in ("WY", "CO", "TX", "NY", "AZ", "FL", "MN", "DC"):
        for year in (1950, 1979, 1980, 1995, 2004, 2005, 2008, 2009, 2011, 2013, 2014, 2016, 2017, 2018, 2022):
            for bt, proto, area, floors in (("Office", "SmallOffice", 20000, 1), ("Lodging", "SmallHotel", 30000, 3)):
                env, hvac = S.resolved_templates(ctx(st, year, bt, area, floors))
                guard = S._doe_ref_guard
                assert L.code_era_template(year, proto, st) == guard(env, proto), (st, year, proto)
                assert L.hvac_template(year, proto, 2026, st) == guard(hvac, proto), (st, year, proto)


def test_california_uses_deer_only_with_its_cec_zone():
    c = ctx("CA", 2010)
    assert S.resolved_templates(c) == (None, None)                       # no CEC zone: the ladder
    assert S.resolved_templates(c, cec_zone="12") == ("DEER 2007", "DEER 2007")
    assert S.resolved_templates(ctx("CA", 1970), cec_zone="12") == ("DEER Pre-1975", "DEER 2003")   # HVAC at 2006


def test_cec_zone_lookup_matches_the_reference_cities():
    from modules.utils.weather import get_cec_climate_zone
    for lat, lon, want in ((37.7749, -122.4194, "3"), (38.5816, -121.4944, "12"), (34.0522, -118.2437, "9"),
                           (33.8303, -116.5453, "15"), (40.8021, -124.1637, "1"), (39.5296, -119.8138, None),
                           (43.48, -110.76, None)):
        assert get_cec_climate_zone(lat, lon) == want, (lat, lon)


def test_islands_outside_the_cec_layer_take_the_cec_zip_assignment():
    from modules.utils.weather import get_cec_climate_zone
    assert get_cec_climate_zone(33.3428, -118.3278) is None                 # Avalon: no polygon, no postcode
    z = get_cec_climate_zone(33.3428, -118.3278, "90704")
    assert z is not None and z.isdigit()                                    # the CEC ZIP list's zone for Avalon
    assert get_cec_climate_zone(39.5296, -119.8138, "89501") is None        # Reno: not a California ZIP



def test_surrogate_mirror_matches_deer_and_full_state_names():
    """California with a CEC zone builds on DEER, so the surrogate must label it DEER (review 2026-10-03: it labelled
    every CA record with the ASHRAE ladder); without coordinates both fall to the ladder. Full state names normalise."""
    sys.path.insert(0, os.path.join(REPO, "surrogate"))
    from app.modules.sampling import sim_levers as L
    from modules.utils.cec_zones import get_cec_climate_zone
    sac = (38.5816, -121.4944)
    for year in (1970, 1990, 2005, 2010, 2016, 2022):
        c = S.build_asset_ctx(dict(state="CA", year_built=year, area=20000, floor_count=1), building_type="Office")
        env, hvac = S.resolved_templates(c, cec_zone=get_cec_climate_zone(*sac))
        assert env.startswith("DEER")
        assert L.sim_levers("Office", 20000, 1, year, "CA", *sac)[1:] == (env, hvac), year
        assert not L.sim_levers("Office", 20000, 1, year, "CA")[1].startswith("DEER")   # no zone: the ladder, as the sim
    for full, abbr in (("Arizona", "AZ"), ("new york", "NY")):
        assert L.sim_levers("Office", 20000, 1, 2016, full) == L.sim_levers("Office", 20000, 1, 2016, abbr)


def test_no_ashrae_insulation_preset_for_a_building_built_on_deer():
    """The presets are 90.1 R-values for ASHRAE construction sets; on the DEER construction a CA building is built with
    they do not meet the code they are labelled with (2010 Sacramento office: 42% over U_max; review 2026-10-03)."""
    from modules.utils.cec_zones import get_cec_climate_zone
    c = S.build_asset_ctx(dict(state="CA", year_built=2010, area=20000, floor_count=1), building_type="Office",
                          climate_zone="3B")
    assert S.insulation_presets(c, "3B", get_cec_climate_zone(38.5816, -121.4944)) == {}
    assert S.insulation_presets(c, "3B")                      # no CEC zone: the ladder template, as the simulation


def test_deer_offices_get_the_nist_type_the_measure_infers_elsewhere():
    """Outside California the NIST measure classes an Office model by floor area (under 2,750 m2 SmallOffice); the DEER
    path named the prototype's type, so 25,000 to 29,600 ft2 California offices got MediumOffice (review 2026-10-03)."""
    rb = open(os.path.join(SOLVER, "upload", "PowerTwin.rb")).read()
    seg = rb.split("if deer_template\n                  ashrae_proto = lookup_building_type", 1)[1][:1500]
    assert "%w[SmallOffice MediumOffice].include?(ashrae_proto)" in seg and "model_m2 < 2750.0 ? 'SmallOffice' : 'MediumOffice'" in seg
