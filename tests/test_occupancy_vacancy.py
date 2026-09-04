"""WP-02 evidence set: occupancy and vacancy correctness (Q/A register R-005/R-006/R-007/R-009,
D-20, D-28, L-018).

Reproduce-first: every test here was written against the DEFECTIVE behavior and must FAIL before
the fix lands (mutation check: reverting the fix must restore the failures). Feature-generation
level (T2): drives the real process_feature with constructed inputs, no simulation runtime needed.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "solver", "app"))

os.environ.setdefault("URBANOPT_DYNAMIC_DEFAULTS", "true")
os.environ.setdefault("URBANOPT_STOCHASTIC_SAMPLING", "true")

pytest.importorskip("rich", reason="solver runtime deps unavailable")
import modules.simulation.generateFeatureFile as gff  # noqa: E402
from modules.simulation.sim_params_spec import build_asset_ctx, is_vacant  # noqa: E402

# A county with a real ACS vacancy rate so the stochastic draw is live (Teton County WY has
# high seasonal vacancy; any county in the file works -- the helper below verifies draws exist).
COUNTY_FIPS = "56039"
STATE = "WY"


def _run(metadata, building_type="Single-Family Detached", bid="77", area=2000.0,
         name="t", weather="USA_WY_Jackson.Hole.725776_TMY3.epw", cz="6B", year=1995):
    feature = {"type": "Feature",
               "properties": {"id": bid, "asset_id": bid, "type": "Building"},
               "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}}
    md = dict(metadata)
    md.setdefault("county_fips", COUNTY_FIPS)
    md.setdefault("state", STATE)
    out = gff.process_feature(
        feature,
        {bid: area}, {bid: building_type}, {bid: name}, {bid: (STATE, weather)}, {bid: cz}, {bid: year},
        {bid: md},
    )
    assert out is not None, "process_feature dropped the building"
    final_json, _bid, _name = out  # returns (feature_json, building_id, building_name)
    return final_json["features"][0]["properties"] if "features" in final_json else final_json["properties"]


def _vacant_drawing_id():
    """Find a building id whose hash draw IS vacant under this county (deterministic)."""
    for i in range(1, 2000):
        ctx = build_asset_ctx({"county_fips": COUNTY_FIPS, "state": STATE, "area": 2000,
                               "floor_count": 1},
                              building_type="Single-Family Detached", climate_zone="6B",
                              building_id=str(i))
        if is_vacant(ctx):
            return str(i)
    pytest.skip("no vacant draw found in 2000 ids -- vacancy reference unavailable?")


def test_string_true_vacant_is_honored():
    # R-005: JSON string "true" must mean vacant, not silently occupied.
    props = _run({"vacant": "true"})
    assert props.get("vacant") is True, "string 'true' was not honored as vacant"
    assert props["number_of_occupants"] == 0


def test_truthy_override_never_less_vacant_than_absent():
    # R-005 aggravation: a truthy-but-not-True override must not SUPPRESS the stochastic draw.
    bid = _vacant_drawing_id()
    props = _run({"vacant": "true"}, bid=bid)
    assert props.get("vacant") is True, (
        "a string 'true' produced an OCCUPIED building that would have been vacant with no override"
    )


def test_explicit_occupant_count_beats_stochastic_vacancy():
    # R-007: explicit metadata is the highest precedence tier; the hash draw must not zero it.
    bid = _vacant_drawing_id()
    props = _run({"number_of_occupants": 4}, bid=bid)
    assert props["number_of_occupants"] == 4, (
        "explicit occupant count was overwritten by the stochastic vacancy draw"
    )
    assert props.get("vacant") is not True


def test_commercial_flat_occupancy_not_delivered_when_unresolved(monkeypatch):
    # D-20/L-018 commercial sibling: with dynamic defaults OFF and no explicit count, the
    # ''-omit sentinel must survive to the feature JSON so the measure's area-scaled default
    # applies -- not an area-independent archetype constant (hotel=20, office=100).
    monkeypatch.setenv("URBANOPT_DYNAMIC_DEFAULTS", "false")
    props = _run({}, building_type="Lodging", area=300_000.0)
    assert "number_of_occupants" not in props, (
        f"flat archetype constant {props.get('number_of_occupants')!r} delivered for a "
        "300k ft2 hotel instead of omitting for the area-scaled measure default"
    )


def test_multifamily_fallback_is_per_unit():
    # L-018 residential: when the resolver can't produce a per-unit count (non-numeric bedrooms),
    # the fallback must be PER-UNIT like its consumer (geometry_unit_num_occupants), never the
    # whole-building archetype constant.
    props = _run({"bedrooms": "n/a", "number_of_units": 5}, building_type="Multifamily",
                 area=5000.0)
    occ = props["number_of_occupants"]
    assert occ == 4, (
        f"MF fallback delivered {occ} per unit (x5 units); expected the whole-building "
        "archetype 20 divided by units = 4"
    )


def test_vacant_subtype_is_modeled_vacant():
    # D-28: a building literally typed 'Vacant' must simulate vacant without needing metadata.
    props = _run({}, building_type="Vacant", area=8000.0)
    assert props.get("vacant") is True, "subtype 'Vacant' simulated as occupied"


def test_residential_vacant_branch_suppresses_water_and_plug_loads():
    # R-009 (mapper side, T1 contract): the residential vacant branch must suppress the
    # occupant-driven loads the commercial branch already suppresses. Bedroom-count-driven DHW
    # and plug/appliance loads at full draw inside a 0-occupant 55F house is the defect.
    rb = open(os.path.join(REPO, "solver", "upload", "PowerTwin.rb")).read()
    branch = rb.split("if res_props[:vacant]", 1)[1].split("\n            end", 1)[0]
    assert "water_fixtures_usage_multiplier" in branch, (
        "residential vacant branch does not suppress hot-water fixture usage"
    )
    assert "misc_plug_loads_television_annual_kwh" in branch or "misc_plug_loads" in branch, (
        "residential vacant branch does not suppress plug/appliance loads"
    )


def test_validate_metadata_rejects_garbage_vacant():
    # R-006: vacant is validated at the boundary; bool-likes accepted, garbage rejected.
    from modules.simulation.sim_params_spec import validate_metadata
    ok, errs = validate_metadata({"vacant": "yes please"})
    assert not ok and any("vacant" in e for e in errs)
    for good in (True, False, "true", "false", None, ""):
        ok, errs = validate_metadata({"vacant": good})
        assert ok, f"bool-like {good!r} rejected: {errs}"
