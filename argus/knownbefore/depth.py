"""Top-of-book size on open markets right now: how many contracts sit at the best price.

Candles carry prices and volume but not depth, so the backtest cannot say how many contracts a bet
could have filled. This reads the live market list (yes_ask_size / yes_bid_size at the best price)
for the series each strategy trades. Buying Yes takes the ask size; buying No takes the Yes bid size.
A snapshot, not a history: it says what the book looked like at the moment it ran.
"""
import statistics
from datetime import datetime, timezone

from . import http


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def snapshot(series_list):
    out = []
    for s in series_list:
        for m in http.kalshi_paged("/markets", "markets", {"series_ticker": s, "status": "open", "limit": 1000}):
            b, a = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
            out.append({"series": s, "ticker": m["ticker"], "close": m["close_time"], "yes_bid": b, "yes_ask": a,
                        "ask_size": _f(m.get("yes_ask_size_fp")) or 0.0, "bid_size": _f(m.get("yes_bid_size_fp")) or 0.0,
                        "vol_24h": _f(m.get("volume_24h_fp")) or 0.0})
    return out


def summarize(rows, series):
    """Median contracts at the best price, per side, for markets with a two-sided quote within 5c."""
    rs = [r for r in rows if r["series"] in series and r["yes_bid"] and r["yes_ask"]
          and 0 < r["yes_bid"] < r["yes_ask"] < 1 and r["yes_ask"] - r["yes_bid"] <= 0.05 + 1e-9]
    if not rs:
        return {"markets": 0}
    sizes = [r["ask_size"] for r in rs] + [r["bid_size"] for r in rs]
    dollars = [r["ask_size"] * r["yes_ask"] for r in rs] + [r["bid_size"] * (1 - r["yes_bid"]) for r in rs]
    return {"markets": len(rs), "median_size": statistics.median(sizes), "p25_size": statistics.quantiles(sizes, n=4)[0]
            if len(sizes) > 3 else min(sizes), "median_dollars": statistics.median(dollars),
            "vol_24h": sum(r["vol_24h"] for r in rs)}


def report(rows, groups=None):
    groups = groups or {s: [s] for s in sorted({r["series"] for r in rows})}
    L = [f"Top of book at {datetime.now(timezone.utc):%Y-%m-%d %H:%M}Z (two-sided quotes within 5c only)",
         f"{'group':<22}{'markets':>8}{'median ct':>10}{'25th pct':>9}{'median $':>10}{'24h vol':>10}"]
    for g, ss in groups.items():
        s = summarize(rows, ss)
        if not s["markets"]:
            L.append(f"{g:<22}{0:>8}   (no two-sided quotes)")
            continue
        L.append(f"{g:<22}{s['markets']:>8}{s['median_size']:>10.0f}{s['p25_size']:>9.0f}{s['median_dollars']:>10.0f}{s['vol_24h']:>10.0f}")
    return "\n".join(L)
