"""The weather model: a forecast, corrected for the station's habit, with an honest spread.

Plain words:
  1. Take NOAA's latest forecast (NBM) for the settlement station.
  2. Look back at past days: how far off was that same forecast, made the same number of
     hours ahead, from what the market actually settled on? The average miss is the station
     "bias" (e.g. "NBM runs 1.2F cold at Midway"); add it to today's forecast.
  3. How big were the misses (their standard deviation)? That is the spread of the curve.
  4. The curve is a bell curve around (forecast + bias). Settlement is a whole degree, so
     "57 to 58" means the true value lands between 56.5 and 58.5.

Only days whose result was already public at the moment of the forecast are used. Past days
count if they are in the last RECENT_DAYS days, or within SEASON_DAYS of the same date in an
earlier year (a station's bias changes with the season).
"""
import math
import statistics
from dataclasses import dataclass

from scipy.stats import norm

from . import nbm
from .truth import known_before

RECENT_DAYS = 60
SEASON_DAYS = 30
MIN_N = 20           # fewer past errors than this: no bias, NBM's own spread
MIN_SPREAD = 1.0     # deg F; never claim to know a temperature better than this
FALLBACK_SPREAD = 2.5


@dataclass
class Fit:
    bias: float
    spread: float
    n: int
    source: str
    errors: list


def _in_window(d, target):
    if 0 < (target - d).days <= RECENT_DAYS:
        return True
    if d.year < target.year:
        doy = abs(d.timetuple().tm_yday - target.timetuple().tm_yday)
        return min(doy, 365 - doy) <= SEASON_DAYS
    return False


def past_errors(fc_index, truth, kind, target, lead, t):
    """[(day, settled - forecast)] for past days, each with the forecast made `lead` before its column.

    No look-ahead: a day counts only if its result was public before t, and each forecast
    is the one that was published by (that day's column - lead).
    """
    out = []
    for d, s in sorted(known_before(truth, t).items()):
        if not _in_window(d, target):
            continue
        as_of = nbm.target_column(kind, d) - lead
        if as_of > t:
            continue
        f = nbm.forecast_at(fc_index, kind, d, as_of)
        if f is not None:
            out.append((d, s.value - f.txn))
    return out


def fit(errors, nbm_xnd=None):
    errs = [e for _, e in errors]
    if len(errs) < MIN_N:
        spread = max(MIN_SPREAD, (nbm_xnd or 0) * 1.0, FALLBACK_SPREAD if not nbm_xnd else 0)
        return Fit(0.0, spread, len(errs), f"too little history (n={len(errs)}): no bias, NBM spread", errs)
    bias = statistics.fmean(errs)
    spread = max(MIN_SPREAD, statistics.stdev(errs))
    return Fit(bias, spread, len(errs), f"past errors at this lead, n={len(errs)}", errs)


class TempCurve:
    """Normal curve for the settled temperature."""

    def __init__(self, centre, spread):
        self.centre, self.spread = centre, spread

    def cdf(self, x):
        return float(norm.cdf(x, self.centre, self.spread))


def temp_prob(c, cdf):
    """P(contract resolves YES). Same shapes and logic as argus.markets.model_prob, but the
    settlement value is a whole degree, so the continuity correction is half a degree."""
    half = 0.5
    if c.kind == "between":
        return max(0.0, cdf(c.high + half) - cdf(c.low - half))
    if c.kind == "greater":                 # "65 or above" is strike 64, strictly greater
        return 1.0 - cdf(c.low + half)
    if c.kind == "greater_or_equal":
        return 1.0 - cdf(c.low - half)
    if c.kind == "less":                    # "56 or below" is strike 57, strictly less
        return cdf(c.high - half)
    if c.kind == "less_or_equal":
        return cdf(c.high + half)
    raise ValueError(f"Unknown strike type {c.kind!r}")


def lead_of(kind, day, t):
    """How far ahead of its target column a forecast made at t is."""
    return nbm.target_column(kind, day) - t


def isfinite(x):
    return x is not None and math.isfinite(x)
