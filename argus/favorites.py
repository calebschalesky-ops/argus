"""Favorites basket backtest: does buying high-probability Yes contracts beat Kalshi's fee?

Plain words: Kalshi is zero-sum, so spreading money across many favorites only lowers
the swings. It makes money only if favorites win MORE often than their price says,
by more than the taker fee. This module checks that on settled markets.

What it does:
  1. Picks series at random in every Kalshi category (no 15-minute series, no multi-leg
     combos) and lists their settled markets from both the live and the historical tier.
     It samples a fixed number of events per category per week so sports do not drown
     everything else, and a fixed number of markets per event.
  2. For each market, reads the Yes ask at 1 hour, 1 day, 7 days and 30 days before close,
     from Kalshi's public candles, using only candles that ENDED at or before that moment.
  3. Buckets those prices (70-80, 80-85, 85-90, 90-95, 95-99c) and scores them against
     the known result after the taker fee (argus/fees.py).
  4. Simulates the weekly basket: buy one contract of every Yes in the bucket, with a cap
     per category per week and per close date.

Run:   python -m argus.favorites                (fetch what is not cached, then report)
       python -m argus.favorites --report-only  (re-report from the saved observations)
       python -m argus.favorites basket         (LIVE paper basket: log today's favorites)
       python -m argus.favorites settle         (score the basket's settled picks)
Raw API responses are cached under data/cache/favorites/ (gitignored); a re-run is free.
Read-only GETs, public data, no key. It never trades.
"""
import argparse
import hashlib
import json
import math
import random
import threading
import time as _time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .fees import taker_fee

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "favorites"
OBS_FILE = ROOT / "data" / "cache" / "favorites_obs.json"
TABLES = ROOT / "research" / "favorites_tables.md"
BASE = "https://api.elections.kalshi.com/trade-api/v2"
HIST_CUTOFF = datetime(2026, 8, 23, tzinfo=timezone.utc)   # /historical/cutoff, read 2026-10-22

HORIZONS = {"1h": 3600, "1d": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
# How old a quote may be and still count as "the price then". Candles only appear when
# something changed, so a quiet market's last candle can be hours old.
MAX_AGE = {"1h": 3 * 3600, "1d": 24 * 3600, "7d": 48 * 3600, "30d": 72 * 3600}
BUCKETS = [("70-80", 70, 79), ("80-85", 80, 84), ("85-90", 85, 89), ("90-95", 90, 94), ("95-99", 95, 99)]
EARLY_CLOSE_DAYS = 60    # closed this long before its latest expiry = an early "it happened" close
EARLY_VS_SERIES_DAYS = 2 # ... or this much earlier than the series normally closes vs its latest expiry
MAX_SPREAD = 0.05        # a price counts only with a two-sided book this tight (empty books show 1c/99c)
MAX_RPS = 4              # polite: sibling sessions read the same API

_ALLOWED = ("/series", "/markets", "/historical/markets")


# ---------------- read-only fetching (cached) ----------------

_session = requests.Session()
_session.headers["User-Agent"] = "argus-research/0.1 (read-only)"
_lock = threading.Lock()
_last = [0.0]


def _get(path, params=None, cache=True):
    """One public GET, cached raw on disk unless cache=False (live prices must be fresh).

    Refuses anything that is not public market data.
    """
    if not path.startswith(_ALLOWED) or "/portfolio" in path or "/orders" in path:
        raise PermissionError(f"Argus is read-only; refusing path {path}")
    params = params or {}
    key = hashlib.sha1((path + json.dumps(params, sort_keys=True)).encode()).hexdigest()
    f = CACHE / f"{key}.json"
    if cache and f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    for attempt in range(30):
        with _lock:
            wait = _last[0] + 1 / MAX_RPS - _time.monotonic()
            if wait > 0:
                _time.sleep(wait)
            _last[0] = _time.monotonic()
        try:
            r = _session.get(BASE + path, params=params, timeout=30)
        except requests.RequestException:
            _time.sleep(3)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            _time.sleep(min(3 * (attempt + 1), 60))
            continue
        break
    if r.status_code == 404:
        data = {}
    else:
        r.raise_for_status()
        data = r.json()
    if cache:
        CACHE.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data), encoding="utf-8")
    return data


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _ts(dt):
    return int(dt.timestamp())


