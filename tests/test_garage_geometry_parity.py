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
    """Transcription of the garage_fits lambda in PowerTwin.rb."""
    gw = 12.0 * cars
    gd, protrusion, aspect = 20.0, 1.0, 2.0
    cfa = max(cfa, 144.0)
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
    for cfa in range(300, 6001, 50):
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


def test_ruby_lambda_still_transcribes_this_formula():
    """Anchor the Ruby side: the constants and structure this test transcribes
    must still exist in PowerTwin.rb (assignment-anchored, not comment-anchored)."""
    src = open(os.path.join(REPO, "solver", "upload", "PowerTwin.rb")).read()
    for needle in ("garage_fits = lambda",
                   "(cfa + 2 * inside - (nf - 1) * bonus) / (nf + 1)",
                   "(cfa + inside - (nf - 1) * bonus) / nf",
                   "Math.sqrt(fp * aspect)",
                   "gw < length"):
        assert needle in src, f"PowerTwin.rb garage predicate drifted: missing {needle!r}"
