"""Settled markets (with their rules and results) and the prices Kalshi showed before they closed.

Listing and candle requests copy argus/favorites.py exactly (same paths, same parameters), so a
series or market that thread already read comes out of its cache for free. Requests go through
our own logged client (http.py); nothing here edits another thread's files.

No look-ahead: a price "at time t" is the close of the latest candle that ENDED at or before t,
and it counts only with a real two-sided book (bid within 5c of the ask). The result is used
only to score.
"""
from datetime import datetime, timedelta, timezone

from . import http
from ..favorites import HIST_CUTOFF, HORIZONS, MAX_AGE, early_closed, expiry_gap_days  # noqa: F401 (re-exported)

MAX_SPREAD = 0.05
UNREACHED = []          # markets whose candles Kalshi kept refusing this run


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def ts(dt):
    return int(dt.timestamp())


def series_of(m):
    return m["event_ticker"].split("-", 1)[0]


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def settled_series(series, max_pages=3):
    """Settled markets of one series, historical and live tier (same requests as favorites)."""
    out = {}
    for path, params, tier in (
            ("/historical/markets", {"series_ticker": series, "limit": 1000}, "historical"),
            ("/markets", {"series_ticker": series, "status": "settled", "limit": 1000}, "live")):
        for _ in range(max_pages):
            data = http.get(path, params, cache=True) or {}
            for m in data.get("markets", []):
                m["_tier"] = tier
                out.setdefault(m["ticker"], m)
            if not data.get("cursor"):
                break
            params = dict(params, cursor=data["cursor"])
    return [m for m in out.values() if m.get("result") in ("yes", "no", "scalar")]


def _windows(close):
    c = ts(close)
    return [("hour", 60, c - HORIZONS["1d"] - MAX_AGE["1d"] - 3600, c - HORIZONS["1h"]),
            ("day", 1440, c - HORIZONS["30d"] - MAX_AGE["30d"] - 86400, c - HORIZONS["7d"])]


def candles(m, which=("hour",)):
    """{'hour': [...], 'day': [...]} candles of one market, never later than 1 hour before close."""
    close, out = parse_time(m["close_time"]), {}
    for name, period, start, end in _windows(close):
        if name not in which:
            continue
        if name == "day" and parse_time(m["open_time"]) > close - timedelta(seconds=HORIZONS["7d"]):
            out[name] = []
            continue
        params = {"start_ts": start, "end_ts": end, "period_interval": period}
        path = (f"/historical/markets/{m['ticker']}/candlesticks" if m.get("_tier") == "historical"
                else f"/series/{series_of(m)}/markets/{m['ticker']}/candlesticks")
        try:
            d = http.get(path, params, cache=True)
        except RuntimeError:             # rate-limited 30 times in a row: no price (counted as unpriced, never guessed)
            UNREACHED.append(m["ticker"])
            d = {}
        out[name] = (d or {}).get("candlesticks", [])
    return out


def _px(side):
    if not side:
        return None
    v = num(side.get("close_dollars", side.get("close")))
    return v if v is not None and 0 < v < 1 else None


def book_at(cs, t, max_age):
    """(yes_bid, yes_ask) at time t from the latest candle that ENDED at or before t (and within
    max_age seconds), only if both sides exist and the spread is at most 5c; else (None, None)."""
    tt = ts(t)
    ok = [c for c in cs if tt - max_age < c["end_period_ts"] <= tt]
    if not ok:
        return None, None
    c = max(ok, key=lambda c: c["end_period_ts"])
    bid, ask = _px(c.get("yes_bid")), _px(c.get("yes_ask"))
    if bid is None or ask is None or ask - bid > MAX_SPREAD + 1e-9 or ask < bid:
        return None, None
    return bid, ask


def price_before_close(m, cs, horizon="1h"):
    """(bid, ask) `horizon` before close, if the market was open then and had a real book."""
    close = parse_time(m["close_time"])
    t = close - timedelta(seconds=HORIZONS[horizon])
    if not parse_time(m["open_time"]) <= t:
        return None, None
    key = "hour" if horizon in ("1h", "1d") else "day"
    return book_at(cs.get(key, []), t, MAX_AGE[horizon])


def cached_listings(dirs=None):
    """Every settled market in the cached listing pages of this and sibling threads (no API calls).

    The favorites thread listed ~800 series across every category; those pages are the corpus.
    Tier (live/historical, which decides the candle path) follows Kalshi's cutoff on settlement time."""
    import json
    from . import CACHE, SIBLING_CACHES
    out = {}
    for d in (dirs or [CACHE, *SIBLING_CACHES]):
        if not d.exists():
            continue
        for f in d.glob("*.json"):
            if f.stat().st_size < 200 or f.name.startswith("open_markets"):
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if not isinstance(data, dict) or "markets" not in data:
                continue
            for m in data["markets"]:
                if m.get("result") not in ("yes", "no", "scalar") or m["ticker"] in out:
                    continue
                if m.get("mve_collection_ticker") or m["ticker"].startswith("KXMVE"):
                    continue
                settle = m.get("settlement_ts") or m["close_time"]
                m["_tier"] = "historical" if parse_time(settle) < HIST_CUTOFF else "live"
                out[m["ticker"]] = m
    return list(out.values())
