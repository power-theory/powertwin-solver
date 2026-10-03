"""Insulation inputs after the 2026-09-29 owner decision (vault Insulation Presets Plan): the R-value is the only
insulation input, a saved wall/roof_material is ignored, and the defaults preview offers code R-value presets for the
building's climate zone. Also the residential floor_height fix: an explicit value is the house's ceiling height and
the preview shows houses the 8 ft they simulate unset."""
import json
import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOLVER = os.path.join(REPO, "solver")
sys.path.insert(0, os.path.join(SOLVER, "app"))
os.environ.setdefault("POWERTWIN_LOG_DIR", "/tmp")

from modules.simulation import sim_params_spec as S  # noqa: E402

# Teton 15973's resolver inputs (Single-Family Detached, 1964, 1 floor); Teton is IECC zone 7A
HOUSE = {"county_fips": "56039", "state": "WY", "year_built": 1964, "area": 1216.0, "floor_count": 1,
         "latitude": 43.4799, "longitude": -110.7625}
TIERS = ("Standard", "Insulated", "Super Insulated")

# IECC 2009 Table 402.1.1 and IECC 2021 Table R402.1.3, from the code text fetched 2026-09-29 (ceiling; cavity-only
# wood-frame wall). M4 is Marine 4, grouped with zone 5 in both editions.
IECC = {"IECC 2009": {"ceiling": {1: 30, 2: 30, 3: 30, 4: 38, 5: 38, "M4": 38, 6: 49, 7: 49, 8: 49},
                      "wall_cavity": {1: 13, 2: 13, 3: 13, 4: 13, 5: 20, "M4": 20, 6: 20, 7: 21, 8: 21}},
        "IECC 2021": {"ceiling": {0: 30, 1: 30, 2: 49, 3: 49, 4: 60, 5: 60, "M4": 60, 6: 60, 7: 60, 8: 60},
                      "wall_cavity": {0: 13, 1: 13, 2: 13, 3: 20, 4: 30, 5: 30, "M4": 30, 6: 30, 7: 30, 8: 30}}}


@pytest.fixture(autouse=True)
def hosted(monkeypatch):
    monkeypatch.setenv("URBANOPT_DYNAMIC_DEFAULTS", "true")
    monkeypatch.setenv("URBANOPT_STOCHASTIC_SAMPLING", "false")


def resolved(md, field, bt, cz="7A"):
    return S.get_param(md, field, S.build_asset_ctx(md, building_type=bt, climate_zone=cz))


# ---- the tier is no longer an input ------------------------------------------------------------------------------
@pytest.mark.parametrize("bt", ["Single-Family Detached", "Multifamily", "Office", "Retail", "Warehouse"])
@pytest.mark.parametrize("tier", TIERS)
def test_a_saved_tier_changes_nothing(bt, tier):
    md = dict(HOUSE, wall_material=tier, roof_material=tier)
    for f in ("wall_r_value", "roof_r_value", "wall_material", "roof_material"):
        assert resolved(md, f, bt) == resolved(HOUSE, f, bt), f


def test_a_saved_tier_is_not_an_explicit_param():
    from modules.simulation import generateFeatureFile as G
    assert not set(S.RETIRED_INPUT_FIELDS) & set(G._EXPLICIT_PARAM_KEYS)
    assert "wall_r_value" in G._EXPLICIT_PARAM_KEYS and "floor_height" in G._EXPLICIT_PARAM_KEYS


def test_stored_tiers_still_validate():
    assert S.validate_metadata({"wall_material": "Super Insulated", "roof_material": "Standard"})[0]
    # an out-of-enum stored tier (bulk free-form, API) is ignored, so it must not 400 a re-simulation (review #12)
    assert S.validate_metadata({"wall_material": "Super Insulated Wall"})[0]


# ---- presets ---------------------------------------------------------------------------------------------------------
def presets(md, bt, cz):
    return S.insulation_presets(S.build_asset_ctx(md, building_type=bt, climate_zone=cz))


def test_reference_table_matches_the_code_text():
    ref = json.load(open(os.path.join(SOLVER, "upload", "reference_data", "insulation_presets.json")))
    for ed, cols in IECC.items():
        for col, by_zone in cols.items():
            assert {str(z): v for z, v in by_zone.items()} == ref["residential"][ed][col], (ed, col)