def series_map():
    """series ticker -> (category, frequency)."""
    data = _get("/series", {})
    return {s["ticker"]: (s.get("category") or "Other", s.get("frequency") or "")
            for s in data.get("series", [])}


def series_meta():
    """series ticker -> the full Kalshi series object (category, fee_type, fee_multiplier, ...)."""
    return {x["ticker"]: x for x in _get("/series", {}).get("series", [])}


def series_of(event_ticker):
    return event_ticker.split("-", 1)[0]


def list_series_settled(series, max_pages=3):
    """Settled markets of one series, from both the historical and the live tier.

    (The historical tier ignores close-time filters, so we list by series instead of by day.)
    """
    out = {}
    for path, params, tier in (
            ("/historical/markets", {"series_ticker": series, "limit": 1000}, "historical"),
            ("/markets", {"series_ticker": series, "status": "settled", "limit": 1000}, "live")):
        for _ in range(max_pages):
            data = _get(path, params)
            for m in data.get("markets", []):
                m["_tier"] = tier
                out.setdefault(m["ticker"], m)
            if not data.get("cursor"):
                break
            params = dict(params, cursor=data["cursor"])
    return list(out.values())


def pick_series(smap, per_category, seed=3):
    """A random set of series per category (fixed seed), skipping 15-minute and combo series."""
    rng = random.Random(seed)
    by_cat = defaultdict(list)
    for t, (cat, freq) in smap.items():
        if is_fifteen_minute(t, freq) or t.startswith("KXMVE"):
            continue
        by_cat[cat].append(t)
    out = []
    for cat in sorted(by_cat):
        ts = sorted(by_cat[cat])
        rng.shuffle(ts)
        out.extend(ts[:per_category])
    return out


def is_fifteen_minute(ticker, frequency):
    return frequency == "fifteen_min" or series_of(ticker).endswith("15M")


def usable(m, smap):
    """Settled yes/no, real trading, not a 15-minute market, not a multi-leg combo."""
    if m.get("result") not in ("yes", "no"):
        return False
    if m["ticker"].startswith("KXMVE") or m.get("mve_collection_ticker"):
        return False
    _, freq = smap.get(series_of(m["event_ticker"]), ("Other", ""))
    if is_fifteen_minute(m["event_ticker"], freq):
        return False
    try:
        return float(m.get("volume_fp") or m.get("volume") or 0) > 0
    except ValueError:
        return False


def sample(markets, smap, events_per_cat, markets_per_event, seed=7):
    """Stratified sample: per (week, category) at most N events, per event at most M markets.

    Choices are random with a fixed seed and never look at the result or the price.
    """
    rng = random.Random(seed)
    by_event = defaultdict(list)
    for m in markets:
        by_event[m["event_ticker"]].append(m)
    groups = defaultdict(list)
    for e, ms in by_event.items():
        close = parse_time(ms[0]["close_time"])
        week = (close - timedelta(days=close.weekday())).date().isoformat()
        cat = smap.get(series_of(e), ("Other", ""))[0]
        groups[(week, cat)].append(e)
    out = []
    for key in sorted(groups):
        evs = sorted(groups[key])
        rng.shuffle(evs)
        for e in evs[:events_per_cat]:
            ms = sorted(by_event[e], key=lambda m: m["ticker"])
            rng.shuffle(ms)
            out.extend(ms[:markets_per_event])
    return out


# ---------------- prices at a moment (no look-ahead) ----------------

def _px(side):
    if not side:
        return None
    v = side.get("close_dollars", side.get("close"))
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if 0 < v < 1 else None


def quote_at(candles, t_ts, max_age):
    """(price, source, age_seconds) as of time t_ts, or (None, None, None).

    Only candles that ENDED at or before t count; a candle ending later holds future prices.
    The Yes ask is the price you would pay, and it only counts with a bid within MAX_SPREAD
    under it: an empty book shows a 99c ask over a 1c bid, which is not a favorite.
    If the candle has no ask, the last trade price in that candle is used (marked "last"),
    again only with a bid within MAX_SPREAD under it.
    """
    ok = [c for c in candles if t_ts - max_age < c["end_period_ts"] <= t_ts]
    if not ok:
        return None, None, None
    c = max(ok, key=lambda c: c["end_period_ts"])
    ask, bid = _px(c.get("yes_ask")), _px(c.get("yes_bid"))
    tight = lambda p: bid is not None and p - bid <= MAX_SPREAD + 1e-9
    if ask is not None:
        return (ask, "ask", t_ts - c["end_period_ts"]) if tight(ask) else (None, None, None)
    last = _px(c.get("price"))
    if last is not None and tight(last):
        return last, "last", t_ts - c["end_period_ts"]
    return None, None, None


