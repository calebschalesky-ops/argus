"""`python -m argus.lab sync`: fill and top up the lake from Kalshi's public API.

Steps, each resumable (progress is saved in the lake's `state` table after every page):
  1. series     one GET /series: categories, frequencies, fee types.
  2. import     sibling sessions' caches under data/cache/ (free; no network).
  3. backfill   per series, every settled market since 2025-04-01, live tier
                (GET /markets?series_ticker=&status=settled) and historical tier
                (GET /historical/markets?series_ticker=, newest first, stopped once older than
                2025-04-01). Measured 2026-10-22: Kalshi settles 100,000+ markets a day, nearly
                all zero-volume strikes of the 78 hourly series (crypto, FX, temperature), so a
                full global listing would take days. Hourly series are listed last and at most
                HEAVY_PAGES pages per run, newest first; everything else is listed in full.
  4. recent     the last few days for every series at once (GET /markets?status=settled with a
                close-time window), so the nightly run picks up new results cheaply.
  5. candles    hourly candles for the last 9 days of every market that traded, via the batch
                route GET /markets/candlesticks?market_tickers=..., ~35 markets per request.
                Days are visited in a fixed shuffled order, so a half-filled lake is an even
                sample across time (not just the newest weeks).
  6. tapes      trade tapes for markets named by the miner (maker rules need proof of fills).
A time budget (--minutes) stops any step cleanly; the next run continues.
"""
import random
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests

from . import http, lake

HEAVY_FREQ = {"hourly"}
HEAVY_PAGES = 10          # pages (1,000 markets each) per heavy series per tier per run
RECENT_DAYS = 3


class Budget:
    def __init__(self, minutes):
        self.end = time.time() + minutes * 60 if minutes else None

    def left(self):
        return self.end is None or time.time() < self.end


def ts(dt):
    return int(dt.timestamp())


def sync_series(con):
    items = (http.get("/series", {}) or {}).get("series", [])
    lake.insert_series(con, items)
    con.commit()
    return len(items)


def series_order(con):
    """Every series worth listing, cheap and important first; hourly ladders last."""
    rows = con.execute("SELECT ticker, frequency FROM series").fetchall()
    rank = {"daily": 0, "weekly": 1, "monthly": 2, "quarterly": 3, "annual": 4, "one_off": 5, "custom": 6}
    keep = [(t, f) for t, f in rows if f != "fifteen_min" and not t.startswith("KXMVE")]
    return sorted(keep, key=lambda x: (x[1] in HEAVY_FREQ, rank.get(x[1], 7), x[0]))


def _list_tier(con, series, tier, freq, budget, stats):
    """Page one series on one tier, saving the cursor after every page."""
    key = f"list:{tier}:{series}"
    st = lake.get_state(con, key, {})
    if st.get("done"):
        return
    path = "/markets" if tier == "live" else "/historical/markets"
    params = {"series_ticker": series, "limit": 1000}
    if tier == "live":
        params.update(status="settled", min_close_ts=ts(lake.SINCE))
    if st.get("cursor"):
        params["cursor"] = st["cursor"]
    pages = 0
    while budget.left():
        d = http.get(path, params) or {}
        items = d.get("markets", [])
        stats["markets"] += lake.insert_markets(con, items, tier)
        stats["pages"] += 1
        pages += 1
        cursor = d.get("cursor") or None
        oldest = min((lake._ts(m.get("close_time")) or 0 for m in items), default=0)
        finished = not cursor or not items or (tier == "historical" and oldest < ts(lake.SINCE))
        lake.set_state(con, key, {"done": finished, "cursor": None if finished else cursor,
                                  "pages": st.get("pages", 0) + pages})
        con.commit()
        if finished or (freq in HEAVY_FREQ and pages >= HEAVY_PAGES):
            return
        params["cursor"] = cursor


def backfill(con, budget, verbose=True):
    stats = defaultdict(int)
    order = series_order(con)
    for i, (s, freq) in enumerate(order):
        if not budget.left():
            break
        for tier in ("live", "historical"):
            if budget.left():
                _list_tier(con, s, tier, freq, budget, stats)
        if verbose and i % 500 == 0:
            print(f"  backfill: series {i}/{len(order)}, {stats['markets']} markets, {stats['pages']} pages", flush=True)
    left = sum(1 for s, _ in order for tier in ("live", "historical")
               if not lake.get_state(con, f"list:{tier}:{s}", {}).get("done"))
    return dict(stats, series_tiers_unfinished=left)