@pytest.mark.parametrize("cz,zone", [("1A", 1), ("3B", 3), ("4A", 4), ("4C", "M4"), ("5A", 5), ("6B", 6), ("7", 7), ("8", 8)])
def test_house_presets_are_the_iecc_minimums(cz, zone):
    got = presets(HOUSE, "Single-Family Detached", cz)
    for field, col in (("wall_r_value", "wall_cavity"), ("roof_r_value", "ceiling")):
        want = [IECC[ed][col][zone] for ed in ("IECC 2009", "IECC 2021") if zone in IECC[ed][col]]
        assert [p["value"] for p in got[field]] == want
        assert got[field][0]["label"] == "Code minimum, 2009 IECC"
        assert got[field][-1]["label"] == "Current code, 2021 IECC"


def test_commercial_conversion_from_the_standards_data():
    """A 1964 office is built to DOE Ref Pre-1980 with the 'Office' set: mass wall, IEAD roof. 90.1-2019 zone 7:
    mass wall U-0.071, IEAD roof U-0.028 (openstudio-standards 0.7.1). Insulation R = 1/U - other layers - films
    (wall: 1in stucco 0.2077 + 8in concrete 0.8801 + 1/2in gypsum 0.4507, films 0.85; roof: membrane 0.3371 +
    metal deck 0.0001, films 0.78)."""
    got = presets(dict(HOUSE, area=40000, floor_count=3), "Office", "7A")
    assert got["wall_r_value"][-1]["value"] == pytest.approx(round(1 / 0.071 - (0.2076612641 + 0.8801091616 + 0.4507121527) - 0.85, 2))
    assert got["roof_r_value"][-1]["value"] == pytest.approx(round(1 / 0.028 - (0.3371468859 + 0.0001003226739) - 0.78, 2))
    assert "Mass wall" in got["wall_r_value"][-1]["source"] and "DOE Ref Pre-1980" in got["wall_r_value"][-1]["source"]


def test_presets_target_the_construction_the_simulation_builds():
    """Teton 14616 (Mixed use -> Office, 22,940 sq ft, 1980) simulated on 2026-09-29 as 'TYPICAL INSULATED EXTERIOR
    MASS WALL' and 'TYPICAL IEAD ROOF' (eplustbl Opaque Exterior); with R-17.43 / R-57.48 the built U was 0.0505 /
    0.0171, exactly 1/(R + other layers + films). A SmallOffice prototype does not mean a wood-framed attic building:
    the construction set is looked up by 'Office' in the year's template."""
    md = dict(HOUSE, year_built=1980, area=22940, floor_count=1)
    src = presets(md, "Mixed use", "7A")["wall_r_value"][0]["source"]
    assert "Mass wall" in src and "SmallOffice" in src and "DOE Ref 1980-2004" in src
    assert "IEAD roof" in presets(md, "Mixed use", "7A")["roof_r_value"][0]["source"]


def test_template_follows_the_mapper():
    rb = open(os.path.join(SOLVER, "upload", "PowerTwin.rb")).read()
    body = rb.split("def lookup_template_by_year_built", 1)[1].split("def residential_building_types", 1)[0]
    ashrae = body.split("else", 1)[1]            # the non-DEER branch
    for rule in ("can_use_doe_ref && year_built < 1980", "return 'DOE Ref Pre-1980'", "can_use_doe_ref && year_built <= 2004",
                 "return 'DOE Ref 1980-2004'", "year_built <= 2007", "return '90.1-2004'", "year_built <= 2010",
                 "return '90.1-2007'", "year_built <= 2013", "return '90.1-2010'", "return '90.1-2013'"):
        assert rule in ashrae, rule
    assert "DOE_REF_INCOMPATIBLE = %w[SmallHotel Laboratory]" in rb
    cases = {1979: "DOE Ref Pre-1980", 1980: "DOE Ref 1980-2004", 2004: "DOE Ref 1980-2004", 2005: "90.1-2004",
             2007: "90.1-2004", 2008: "90.1-2007", 2010: "90.1-2007", 2011: "90.1-2010", 2013: "90.1-2010", 2014: "90.1-2013"}
    for y, t in cases.items():
        assert S._preset_template(y, "MediumOffice") == t, y
    assert S._preset_template(1970, "Laboratory") == "90.1-2004"
    for bad in (None, "", "abc", "inf", "1e999"):              # no year: the site default template (review #8, #11)
        assert S._preset_template(bad, "Office") == "90.1-2013", bad


