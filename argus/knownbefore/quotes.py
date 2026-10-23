"""Kalshi prices as they stood at a past moment, and the live order book.

Settled markets come from both tiers (live tier after 2026-08-23, historical before; /historical/cutoff
read 2026-10-22). Hourly candles: the live tier has one event-level request for all markets of an
event; the historical tier only has per-market candles (the batch route GET /markets/candlesticks
returned nothing for historical tickers on 2026-10-22). Quotes are picked with argus.backtest.quote_at,
which never uses a candle that ended after the moment being replayed.
"""
from datetime import datetime, timezone

from .. import backtest as oil_bt
from . import http

MAX_SPREAD = 0.05     # a price counts only with a real bid within 5c of the ask


def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def settled_markets(series):
    """Every settled market of a series from both tiers, tagged with `_tier`."""
    out = {}
    for path, params, tier in (("/historical/markets", {"series_ticker": series, "limit": 1000}, "historical"),
                               ("/markets", {"series_ticker": series, "status": "settled", "limit": 1000}, "live")):
        for m in http.kalshi_paged(path, "markets", params):
            m["_tier"] = tier
            out[m["ticker"]] = m
    return list(out.values())


def event_candles(series, event_ticker, markets, start, end):
    """{market_ticker: [hourly candles]} for one settled event over [start, end]. Cached (cannot change)."""
    params = {"start_ts": int(start.timestamp()), "end_ts": int(end.timestamp()), "period_interval": 60}
    if all(m.get("_tier") == "live" for m in markets):
        data = http.kalshi(f"/series/{series}/events/{event_ticker}/candlesticks", params, cache=True) or {}
        return dict(zip(data.get("market_tickers", []), data.get("market_candlesticks", [])))
    out = {}
    for m in markets:
        path = (f"/historical/markets/{m['ticker']}/candlesticks" if m.get("_tier") == "historical"
                else f"/series/{series}/markets/{m['ticker']}/candlesticks")
        data = http.kalshi(path, params, cache=True) or {}
        out[m["ticker"]] = data.get("candlesticks", [])
    return out


def quote(candles, t):
    """(yes_bid, yes_ask, volume_last_hour) at time t, or None when there is no usable two-sided price.

    Usable = the candle ended at or before t (oil_bt.quote_at), a bid exists, the ask is under $1,
    and the ask is within MAX_SPREAD of the bid (empty books show fake 1c/99c prices).
    """
    bid, ask = oil_bt.quote_at(candles or [], t)
    if not bid or not ask or ask >= 1 or bid <= 0 or ask - bid > MAX_SPREAD + 1e-9:
        return None
    ts = int(t.timestamp())
    last = [c for c in candles if ts - 3600 < c["end_period_ts"] <= ts]
    vol = float(max(last, key=lambda c: c["end_period_ts"]).get("volume") or 0) if last else 0.0
    return bid, ask, vol


def top_of_book(ticker):
    """Live order book top: {yes_bid, yes_ask, size at yes ask, size at yes bid} (contracts)."""
    data = http.kalshi(f"/markets/{ticker}/orderbook") or {}
    ob = data.get("orderbook_fp") or data.get("orderbook") or {}
    yes = [(float(p), float(q)) for p, q in (ob.get("yes_dollars") or [])]
    no = [(float(p), float(q)) for p, q in (ob.get("no_dollars") or [])]
    best_yes = max(yes) if yes else None
    best_no = max(no) if no else None
    return {
        "yes_bid": best_yes[0] if best_yes else None, "yes_bid_size": best_yes[1] if best_yes else 0.0,
        "yes_ask": round(1 - best_no[0], 4) if best_no else None, "yes_ask_size": best_no[1] if best_no else 0.0,
    }
