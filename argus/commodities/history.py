"""Settled commodity events and their past quotes, for replaying strategies with no look-ahead.

Plain words: we list markets that have already settled, then read Kalshi's public candles
(bid, ask and trade prices per minute or per hour). A strategy may only use a candle that
ENDED at or before the moment it pretends to act; trades in candles that end later are used
only to decide whether a resting order would have been filled, and results are used only to
score. Tests in tests/test_commodities.py prove each of these rules.

Live tier only: Kalshi moved markets that settled before 2026-08-23 to a "historical" tier
that has per-market candles only (one request per strike), too slow to read for 100+ series
while sibling sessions share the rate limit. So the sample starts 2026-08-23.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from . import http
from ..model import parse_time

LIVE_SINCE = datetime(2026, 8, 23, tzinfo=timezone.utc)   # /historical/cutoff, read 2026-10-22


def ts(dt):
    return int(dt.timestamp())


def num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def settled_markets(series, since=LIVE_SINCE, until=None):
    """Every settled live-tier market of a series that closed in [since, until).

    The list is cached per calendar day (new results keep arriving; a day-old list is fine)."""
    until = until or datetime.now(timezone.utc)
    params = {"series_ticker": series, "status": "settled", "limit": 1000,
              "min_close_ts": ts(since), "max_close_ts": ts(until.replace(hour=0, minute=0, second=0, microsecond=0))}
    ms = http.paged("/markets", "markets", params, cache=True)
    return [m for m in ms if m.get("result") in ("yes", "no")]


def by_event(markets):
    out = defaultdict(list)
    for m in markets:
        out[m["event_ticker"]].append(m)
    return dict(out)


def event_candles(series, event, start, end, period):
    """{market_ticker: [candles]} for every market of one event, start..end (period 1 or 60 min).

    Kalshi caps how many candles one answer holds and says where it stopped (adjusted_end_ts),
    so we keep asking from there. Settled events never change, so answers are cached."""
    out = defaultdict(list)
    s, e = ts(start), ts(end)
    for _ in range(40):
        d = http.get(f"/series/{series}/events/{event}/candlesticks",
                     {"start_ts": s, "end_ts": e, "period_interval": period}, cache=True)
        if not d:
            break
        for t, cs in zip(d.get("market_tickers", []), d.get("market_candlesticks", [])):
            have = {c["end_period_ts"] for c in out[t]}
            out[t].extend(c for c in cs if c["end_period_ts"] not in have)
        adj = d.get("adjusted_end_ts") or e
        if adj >= e or adj <= s:
            break
        s = adj
    for t in out:
        out[t].sort(key=lambda c: c["end_period_ts"])
    return dict(out)


def _close(side):
    if not side:
        return None
    return num(side.get("close_dollars", side.get("close")))


def quote_at(candles, t, max_age):
    """(yes_bid, yes_ask) Kalshi showed at time t: the closing bid/ask of the latest candle that
    ENDED at or before t, if it ended within max_age seconds. A candle ending after t holds
    prices from the future and is never used. Missing sides come back as None."""
    tt = ts(t)
    ok = [c for c in candles if tt - max_age < c["end_period_ts"] <= tt]
    if not ok:
        return None, None
    c = max(ok, key=lambda c: c["end_period_ts"])
    bid, ask = _close(c.get("yes_bid")), _close(c.get("yes_ask"))
    bid = bid if bid and 0 < bid < 1 else None
    ask = ask if ask and 0 < ask < 1 else None
    return bid, ask


def trades_after(candles, t, until, period_s):
    """(lowest, highest) trade price printed after t, up to `until`; (None, None) if none.

    Only candles whose whole period starts at or after t are used (a candle that began before t
    may hold a trade from before t, so it is skipped: conservative for fills)."""
    lo, hi = None, None
    a, b = ts(t), ts(until)
    for c in candles:
        end = c["end_period_ts"]
        if end - period_s < a or end > b:
            continue
        if num(c.get("volume_fp") or c.get("volume")) in (None, 0, 0.0):
            continue
        p = c.get("price") or {}
        l, h = num(p.get("low_dollars", p.get("low"))), num(p.get("high_dollars", p.get("high")))
        if l is not None:
            lo = l if lo is None else min(lo, l)
        if h is not None:
            hi = h if hi is None else max(hi, h)
    return lo, hi


def yes_won(m):
    return m.get("result") == "yes"


def settle_value(m):
    return num(m.get("expiration_value"))


def close_of(m):
    return parse_time(m["close_time"])


def open_of(m):
    return parse_time(m["open_time"])


def existed_at(m, t):
    return open_of(m) <= t < close_of(m)


def hours_back(close, hours):
    return close - timedelta(hours=hours)