def _windows(close):
    """Two candle requests per market cover all four horizons: hourly near close, daily further out."""
    c = _ts(close)
    return [("hour", 60, c - HORIZONS["1d"] - MAX_AGE["1d"] - 3600, c - HORIZONS["1h"]),
            ("day", 1440, c - HORIZONS["30d"] - MAX_AGE["30d"] - 86400, c - HORIZONS["7d"])]


def _need_daily(m):
    return parse_time(m["open_time"]) <= parse_time(m["close_time"]) - timedelta(seconds=HORIZONS["7d"])


def candles_for(m):
    """{'hour': [...], 'day': [...]} candles for one market, never later than 1 hour before close."""
    close = parse_time(m["close_time"])
    out = {}
    for name, period, start, end in _windows(close):
        if name == "day" and not _need_daily(m):
            out[name] = []
            continue
        params = {"start_ts": start, "end_ts": end, "period_interval": period}
        if m["_tier"] == "historical":
            data = _get(f"/historical/markets/{m['ticker']}/candlesticks", params)
            out[name] = data.get("candlesticks", []) if data else []
        else:
            s = series_of(m["event_ticker"])
            data = _get(f"/series/{s}/markets/{m['ticker']}/candlesticks", params)
            out[name] = data.get("candlesticks", []) if data else []
    return out


def bucket_of(price):
    """Price bucket label for a Yes price in dollars, or None outside 70-99c."""
    if price is None:
        return None
    cents = int(round(price * 100))
    for label, lo, hi in BUCKETS:
        if lo <= cents <= hi:
            return label
    return None


def expiry_gap_days(m):
    latest = m.get("latest_expiration_time")
    if not latest:
        return None
    return (parse_time(latest) - parse_time(m["close_time"])).total_seconds() / 86400


def early_closed(m, series_gap=None):
    """True if the market closed early, at a moment nobody could have known in advance.

    Every series keeps a fixed gap between close and latest expiry (sports games ~2 weeks,
    daily ranges ~1 week). A market whose gap is well above its series' usual gap, or above
    EARLY_CLOSE_DAYS outright, closed early because the thing happened. For those,
    'N days before close' looks into the future, so they are kept out of the main table
    (their numbers are reported separately).
    """
    gap = expiry_gap_days(m)
    if gap is None:
        return False
    if gap > EARLY_CLOSE_DAYS:
        return True
    return series_gap is not None and gap > series_gap + EARLY_VS_SERIES_DAYS


def observations(m, candles, category, series_gap=None):
    """One row per horizon where the market was open and had a price then."""
    close = parse_time(m["close_time"])
    settle = parse_time(m["settlement_ts"]) if m.get("settlement_ts") else close
    rows = []
    for h, secs in HORIZONS.items():
        t = close - timedelta(seconds=secs)
        if not parse_time(m["open_time"]) <= t < close:
            continue
        cs = candles["hour"] if h in ("1h", "1d") else candles["day"]
        price, src, age = quote_at(cs, _ts(t), MAX_AGE[h])
        if price is None:
            continue
        rows.append({
            "ticker": m["ticker"], "event": m["event_ticker"], "series": series_of(m["event_ticker"]),
            "category": category, "tier": m["_tier"], "horizon": h, "t": t.isoformat(),
            "close": close.isoformat(), "settle": settle.isoformat(), "price": price, "src": src,
            "age_h": round(age / 3600, 2), "bucket": bucket_of(price), "won": m["result"] == "yes",
            "early": early_closed(m, series_gap),
        })
    return rows


# ---------------- scoring ----------------

def profit(price, won):
    """Profit of one Yes contract bought at `price`, after the taker fee."""
    return (1 - price if won else -price) - taker_fee(price)


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return mid - half, mid + half