def recent(con, budget, days=RECENT_DAYS, verbose=True):
    """Everything that settled in the last few days, all series at once."""
    fifteen = {r[0] for r in con.execute("SELECT ticker FROM series WHERE frequency='fifteen_min'")}
    now = datetime.now(timezone.utc)
    since = lake.get_state(con, "recent_since")
    start = datetime.fromisoformat(since) if since else now - timedelta(days=days)
    start = min(start, now - timedelta(hours=6))
    params = {"status": "settled", "limit": 1000, "mve_filter": "exclude",
              "min_close_ts": ts(start), "max_close_ts": ts(now)}
    stats = defaultdict(int)

    def page(items, cursor):
        ms = [m for m in items if lake.series_of(m["event_ticker"]) not in fifteen]
        stats["markets"] += lake.insert_markets(con, ms, "recent")
        stats["pages"] += 1
        con.commit()

    while budget.left():
        d = http.get("/markets", params) or {}
        page(d.get("markets", []), d.get("cursor"))
        if not d.get("cursor") or not d.get("markets"):
            lake.set_state(con, "recent_since", (now - timedelta(hours=6)).isoformat())
            con.commit()
            break
        params["cursor"] = d["cursor"]
    return dict(stats)


def _windows(close_ts, open_ts):
    start = max(open_ts or 0, close_ts - lake.CANDLE_DAYS * 86400)
    return start - start % 3600, close_ts


def _chunks(ms):
    """Group markets (sorted by close) so tickers x hours spanned stays under Kalshi's cap."""
    out, cur = [], []
    for m in ms:
        trial = cur + [m]
        s = min(_windows(x["close_ts"], x["open_ts"])[0] for x in trial)
        e = max(x["close_ts"] for x in trial)
        if cur and (len(trial) * (e - s) / 3600 > lake.CANDLE_CAP or len(trial) > 100):
            out.append(cur)
            trial = [m]
        cur = trial
    if cur:
        out.append(cur)
    return out


def _fetch_historical(con, m, stats):
    """Markets that settled before Kalshi's historical cutoff: the batch route returns nothing for
    them (measured 2026-10-22: 14,046 of 17,157 came back empty, including 30,000-contract markets),
    so they are read one by one from /historical/markets/{ticker}/candlesticks."""
    lo, hi = _windows(m["close_ts"], m["open_ts"])
    d = http.get(f"/historical/markets/{m['ticker']}/candlesticks",
                 {"start_ts": lo, "end_ts": hi, "period_interval": 60}) or {}
    cs = [c for c in d.get("candlesticks") or [] if lo < int(c["end_period_ts"]) <= hi]
    lake.insert_candles(con, [lake.candle_row(m["ticker"], 60, c) for c in cs])
    con.execute("INSERT OR REPLACE INTO candle_done VALUES (?,?,?,?,?,?)",
                (m["ticker"], 60, lo, hi, len(cs), "historical"))
    con.commit()
    stats["markets"] += 1
    stats["candles"] += len(cs)
    stats["requests"] += 1


def _fetch_chunk(con, chunk, stats):
    if chunk and chunk[0].get("tier") == "historical":
        for m in chunk:
            _fetch_historical(con, m, stats)
        return
    s = min(_windows(m["close_ts"], m["open_ts"])[0] for m in chunk)
    e = max(m["close_ts"] for m in chunk)
    try:
        d = http.get("/markets/candlesticks", {"market_tickers": ",".join(m["ticker"] for m in chunk),
                                               "start_ts": s, "end_ts": e, "period_interval": 60}) or {}
    except requests.HTTPError as ex:
        if ex.response is not None and ex.response.status_code == 400 and len(chunk) > 1:
            half = len(chunk) // 2
            _fetch_chunk(con, chunk[:half], stats)
            _fetch_chunk(con, chunk[half:], stats)
            return
        stats["failed"] += len(chunk)
        return
    got = {x.get("market_ticker"): x.get("candlesticks") or [] for x in d.get("markets", [])}
    rows, done = [], []
    for m in chunk:
        lo, hi = _windows(m["close_ts"], m["open_ts"])
        cs = [c for c in got.get(m["ticker"], []) if lo < int(c["end_period_ts"]) <= hi]
        rows += [lake.candle_row(m["ticker"], 60, c) for c in cs]
        done.append((m["ticker"], 60, lo, hi, len(cs), lake.now_iso()))
    lake.insert_candles(con, rows)
    con.executemany("INSERT OR REPLACE INTO candle_done VALUES (?,?,?,?,?,?)", done)
    con.commit()
    stats["markets"] += len(chunk)
    stats["candles"] += len(rows)
    stats["requests"] += 1


