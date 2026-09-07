"""The garage-geometry predicate in PowerTwin.rb must be an EXACT replica of
the vendored BuildResidentialHPXML acceptance math (geometry.rb). Divergence in
either direction crashed 98% of one stock's gml arm (2,509/2,563 failures):
too-lax -> the measure hard-rejects or silently drops the garage while
garage-located duct/appliance references dangle; too-strict -> garages vanish
from homes that could have them. This test transcribes BOTH formulas from their
sources and sweeps a realistic grid asserting equivalence.
"""
import math
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def measure_accepts(cfa, nf, cars, conditioned_basement, gd=20.0, protrusion=1.0, aspect=2.0):
    """Transcription of BuildResidentialHPXML/resources/geometry.rb (vendored
    urbanopt-cli 1.0.1): footprint shrinks by garage bonus area at protrusion 1,
    conditioned basements change the divisor, reject iff garage_width >= length."""
    gw = 12.0 * cars
    ga = gw * gd
    inside = ga * (1.0 - protrusion)
    bonus = ga * protrusion
    if conditioned_basement:
        fp = (cfa + 2 * inside - (nf - 1) * bonus) / (nf + 1)
    else:
        fp = (cfa + inside - (nf - 1) * bonus) / nf
    if fp <= 0:
        return False
    length = math.sqrt(fp * aspect)
    width = fp / length
    if gw >= length and gd > 0:
        return False
    if (1.0 - protrusion) * gd >= width and gw > 0:
        return False
    return True


def mapper_accepts(cfa, nf, cars, conditioned_basement):
    """Transcription of the garage_fits lambda in PowerTwin.rb (raw cfa, as
    residential/util.rb passes feature floor_area straight through for a
    detached unit)."""
    gw = 12.0 * cars
    gd, protrusion, aspect = 20.0, 1.0, 2.0
    nf = max(nf, 1)
    ga = gw * gd
    inside = ga * (1.0 - protrusion)
    bonus = ga * protrusion
    if conditioned_basement:
        fp = (cfa + 2 * inside - (nf - 1) * bonus) / (nf + 1)
    else:
        fp = (cfa + inside - (nf - 1) * bonus) / nf
    if fp <= 0:
        return False
    length = math.sqrt(fp * aspect)
    width = fp / length
    return gw < length and (1.0 - protrusion) * gd < width


def test_mapper_predicate_matches_measure_exactly():
    mismatches = []
    # 50 sqft up: one stock's fix arm still hard-rejected 19 tiny footprints
    # (down to 58 sqft) because the lambda floored cfa at 144 while the measure
    # saw the raw value.
    for cfa in range(50, 6001, 25):
        for nf in (1, 2, 3):
            for cars in (1, 2, 3):
                for cb in (False, True):
                    if mapper_accepts(cfa, nf, cars, cb) != measure_accepts(cfa, nf, cars, cb):
                        mismatches.append((cfa, nf, cars, cb))
    assert not mismatches, f"predicate diverges from the measure at: {mismatches[:8]} (+{len(mismatches)} total)"


def test_gml_arm_failure_class_now_rejected():
    # Real failing shape from one stock's gml arm: small MULTI-story homes. At
    # protrusion 1.0 a 1-story garage floats above the footprint (always fits),
    # but each extra story subtracts the garage bonus area from the footprint:
    # 900 sqft 2-story -> footprint 210, length 20.5 ft < 24 ft garage -> reject.
    assert not measure_accepts(900, 2, 2, False)
    assert not mapper_accepts(900, 2, 2, False)
    assert measure_accepts(900, 1, 2, False)
    assert mapper_accepts(900, 1, 2, False)
    assert measure_accepts(2200, 2, 2, False)
    assert mapper_accepts(2200, 2, 2, False)


def test_tiny_footprint_rejected_like_the_measure():
    # 58 sqft one-story (a real reject in that arm): footprint 58 -> length 10.8 ft,
    # a 12 ft one-car garage is wider than the unit.
    assert not measure_accepts(58, 1, 1, False)
    assert not mapper_accepts(58, 1, 1, False)


def test_ruby_lambda_still_transcribes_this_formula():
    """Anchor the Ruby side: the constants and structure this test transcribes
    must still exist in PowerTwin.rb (assignment-anchored, not comment-anchored)."""
    src = open(os.path.join(REPO, "solver", "upload", "PowerTwin.rb")).read()
    for needle in ("garage_fits = lambda",
                   "cfa = feature.floor_area.to_f",
                   "(cfa + 2 * inside - (nf - 1) * bonus) / (nf + 1)",
                   "(cfa + inside - (nf - 1) * bonus) / nf",
                   "Math.sqrt(fp * aspect)",
                   "gw < length"):
        assert needle in src, f"PowerTwin.rb garage predicate drifted: missing {needle!r}"
    assert "144.0].max" not in src.split("garage_fits = lambda")[1].split("cars = if")[0], \
        "the cfa floor is back inside garage_fits; the measure uses the raw floor area"


def test_dropped_garage_leaves_no_width_and_no_garage_location():
    """The residual failures of one stock's fix arm: residential/util.rb seeds
    geometry_garage_width = 0.0 before the mapper block, the block only
    overwrote it when a garage was kept, and the duct space-coherence check used
    PowerTwinRefs.present?(0.0) == true to admit 'garage' as a duct location on
    a home with no garage surfaces. Both halves are anchored on their
    assignment lines."""
    src = open(os.path.join(REPO, "solver", "upload", "PowerTwin.rb")).read()
    block = src.split("garage_fits = lambda")[1].split("if ducted && ")[0]
    assert "args[:geometry_garage_width] = 0.0" in block, \
        "a dropped garage must explicitly zero geometry_garage_width"
    coherence = src.split("if ducted && ")[1].split("allowed << 'garage'")[0]
    assert "has_garage = args[:geometry_garage_width].to_f * args[:geometry_garage_depth].to_f > 0" in coherence, \
        "has_garage must test positivity (measure.rb: width * depth > 0), not presence"
    assert "PowerTwinRefs.present?(args[:geometry_garage_width])" not in src, \
        "presence test on the garage width is the dangling-garage-reference bug"