def test_presets_follow_the_prototype_the_mapper_builds():
    """PowerTwin.rb lookup_building_type is the truth for which prototype a subtype becomes (a DOE prototype always since
    2026-10-03; under a DEER template create_bar maps it with openstudio-standards' doe_to_deer_building_type)."""
    rb = open(os.path.join(SOLVER, "upload", "PowerTwin.rb")).read()
    branch = rb.split("def lookup_building_type(", 1)[1].split("cannot be mapped to an ASHRAE building type", 1)[0]
    assert "return 'Nrs'" not in branch and "return 'OfS'" not in branch, "the legacy URBANopt DEER table must not come back"
    ruby = dict(re.findall(r"when '([^']+)'\s+return '([^']+)'", branch))
    for bt, proto in S._PRESET_PROTOTYPE.items():
        assert ruby[bt] == proto, bt
    commercial = {k for k, v in ruby.items() if v not in ("MidriseApartment", "Mixed use")}
    assert commercial - set(S._PRESET_PROTOTYPE) == set()      # Office and Lodging are sized in code, not in the case
    assert S._preset_prototype({"building_type": "Office", "area": 24000}) == "SmallOffice"
    assert S._preset_prototype({"building_type": "Office", "area": 100001}) == "LargeOffice"
    assert S._preset_prototype({"building_type": "Lodging", "floor_count": 4}) == "LargeHotel"


def test_current_code_below_the_minimum_is_not_offered():
    """90.1-2019 relaxed semiheated metal-building walls in zone 4 (U-0.162 vs 0.134): only the minimum is offered."""
    got = presets(HOUSE, "Nonrefrigerated warehouse", "4A")
    assert [p["label"] for p in got["wall_r_value"]] == ["Code minimum, ASHRAE 90.1-2007"]


@pytest.mark.parametrize("bt,cz", [("Office", None), ("Single-Family Detached", ""), ("Uncovered Parking", "5A")])
def test_no_presets_without_a_prototype_or_zone(bt, cz):
    assert presets(HOUSE, bt, cz) == {}


def test_mixed_use_gets_the_office_it_simulates_as():
    """asset_subtypes.csv remaps Mixed use to Office (effective_id); the simulation builds it as an Office."""
    assert presets(HOUSE, "Mixed use", "7A") == presets(HOUSE, "Office", "7A")


# ---- preview -------------------------------------------------------------------------------------------------------
@pytest.fixture
def preview(monkeypatch):
    monkeypatch.chdir(SOLVER)
    monkeypatch.syspath_prepend(os.path.join(SOLVER, "app"))
    monkeypatch.setenv("API_SOLVER_TOKEN", "t")
    import flask
    import views
    app = flask.Flask("t")
    app.add_url_rule("/r", view_func=views.resolve_defaults_preview, methods=["POST"])
    c = app.test_client()
    return lambda md, bt: c.post("/r", json={"metadata": md, "building_type": bt}, headers={"api-token": "t"}).get_json()


def test_preview_offers_presets_for_the_buildings_zone(preview):
    got = preview(HOUSE, "Single-Family Detached")          # coordinates resolve to zone 7A, as in the simulation
    assert [p["value"] for p in got["presets"]["wall_r_value"]] == [21.0, 30.0]
    assert [p["value"] for p in got["presets"]["roof_r_value"]] == [49.0, 60.0]


def test_preview_shows_a_house_its_simulated_ceiling(preview):
    got = preview(HOUSE, "Single-Family Detached")
    assert got["resolved"]["floor_height"] == S.RESIDENTIAL_UNSET_CEILING_HEIGHT_FT == 8.0
    office = preview(dict(HOUSE, area=60000, floor_count=3), "Office")
    assert office["resolved"]["floor_height"] == 13.0                    # commercial: the MediumOffice typical story


# ---- residential floor height -----------------------------------------------------------------------------------------
def test_mapper_passes_only_an_explicit_floor_height_to_a_house():
    src = open(os.path.join(SOLVER, "upload", "PowerTwin.rb")).read()
    after = src.split("residential(scenario, feature, args, building_type)", 1)[1][:1500]
    assert "PowerTwinRefs.explicit_param?(raw_props_res, 'floor_height')" in after
    assert "args[:geometry_average_ceiling_height] = raw_props_res[:floor_height].to_f" in after
    assert "def self.explicit_param?(props, key)" in open(os.path.join(SOLVER, "upload", "powertwin_refs.rb")).read()