def candles(con, budget, verbose=True, seed=20261022):
    """Hourly candles for every traded market that has none yet, day by day in a shuffled order."""
    todo = con.execute("""SELECT m.ticker, m.open_ts, m.close_ts, s.frequency, m.tier FROM markets m
                          LEFT JOIN series s ON s.ticker = m.series
                          LEFT JOIN candle_done d ON d.ticker = m.ticker AND d.period = 60
                          WHERE m.volume > 0 AND d.ticker IS NULL""").fetchall()
    by_day = defaultdict(list)
    # order: live tier (about 35 markets per request), then historical tier (one request per market),
    # hourly ladders (75k markets, mostly crypto/FX strikes) last in each
    for t, o, c, f, tier in todo:
        key = (tier == "historical", f in HEAVY_FREQ, c // 86400)
        by_day[key].append({"ticker": t, "open_ts": o, "close_ts": c, "tier": tier})
    rng = random.Random(seed)
    days = []
    for group in ((False, False), (True, False), (False, True), (True, True)):
        part = sorted(d for d in by_day if d[:2] == group)
        rng.shuffle(part)
        days += part
    stats = defaultdict(int)
    t0 = time.time()
    for i, day in enumerate(days):
        if not budget.left():
            break
        for chunk in _chunks(sorted(by_day[day], key=lambda m: m["close_ts"])):
            if not budget.left():
                break
            _fetch_chunk(con, chunk, stats)
        if verbose and i % 10 == 0:
            print(f"  candles: day {i}/{len(days)}, {stats['markets']}/{len(todo)} markets, "
                  f"{stats['requests']} requests, {time.time() - t0:.0f}s", flush=True)
    return dict(stats, markets_waiting=len(todo) - stats["markets"])


def tapes(con, tickers, budget, max_pages=20):
    """Trade tape (every fill) for named markets: live tier /markets/trades, older ones
    /historical/trades. Stored once; a market's tape never changes after it settles."""
    have = {r[0] for r in con.execute("SELECT ticker FROM tape_done")}
    rows = {r[0]: r[1] for r in con.execute("SELECT ticker, close_ts FROM markets")}
    stats = defaultdict(int)
    for t in tickers:
        if t in have or t not in rows or not budget.left():
            continue
        fills, complete = [], True
        paths = ["/markets/trades"] if rows[t] >= ts(lake.HIST_CUTOFF) else ["/historical/trades", "/markets/trades"]
        for path in paths:
            params = {"ticker": t, "limit": 1000}
            for _ in range(max_pages):
                d = http.get(path, params) or {}
                for x in d.get("trades", []):
                    fills.append([lake._ts(x["created_time"]) if "created_time" in x else None,
                                  1 if x.get("taker_side") == "yes" else 0,
                                  lake._f(x.get("yes_price_dollars", x.get("yes_price"))),
                                  lake._f(x.get("count_fp", x.get("count"))), x["trade_id"]])
                if not d.get("cursor") or not d.get("trades"):
                    break
                params["cursor"] = d["cursor"]
            else:
                complete = False
        fills = [f for f in fills if f[0] is not None and f[2] is not None]
        for f in fills:
            if f[2] > 1.5:
                f[2] /= 100
        lake.insert_trades(con, t, fills)
        con.execute("INSERT OR REPLACE INTO tape_done VALUES (?,?,?,?,?)",
                    (t, 1 if complete else 0, len(fills), lake.now_iso(), "lab"))
        con.commit()
        stats["tapes"] += 1
        stats["fills"] += len(fills)
    return dict(stats)


def run(minutes=None, steps=("series", "import", "recent", "candles", "backfill", "candles"), verbose=True, con=None):
    con = con or lake.connect()
    budget = Budget(minutes)
    report = {}
    t0 = time.time()
    for i, step in enumerate(steps):
        name = step if step not in report else f"{step}_{i}"
        if step == "series":
            report[name] = sync_series(con)
        elif step == "import":
            if verbose:
                print("Importing sibling caches ...", flush=True)
            report[name] = lake.import_caches(con, verbose=verbose)
            report[name]["credited_commodity_events"] = lake.mark_imported_coverage(con)
            report[name]["credited_historical"] = lake.credit_historical_imports(con)
        elif not budget.left():
            continue
        elif step == "recent":
            report[name] = recent(con, budget, verbose=verbose)
        elif step == "backfill":
            report[name] = backfill(con, budget, verbose=verbose)
        elif step == "candles":
            report[name] = candles(con, budget, verbose=verbose)
    report["api_calls"] = http.stats["calls"]
    report["http_429"] = http.stats["429"]
    report["minutes"] = round((time.time() - t0) / 60, 1)
    report["counts"] = lake.counts(con)
    report["disk_mb"] = round(lake.disk_bytes() / 1e6, 1)
    lake.set_state(con, "last_sync", {"at": lake.now_iso(), **{k: v for k, v in report.items() if k != "counts"}})
    con.commit()
    return report
