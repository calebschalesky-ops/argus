"""Settled Mentions markets and their past quotes, for replays with no look-ahead.

Kalshi keeps markets that settled before its cutoff (2026-08-23 on 2026-10-22) in a
"historical" tier with per-market candles; newer ones are in the live tier with per-event
candles. Both are read here. A replay may only use a candle that ENDED at or before the
moment it pretends to act; later candles are used only to decide whether a resting order
was filled, and results only to score.
"""
import json
from collections import defaultdict
from datetime import datetime, timezone

from . import CACHE, DATA, http
from ..model import parse_time

SERIES_FILE = DATA / "series.json"
MARKETS_FILE = CACHE / "settled_markets.json"


def ts(dt):
    return int(dt.timestamp())


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def mention_series(refresh=False):
    """Every series in Kalshi's Mentions category (cached per run date)."""
    if SERIES_FILE.exists() and not refresh:
        return json.loads(SERIES_FILE.read_text(encoding="utf-8"))
    s = (http.kalshi("/series", {"category": "Mentions"}) or {}).get("series", [])
    DATA.mkdir(parents=True, exist_ok=True)
    SERIES_FILE.write_text(json.dumps(s), encoding="utf-8")
    return s


def _slim(m, tier):
    keep = ("ticker", "event_ticker", "title", "yes_sub_title", "custom_strike", "rules_primary",
            "rules_secondary", "open_time", "close_time", "expiration_time", "settlement_ts",
            "occurrence_datetime", "result", "volume_fp", "status", "can_close_early",
            "last_price_dollars", "yes_bid_dollars", "yes_ask_dollars", "open_interest_fp")
    out = {k: m.get(k) for k in keep}
    out["_tier"] = tier
    return out


def series_markets(series):
    """Settled markets (both tiers) and open markets of one series."""
    out = {}
    for path, params, tier in (
            ("/historical/markets", {"series_ticker": series, "limit": 1000}, "historical"),
            ("/markets", {"series_ticker": series, "status": "settled", "limit": 1000}, "live"),
            ("/markets", {"series_ticker": series, "status": "open", "limit": 1000}, "open")):
        for m in http.kalshi_paged(path, "markets", params):
            out[m["ticker"]] = _slim(m, tier)
    return list(out.values())


def load_all(refresh=False, verbose=True):
    """{series: [markets]} for every Mentions series. Cached on disk; refresh re-reads Kalshi."""
    if MARKETS_FILE.exists() and not refresh:
        return json.loads(MARKETS_FILE.read_text(encoding="utf-8"))
    out = {}
    series = mention_series(refresh=refresh)
    for i, s in enumerate(series):
        out[s["ticker"]] = series_markets(s["ticker"])
        if verbose and i % 25 == 0:
            print(f"  {i}/{len(series)} series read", flush=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    MARKETS_FILE.write_text(json.dumps(out), encoding="utf-8")
    return out


def by_event(markets):
    out = defaultdict(list)
    for m in markets:
        out[m["event_ticker"]].append(m)
    return dict(out)


def settled(markets):
    return [m for m in markets if m.get("result") in ("yes", "no")]


def close_of(m):
    return parse_time(m["close_time"])


def open_of(m):
    return parse_time(m["open_time"])


def market_candles(m, start, end, period=60):
    """Candles for one market, start..end. Historical-tier markets have their own route;
    live-tier ones are read through the event route. Settled markets never change: cached."""
    s, e = ts(start), ts(end)
    if m.get("_tier") == "historical":
        d = http.kalshi(f"/historical/markets/{m['ticker']}/candlesticks",
                        {"start_ts": s, "end_ts": e, "period_interval": period}, cache=True) or {}
        return sorted(d.get("candlesticks", []), key=lambda c: c["end_period_ts"])
    series = m["event_ticker"].split("-")[0]
    return event_candles(series, m["event_ticker"], start, end, period).get(m["ticker"], [])


def event_candles(series, event, start, end, period=60):
    out = defaultdict(list)
    s, e = ts(start), ts(end)
    for _ in range(40):
        d = http.kalshi(f"/series/{series}/events/{event}/candlesticks",
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
    """(yes_bid, yes_ask) at time t: the closing bid/ask of the latest candle that ENDED at or
    before t and within max_age seconds of it. Candles ending after t are never used."""
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
    """(lowest, highest) Yes trade price printed strictly after t, up to `until`.
    A candle that began before t is skipped (it may hold a trade from before t)."""
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
