"""Read-only GETs for the known-before package: paced, retried, cached, logged.

Two kinds of host:
  - Kalshi's public market-data API, only on the read-only paths below, never with credentials.
  - Official public data hosts on the allowlist (NWS, Iowa State's archive of NWS reports, ...).
Every network request (and every refusal) is a row in data/knownbefore/api_calls.csv.
Answers that can no longer change (candles of settled markets, last year's observations)
are cached on disk under data/cache/knownbefore/ so a re-run is free.
"""
import csv
import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from . import CACHE, DATA

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_PATHS = ("/series", "/events", "/markets", "/historical/markets", "/historical/cutoff")
FORBIDDEN = {"portfolio", "order", "orders", "auth", "login", "account", "balance", "fills", "positions"}
# Public, official (or the official record's archive) data hosts. Nothing here needs an account.
HOSTS = {
    "mesonet.agron.iastate.edu",   # Iowa State archive of NWS METAR observations and CLI reports
    "api.weather.gov",             # NWS: live station observations, CLI products
}
CALL_LOG = DATA / "api_calls.csv"
MAX_RPS = {"kalshi": 4, "other": 1}   # polite: sibling sessions read the same Kalshi API

_session = requests.Session()
_session.headers["User-Agent"] = "argus-research/0.1 (read-only research; contact via github)"
_lock = threading.Lock()
_last = {"kalshi": 0.0, "other": 0.0}


def check_kalshi(path):
    segs = set(path.lower().strip("/").split("/"))
    if not path.startswith(KALSHI_PATHS) or segs & FORBIDDEN:
        raise PermissionError(f"Argus is read-only; refusing Kalshi path {path}")


def check_host(url):
    if urlparse(url).hostname not in HOSTS:
        raise PermissionError(f"{urlparse(url).hostname} is not on the known-before allowlist")


def _log_call(url, params, status):
    DATA.mkdir(parents=True, exist_ok=True)
    new = not CALL_LOG.exists()
    with _lock, open(CALL_LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["at_utc", "method", "url", "params", "status"])
        w.writerow([datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "GET", url,
                    "&".join(f"{k}={v}" for k, v in sorted(_items(params)) if k != "cursor"), status])


def _items(params):
    if isinstance(params, dict):
        return params.items()
    return list(params or [])


def _fetch(url, params, lane, as_text=False, tries=14):
    for attempt in range(tries):
        with _lock:
            wait = _last[lane] + 1 / MAX_RPS[lane] - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _last[lane] = time.monotonic()
        try:
            r = _session.get(url, params=params, timeout=60)
        except requests.RequestException:
            _log_call(url, params, "NETWORK-ERROR")
            time.sleep(3)
            continue
        _log_call(url, params, r.status_code)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(min(2 * (attempt + 1), 20))
            continue
        if r.status_code == 404:
            return "" if as_text else {}
        r.raise_for_status()
        return r.text if as_text else r.json()
    raise RuntimeError(f"kept being refused: {url}")


def _cached(key_src, fetch, as_text):
    key = hashlib.sha1(key_src.encode()).hexdigest()
    f = CACHE / (key + (".txt" if as_text else ".json"))
    if f.exists():
        raw = f.read_text(encoding="utf-8")
        return raw if as_text else json.loads(raw)
    data = fetch()
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(data if as_text else json.dumps(data), encoding="utf-8")
    return data


def kalshi(path, params=None, cache=False):
    """One public Kalshi GET. cache=True only for data that cannot change."""
    try:
        check_kalshi(path)
    except PermissionError:
        _log_call(KALSHI + path, params, "REFUSED")
        raise
    params = dict(params or {})
    if not cache:
        return _fetch(KALSHI + path, params, "kalshi")
    return _cached(path + json.dumps(params, sort_keys=True),
                   lambda: _fetch(KALSHI + path, params, "kalshi"), False)


def kalshi_paged(path, key, params, cache=False, max_pages=200):
    params, out = dict(params), []
    for _ in range(max_pages):
        data = kalshi(path, params, cache=cache) or {}
        out.extend(data.get(key, []))
        if not data.get("cursor"):
            break
        params["cursor"] = data["cursor"]
    return out


def public(url, params=None, cache=False, as_text=False):
    """One GET to an allowlisted public data host. `params` may be a list of pairs (repeated keys)."""
    try:
        check_host(url)
    except PermissionError:
        _log_call(url, params, "REFUSED")
        raise
    if not cache:
        return _fetch(url, params, "other", as_text)
    return _cached(url + json.dumps(_items(params) if not isinstance(params, dict) else sorted(params.items())),
                   lambda: _fetch(url, params, "other", as_text), as_text)