def cell_stats(rows):
    n = len(rows)
    if not n:
        return None
    wins = sum(r["won"] for r in rows)
    cost = sum(r["price"] + taker_fee(r["price"]) for r in rows)
    pl = sum(profit(r["price"], r["won"]) for r in rows)
    days = sum((parse_time(r["settle"]) - parse_time(r["t"])).total_seconds() / 86400 for r in rows) / n
    roi = pl / cost
    lo, hi = wilson(wins, n)
    return {
        "n": n, "events": len({r["event"] for r in rows}), "win": wins / n, "win_lo": lo, "win_hi": hi,
        "avg_price": sum(r["price"] for r in rows) / n, "breakeven": cost / n,
        "profit": pl / n, "roi": roi, "days": days, "ann": roi * 365 / max(days, 1 / 24),
    }


def table(obs, horizons=HORIZONS, by_category=True):
    """{(bucket, horizon, category): stats}; category 'ALL' pools every category."""
    groups = defaultdict(list)
    for r in obs:
        if not r["bucket"] or r["horizon"] not in horizons:
            continue
        groups[(r["bucket"], r["horizon"], "ALL")].append(r)
        if by_category:
            groups[(r["bucket"], r["horizon"], r["category"])].append(r)
    return {k: cell_stats(v) for k, v in groups.items()}


# ---------------- the weekly basket ----------------

def basket(obs, bucket, horizon="7d", cap_category=5, cap_close_date=3, seed=11):
    """Each week, buy one Yes of every market in `bucket` at `horizon`, within the caps.

    The week is the week of the buy moment. Caps: at most `cap_category` contracts per
    category per week, and at most `cap_close_date` contracts settling on any one date.
    Which ones get dropped by a cap is random (fixed seed), never by result.
    """
    rng = random.Random(seed)
    cand = [r for r in obs if r["bucket"] == bucket and r["horizon"] == horizon and not r["early"]]
    by_week = defaultdict(list)
    for r in cand:
        t = parse_time(r["t"])
        by_week[(t - timedelta(days=t.weekday())).date().isoformat()].append(r)
    bought, per_close = [], Counter()
    for week in sorted(by_week):
        rs = sorted(by_week[week], key=lambda r: r["ticker"])
        rng.shuffle(rs)
        per_cat = Counter()
        for r in rs:
            cd = r["close"][:10]
            if per_cat[r["category"]] >= cap_category or per_close[cd] >= cap_close_date:
                continue
            per_cat[r["category"]] += 1
            per_close[cd] += 1
            bought.append(dict(r, week=week, pl=profit(r["price"], r["won"]),
                               cost=r["price"] + taker_fee(r["price"])))
    return bought


def basket_summary(bought):
    if not bought:
        return None
    month, day, event, cluster = defaultdict(float), defaultdict(float), defaultdict(float), defaultdict(float)
    for b in bought:
        month[b["settle"][:7]] += b["pl"]
        day[b["settle"][:10]] += b["pl"]
        event[b["event"]] += b["pl"]
        cluster[(b["category"], b["close"][:10])] += b["pl"]
    total = sum(b["pl"] for b in bought)
    cost = sum(b["cost"] for b in bought)
    weeks = sorted({b["week"] for b in bought})
    per_week_cost = defaultdict(float)
    for b in bought:
        per_week_cost[b["week"]] += b["cost"]
    days = sum((parse_time(b["settle"]) - parse_time(b["t"])).total_seconds() / 86400 for b in bought) / len(bought)
    worst_cluster = min(cluster.items(), key=lambda kv: kv[1])
    best_cluster = max(cluster.items(), key=lambda kv: kv[1])
    worst_event = min(event.items(), key=lambda kv: kv[1])
    return {
        "contracts": len(bought), "wins": sum(b["won"] for b in bought), "pl": total, "cost": cost,
        "roi": total / cost, "days": days, "weeks": len(weeks),
        "avg_week_cost": sum(per_week_cost.values()) / len(weeks),
        "months": dict(sorted(month.items())), "worst_month": min(month.items(), key=lambda kv: kv[1]),
        "worst_day": min(day.items(), key=lambda kv: kv[1]),
        "worst_cluster": worst_cluster, "best_cluster": best_cluster, "worst_event": worst_event,
        "pl_without_worst_cluster": total - worst_cluster[1],
        "pl_without_best_cluster": total - best_cluster[1],
    }


# ---------------- run + report ----------------

