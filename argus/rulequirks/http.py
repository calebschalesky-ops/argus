"""Read-only Kalshi GETs for the rule-quirks package: paced, retried, cached, logged.

The only thing this file can do is send a GET, with no credentials, to Kalshi's public
market-data paths below. Every network request (and every refusal) is written to
data/rulequirks/api_calls.csv. Answers that cannot change (settled markets, their candles)
are cached under data/cache/rulequirks/; sibling caches with the same key scheme are read
first, so a market another thread already fetched costs no call.
"""
import csv
import hashlib
import json
import threading
import time
from datetime import datetime, timezone

import requests

from . import CACHE, DATA, SIBLING_CACHES

BASE = "https://api.elections.kalshi.com/trade-api/v2"
ALLOWED = ("/series", "/events", "/markets", "/historical/markets", "/historical/cutoff")
FORBIDDEN = {"portfolio", "order", "orders", "auth", "login", "account", "balance", "fills", "positions"}
CALL_LOG = DATA / "api_calls.csv"
MAX_RPS = 4                      # polite: sibling sessions read the same API

_session = requests.Session()
_session.headers["User-Agent"] = "argus-research/0.1 (read-only)"
_lock, _log_lock = threading.Lock(), threading.Lock()
_last = [0.0]


def check_allowed(path):
    segs = set(path.lower().strip("/").split("/"))
    if not path.startswith(ALLOWED) or segs & FORBIDDEN:
        raise PermissionError(f"Argus is read-only; refusing path {path}")


def _log_call(path, params, status):
    with _log_lock:
        DATA.mkdir(parents=True, exist_ok=True)
        new = not CALL_LOG.exists()
        with open(CALL_LOG, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["at_utc", "method", "url", "params", "status"])
            w.writerow([datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "GET", BASE + path,
                        "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()) if k != "cursor"), status])


def _fetch(path, params):
    for attempt in range(30):
        with _lock:
            wait = _last[0] + 1 / MAX_RPS - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.monotonic()
        try:
            r = _session.get(BASE + path, params=params, timeout=40)
        except requests.RequestException:
            _log_call(path, params, "NETWORK-ERROR")
            time.sleep(3)
            continue
        _log_call(path, params, r.status_code)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(min(3 * (attempt + 1), 60))
            continue
        if r.status_code in (400, 404):
            return {}
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Kalshi kept refusing {path} (rate limited)")


def cache_key(path, params):
    return hashlib.sha1((path + json.dumps(params, sort_keys=True)).encode()).hexdigest()


def get(path, params=None, cache=False):
    """One public GET. cache=True keeps the answer on disk (use only for data that cannot change)."""
    try:
        check_allowed(path)
    except PermissionError:
        _log_call(path, params, "REFUSED")
        raise
    params = dict(params or {})
    if not cache:
        return _fetch(path, params)
    key = cache_key(path, params)
    for d in [CACHE, *SIBLING_CACHES]:
        f = d / f"{key}.json"
        if f.exists():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                pass
    data = _fetch(path, params)
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / f"{key}.json").write_text(json.dumps(data), encoding="utf-8")
    return data


def paged(path, key, params, cache=False, max_pages=500):
    params, out = dict(params), []
    for _ in range(max_pages):
        data = get(path, params, cache=cache) or {}
        out.extend(data.get(key, []))
        if not data.get("cursor"):
            break
        params["cursor"] = data["cursor"]
    return out
