"""Option maths: from Deribit's implied volatilities to the chance BTC/ETH ends above a price.

Plain words. An option's price says how much the market will pay for "BTC above K at time T".
Deribit quotes that as an implied volatility (IV) per strike: a "smile", because far-away
strikes are priced with more volatility than the middle. Here:

  1. Each option trade gives (strike K, expiry T, IV, Deribit's index price S at that moment).
     Its moneyness is u = ln(K / S) / sqrt(years to expiry), so options of different expiries
     sit on one scale.
  2. A smile IV(u) = a + b*u + c*u^2 is fitted through the out-of-the-money points (the liquid side).
  3. For Kalshi's settlement time, the same smile is read at u = ln(K / S) / sqrt(Kalshi's years left):
     volatility per sqrt(year) is assumed flat between now and Deribit's next expiry
     (Deribit lists daily expiries at 08:00 UTC; Kalshi's crypto ladders close at 5 PM New York).
  4. P(price > K) = -dC/dK: the slope of the Black-76 call price across strikes. Taking the slope
     of prices built from the smile includes the skew, which N(d2) alone would miss.
  5. Kalshi settles on a 60-second AVERAGE of CF Benchmarks' index. An average over the last
     minute moves like a price 40 seconds earlier, so 40 s are taken off the horizon.

Rates are taken as zero: over hours to a day, interest moves BTC's forward by a few basis points.
No network here; pure arithmetic.
"""
import math

import numpy as np

YEAR = 365.0 * 86400          # Deribit annualises IV on a 365-day year
AVG_SECONDS = 40.0            # variance of a 60-second average = that of a price 40 s before the end


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def black_call(F, K, tau, sigma):
    """Undiscounted Black-76 call price (same units as F)."""
    if tau <= 0 or sigma <= 0:
        return max(F - K, 0.0)
    sd = sigma * math.sqrt(tau)
    d1 = (math.log(F / K) + 0.5 * sd * sd) / sd
    return F * _ncdf(d1) - K * _ncdf(d1 - sd)


class Smile:
    """IV(u) fitted through option points; u = ln(K/S)/sqrt(tau). IV as a fraction (0.45 = 45%)."""

    def __init__(self, coef, u_lo, u_hi, n_points, n_strikes):
        self.coef = coef            # (a, b, c)
        self.u_lo, self.u_hi = u_lo, u_hi
        self.n_points, self.n_strikes = n_points, n_strikes

    def iv(self, u):
        u = min(max(u, self.u_lo), self.u_hi)   # flat beyond the strikes that actually traded
        a, b, c = self.coef
        return a + b * u + c * u * u

    @property
    def atm(self):
        return self.iv(0.0)


def fit_smile(points, min_strikes=4):
    """Weighted quadratic smile through points [(u, iv, weight, strike), ...]. None if too thin.

    Falls back to a straight line, then to a flat weighted mean, whenever the curve would be
    upside down (c < 0) or negative anywhere inside the traded range."""
    pts = [p for p in points if p[1] and 0.05 < p[1] < 5]
    strikes = {p[3] for p in pts}
    if len(strikes) < min_strikes:
        return None
    u = np.array([p[0] for p in pts])
    v = np.array([p[1] for p in pts])
    w = np.sqrt(np.array([max(p[2], 1e-6) for p in pts]))
    lo, hi = float(u.min()), float(u.max())
    for deg in (2, 1, 0):
        if len(strikes) <= deg:
            continue
        X = np.vander(u, deg + 1, increasing=True)
        coef, *_ = np.linalg.lstsq(X * w[:, None], v * w, rcond=None)
        coef = list(coef) + [0.0] * (3 - len(coef))
        s = Smile(tuple(float(c) for c in coef), lo, hi, len(pts), len(strikes))
        grid = np.linspace(lo, hi, 25)
        if (deg < 2 or coef[2] >= 0) and min(s.iv(g) for g in grid) > 0.05:
            return s
    return None


def prob_above(K, S, tau_years, smile):
    """Risk-neutral P(price at the horizon > K), smile included (slope of call prices)."""
    if tau_years <= 0:
        return 1.0 if S > K else 0.0
    sq = math.sqrt(tau_years)

    def call(k):
        return black_call(S, k, tau_years, smile.iv(math.log(k / S) / sq))

    h = K * 5e-4
    p = -(call(K + h) - call(K - h)) / (2 * h)
    return min(max(p, 1e-6), 1 - 1e-6)


def kalshi_tau(seconds_left):
    """Years of price movement left for a 60-second-average settlement."""
    return max(seconds_left - AVG_SECONDS, 1.0) / YEAR


def market_prob(m, S, tau_years, smile):
    """P(Yes) for one Kalshi crypto market dict (strike_type greater / less / between)."""
    st, lo, hi = m.get("strike_type"), m.get("floor_strike"), m.get("cap_strike")
    if st == "greater" and lo is not None:
        return prob_above(float(lo), S, tau_years, smile)
    if st == "less" and hi is not None:
        return 1 - prob_above(float(hi), S, tau_years, smile)
    if st == "between" and lo is not None and hi is not None:
        p = prob_above(float(lo), S, tau_years, smile) - prob_above(float(hi), S, tau_years, smile)
        return min(max(p, 1e-6), 1 - 1e-6)
    return None
