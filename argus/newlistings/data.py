"""Fetching and the per-market record: prices 1h, 6h and 24h after open, 24h before close, early fills.

Read-only: GET only, public market-data paths only, no credentials. Raw answers are cached on
disk; every network request (and every refusal) goes to CALL_LOG. Series listings reuse the
favorites backtest's cache (same random draw of series), read-only, so they cost no requests.
"""
import csv
import gzip
import hashlib
import json
import random
import threading
import time as _time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

from .. import favorites as fav
from . import CACHE, CALL_LOG, OBS_FILE

BASE = "https://api.elections.kalshi.com/trade-api/v2"
_ALLOWED = ("/series", "/markets", "/historical/markets", "/historical/trades", "/events")
_FORBIDDEN = {"portfolio", "order", "orders", "auth", "login", "account", "balance", "fills", "positions"}
MAX_RPS = 4                    # polite: sibling sessions read the same API

SINCE = datetime(2025, 4, 1, tzinfo=timezone.utc)
UNTIL = datetime(2026, 10, 20, tzinfo=timezone.utc)
EARLY_H = (1, 6, 24)           # hours after open_time
LATE_H = 24                    # hours before close_time
MIN_LIFE_H = 48                # so "24h after open" comes before "24h before close"
MAX_AGE_H = {1: 1, 6: 3, 24: 6, "late": 6}   # a price older than this at the moment does not count
MAX_SPREAD = 0.05              # a real book: bid within 5c of the ask (empty books show 1c/99c)
FILL_WINDOW_H = 24             # a resting order stays up this long
EVENTS_PER_CAT_WEEK = 8
MARKETS_PER_EVENT = 3

_session = requests.Session()
_session.headers["User-Agent"] = "argus-research/0.1 (read-only)"
_lock, _log_lock = threading.Lock(), threading.Lock()
_last = [0.0]


def check_allowed(path):
    segs = set(path.lower().strip("/").split("/"))
    if not path.startswith(_ALLOWED) or segs & _FORBIDDEN:
        raise PermissionError(f"Argus is read-only; refusing path {path}")


