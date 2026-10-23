"""Turn Kalshi market data into simple "contracts" and do the range maths.

Kalshi oil markets come in three shapes:
  - "between": a range, e.g. $98.00 to $98.99      (floor_strike=98, cap_strike=98.99)
  - "greater": above a strike, e.g. "Above $99.49" (floor_strike=99.49)
  - "less":    below a strike, e.g. "Below $87.00" (cap_strike=87)
Settlement values are rounded to the cent, so every check below works in cents.
"""
from dataclasses import dataclass


def _f(x):
    """Kalshi sends prices as strings like '0.4600'. Missing -> None."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


@dataclass
class Contract:
    ticker: str
    event_ticker: str
    label: str
    kind: str            # "between", "greater" or "less"
    low: float | None    # floor_strike
    high: float | None   # cap_strike
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None
    no_ask: float | None
    close_time: str
    rules: str = ""
    volume_24h: float = 0.0

    @classmethod
    def from_api(cls, m):
        return cls(
            ticker=m["ticker"],
            event_ticker=m["event_ticker"],
            label=m.get("yes_sub_title") or m.get("subtitle") or m["ticker"],
            kind=m.get("strike_type") or "",
            low=_f(m.get("floor_strike")),
            high=_f(m.get("cap_strike")),
            yes_bid=_f(m.get("yes_bid_dollars")),
            yes_ask=_f(m.get("yes_ask_dollars")),
            no_bid=_f(m.get("no_bid_dollars")),
            no_ask=_f(m.get("no_ask_dollars")),
            close_time=m.get("close_time", ""),
            rules=(m.get("rules_primary") or "") + "\n" + (m.get("rules_secondary") or ""),
            volume_24h=_f(m.get("volume_24h_fp")) or 0.0,
        )

    def sort_key(self):
        return self.low if self.low is not None else (self.high or 0) - 1e6


def contains(c, value):
    """Does a settlement value make this contract resolve YES?"""
    v = round(value, 2)
    if c.kind == "between":
        return c.low <= v <= c.high
    if c.kind in ("greater", "greater_or_equal"):
        return v > c.low if c.kind == "greater" else v >= c.low
    if c.kind in ("less", "less_or_equal"):
        return v < c.high if c.kind == "less" else v <= c.high
    raise ValueError(f"Unknown strike type {c.kind!r} on {c.ticker}")


def find_range(contracts, value):
    """Which range (between/tail) contains the value? Used by `settle`."""
    hits = [c for c in contracts if contains(c, value)]
    return hits


def model_prob(c, cdf):
    """Probability the contract resolves YES, given a price CDF.

    Because settlement is rounded to the cent, "$98.00 to $98.99" really means
    the true price lands between 97.995 and 98.995.
    """
    half = 0.005
    if c.kind == "between":
        return max(0.0, cdf(c.high + half) - cdf(c.low - half))
    if c.kind == "greater":                 # strictly above the strike
        return 1.0 - cdf(c.low + half)
    if c.kind == "greater_or_equal":
        return 1.0 - cdf(c.low - half)
    if c.kind == "less":                    # strictly below the strike
        return cdf(c.high - half)
    if c.kind == "less_or_equal":
        return cdf(c.high + half)
    raise ValueError(f"Unknown strike type {c.kind!r}")


def mid(c):
    """Midpoint of the Yes bid and ask, or whichever side exists."""
    b, a = c.yes_bid, c.yes_ask
    if b is not None and a is not None and a > 0:
        return (b + a) / 2
    return a if a else b


# ---------------- ladders ("Above $X" markets) ----------------

def ladder_points(contracts):
    """(strike, P(above strike)) pairs from an "Above $X" ladder, cleaned up.

    Prices are midpoints. P(above) must go DOWN as the strike goes up, so any
    bump caused by a stale quote is flattened.
    """
    pts = sorted((c.low, mid(c)) for c in contracts
                 if c.kind == "greater" and c.low is not None and mid(c) is not None)
    clean, running = [], 1.0
    for k, p in pts:
        running = min(running, p)
        clean.append((k, running))
    return clean


def ladder_prob_above(points, strike):
    """P(price > strike) read off the ladder, straight-line between strikes."""
    if not points:
        return None
    if strike <= points[0][0]:
        return points[0][1]
    if strike >= points[-1][0]:
        return points[-1][1]
    for (k1, p1), (k2, p2) in zip(points, points[1:]):
        if k1 <= strike <= k2:
            w = (strike - k1) / (k2 - k1)
            return p1 + w * (p2 - p1)


def ladder_median(points):
    """The price where the ladder crosses 50%: the crowd's current price."""
    for (k1, p1), (k2, p2) in zip(points, points[1:]):
        if p1 >= 0.5 >= p2 and p1 != p2:
            return k1 + (p1 - 0.5) / (p1 - p2) * (k2 - k1) + 0.005
    return None


def ladder_implied(c, points):
    """What the daily ladder says a weekly range (or tail) is worth."""
    if c.kind == "between":
        a = ladder_prob_above(points, c.low - 0.01)
        b = ladder_prob_above(points, c.high)
        return None if a is None or b is None else max(0.0, a - b)
    if c.kind == "greater":
        return ladder_prob_above(points, c.low)
    if c.kind == "less":
        a = ladder_prob_above(points, c.high - 0.01)
        return None if a is None else 1.0 - a
    return None
