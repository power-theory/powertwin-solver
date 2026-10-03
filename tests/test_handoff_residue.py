"""WP-06 evidence set: Python->Ruby handoff residue (Q/A R-002 enum mismatch, R-029 territories,
R-030 live variant, R-038 dead arm, R-001/D-35 stale artifacts)."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "solver", "app"))

RB = open(os.path.join(REPO, "solver", "upload", "PowerTwin.rb")).read()
GFF = open(os.path.join(REPO, "solver", "app", "modules", "simulation",
                        "generateFeatureFile.py")).read()


def test_system_type_enum_matches_the_measure():
    # R-002: ENUM_VALUES accepted 15 'Residential - *' values plus one wording near-miss that
    # create_typical_building_from_model's choice list rejects -- validated metadata then failed
    # (or silently no-op'd) at measure run time.
    from modules.simulation.sim_params_spec import ENUM_VALUES
    residential = [v for v in ENUM_VALUES["system_type"] if v.startswith("Residential")]
    assert not residential, (
        f"{len(residential)} Residential-* system types pass validation but the commercial "
        "measure rejects them (residential buildings never consume system_type)"
    )
    near_miss = "DOAS with fan coil district chilled water and boiler"
    if near_miss in ENUM_VALUES["system_type"]:
        assert near_miss in RB, (
            "spec accepts the 'and boiler' wording but the mapper never remaps it to the "
            "measure's 'with boiler'"
        )


def test_territories_have_climate_zone_fallback():
    # R-029: PR/VI/GU/AS/MP were absent from climate_zone_fallback, so their buildings hit the
    # empty rescue and silently simulated with the osw's Buffalo 6A climate arguments.
    seg = GFF.split("climate_zone_fallback", 1)[1][:4000]
    for st in ("PR", "VI", "GU"):
        assert f"'{st}'" in seg, f"territory {st} missing from climate_zone_fallback"


def test_empty_weather_key_fails_loud():
    # R-030 live variant: a present-but-empty weather_filename short-circuited BOTH the
    # assignment and the fallback, silently keeping the Buffalo default EPW.
    seg = RB.split("# set weather file", 1)[1][:1200]
    assert "raise" in seg, (
        "empty weather_filename silently keeps the osw default weather"
    )


def test_swh_heatpump_arm_is_reachable():
    # R-038 removed a 'HeatPump' arm nothing could reach. Since 2026-09-30 a commercial heat pump water heater sets
    # ep_fuel = 'HeatPump' before the case, so the arm is live (an office's explicit HPWH had built a gas heater).
    seg = RB.split("swh_fuel_map", 1)[1][:1600]
    assert "ep_fuel = 'HeatPump' if props[:water_heater_type]" in seg
    assert seg.index("ep_fuel = 'HeatPump'") < seg.index("when 'Electricity', 'NaturalGas', 'HeatPump'")


def test_stale_construction_lookup_comment_gone():
    # R-001 residue: the gFF comment still claimed urbanopt's construction-set lookup consumes
    # the surface-suffixed material name; nothing does (Ruby comment says so explicitly).
    assert "construction-set" not in GFF, (
        "stale comment claims the material tier strings are consumed"
    )


def test_dead_mapper_locals_removed():
    # D-35: state/region/vintage computed at PowerTwin.rb ~1224 with a comment claiming
    # downstream lookups; nothing reads them.
    seg = RB.split("Asset context for the reference-data lookups", 1)
    assert len(seg) == 1, "dead state/region/vintage locals still computed in the mapper"
