"""Read-only HTTP for the mentions engine: GET only, no credentials, allowlisted hosts.

  - Kalshi        api.elections.kalshi.com  public market data (series, events, markets, candles)
  - Federal Reserve  www.federalreserve.gov  official FOMC press-conference transcripts (PDF)

Every network request (and every refusal) is written to data/mentions/api_calls.csv.
Answers that can no longer change (candles of settled markets, past transcripts) are cached
under data/cache/mentions/; cache hits are not network calls and are not logged.
"""
import csv
import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests

from . import CACHE, DATA

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
FED = "https://www.federalreserve.gov"

ALLOWLIST = {
    "api.elections.kalshi.com": ("/trade-api/v2/markets", "/trade-api/v2/series", "/trade-api/v2/events",
                                 "/trade-api/v2/historical/"),
    "www.federalreserve.gov": ("/mediacenter/files/", "/monetarypolicy/"),
}
FORBIDDEN_SEGMENTS = {"portfolio", "order", "orders", "auth", "login", "account", "accounts",
                      "balance", "fills", "positions", "private"}
PACE = {"api.elections.kalshi.com": 1 / 4, "www.federalreserve.gov": 1.0}
CALL_LOG = DATA / "api_calls.csv"

_session = requests.Session()
_session.headers["User-Agent"] = "argus-research/0.1 (read-only)"
_lock = threading.Lock()
_last = {}


def check_allowed(url):
    parts = urlsplit(url)
    prefixes = ALLOWLIST.get(parts.hostname or "")
    path = parts.path.lower()
    if parts.scheme != "https" or prefixes is None:
        raise PermissionError(f"Argus mentions is read-only; host not allowed: {url}")
    if not path.startswith(prefixes) or FORBIDDEN_SEGMENTS & set(path.split("/")):
        raise PermissionError(f"Argus mentions is read-only; path not allowed: {url}")


def _log_call(url, params, status):
    DATA.mkdir(parents=True, exist_ok=True)
    new = not CALL_LOG.exists()
    with _lock, open(CALL_LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["at_utc", "method", "url", "params", "status"])
        w.writerow([datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "GET", url,
                    "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()) if k != "cursor"), status])


def _pace(host):
    with _lock:
        wait = _last.get(host, 0) + PACE.get(host, 1.0) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last[host] = time.monotonic()


def _fetch(url, params, raw):
    host = urlsplit(url).hostname
    for attempt in range(20):
        _pace(host)
        try:
            r = _session.get(url, params=params, timeout=40)
        except requests.RequestException:
            _log_call(url, params, "NETWORK-ERROR")
            time.sleep(3)
            continue
        _log_call(url, params, r.status_code)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(min(2 * (attempt + 1), 20))
            continue
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content if raw else r.json()
    raise RuntimeError(f"kept refusing {url} (rate limited)")


def get(url, params=None, cache=False, raw=False):
    """One public GET. cache=True keeps the answer on disk (only for data that cannot change)."""
    try:
        check_allowed(url)
    except PermissionError:
        _log_call(url, params, "REFUSED")
        raise
    params = dict(params or {})
    if not cache:
        return _fetch(url, params, raw)
    key = hashlib.sha1((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()
    f = CACHE / (key + (".bin" if raw else ".json"))
    if f.exists():
        return f.read_bytes() if raw else json.loads(f.read_text(encoding="utf-8"))
    data = _fetch(url, params, raw)
    if data is None:
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    if raw:
        f.write_bytes(data)
    else:
        f.write_text(json.dumps(data), encoding="utf-8")
    return data


def kalshi(path, params=None, cache=False):
    return get(KALSHI + path, params, cache=cache)


def kalshi_paged(path, key, params, cache=False, max_pages=200):
    params, out = dict(params), []
    for _ in range(max_pages):
        data = kalshi(path, params, cache=cache) or {}
        out.extend(data.get(key, []))
        if not data.get("cursor"):
            break
        params["cursor"] = data["cursor"]
    return out
