"""Read-only HTTP helper for the smart-money package.

The only thing this file can do is send a GET request, with no credentials, to a
short list of public read endpoints. `get()` refuses every other host or path, so
there is no way to reach an order, portfolio, wallet or account endpoint from here.
Every request (allowed or refused) is written to smartmoney/api_calls.csv.
"""
import csv
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests

from . import SMART_DIR

POLY_DATA = "https://data-api.polymarket.com"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

# host -> path prefixes that may be read. Public market data only.
ALLOWLIST = {
    "data-api.polymarket.com": (
        "/v2/leaderboard", "/v2/positions", "/v2/trades", "/v2/user-stats",
        "/v2/prices-history", "/v2/resolutions",
    ),
    "api.elections.kalshi.com": (
        "/trade-api/v2/markets/", "/trade-api/v2/series",
    ),
}
# never allowed as a path segment, even under an allowed prefix (matched on whole
# segments, so a market ticker such as KXBALANCEPOWER... is not caught by "balance")
FORBIDDEN_SEGMENTS = {"portfolio", "order", "orders", "auth", "login", "wallet", "wallets",
                      "account", "accounts", "balance", "approvals", "fills", "positions-write"}

CALL_LOG = SMART_DIR / "api_calls.csv"

_session = requests.Session()
_session.headers["User-Agent"] = "argus-research/0.1 (read-only)"


def check_allowed(url):
    """Raise PermissionError unless `url` is a public read endpoint on the allowlist."""
    parts = urlsplit(url)
    path = parts.path.lower()
    prefixes = ALLOWLIST.get(parts.hostname or "")
    if parts.scheme != "https" or prefixes is None:
        raise PermissionError(f"Argus smart-money is read-only; host not allowed: {url}")
    if not path.startswith(prefixes) or FORBIDDEN_SEGMENTS & set(path.split("/")):
        raise PermissionError(f"Argus smart-money is read-only; path not allowed: {url}")


def _log_call(url, params, status, note=""):
    SMART_DIR.mkdir(exist_ok=True)
    new = not CALL_LOG.exists()
    with open(CALL_LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["at_utc", "method", "url", "params", "status", "note"])
        w.writerow([datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "GET", url,
                    "&".join(f"{k}={v}" for k, v in (params or {}).items() if k != "cursor"),
                    status, note])


PACE_SECONDS = 0.25      # small gap between requests so public rate limits are respected


def get(url, params=None, retries=7):
    """One GET to an allowlisted public endpoint; returns the JSON. Only GET exists here."""
    try:
        check_allowed(url)
    except PermissionError:
        _log_call(url, params, "REFUSED")
        raise
    for attempt in range(retries):
        time.sleep(PACE_SECONDS)
        r = _session.get(url, params=params, timeout=30)
        _log_call(url, params, r.status_code)
        if r.status_code == 429 or r.status_code >= 500:   # rate limited / busy: back off, retry
            wait = r.headers.get("Retry-After")
            time.sleep(float(wait) if wait and wait.replace(".", "", 1).isdigit() else min(2 ** attempt, 30))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return None


def poly(path, params=None):
    return get(POLY_DATA + path, params)


def poly_paged(path, params, max_pages=5):
    """Follow Polymarket v2 `pagination.next_cursor`."""
    params = dict(params)
    out = []
    for _ in range(max_pages):
        d = poly(path, params) or {}
        out.extend(d.get("data") or [])
        cur = (d.get("pagination") or {}).get("next_cursor")
        if not cur:
            break
        params["cursor"] = cur
    return out


def kalshi(path, params=None):
    return get(KALSHI + path, params)


def kalshi_paged(path, key, params, max_pages=20):
    params = dict(params)
    out = []
    for _ in range(max_pages):
        d = kalshi(path, params) or {}
        out.extend(d.get(key, []))
        if not d.get("cursor"):
            break
        params["cursor"] = d["cursor"]
    return out
