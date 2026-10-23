"""The baseline probability model.

Plain words: we take today's price, ask "how much does oil usually move per
day?", stretch that over the time left (plus extra for big scheduled events),
and get a curve of where the price could settle. The chance of each Kalshi range
is the share of that curve that falls inside the range.

The curve is a Student-t with 5 degrees of freedom on the log of the price,
not a bell curve, because oil makes big jumps more often than a bell curve says.
"""
import math
import statistics
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from scipy.stats import t as student_t

ET = ZoneInfo("America/New_York")
DEFAULT_DF = 5


# ---------------- volatility ----------------

def log_returns(prices):
    return [math.log(b / a) for a, b in zip(prices, prices[1:]) if a > 0 and b > 0]


def ewma_vol(returns, lam=0.94):
    """Volatility that weights recent days more (RiskMetrics style, lambda 0.94)."""
    if len(returns) < 2:
        return None
    var = statistics.pvariance(returns[:10]) if len(returns) >= 10 else returns[0] ** 2
    for r in returns:
        var = lam * var + (1 - lam) * r * r
    return math.sqrt(var)


def simple_vol(returns):
    return statistics.stdev(returns) if len(returns) >= 2 else None


# ---------------- time ----------------

def trading_days(start, end):
    """Trading days between two times, counting only Monday-Friday hours (New York time).

    24 weekday hours = 1 trading day. Oil futures trade almost 24 hours on weekdays,
    so an hour on a Tuesday counts; an hour on a Saturday does not.
    """
    if end <= start:
        return 0.0
    hours = 0.0
    t = start
    step = timedelta(minutes=30)
    while t < end:
        nxt = min(t + step, end)
        if t.astimezone(ET).weekday() < 5:
            hours += (nxt - t).total_seconds() / 3600
        t = nxt
    return hours / 24


# ---------------- the distribution ----------------

class PriceCurve:
    """Where the settlement price could land.

    anchor:     today's price
    vol_daily:  typical daily move as a fraction (0.024 = 2.4%)
    days:       trading days until settlement
    event_days: extra "days" of movement for scheduled events
    shift:      news shift as a fraction (+0.01 = centre moved up 1%)
    """

    def __init__(self, anchor, vol_daily, days, event_days=0.0, shift=0.0, df=DEFAULT_DF):
        self.anchor = anchor
        self.centre = anchor * math.exp(shift)
        self.df = df
        total_days = max(days + event_days, 1e-6)
        self.sigma = vol_daily * math.sqrt(total_days)
        # a Student-t with df degrees of freedom has variance df/(df-2); scale it so
        # its spread matches sigma
        self.scale = self.sigma * math.sqrt((df - 2) / df)

    def cdf(self, price):
        """Chance the settlement is at or below `price`."""
        if price <= 0:
            return 0.0
        z = (math.log(price) - math.log(self.centre)) / self.scale
        return float(student_t.cdf(z, self.df))


def now_utc():
    return datetime.now(timezone.utc)


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
