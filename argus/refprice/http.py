"""Read-only HTTP for reference pricing: GET only, no credentials, allowlisted public endpoints.

  - Kalshi       api.elections.kalshi.com  /trade-api/v2/markets, /events, /series, /historical/markets
  - Deribit      www.deribit.com           /api/v2/public/...   (never /private/)
  - Deribit      history.deribit.com       /api/v2/public/...   (free trade archive)
  - FRED         fred.stlouisfed.org       /graph/fredgraph.csv
  - Atlanta Fed  www.atlantafed.org        public research data files
  - Cboe         cdn.cboe.com              /api/global/...      (public index history files)
Every network request (and every refusal) goes to data/refprice/api_calls.csv. Responses that can
never change (a settled market, trades in a finished hour) may be cached on disk; cache hits are
not network calls and are not logged.
"""
import csv
import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests

from . import CACHE, CALL_LOG, DATA

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
DERIBIT = "https://www.deribit.com/api/v2/public"
DERIBIT_HIST = "https://history.deribit.com/api/v2/public"

ALLOWLIST = {
    "api.elections.kalshi.com": ("/trade-api/v2/markets", "/trade-api/v2/events", "/trade-api/v2/series",
                                 "/trade-api/v2/historical/markets", "/trade-api/v2/historical/cutoff"),
    "www.deribit.com": ("/api/v2/public/",),
    "history.deribit.com": ("/api/v2/public/",),
    "fred.stlouisfed.org": ("/graph/fredgraph.csv",),
    "www.atlantafed.org": ("/",),
    "cdn.cboe.com": ("/api/global/",),
}
FORBIDDEN_SEGMENTS = {"portfolio", "order", "orders", "auth", "login", "account", "accounts", "balance",
                      "balances", "fills", "positions", "private", "wallet", "wallets", "withdraw",
                      "withdrawals", "deposits", "api-keys", "api_keys", "buy", "sell", "cancel"}
# seconds between requests per host. Kalshi's public limit is shared with sibling sessions.
PACE = {"api.elections.kalshi.com": 0.3, "www.deribit.com": 0.1, "history.deribit.com": 0.1}

_session = requests.Session()
_session.headers["User-Agent"] = "argus-research/0.1 (read-only)"
_lock = threading.Lock()
_last = {}
stats = {"calls": 0, "429": 0}


def check_allowed(url):
    """Raise PermissionError unless `url` is an allowlisted public read endpoint."""
    parts = urlsplit(url)
    prefixes = ALLOWLIST.get(parts.hostname or "")
    path = parts.path.lower()
    if parts.scheme != "https" or prefixes is None:
        raise PermissionError(f"Argus refprice is read-only; host not allowed: {url}")
    words = set(path.split("/")) | set(path.replace("/", "_").split("_"))
    if not path.startswith(prefixes) or FORBIDDEN_SEGMENTS & words:
        raise PermissionError(f"Argus refprice is read-only; path not allowed: {url}")


def _log_call(url, params, status):
    DATA.mkdir(parents=True, exist_ok=True)
    with _lock:
        new = not CALL_LOG.exists()
        with open(CALL_LOG, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["at_utc", "method", "url", "params", "status"])
            shown = {k: (v if len(str(v)) < 200 else f"{str(v)[:60]}...({str(v).count(',') + 1} items)")
                     for k, v in (params or {}).items() if k != "cursor"}
            w.writerow([datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "GET", url,
                        "&".join(f"{k}={v}" for k, v in shown.items()), status])


def _pace(host):
    gap = PACE.get(host, 0.5)
    with _lock:
        wait = _last.get(host, 0) + gap - time.monotonic()
        _last[host] = max(time.monotonic(), _last.get(host, 0) + gap)
    if wait > 0:
        time.sleep(wait)


def _cache_file(url, params):
    key = hashlib.sha1((url + json.dumps(params or {}, sort_keys=True)).encode()).hexdigest()
    return CACHE / "http" / key[:2] / f"{key}.json"


def get(url, params=None, cache=False, retries=40, text=False):
    """One GET to an allowlisted public endpoint; parsed JSON (or text), None on 404."""
    try:
        check_allowed(url)
    except PermissionError:
        _log_call(url, params, "REFUSED")
        raise
    f = _cache_file(url, params) if cache else None
    if f is not None and f.exists():
        raw = f.read_text(encoding="utf-8")
        return raw if text else json.loads(raw)
    host = urlsplit(url).hostname
    r = None
    for attempt in range(retries):
        _pace(host)
        try:
            r = _session.get(url, params=params, timeout=60)
        except requests.RequestException as e:
            _log_call(url, params, f"ERROR {type(e).__name__}")
            time.sleep(min(2 ** attempt, 20))
            continue
        stats["calls"] += 1
        _log_call(url, params, r.status_code)
        if r.status_code == 429 or r.status_code >= 500:
            stats["429"] += r.status_code == 429
            time.sleep(min(1.5 * (attempt + 1), 30))   # sibling sessions share the limit: wait
            continue
        if r.status_code == 404:
            return None
        r.raise_for_status()
        if f is not None:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(r.text, encoding="utf-8")
        return r.text if text else r.json()
    if r is not None:
        r.raise_for_status()
    raise RuntimeError(f"no response from {url}")


def kalshi(path, params=None, cache=False):
    return get(KALSHI + path, params, cache)


def deribit(method, params=None, cache=False, history=False):
    """A Deribit public method's `result`, from the live API or the free history archive."""
    d = get((DERIBIT_HIST if history else DERIBIT) + "/" + method, params, cache)
    if d is None or "result" not in d:
        raise RuntimeError(f"Deribit {method} error: {(d or {}).get('error')}")
    return d["result"]