def test_preset_labels_keep_the_shape_the_frontend_translates():
    """powertwin parses 'Code minimum, <edition>' / 'Current code, <edition>' and translates the frame in 12 locales,
    passing the edition through (2026-09-30): changing the shape breaks the picker's copy."""
    for key, label in S.PRESET_LABELS.items():
        frame, edition = label.split(", ", 1)
        assert frame in ("Code minimum", "Current code") and key.split()[-1] in edition, label


# ---- commercial floor height -------------------------------------------------------------------------------------
@pytest.mark.parametrize("bt,area,floors,want", [("Office", 20000, 1, 10.0), ("Office", 60000, 3, 13.0), ("Retail other than mall", 20000, 1, 20.0),
                                                 ("Nonrefrigerated warehouse", 15000, 1, 28.0), ("Strip shopping mall", 20000, 1, 17.0),
                                                 ("Public assembly", 20000, 1, 13.0), ("Lodging", 20000, 2, 9.0), ("Mixed use", 20000, 1, 10.0)])
def test_commercial_floor_height_is_the_prototype_story(bt, area, floors, want):
    """openstudio-standards create_bar building_form_defaults typical_story of the prototype PowerTwin.rb builds."""
    md = dict(HOUSE, area=area, floor_count=floors)
    assert resolved(md, "floor_height", bt) == want
    assert resolved(dict(md, floor_height=12), "floor_height", bt) == 12.0          # an explicit value still wins


ZONING_FT = 3.6 / 0.3048


@pytest.mark.parametrize("bt,area,floors", [("Vacant", 382, 1), ("Vacant", 20000, 1), ("Vacant", 414, 4),
                                            ("Nonrefrigerated warehouse", 94, 1), ("Retail other than mall", 96, 1),
                                            ("Office", 999, 1), ("Office", None, 1), ("Office", "n/a", 1)])
def test_outside_the_prototypes_domain_keeps_the_zoning_story(bt, area, floors):
    """ComStock samples no building under 1,000 ft2 and has no Vacant type; there the prototype story built 28 ft
    sheds that failed in EnergyPlus (floor-fit run 2026-09-30), so they keep urbanopt-geojson's 3.6 m."""
    md = dict(HOUSE, area=area, floor_count=floors)
    if area is None:
        md.pop("area")
    assert resolved(md, "floor_height", bt) == pytest.approx(ZONING_FT)
    assert resolved(dict(md, floor_height=12), "floor_height", bt) == 12.0          # an explicit value still wins


def test_the_domain_starts_at_comstocks_smallest_building():
    md = dict(HOUSE, area=1000, floor_count=1)
    assert resolved(md, "floor_height", "Nonrefrigerated warehouse") == 28.0
    assert S.URBANOPT_ZONING_STORY_FT * 0.3048 == pytest.approx(3.6)                  # PowerTwin's ft -> m gives 3.6 m back


def test_houses_keep_no_floor_height_default():
    assert S.resolve_default("floor_height", S.build_asset_ctx(HOUSE, building_type="Single-Family Detached", climate_zone="7A")) == (None, None)


def test_feature_carries_the_story_height_for_commercial_only():
    src = open(os.path.join(SOLVER, "app", "modules", "simulation", "generateFeatureFile.py")).read()
    assert 'if building_type not in RESIDENTIAL_TYPES:\n        new_properties["floor_to_floor_height"] = floor_height' in src


def test_house_presets_need_dynamic_defaults(monkeypatch):
    """PowerTwin.rb reads a house's wall/roof R inside `if res_props[:dynamic_defaults]` (review #13)."""
    monkeypatch.setenv("URBANOPT_DYNAMIC_DEFAULTS", "false")
    assert presets(HOUSE, "Single-Family Detached", "7A") == {}


@pytest.mark.parametrize("md,bt", [(dict(HOUSE, year_built="inf"), "Office"), (dict(HOUSE, latitude=10 ** 400), "Office"),
                                   (HOUSE, ["Office"]), (dict(HOUSE, floor_count="2.0"), "Lodging")])
