"""Numeric precision contract for emitted sensor values (Dev/Conventions, 2026-09-19).

Every writer of a sensor value rounds to SIG_FIGS significant figures: the per-building cleaned
reports, the cluster consolidation and the local pack path (each after its resample sum). Why
significant figures and not decimals: the streams span 0.0001 tons of propane per hour to
hundreds of thousands of kWh, and a fixed decimal cap either zeroes the small streams or keeps
float noise on the large ones. 4 significant figures is 0.01% relative, a hundred times finer than
the model's error against utility actuals, and fits sensor_logs.value NUMERIC(14, 6) exactly for
anything at or above 0.001 (below that the column's 6-decimal scale rounds further, as today).
"""

from __future__ import annotations

import math

import numpy as np

SIG_FIGS = 4
# Switch (owner 2026-09-19): POWERTWIN_SIG_FIGS unset -> 4; a positive integer -> that many
# figures; 0 / off / false / none -> rounding disabled (values pass through untouched). Read at
# call time so a per-run environment (cluster job script, docker compose) controls it.
ENV_VAR = "POWERTWIN_SIG_FIGS"


def sig_figs_setting() -> int:
    """Active number of significant figures, or 0 when the rule is switched off."""
    import os

    raw = os.environ.get(ENV_VAR)
    if raw is None or raw.strip() == "":
        return SIG_FIGS
    v = raw.strip().lower()
    if v in ("0", "off", "false", "none", "no"):
        return 0
    try:
        n = int(v)
    except ValueError:
        raise ValueError(f"{ENV_VAR}={raw!r}: expected a positive integer or off")
    if n < 1:
        return 0
    return n


def sig(x, n: int | None = None):
    """Round x to n significant figures (default: the POWERTWIN_SIG_FIGS setting). None, NaN and
    infinities pass through; 0 stays 0; with the rule switched off x is returned unchanged."""
    if n is None:
        n = sig_figs_setting()
    if n == 0 or x is None:
        return x
    try:
        v = float(x)
    except (TypeError, ValueError):
        return x
    if v == 0.0 or not math.isfinite(v) or abs(v) < 1e-300:
        return v
    # same algorithm as round_series (round half to even on the scaled float) so the scalar and
    # the vectorised helper agree bit for bit, ties included. digits >= 0 scales UP by an exact
    # integer power; digits < 0 divides by the exact positive power instead of multiplying by
    # its inexact reciprocal, which left 12th-digit noise (711700000.0000001) and made the two
    # helpers disagree in the 10**(n+4) decade (review 2026-09-20)
    digits = n - 1 - int(math.floor(math.log10(abs(v))))
    if digits >= 0:
        scale = 10.0 ** digits
        return float(np.round(v * scale) / scale)
    p = 10.0 ** (-digits)
    return float(np.round(v / p) * p)


def sig4(x):
    return sig(x)


def round_series(series, n: int | None = None):
    """Vectorised sig() for a pandas Series of floats (NaN and 0 preserved); unchanged when the
    rule is switched off."""
    if n is None:
        n = sig_figs_setting()
    if n == 0:
        return series
    vals = series.to_numpy(dtype=float, copy=True)
    nz = np.isfinite(vals) & (vals != 0.0) & (np.abs(vals) >= 1e-300)
    if nz.any():
        vals[nz] = _round_sig_array(vals[nz], n)
    return series.__class__(vals, index=series.index, name=series.name)


def _round_sig_array(v, n):
    """Vectorised body shared with sig(): exact integer powers on both branches."""
    digits = n - 1 - np.floor(np.log10(np.abs(v)))
    up = digits >= 0
    out = np.empty_like(v)
    scale = np.power(10.0, digits[up])
    out[up] = np.round(v[up] * scale) / scale
    p = np.power(10.0, -digits[~up])
    out[~up] = np.round(v[~up] / p) * p
    return out
