"""Numeric precision contract (Dev/Conventions 2026-09-19): every sensor-value writer emits 4
significant figures, so a small-unit stream keeps its magnitude, a large one loses its float noise,
and sums of rounded hourly values stay within a hundredth of a percent of the raw sums."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "solver", "app"))

from modules.utils.precision import sig, sig4, round_series, SIG_FIGS  # noqa: E402


def test_sig_figs_follow_the_magnitude():
    assert SIG_FIGS == 4
    assert sig4(87.6885603185128) == 87.69
    assert sig4(244123.456) == 244100.0
    assert sig4(0.000123456) == 0.0001235
    assert sig4(0.00004567) == 0.00004567
    assert sig4(0.0) == 0.0 and sig4(-1.23456) == -1.235
    assert sig4(None) is None and sig4("x") == "x"
    assert np.isnan(sig(float("nan"))) and sig(float("inf")) == float("inf")


def test_round_series_matches_scalar_and_keeps_nan_and_zero():
    s = pd.Series([87.6885603185128, 0.0, np.nan, 0.000123456, 244123.456, -2.5e-7], name="value")
    r = round_series(s)
    assert list(r.index) == list(s.index) and r.name == "value"
    assert r.iloc[0] == 87.69 and r.iloc[1] == 0.0 and np.isnan(r.iloc[2])
    assert r.iloc[3] == 0.0001235 and r.iloc[4] == 244100.0 and r.iloc[5] == -2.5e-7
    for raw, got in zip(s.dropna(), r.dropna()):
        assert got == sig4(raw)


def test_rounded_hourly_sums_stay_within_the_contract():
    rng = np.random.default_rng(0)
    hourly = pd.Series(rng.lognormal(mean=2.0, sigma=1.5, size=8760))
    rel = abs(round_series(hourly).sum() - hourly.sum()) / hourly.sum()
    assert rel < 1e-4  # 0.01%: individual rounding errors are unbiased and average out
    # nothing real is zeroed: every nonzero input stays nonzero
    small = pd.Series([1e-4, 3e-5, 7e-6, 1e-6])
    assert (round_series(small) != 0).all()


def test_values_fit_the_db_column():
    """sensor_logs.value is NUMERIC(14, 6): 4 significant figures fit exactly for anything at or
    above 0.001; below that the column's 6-decimal scale rounds further (today's behavior, the
    smallest real hourly readings sit near 0.0001, so at worst one figure is lost there)."""
    for v in (0.001234, 1.0, 12345.678, 244123.456, 9.9e7):
        r = sig4(v)
        assert round(r, 6) == r and abs(r) < 1e8
    assert round(sig4(0.0001234), 6) == 0.000123  # the DB floor, unchanged by this contract


def test_env_switch_turns_the_rule_off_and_sets_the_figure_count(monkeypatch):
    from modules.utils import precision as P
    monkeypatch.setenv(P.ENV_VAR, "off")
    assert P.sig_figs_setting() == 0 and P.sig4(87.6885603185128) == 87.6885603185128
    s = pd.Series([87.6885603185128, 0.000123456])
    assert list(P.round_series(s)) == list(s)
    monkeypatch.setenv(P.ENV_VAR, "3")
    assert P.sig4(87.6885603185128) == 87.7 and list(P.round_series(s)) == [87.7, 0.000123]
    monkeypatch.setenv(P.ENV_VAR, "")
    assert P.sig_figs_setting() == 4
    monkeypatch.setenv(P.ENV_VAR, "lots")
    with pytest.raises(ValueError):
        P.sig_figs_setting()


def _standalone_consolidate(monkeypatch):
    """Load consolidate_sensor_logs.py the way the cluster runs it: as a file, with neither the
    package nor a sibling precision.py importable, so the inline fallback is what executes."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "solver", "app", "modules", "utils", "consolidate_sensor_logs.py")
    monkeypatch.setitem(sys.modules, "modules.utils.precision", None)
    monkeypatch.setitem(sys.modules, "precision", None)
    spec = importlib.util.spec_from_file_location("consolidate_standalone", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SWEEP = np.concatenate([np.logspace(-6, 12, 3000) * 1.234567891, np.logspace(-6, 12, 3000) * 7.117110254539254,
                        [711711025.4539254, 123456789.0, 13012615.63, 99995.0, 2.5e-4, 0.12345]])


def test_consolidation_fallback_matches_the_package_helper(monkeypatch):
    """The cluster runs consolidate_sensor_logs.py standalone; its inline fallback must equal the
    package function bit for bit, including the off switch and a negative setting (review
    2026-09-20: the old copy zeroed every value for -1 while the package passed them through)."""
    mod = _standalone_consolidate(monkeypatch)
    s = pd.Series(np.r_[SWEEP, [0.0, np.nan, 87.6885603185128]])
    for setting in (None, "3", "6", "off", "-1", "0"):
        if setting is None:
            monkeypatch.delenv("POWERTWIN_SIG_FIGS", raising=False)
        else:
            monkeypatch.setenv("POWERTWIN_SIG_FIGS", setting)
        a, b = mod.round_series(s).to_numpy(), round_series(s).to_numpy()
        assert np.array_equal(a, b, equal_nan=True), f"fallback diverges at POWERTWIN_SIG_FIGS={setting}"
    monkeypatch.setenv("POWERTWIN_SIG_FIGS", "-1")
    assert np.array_equal(mod.round_series(s).to_numpy(), s.to_numpy(), equal_nan=True)


def test_sig_and_round_series_are_bit_identical_and_noise_free():
    """Review 2026-09-20: np.power(10.0, -5.0) is not 1e-05, so the old helpers left binary noise
    (711711025.45 -> 711700000.0000001) and disagreed in the 10**(n+4) decade. Both now round on
    exact powers: the repr of every result carries at most n significant digits."""
    for n in (3, 4, 6):
        scalar = np.array([sig(v, n) for v in SWEEP])
        vector = round_series(pd.Series(SWEEP), n).to_numpy()
        assert np.array_equal(scalar, vector), f"scalar/vector mismatch at n={n}"
        for v in vector.tolist():
            mant = repr(v).split("e")[0].replace("-", "").replace(".", "").lstrip("0").rstrip("0")
            assert len(mant) <= n, f"{v!r} carries more than {n} significant digits"
    assert sig(711711025.4539254) == 711700000.0 and repr(sig(711711025.4539254)) == "711700000.0"
    assert sig(123456789.0) == 123500000.0
    assert sig(1e-310) == 1e-310  # below the log10 floor: passed through, no OverflowError


def test_scalar_and_vector_agree_on_ties():
    vals = [123.45, 0.12345, 2.5e-4, 99995.0, 1234.5]
    assert [sig4(v) for v in vals] == list(round_series(pd.Series(vals)))
