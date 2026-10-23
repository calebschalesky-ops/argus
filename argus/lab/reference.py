"""`python -m argus.lab reference`: the one strategy that passed, re-run through the bench.

research/commodities.md (2026-10-22) found that buying the favourite at 85-97c about 60 minutes
before a Pyth/ICE commodity settle made +2.4% (537 bets, 233 events, 2026-08-23 to 2026-10-22).
The same rule is written here as a bench hypothesis. If the bench is honest it should land
near that number on the same window; the full lake (back to 2025-04-01) is shown beside it.
Only the 60-minute version: the lake keeps hourly candles, so 30 and 15 minutes are not testable.
"""
from datetime import datetime, timezone

from . import bench, lake, stats

FAV_LO, FAV_HI = 0.85, 0.97
SAME_WINDOW = (datetime(2026, 8, 23, tzinfo=timezone.utc), datetime(2026, 10, 22, tzinfo=timezone.utc))


def latefav(m, hist, t):
    """Favourite at 85-97c, one hour before a commodity settle (not AAA gas, not hourly series)."""
    bid, ask = bench.quote(hist, t, bench.max_age(1))
    if bid is None:
        return None
    for side in ("yes", "no"):
        s_ask, _ = bench.side_prices(side, bid, ask)
        if FAV_LO - 1e-9 <= s_ask <= FAV_HI + 1e-9:
            return bench.Call(side=side)
    return None


def where(m):
    return (m.get("category") == "Commodities" and m.get("frequency") not in ("hourly", "fifteen_min")
            and not m["series"].startswith("KXAAAGAS"))


HYPOTHESIS = bench.Hypothesis("reference:latefav-60m", 1, latefav, where)


def run(con=None):
    con = con or lake.connect()
    recs = bench.run_lake([HYPOTHESIS], con, "s.category = 'Commodities'")[HYPOTHESIS.name]
    lo, hi = (int(x.timestamp()) for x in SAME_WINDOW)
    same = [r for r in recs if lo <= r["close_ts"] < hi]
    out = ["Reference check: late-day commodity favourites (85-97c, 60 min before close, taker)",
           "research/commodities.md, 2026-08-23..2026-10-22: 537 bets, 233 events, 96% won at 92.8c, +$12.01, ROI +2.4%",
           stats.format_summary("Bench, same window (closes 2026-08-23..2026-10-21)", stats.summary(same)),
           stats.format_summary("Bench, whole lake (closes 2025-04-01 onward)", stats.summary(recs))]
    return "\n".join(out)