def test_preview_survives_malformed_input(preview, md, bt):
    """Malformed input the endpoint answered with 200 before the change must not become a 500 (review #11)."""
    got = preview(md, bt)
    assert "resolved" in got and "presets" in got


@pytest.mark.parametrize("value,explicit", [(25, True), (20, True), (5, True), ("abc", False)])
def test_a_clamped_floor_height_is_still_explicit(value, explicit):
    """get_param clamps an out-of-range number; the edit is still the user's (review #9)."""
    from modules.simulation import generateFeatureFile as G
    md = dict(HOUSE, floor_height=value)
    ctx = S.build_asset_ctx(md, building_type="Single-Family Detached", climate_zone="7A")
    assert G._accepted(md, "floor_height", ctx) is explicit


def test_code_minimum_presets_use_the_older_zone_map():
    """Franklin County OH is zone 4A on the 2021 map and 5A on the map the 2009 IECC was written for (review #3)."""
    ctx = S.build_asset_ctx(dict(HOUSE, year_built=1995), building_type="Single-Family Detached", climate_zone="4A")
    got = S.insulation_presets(ctx, "5A")
    assert [p["value"] for p in got["wall_r_value"]] == [20.0, 30.0]      # 2009 zone 5 wall, 2021 zone 4 wall
    assert [p["value"] for p in got["roof_r_value"]] == [38.0, 60.0]
    assert S.insulation_presets(ctx) == S.insulation_presets(ctx, "4A")   # no older zone: the 2021 zone


def test_no_preset_the_measure_would_skip_or_reject():
    """A 1995 office in 2A is built to DOE Ref 1980-2004 with an R-0.55 mass-wall insulation layer: the openstudio-ee
    wall measure leaves such a construction unaltered, so no wall preset is offered (review #1)."""
    got = presets(dict(HOUSE, year_built=1995, area=40000, floor_count=3), "Office", "2A")
    assert "wall_r_value" not in got and got["roof_r_value"]
    for bt in ("Office", "Nonrefrigerated warehouse", "Retail other than mall"):
        for cz in ("0A", "1A", "2B", "3C", "5A", "7"):
            for field, items in presets(dict(HOUSE, year_built=2015, area=40000), bt, cz).items():
                assert all(p["value"] >= 1.0 for p in items), (bt, cz, field)


def test_hotels_use_the_category_the_template_builds():
    """openstudio-standards has a residential building-level set only for SmallHotel in the 90.1 templates; a
    LargeHotel is built with the nonresidential set (review #5)."""
    big = presets(dict(HOUSE, year_built=1970, floor_count=6), "Lodging", "5A")
    small = presets(dict(HOUSE, year_built=2015, floor_count=2), "Lodging", "5A")
    assert "LargeHotel" in big["wall_r_value"][0]["source"] and "SmallHotel" in small["wall_r_value"][0]["source"]
    ref = json.load(open(os.path.join(SOLVER, "upload", "reference_data", "insulation_presets.json")))["commercial"]
    assert "LargeHotel" not in sum(ref["residential_sets"].values(), [])
    r_nonres = ref["insulation_r"]["90.1-2019"]["wall|Mass|Nonresidential"]["5"]
    assert big["wall_r_value"][-1]["value"] == r_nonres


@pytest.mark.parametrize("bt,want", [("Office", ZONING_FT), ("Nonrefrigerated warehouse", ZONING_FT),
                                     ("Single-Family Detached", 8.0), ("Multifamily", 8.0)])
def test_unset_floor_height_is_what_urbanopt_builds_with_dynamic_defaults_off(monkeypatch, bt, want):
    """DD off must stay plain URBANopt: with the zoning patch honouring floor_to_floor_height, the flat 9 ft would have
    built every commercial story at 9 ft (2026-09-30); a house's feature carried 9 while it built 8."""
    monkeypatch.setenv("URBANOPT_DYNAMIC_DEFAULTS", "false")
    md = dict(HOUSE, area=20000, floor_count=2)
    assert resolved(md, "floor_height", bt) == pytest.approx(want)
    assert resolved(dict(md, floor_height=12), "floor_height", bt) == 12.0


def test_unset_house_floor_height_is_its_ceiling_with_dynamic_defaults_on():
    assert resolved(HOUSE, "floor_height", "Single-Family Detached") == 8.0
