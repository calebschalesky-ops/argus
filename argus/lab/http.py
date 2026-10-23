"""Read-only Kalshi GETs for the edge factory: paced, retried, logged.

The only thing this file can do is send a GET, with no credentials, to Kalshi's public
market-data paths below. Every network request (and every refusal) is written to
data/lake/api_calls.csv. Nothing is cached here: the lake itself (lake.py) is the cache.
"""
import csv
import threading
import time
from datetime import datetime, timezone

import requests

from . import CALL_LOG, LAKE

BASE = "https://api.elections.kalshi.com/trade-api/v2"
ALLOWED = ("/series", "/events", "/markets", "/historical/markets", "/historical/trades",
           "/historical/cutoff")
FORBIDDEN = {"portfolio", "order", "orders", "auth", "login", "account", "balance", "fills",
             "positions", "api-keys", "api_keys"}
MAX_RPS = 4                      # polite: sibling sessions read the same API

_session = requests.Session()
_session.headers["User-Agent"] = "argus-research/0.1 (read-only)"
_lock = threading.Lock()
_log_lock = threading.Lock()
_last = [0.0]
stats = {"calls": 0, "429": 0}


def check_allowed(path):
    segs = set(path.lower().strip("/").split("/"))
    if not path.startswith(ALLOWED) or segs & FORBIDDEN:
        raise PermissionError(f"Argus is read-only; refusing path {path}")


def _log_call(path, params, status):
    LAKE.mkdir(parents=True, exist_ok=True)
    with _log_lock:
        new = not CALL_LOG.exists()
        with open(CALL_LOG, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["at_utc", "method", "url", "params", "status"])
            shown = {k: (v if len(str(v)) < 200 else f"{str(v)[:60]}...({str(v).count(',') + 1} items)")
                     for k, v in sorted((params or {}).items()) if k != "cursor"}
            w.writerow([datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "GET", BASE + path,
                        "&".join(f"{k}={v}" for k, v in shown.items()), status])


def get(path, params=None, retries=14):
    """One public GET; parsed JSON, {} on 404. Raises on a 400 (caller may split the request)."""
    try:
        check_allowed(path)
    except PermissionError:
        _log_call(path, params, "REFUSED")
        raise
    params = dict(params or {})
    r = None
    for attempt in range(retries):
        with _lock:
            wait = _last[0] + 1 / MAX_RPS - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.monotonic()
        try:
            r = _session.get(BASE + path, params=params, timeout=60)
        except requests.RequestException as e:
            _log_call(path, params, f"ERROR {type(e).__name__}")
            time.sleep(min(3 * (attempt + 1), 30))
            continue
        stats["calls"] += 1
        _log_call(path, params, r.status_code)
        if r.status_code == 429 or r.status_code >= 500:
            stats["429"] += r.status_code == 429
            time.sleep(min(2 * (attempt + 1), 30))
            continue
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return r.json()
    if r is not None:
        r.raise_for_status()
    raise RuntimeError(f"Kalshi kept refusing {path}")


def paged(path, key, params, max_pages=100000, on_page=None):
    """Follow Kalshi's cursor. With on_page, each page's items go to the callback (with the
    cursor that fetches the NEXT page, so a caller can save it and resume) instead of a list."""
    params, out = dict(params), []
    for _ in range(max_pages):
        data = get(path, params) or {}
        items = data.get(key, [])
        cursor = data.get("cursor") or None
        if on_page:
            on_page(items, cursor)
        else:
            out.extend(items)
        if not cursor or not items:
            break
        params["cursor"] = cursor
    return out