def collect(since, until, series_per_cat, events_per_cat, markets_per_event, verbose=True):
    smap = series_map()
    picked = pick_series(smap, series_per_cat)
    listed, errors = [], []

    def listing(series):
        try:
            return list_series_settled(series)
        except Exception as ex:          # one series that will not load never stops the run
            errors.append((series, str(ex)[:120]))
            return []

    with ThreadPoolExecutor(max_workers=3) as pool:
        for ms in pool.map(listing, picked):
            listed.extend(m for m in ms if usable(m, smap)
                          and since <= parse_time(m["close_time"]) < until + timedelta(days=1))
    gaps = defaultdict(list)
    for m in listed:
        g = expiry_gap_days(m)
        if g is not None:
            gaps[series_of(m["event_ticker"])].append(g)
    usual = {k: sorted(v)[len(v) // 2] for k, v in gaps.items()}     # median gap per series
    chosen = sample(listed, smap, events_per_cat, markets_per_event)
    if verbose:
        print(f"{len(picked)} series listed, {len(listed)} usable settled markets in range, "
              f"{len(chosen)} sampled; reading candles ...", flush=True)
    obs, done = [], [0]

    def one(m):
        try:
            s = series_of(m["event_ticker"])
            rows = observations(m, candles_for(m), smap.get(s, ("Other", ""))[0], usual.get(s))
        except Exception as ex:          # one bad market never stops the run
            errors.append((m["ticker"], str(ex)[:120]))
            rows = []
        done[0] += 1
        if verbose and done[0] % 500 == 0:
            print(f"  {done[0]}/{len(chosen)} markets", flush=True)
        return rows

    with ThreadPoolExecutor(max_workers=8) as pool:
        for rows in pool.map(one, chosen):
            obs.extend(rows)
    meta = {"since": since.isoformat(), "until": until.isoformat(), "series": len(picked),
            "listed": len(listed), "sampled": len(chosen), "errors": len(errors),
            "error_examples": errors[:5], "series_per_cat": series_per_cat, "events_per_cat": events_per_cat,
            "markets_per_event": markets_per_event}
    OBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    OBS_FILE.write_text(json.dumps({"meta": meta, "obs": obs}), encoding="utf-8")
    return meta, obs


def _pct(x, d=1):
    return "n/a" if x is None else f"{x * 100:+.{d}f}%" if x < 0 or d == 2 else f"{x * 100:.{d}f}%"


def report(meta, obs):
    main = [r for r in obs if not r["early"]]
    L = ["# Favorites backtest — generated tables",
         "",
         f"Generated by `python -m argus.favorites`. Sample: settled markets closing {meta['since']} to "
         f"{meta['until']}, {meta['series']} randomly picked series ({meta['series_per_cat']} per category), {meta['listed']} usable markets listed, "
         f"{meta['sampled']} sampled ({meta['events_per_cat']} events per category per week, "
         f"{meta['markets_per_event']} markets per event). {len(obs)} price observations, "
         f"{sum(r['early'] for r in obs)} of them on early-closed markets (kept out of the tables below).",
         "",
         "Columns: n = contracts, ev = distinct events, win = actual Yes rate (95% range), "
         "be = break-even win rate (price + fee), profit = per contract after fee, ROI = per dollar spent, "
         "days = average days the money is locked, ann = ROI x 365 / days (simple, not compounded).",
         ""]
    hdr = "| bucket | horizon | category | n | ev | win | 95% range | avg price | be | profit/contract | ROI | days | ann ROI |"
    sep = "|" + "---|" * 13

    def line(b, h, c, s):
        return (f"| {b} | {h} | {c} | {s['n']} | {s['events']} | {s['win'] * 100:.1f}% | "
                f"{s['win_lo'] * 100:.0f}-{s['win_hi'] * 100:.0f}% | {s['avg_price'] * 100:.1f}c | "
                f"{s['breakeven'] * 100:.1f}% | {s['profit'] * 100:+.2f}c | {s['roi'] * 100:+.2f}% | "
                f"{s['days']:.1f} | {s['ann'] * 100:+.0f}% |")

    t = table(main)
    L += ["## All categories pooled", "", hdr, sep]
    for b, _, _ in BUCKETS:
        for h in HORIZONS:
            s = t.get((b, h, "ALL"))
            if s:
                L.append(line(b, h, "ALL", s))
    cats = sorted({k[2] for k in t if k[2] != "ALL"})
    L += ["", "## By category (cells with n >= 1)", "", hdr, sep]
    for c in cats:
        for b, _, _ in BUCKETS:
            for h in HORIZONS:
                s = t.get((b, h, c))
                if s:
                    L.append(line(b, h, c, s))

    te = table([r for r in obs if r["early"]], by_category=False)
    if te:
        L += ["", "## Early-closed markets (excluded above: horizon measured from a close nobody could foresee)",
              "", hdr, sep]
        for b, _, _ in BUCKETS:
            for h in HORIZONS:
                s = te.get((b, h, "ALL"))
                if s:
                    L.append(line(b, h, "ALL", s))

    L += ["", "## Weekly basket simulation", "",
          "One contract of every Yes in the bucket at the horizon, bought in the week of the buy moment; "
          "at most 5 per category per week and 3 per close date. P/L after fees, booked on the settle date.", "",
          "| bucket | horizon | contracts | won | P/L | cost | ROI | worst month | worst day | worst cluster (category, close date) | P/L without it |",
          "|" + "---|" * 11]
    sims = {}
    for b, _, _ in BUCKETS:
        for h in HORIZONS:
            s = basket_summary(basket(main, b, h))
            if not s:
                continue
            sims[(b, h)] = s
            wc = s["worst_cluster"]
            L.append(f"| {b} | {h} | {s['contracts']} | {s['wins']} | ${s['pl']:+.2f} | ${s['cost']:.2f} | "
                     f"{s['roi'] * 100:+.2f}% | {s['worst_month'][0]} ${s['worst_month'][1]:+.2f} | "
                     f"{s['worst_day'][0]} ${s['worst_day'][1]:+.2f} | {wc[0][0]} {wc[0][1]} ${wc[1]:+.2f} | "
                     f"${s['pl_without_worst_cluster']:+.2f} |")
    L += ["", "### Monthly P/L per basket (one contract per pick)", ""]
    for (b, h), s in sims.items():
        if h in ("1d", "7d"):
            L.append(f"- **{b} @ {h}**: " + ", ".join(f"{m} ${v:+.2f}" for m, v in s["months"].items())
                     + f"  (best cluster {s['best_cluster'][0][0]} {s['best_cluster'][0][1]} "
                       f"${s['best_cluster'][1]:+.2f}; without it ${s['pl_without_best_cluster']:+.2f})")
    srcs = Counter(r["src"] for r in main if r["bucket"])
    L += ["", f"Price source for bucketed observations: {dict(srcs)} (ask = Yes ask; last = last trade, no ask in that candle).",
          f"Fetch errors: {meta['errors']} {meta['error_examples']}", ""]
    return "\n".join(L), sims


# ---------------- live paper basket (logs today's favorites; never trades) ----------------

FAV_LOG = ROOT / "favorites_log.csv"
LIVE_BAND = (0.80, 0.95)      # side price band (Caleb's 80-95c; see research/favorites_backtest.md)
MIN_VOL_24H = 500
LIVE_DAYS = 7
CAP_CATEGORY, CAP_CLOSE_DATE, CAP_EVENT = 3, 2, 2
ENGINE_TAKER, ENGINE_MAKER = "favorites-basket", "favorites-basket-maker"


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if 0 < v < 1 else None


def open_markets(now, days=LIVE_DAYS, max_pages=40):
    """Open, non-combo markets closing within `days`, read fresh (never from the cache)."""
    params = {"status": "open", "limit": 1000, "mve_filter": "exclude",
              "min_close_ts": _ts(now), "max_close_ts": _ts(now + timedelta(days=days))}
    out = []
    for _ in range(max_pages):
        data = _get("/markets", params, cache=False)
        out.extend(data.get("markets", []))
        if not data.get("cursor"):
            break
        params = dict(params, cursor=data["cursor"])
    return out


def backtest_hit_rates(path=OBS_FILE, horizon="7d"):
    """bucket -> measured win rate in the backtest (pooled, early closes excluded), if it has been run."""
    if not Path(path).exists():
        return {}
    obs = [r for r in json.loads(Path(path).read_text(encoding="utf-8"))["obs"] if not r["early"]]
    t = table(obs, horizons=(horizon,), by_category=False)
    return {b: s["win"] for (b, h, c), s in t.items() if s and s["n"] >= 30}


def candidates(markets, smeta, band=LIVE_BAND, min_vol=MIN_VOL_24H):
    """Every (market, side) whose ask sits in the band, liquid, not a 15-minute market."""
    lo, hi = band
    out = []
    for m in markets:
        s = smeta.get(series_of(m["event_ticker"]), {})
        if is_fifteen_minute(m["event_ticker"], s.get("frequency", "")) or m["ticker"].startswith("KXMVE"):
            continue
        try:
            vol = float(m.get("volume_24h_fp") or 0)
        except ValueError:
            vol = 0
        if vol < min_vol:
            continue
        for side, ask, bid in (("YES", _f(m.get("yes_ask_dollars")), _f(m.get("yes_bid_dollars"))),
                               ("NO", _f(m.get("no_ask_dollars")), _f(m.get("no_bid_dollars")))):
            if ask is None or ask < 0.10 or not lo - 1e-9 <= ask <= hi + 1e-9:
                continue
            if bid is None or ask - bid > MAX_SPREAD + 1e-9:      # no real book behind the ask
                continue
            out.append({"m": m, "side": side, "ask": ask, "bid": bid, "vol": vol,
                        "category": s.get("category") or "Other", "series": series_of(m["event_ticker"])})
    return out


def pick(cands, cap_category=CAP_CATEGORY, cap_close=CAP_CLOSE_DATE, cap_event=CAP_EVENT):
    """Most-traded first, within the caps on category, close date and event. One side per market."""
    per_cat, per_close, per_event, seen, out = Counter(), Counter(), Counter(), set(), []
    for c in sorted(cands, key=lambda c: (-c["vol"], c["m"]["ticker"], c["side"])):
        m = c["m"]
        cd = m["close_time"][:10]
        if (m["ticker"] in seen or per_cat[c["category"]] >= cap_category or per_close[cd] >= cap_close
                or per_event[m["event_ticker"]] >= cap_event):
            continue
        seen.add(m["ticker"])
        per_cat[c["category"]] += 1
        per_close[cd] += 1
        per_event[m["event_ticker"]] += 1
        out.append(c)
    return out


def maker_price(ask, bid):
    """A resting limit at bid + 1c that does not cross the ask, or None if there is no room."""
    if bid is None:
        return None
    p = round(min(bid + 0.01, ask - 0.01), 2)
    return p if p >= bid and p > 0 else None


def basket_rows(picks, smeta, now, hit_rates):
    """Two log rows per pick: taking the ask, and a maker limit at bid + 1c."""
    from .fees import maker_fee, series_maker_fees
    rows = []
    for c in picks:
        m = c["m"]
        charged, mult = series_maker_fees(smeta.get(c["series"]))
        close = parse_time(m["close_time"])
        label = m.get("yes_sub_title") or m.get("title") or ""
        for engine, price, fee in (
                (ENGINE_TAKER, c["ask"], taker_fee(c["ask"], 1, mult)),
                (ENGINE_MAKER, maker_price(c["ask"], c["bid"]), None)):
            if price is None:
                continue
            if fee is None:
                fee = maker_fee(price, 1, mult, charged)
            prob = hit_rates.get(bucket_of(price), price)
            rows.append({
                "logged_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "engine": engine, "series": c["series"],
                "event_ticker": m["event_ticker"], "market_ticker": m["ticker"], "range_label": label[:80],
                "side": c["side"], "model_prob": f"{prob:.4f}", "market_price": f"{price:.2f}",
                "gap_pts": f"{(prob - price - fee) * 100:.1f}", "worth_a_look": "",
                "paper_contracts": "1", "fee_per_contract": f"{fee:.2f}", "anchor_price": "",
                "anchor_kind": "backtest hit rate for the price bucket" if bucket_of(price) in hit_rates else "price",
                "horizon_days": f"{(close - now).total_seconds() / 86400:.2f}",
                "close_time_utc": m["close_time"], "status": "pending",
                "notes": (f"category={c['category']}; vol24h={c['vol']:.0f}; bid={c['bid']}; ask={c['ask']}; "
                          f"maker_fee_series={charged}; fee_mult={mult}; "
                          f"pricing={'taker' if engine == ENGINE_TAKER else 'maker limit bid+1c, fill not guaranteed'}"),
            })
    return rows


def run_basket(now=None, path=FAV_LOG, band=LIVE_BAND):
    from . import log
    now = now or datetime.now(timezone.utc)
    smeta = series_meta()
    cands = candidates(open_markets(now), smeta, band)
    picks = pick(cands)
    pending = {(r["market_ticker"], r["engine"]) for r in log.read_log(path) if r["status"] != "settled"}
    rows = [r for r in basket_rows(picks, smeta, now, backtest_hit_rates())
            if (r["market_ticker"], r["engine"]) not in pending]
    if rows:
        log.append(rows, path)
    return cands, picks, rows


def settle_log(path=FAV_LOG):
    """Settle pending basket rows from Kalshi's results, charging the fee stored on each row."""
    from . import fees, log
    rows = log.read_log(path)
    events = sorted({r["event_ticker"] for r in rows if r["status"] != "settled"})
    n = 0
    for ev in events:
        ms = _get("/markets", {"event_ticker": ev, "limit": 1000}, cache=False).get("markets", [])
        done = {m["ticker"]: m["result"] == "yes" for m in ms if m.get("result") in ("yes", "no")}
        for r in rows:
            if r["event_ticker"] != ev or r["status"] == "settled" or r["market_ticker"] not in done:
                continue
            won = done[r["market_ticker"]] if r["side"] == "YES" else not done[r["market_ticker"]]
            k = int(r["paper_contracts"] or 0)
            r.update(status="settled", settle_value="", won="1" if won else "0",
                     pnl_after_fees=f"{fees.pnl(won, float(r['market_price']), k, fee=float(r['fee_per_contract']) * k):.2f}")
            n += 1
    log.write_log(rows, path)
    return n, len(events)


def main(argv=None):
    argv = list(argv if argv is not None else __import__("sys").argv[1:])
    if argv[:1] == ["basket"]:
        cands, picks, rows = run_basket()
        by_cat = Counter(c["category"] for c in cands)
        print(f"{len(cands)} liquid favorites at {LIVE_BAND[0] * 100:.0f}-{LIVE_BAND[1] * 100:.0f}c closing within "
              f"{LIVE_DAYS} days: {dict(by_cat.most_common())}")
        print(f"Picked {len(picks)} after caps; logged {len(rows)} new rows to {FAV_LOG.name} (paper only)\n")
        for c in picks:
            m = c["m"]
            print(f"  {c['category']:<22} {c['side']:<3} {c['ask'] * 100:4.0f}c (bid {(c['bid'] or 0) * 100:.0f}c) "
                  f"closes {m['close_time'][:16]}  {m['ticker']}  {(m.get('yes_sub_title') or '')[:40]}")
        return
    if argv[:1] == ["settle"]:
        n, ne = settle_log()
        print(f"Settled {n} rows across {ne} pending events in {FAV_LOG.name}")
        return
    p = argparse.ArgumentParser(prog="python -m argus.favorites",
                                description="Backtest a basket of high-probability Yes contracts (paper only). "
                                            "Also: `basket` logs today's live paper picks, `settle` scores them.")
    p.add_argument("--since", default="2025-04-01", help="first close day (YYYY-MM-DD)")
    p.add_argument("--until", default="2026-10-20", help="last close day (YYYY-MM-DD)")
    p.add_argument("--series-per-cat", type=int, default=60, help="series picked at random per category")
    p.add_argument("--events-per-cat", type=int, default=6, help="events sampled per category per week")
    p.add_argument("--markets-per-event", type=int, default=3, help="markets sampled per event")
    p.add_argument("--report-only", action="store_true", help="re-report from data/cache/favorites_obs.json")
    a = p.parse_args(argv)
    if a.report_only:
        saved = json.loads(OBS_FILE.read_text(encoding="utf-8"))
        meta, obs = saved["meta"], saved["obs"]
    else:
        since = datetime.fromisoformat(a.since).replace(tzinfo=timezone.utc)
        until = datetime.fromisoformat(a.until).replace(tzinfo=timezone.utc)
        meta, obs = collect(since, until, a.series_per_cat, a.events_per_cat, a.markets_per_event)
    text, _ = report(meta, obs)
    TABLES.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nWrote {TABLES}")


if __name__ == "__main__":
    main()
