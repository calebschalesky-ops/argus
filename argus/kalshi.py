"""Read-only Kalshi client.

This file can only READ public market data. It has no function that places,
cancels or changes an order, it never sends an API key, and `_get` refuses any
path that is not on the read-only allowlist below. That is on purpose: Argus
never trades.
"""
import time

import requests

BASE = "https://api.elections.kalshi.com/trade-api/v2"

# The only API paths Argus is allowed to touch. All are public, GET-only market data.
READ_ONLY_PREFIXES = ("/series", "/markets", "/events")

_session = requests.Session()
_session.headers["User-Agent"] = "argus-research/0.1 (read-only)"


def _get(path, params=None):
    """Make one GET request to Kalshi's public API and return the JSON."""
    if not path.startswith(READ_ONLY_PREFIXES) or "/portfolio" in path or "/orders" in path:
        raise PermissionError(f"Argus is read-only; refusing path {path}")
    for attempt in range(4):
        r = _session.get(BASE + path, params=params, timeout=30)
        if r.status_code == 429:          # rate limited: wait a little, then retry
            time.sleep(1.5 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


def _paged(path, key, params, max_pages=50):
    """Follow Kalshi's `cursor` to read every page of a list."""
    params = dict(params)
    out = []
    for _ in range(max_pages):
        data = _get(path, params)
        out.extend(data.get(key, []))
        cursor = data.get("cursor")
        if not cursor:
            break
        params["cursor"] = cursor
    return out


def get_series(series_ticker):
    return _get(f"/series/{series_ticker}")["series"]


def all_series():
    return _get("/series")["series"]


def get_markets(series_ticker=None, event_ticker=None, status="open"):
    params = {"limit": 1000}
    if status:
        params["status"] = status
    if series_ticker:
        params["series_ticker"] = series_ticker
    if event_ticker:
        params["event_ticker"] = event_ticker
    return _paged("/markets", "markets", params)


def get_open_events(max_pages=150):
    """Every open event with its markets nested inside (used by the scanner)."""
    return _paged("/events", "events",
                  {"status": "open", "with_nested_markets": "true", "limit": 200},
                  max_pages=max_pages)


def is_fifteen_minute(series):
    """Hard rule: Argus skips every 15-minute market."""
    return series.get("frequency") == "fifteen_min" or series.get("ticker", "").endswith("15M")


def settled_values(series_ticker, max_pages=10):
    """History of official settlement values for a series.

    Returns a list of (close_time, value, contract_note) sorted oldest first, one per event.
    `contract_note` is the rules text, so callers can spot contract rolls.
    """
    markets = _paged("/markets", "markets",
                     {"series_ticker": series_ticker, "status": "settled", "limit": 1000},
                     max_pages=max_pages)
    by_event = {}
    for m in markets:
        v = m.get("expiration_value")
        if v in (None, ""):
            continue
        try:
            value = float(v)
        except ValueError:
            continue
        by_event[m["event_ticker"]] = (m["close_time"], value, m.get("rules_primary", ""))
    return sorted(by_event.values(), key=lambda t: t[0])