def _log_call(path, params, status):
    CACHE.mkdir(parents=True, exist_ok=True)
    with _log_lock:
        new = not CALL_LOG.exists()
        with open(CALL_LOG, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["at_utc", "method", "url", "params", "status"])
            w.writerow([datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "GET", BASE + path,
                        "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()) if k != "cursor"), status])


def _key(path, params):
    return hashlib.sha1((path + json.dumps(params, sort_keys=True)).encode()).hexdigest()


def get(path, params=None, cache=True):
    """One public GET (parsed JSON, {} on 404). Cached unless cache=False (live prices must be fresh)."""
    try:
        check_allowed(path)
    except PermissionError:
        _log_call(path, params, "REFUSED")
        raise
    params = dict(params or {})
    k = _key(path, params)
    mine, theirs = CACHE / "raw" / k[:2] / f"{k}.json.gz", fav.CACHE / f"{k}.json"
    if cache:
        if mine.exists():
            return json.loads(gzip.decompress(mine.read_bytes()))
        if theirs.exists():                     # favorites backtest's cache, same request
            return json.loads(theirs.read_text(encoding="utf-8"))
    r = None
    for attempt in range(30):
        with _lock:
            wait = _last[0] + 1 / MAX_RPS - _time.monotonic()
            if wait > 0:
                _time.sleep(wait)
            _last[0] = _time.monotonic()
        try:
            r = _session.get(BASE + path, params=params, timeout=30)
        except requests.RequestException as e:
            _log_call(path, params, f"ERROR {type(e).__name__}")
            _time.sleep(3)
            continue
        _log_call(path, params, r.status_code)
        if r.status_code == 429 or r.status_code >= 500:
            _time.sleep(min(3 * (attempt + 1), 60))
            continue
        break
    if r is None:
        raise RuntimeError(f"no answer from Kalshi for {path}")
    if r.status_code == 404:
        data = {}
    else:
        r.raise_for_status()
        data = r.json()
    if cache:
        mine.parent.mkdir(parents=True, exist_ok=True)
        mine.write_bytes(gzip.compress(json.dumps(data, separators=(",", ":")).encode()))
    return data


parse_time = fav.parse_time
series_of = fav.series_of


def _ts(dt):
    return int(dt.timestamp())


# ---------------- what kind of market ----------------

def market_type(m, n_in_event):
    if n_in_event <= 1:
        return "single"
    if m.get("strike_type") in ("greater", "less", "between", "greater_or_equal", "less_or_equal"):
        return "numeric ladder"
    return "pick-one list"


def strikes_bin(n):
    return "1" if n <= 1 else "2-5" if n <= 5 else "6-15" if n <= 15 else "16+"


# ---------------- prices at a moment (no look-ahead) ----------------

def book_at(candles, t_ts, max_age_s):
    """{'bid','ask','last'} from the last hourly candle that ENDED at or before t, or None.

    A candle that ends after t holds later prices, so it never counts. Values are closes of that hour.
    """
    ok = [c for c in candles if t_ts - max_age_s < c["end_period_ts"] <= t_ts]
    if not ok:
        return None
    c = max(ok, key=lambda c: c["end_period_ts"])
    return {"bid": fav._px(c.get("yes_bid")), "ask": fav._px(c.get("yes_ask")),
            "last": fav._px(c.get("price")), "age_h": round((t_ts - c["end_period_ts"]) / 3600, 2)}


def real_book(b):
    """True when both sides quote and the bid is within MAX_SPREAD of the ask."""
    return bool(b) and b["bid"] is not None and b["ask"] is not None and 0 <= b["ask"] - b["bid"] <= MAX_SPREAD + 1e-9


def mid(b):
    return round((b["bid"] + b["ask"]) / 2, 4) if real_book(b) else None


def _candles(m, start, end):
    params = {"start_ts": start, "end_ts": end, "period_interval": 60}
    if m["_tier"] == "historical":
        d = get(f"/historical/markets/{m['ticker']}/candlesticks", params)
    else:
        d = get(f"/series/{series_of(m['event_ticker'])}/markets/{m['ticker']}/candlesticks", params)
    return (d or {}).get("candlesticks", []) or []


def candles_for(m):
    """Hourly candles for the first 25 hours of life and around 24h before close: two requests."""
    o, c = _ts(parse_time(m["open_time"])), _ts(parse_time(m["close_time"]))
    early = _candles(m, o, o + (max(EARLY_H) + 1) * 3600)
    late_t = c - LATE_H * 3600
    late = _candles(m, late_t - (MAX_AGE_H["late"] + 1) * 3600, late_t)
    return early, late


def trades_early(m):
    """Fills in the first 1h + FILL_WINDOW_H of life: [[ts, taker_yes, yes_px, count], ...] (one page each tier)."""
    o = _ts(parse_time(m["open_time"]))
    params = {"ticker": m["ticker"], "limit": 1000, "min_ts": o, "max_ts": o + (1 + FILL_WINDOW_H) * 3600}
    fills, seen = [], set()
    paths = []
    if o < _ts(fav.HIST_CUTOFF):
        paths.append("/historical/trades")
    if o + (1 + FILL_WINDOW_H) * 3600 >= _ts(fav.HIST_CUTOFF):
        paths.append("/markets/trades")
    for path in paths:
        for t in (get(path, params) or {}).get("trades", []):
            if t["trade_id"] in seen:
                continue
            seen.add(t["trade_id"])
            fills.append([round(parse_time(t["created_time"]).timestamp(), 3), 1 if t["taker_side"] == "yes" else 0,
                          float(t["yes_price_dollars"]), float(t["count_fp"])])
    return sorted(fills)


def maker_fill(fills, side, limit, t0, t1, strict=False):
    """Would a resting buy at `limit` (side's own price) placed at t0 have filled by t1?

    Only a later TAKER trade that crossed the limit counts. A Yes bid fills when a taker sold Yes
    (taker bought No) at a Yes price at or below the limit; a No bid fills when a taker bought Yes
    at a Yes price at or above 1 - limit. Our bid made a new best price, so we are first at that
    level; strict=True demands a trade strictly through it (no credit for trades at the level).
    """
    for ts, taker_yes, ypx, _n in fills:
        if not t0 < ts <= t1:
            continue
        if side == "yes" and not taker_yes and (ypx < limit - 1e-9 if strict else ypx <= limit + 1e-9):
            return ts
        if side == "no" and taker_yes and (ypx > 1 - limit + 1e-9 if strict else ypx >= 1 - limit - 1e-9):
            return ts
    return None


# ---------------- the sample ----------------

def listed_population(series_per_cat=60):
    """All usable settled markets of the favorites draw of series, with their event sizes."""
    smap = fav.series_map()
    picked = fav.pick_series(smap, series_per_cat)
    raw = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for ms in pool.map(fav.list_series_settled, picked):
            raw.extend(ms)
    n_in_event = defaultdict(int)
    for m in raw:
        n_in_event[m["event_ticker"]] += 1
    usable = [m for m in raw if fav.usable(m, smap)]
    gaps = defaultdict(list)
    for m in usable:
        g = fav.expiry_gap_days(m)
        if g is not None:
            gaps[series_of(m["event_ticker"])].append(g)
    usual_gap = {k: sorted(v)[len(v) // 2] for k, v in gaps.items()}
    return smap, usable, n_in_event, usual_gap


def life_h(m):
    return (parse_time(m["close_time"]) - parse_time(m["open_time"])).total_seconds() / 3600


def in_scope(m):
    o = parse_time(m["open_time"])
    return SINCE <= o and parse_time(m["close_time"]) < UNTIL + timedelta(days=1) and life_h(m) >= MIN_LIFE_H


def sample(markets, smap, events_per_cat=EVENTS_PER_CAT_WEEK, markets_per_event=MARKETS_PER_EVENT, seed=17):
    """Per (open week, category) up to N events, per event up to M markets. Random, fixed seed, blind to prices and results."""
    rng = random.Random(seed)
    by_event = defaultdict(list)
    for m in markets:
        by_event[m["event_ticker"]].append(m)
    groups = defaultdict(list)
    for e, ms in by_event.items():
        o = min(parse_time(x["open_time"]) for x in ms)
        week = (o - timedelta(days=o.weekday())).date().isoformat()
        groups[(week, smap.get(series_of(e), ("Other", ""))[0])].append(e)
    out = []
    for key in sorted(groups):
        evs = sorted(groups[key])
        rng.shuffle(evs)
        for e in evs[:events_per_cat]:
            ms = sorted(by_event[e], key=lambda m: m["ticker"])
            rng.shuffle(ms)
            out.extend(ms[:markets_per_event])
    return out


def had_real_book(early, m, h):
    o = _ts(parse_time(m["open_time"]))
    return real_book(book_at(early, o + h * 3600, MAX_AGE_H[h] * 3600))


def record(m, early, late, fills, category, n_in_event, usual_gap):
    """One row per market: books at each early moment and 24h before close, outcome, early fills."""
    o, c = parse_time(m["open_time"]), parse_time(m["close_time"])
    settle = parse_time(m["settlement_ts"]) if m.get("settlement_ts") else c
    books = {f"h{h}": book_at(early, _ts(o) + h * 3600, MAX_AGE_H[h] * 3600) for h in EARLY_H}
    books["late"] = book_at(late, _ts(c) - LATE_H * 3600, MAX_AGE_H["late"] * 3600)
    return {
        "ticker": m["ticker"], "event": m["event_ticker"], "series": series_of(m["event_ticker"]),
        "category": category, "n_in_event": n_in_event, "type": market_type(m, n_in_event),
        "tier": m["_tier"], "open": o.isoformat(), "close": c.isoformat(), "settle": settle.isoformat(),
        "won": m["result"] == "yes", "early_close": fav.early_closed(m, usual_gap),
        "volume": float(m.get("volume_fp") or 0), "books": books, "fills": fills,
    }


def collect(series_per_cat=60, events_per_cat=EVENTS_PER_CAT_WEEK, markets_per_event=MARKETS_PER_EVENT,
            workers=6, verbose=True):
    smap, usable, n_in_event, usual_gap = listed_population(series_per_cat)
    scoped = [m for m in usable if in_scope(m)]
    chosen = sample(scoped, smap, events_per_cat, markets_per_event)
    if verbose:
        print(f"{len(usable)} usable settled markets, {len(scoped)} opened since {SINCE.date()} and lived 48h+, "
              f"{len(chosen)} sampled; reading candles and early trades ...", flush=True)
    rows, errors, done = [], [], [0]

    def one(m):
        s = series_of(m["event_ticker"])
        try:
            early, late = candles_for(m)
            # trades only matter for the maker test, which needs a real book at 1h or 24h
            fills = trades_early(m) if any(had_real_book(early, m, h) for h in (1, 24)) else []
            r = record(m, early, late, fills, smap.get(s, ("Other", ""))[0], n_in_event[m["event_ticker"]],
                       usual_gap.get(s))
        except Exception as ex:          # one bad market never stops the run
            errors.append((m["ticker"], str(ex)[:120]))
            r = None
        done[0] += 1
        if verbose and done[0] % 250 == 0:
            print(f"  {done[0]}/{len(chosen)}", flush=True)
        return r

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = [r for r in pool.map(one, chosen) if r]
    meta = {"since": SINCE.isoformat(), "until": UNTIL.isoformat(), "series_per_cat": series_per_cat,
            "usable": len(usable), "in_scope": len(scoped), "sampled": len(chosen), "rows": len(rows),
            "errors": len(errors), "error_examples": errors[:5],
            "events_per_cat_week": events_per_cat, "markets_per_event": markets_per_event,
            "scope_by_week": _per_week(scoped)}
    OBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    OBS_FILE.write_text(json.dumps({"meta": meta, "rows": rows}), encoding="utf-8")
    return meta, rows


def _per_week(markets):
    """Listings (in the drawn series) opened per ISO week: for the 'how many a week' estimate."""
    c = defaultdict(int)
    for m in markets:
        o = parse_time(m["open_time"])
        c[(o - timedelta(days=o.weekday())).date().isoformat()] += 1
    return dict(sorted(c.items()))


def load():
    d = json.loads(OBS_FILE.read_text(encoding="utf-8"))
    return d["meta"], d["rows"]
