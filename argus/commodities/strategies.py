"""Four structural commodity strategies, replayed on settled Kalshi events with no look-ahead.

None of these forecasts the price of gold or oil (that lost: see SPEC THREAD-LOG). Each one
looks for a STRUCTURAL reason a price is wrong, and each is logged as its own engine so
`python -m argus board` scores it with the same gate as everything else.

  a. consistency  Markets that settle on the same number must agree. Two kinds:
                  - pair:   two series on the same settlement (e.g. WTI weekly vs WTI daily on Friday,
                            gold hourly 5 PM vs gold daily). Pairs are FOUND, not assumed: two series
                            count only if their settlement values were identical on 90%+ of the
                            dates both settled, on 3+ dates.
                  - ladder: inside one event: a ladder that is not monotone, ranges whose asks sum
                            under $1, or whose No asks pay more than they cost.
                  A "lock" is a set of contracts that pays at least $1 whatever the settlement
                  value, bought at the asks, costing at most 99c including a 1-contract taker fee
                  on every leg. The first hour a lock shows, we paper-buy it once (one per event).
  b. latefav      15, 30 and 60 minutes before a settle: buy the favourite side at 85-97c (taker).
  c. maker        On strikes near the money (Yes mid 25-75c) rest a buy at bid + 1c on each side
                  (commodity series charge no maker fee). It counts as filled only if a LATER
                  trade printed strictly through the limit before the close.
  d. buckets      Every two-sided quote at the replay time, both sides, by price bucket: taker at
                  the ask, and maker at bid + 1c with the same fill rule as (c).

model_prob (what the board calls the model) is walk-forward: the win rate of the same strategy
and price bucket on earlier observations that had ALREADY SETTLED at the moment of the call
(Laplace-smoothed); below 30 such observations it is just the price paid. So "better Brier than
the market" means the strategy's own track record, known at the time, beat the price.

Run:   python -m argus.commodities backtest [--only consistency latefav maker buckets]
       python -m argus.commodities report
Read-only, public data, no key. It never trades.
"""
import bisect
import csv
import hashlib
import json
import random
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone

from . import DATA, history as H, http
from .scan import commodity_series, group_of
from .. import fees, log
from ..markets import Contract
from ..model import ET

ROOT = log.ROOT
LOGS = {"consistency": ROOT / "commodities_consistency_log.csv",
        "latefav": ROOT / "commodities_latefav_log.csv",
        "maker": ROOT / "commodities_maker_log.csv",
        "buckets": ROOT / "commodities_buckets_log.csv"}
REPORT_JSON = DATA / "backtest_summary.json"

LOCK_MIN_PROFIT = 0.01           # a lock must clear at least 1c per set after every leg's fee
LATE_MINUTES = (60, 30, 15)
FAV_LO, FAV_HI = 0.85, 0.97
NEAR_LO, NEAR_HI = 0.25, 0.75    # "near the money" for the maker test (Yes mid)
BUCKETS = [("<10c", 0.00, 0.10), ("10-30c", 0.10, 0.30), ("30-50c", 0.30, 0.50),
           ("50-70c", 0.50, 0.70), ("70-90c", 0.70, 0.90), ("90-99c", 0.90, 1.00)]
BUCKET_MARKETS_PER_EVENT = 8     # cap rows per event (fixed-seed sample) so one event cannot dominate
WALK_MIN_N = 30
HOURLY_WINDOW_DAYS = 7           # consistency replays the last week of each event, hour by hour


# ---------------- small helpers ----------------

def fee1(price):
    """Taker fee on ONE contract (rounded up to the cent: at least 1c on any price 1-99c)."""
    return fees.taker_fee(price, 1)


def bucket_of(price, buckets=BUCKETS):
    for label, lo, hi in buckets:
        if lo - 1e-9 <= price < hi - 1e-9:
            return label
    return None


def call_id(*parts):
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:10]


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def contract(m, bid=None, ask=None):
    c = Contract.from_api(m)
    c.yes_bid, c.yes_ask = bid, ask
    c.no_ask = round(1 - bid, 4) if bid else None
    c.no_bid = round(1 - ask, 4) if ask else None
    return c


def close_gap(m):
    """Time from trading close to the expected settlement reading. Zero for Pyth/ICE ladders;
    about 10 hours for AAA gas (trading stops 11:59 PM, AAA is read at 10 AM)."""
    exp = m.get("expected_expiration_time") or m.get("close_time")
    return H.parse_time(exp) - H.close_of(m)


def usual_gaps(events):
    """Median close-to-reading gap per series: the series' normal schedule."""
    by = defaultdict(list)
    for s, ms in events.values():
        by[s] += [close_gap(m).total_seconds() for m in ms]
    return {s: timedelta(seconds=statistics.median(v)) for s, v in by.items()}


def early_closed(m, usual=timedelta(0)):
    """Closed an hour or more earlier than its series normally does (an 'it happened' close):
    a moment measured back from that close could not have been chosen in advance, so skip it."""
    return close_gap(m) > usual + timedelta(hours=1)


def replay_time(m, frequency):
    """One moment per event for the maker and bucket tests, fixed by the calendar only.

    daily: 10:00 New York on the settle day (the day before if it settles before 1 PM);
    weekly: 10:00 two days before; monthly and longer: 10:00 seven days before."""
    close = H.close_of(m).astimezone(ET)
    back = {"daily": 0, "weekly": 2}.get(frequency, 7)
    d = close.date() - timedelta(days=back)
    if back == 0 and close.hour < 13:
        d -= timedelta(days=1)
    return datetime.combine(d, time(10, 0), tzinfo=ET).astimezone(timezone.utc)


def row(engine, series, m, side, price, t, won, fee, contracts=1, model_prob=None, notes="", value=None,
        status="settled"):
    pnl = fees.pnl(won, price, contracts, fee=fee * contracts) if contracts else 0.0
    mp = price if model_prob is None else model_prob
    return {"call_id": call_id(engine, m["ticker"], side, iso(t)), "logged_at_utc": iso(t), "engine": engine,
            "series": series, "event_ticker": m["event_ticker"], "market_ticker": m["ticker"],
            "range_label": m.get("yes_sub_title") or m["ticker"], "side": side,
            "model_prob": f"{mp:.4f}", "market_price": f"{price:.4f}", "gap_pts": f"{(mp - price) * 100:.1f}",
            "worth_a_look": "1" if contracts else "0", "paper_contracts": str(contracts),
            "fee_per_contract": f"{fee:.2f}", "anchor_price": "", "anchor_kind": "", "vol_daily": "",
            "horizon_days": "", "event_days": "", "news_shift_pct": "", "close_time_utc": m["close_time"],
            "status": status, "settle_value": "" if value is None else f"{value:.4f}".rstrip("0").rstrip("."),
            "won": "1" if won else "0", "pnl_after_fees": f"{pnl:.2f}", "notes": notes}


def family(series):
    """'aaa' for AAA state gas averages (a once-a-day survey, half-cent strikes, wide quotes);
    '' for exchange-priced ladders (Pyth 1-minute closes, ICE settles). Reported apart because
    they behave differently; the split was made after the first run (see research/commodities.md)."""
    return "aaa-" if series.startswith("KXAAAGAS") else ""


def side_won(m, side):
    return H.yes_won(m) if side == "YES" else not H.yes_won(m)


# ---------------- walk-forward model ----------------

def walk_forward(rows, key, min_n=WALK_MIN_N):
    """Set each row's model_prob to the smoothed win rate of earlier rows with the same key that
    had settled (close_time <= this row's call time). Rows with fewer than min_n such earlier
    rows keep the price as their model. Rows with paper_contracts 0 are not counted as evidence."""
    groups = defaultdict(list)
    for r in rows:
        if r["status"] == "settled" and int(r["paper_contracts"]) > 0:
            groups[key(r)].append((H.parse_time(r["close_time_utc"]).timestamp(), int(r["won"])))
    index = {}
    for k, xs in groups.items():
        xs.sort()
        closes = [x[0] for x in xs]
        pref = [0]
        for _, w in xs:
            pref.append(pref[-1] + w)
        index[k] = (closes, pref)
    for r in rows:
        k = key(r)
        if k not in index:
            continue
        closes, pref = index[k]
        n = bisect.bisect_right(closes, H.parse_time(r["logged_at_utc"]).timestamp())
        if n >= min_n:
            p = (pref[n] + 1) / (n + 2)
            r["model_prob"] = f"{p:.4f}"
            r["gap_pts"] = f"{(p - float(r['market_price'])) * 100:.1f}"
            r["notes"] += f"; prior_n={n}"
    return rows


# ---------------- data collection ----------------

def series_table():
    return {s["ticker"]: s for s in commodity_series()}


def load_events(table, frequencies=None, verbose=True):
    """{event_ticker: (series, [markets])} of settled live-tier events, all non-15-minute series."""
    out = {}
    names = [t for t, s in sorted(table.items()) if not frequencies or s.get("frequency") in frequencies]
    for i, t in enumerate(names):
        for e, ms in H.by_event(H.settled_markets(t)).items():
            out[e] = (t, ms)
        if verbose and i % 20 == 19:
            print(f"  listed {i + 1}/{len(names)} series, {len(out)} events", flush=True)
    return out


def hourly(series, event, ms):
    close = max(H.close_of(m) for m in ms)
    start = max(min(H.open_of(m) for m in ms), close - timedelta(days=HOURLY_WINDOW_DAYS))
    start = start.replace(minute=0, second=0, microsecond=0)
    return H.event_candles(series, event, start - timedelta(hours=2), close + timedelta(hours=1), 60)


def minutely(series, event, ms):
    close = max(H.close_of(m) for m in ms)
    return H.event_candles(series, event, close - timedelta(hours=3), close, 1)


def prefetch(jobs, workers=4, verbose=True, what="events"):
    done = [0]

    def run(job):
        fn, args = job
        try:
            fn(*args)
        except Exception as ex:          # one bad event must not stop the run; it is reported
            print(f"  skipped {args[1]}: {ex}", flush=True)
        done[0] += 1
        if verbose and done[0] % 100 == 0:
            print(f"  candles for {done[0]}/{len(jobs)} {what}", flush=True)

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(run, jobs))


# ---------------- (a) consistency ----------------

def legs_at(markets, candles, t, max_age=3600):
    """Every buyable leg at time t: (market, side, ask) for markets that existed then."""
    out = []
    for m in markets:
        if not H.existed_at(m, t):
            continue
        bid, ask = H.quote_at(candles.get(m["ticker"], []), t, max_age)
        if ask:
            out.append((m, "YES", ask))
        if bid:
            out.append((m, "NO", round(1 - bid, 4)))
    return out


EPS = 1e-5     # AAA gas and natgas strikes have 3-4 decimals ($3.8950, $1.999): never round to cents


def pays_yes(c, v):
    """Does settlement value v make contract c resolve Yes? Exact, no rounding (argus.markets.contains
    rounds to the cent, which opens false gaps on 3-decimal strikes)."""
    if c.kind == "between":
        return c.low <= v <= c.high
    if c.kind == "greater":
        return v > c.low
    if c.kind == "greater_or_equal":
        return v >= c.low
    if c.kind == "less":
        return v < c.high
    if c.kind == "less_or_equal":
        return v <= c.high
    raise ValueError(f"Unknown strike type {c.kind!r} on {c.ticker}")


def cells(contracts):
    """Representative settlement values, one inside every stretch of the number line where no
    contract changes its answer: each strike, and a hair either side of it."""
    pts = set()
    for c in contracts:
        for x in (c.low, c.high):
            if x is not None:
                pts.update((x - EPS, x, x + EPS))
    if not pts:
        return [0.0]
    return sorted(pts | {min(pts) - 1, max(pts) + 1})


def payoff_mask(c, side, values):
    m = 0
    for i, v in enumerate(values):
        if pays_yes(c, v) == (side == "YES"):
            m |= 1 << i
    return m


class Payoffs:
    """Which settlement values pay each (market, side), worked out once per event (prices change
    hour to hour; payoffs do not)."""

    def __init__(self, markets):
        cs = {m["ticker"]: contract(m) for m in markets}
        self.values = cells(cs.values())
        self.n = len(self.values)
        self.full = (1 << self.n) - 1
        self.mask, self.span = {}, {}
        for t, c in cs.items():
            for side in ("YES", "NO"):
                mk = payoff_mask(c, side, self.values)
                self.mask[(t, side)] = mk
                bits = [k for k in range(self.n) if mk >> k & 1]
                if bits and bits[-1] - bits[0] + 1 == len(bits):
                    self.span[(t, side)] = (bits[0], bits[-1])
        # exactly one market pays at every value: an exhaustive set of ranges
        yes = [self.mask[(t, "YES")] for t in cs]
        self.exhaustive = len(cs) >= 3 and all(sum(m >> k & 1 for m in yes) == 1 for k in range(self.n))


def best_lock(legs, pay=None):
    """Cheapest set of legs that pays at least $1 at every possible settlement value.

    Checks every pair of legs, and for three or more legs a shortest-path cover using legs that
    pay on one unbroken stretch of values (all Yes ranges, ladder Yes and No). Returns
    (profit_per_set_after_fees, [(market, side, ask, fee)]) or None."""
    if not legs:
        return None
    pay = pay or Payoffs(list({m["ticker"]: m for m, _, _ in legs}.values()))
    info = sorted(((ask + fee1(ask), pay.mask[(m["ticker"], side)], (m, side, ask, fee1(ask)))
                   for m, side, ask in legs), key=lambda x: x[0])
    best = None
    for i, (ci, mi, li) in enumerate(info):
        if ci >= 1:
            break
        for cj, mj, lj in info[i + 1:]:
            if ci + cj >= 1 - LOCK_MIN_PROFIT + 1e-9:
                break
            if li[0]["ticker"] == lj[0]["ticker"]:
                continue                       # Yes + No of one market is never a lock
            if mi | mj == pay.full and (best is None or ci + cj < best[0]):
                best = (ci + cj, [li, lj])
    # interval cover (three or more legs): shortest path over the value cells
    spans = [(pay.span[(l[0]["ticker"], l[1])], c, l) for c, _, l in info
             if c < 1 and (l[0]["ticker"], l[1]) in pay.span]
    dp = [None] * (pay.n + 1)
    dp[0] = (0.0, [])
    for k in range(pay.n):
        if dp[k] is None or dp[k][0] >= 1:
            continue
        for (lo, hi), c, leg in spans:
            if lo <= k <= hi:
                cand = (dp[k][0] + c, dp[k][1] + [leg])
                if dp[hi + 1] is None or cand[0] < dp[hi + 1][0]:
                    dp[hi + 1] = cand
    if dp[pay.n] and len(dp[pay.n][1]) >= 3 and (best is None or dp[pay.n][0] < best[0]):
        best = dp[pay.n]
    if best and 1 - best[0] >= LOCK_MIN_PROFIT - 1e-9:
        return round(1 - best[0], 4), best[1]
    return None


def sell_the_set(markets, legs, pay):
    """Exhaustive ranges (exactly one pays): buying every No pays n-1. Lock if that costs less."""
    if not pay.exhaustive:
        return None
    by = {m["ticker"]: (m, a) for m, s, a in legs if s == "NO"}
    if len(by) != len(markets):
        return None
    cost = sum(a + fee1(a) for _, a in by.values())
    profit = (len(markets) - 1) - cost
    if profit >= LOCK_MIN_PROFIT - 1e-9:
        return round(profit, 4), [(m, "NO", a, fee1(a)) for m, a in by.values()]
    return None


def same_settlement_pairs(events, table):
    """Find series pairs that settle on the same number. Two series are a pair when, on the
    dates both settled something close in value, one of their events settled on the identical
    value on 90%+ of those dates (3+ dates). Returns (stats, good pairs, [(event_a, event_b)])."""
    by_date = defaultdict(list)
    for e, (s, ms) in events.items():
        v = H.settle_value(ms[0])
        if v is None:
            continue
        by_date[H.close_of(ms[0]).astimezone(ET).date()].append((s, e, round(v, 2)))
    stats = defaultdict(lambda: {"dates": 0, "equal": 0})
    matches = defaultdict(list)
    for d, xs in by_date.items():
        seen, eq = set(), defaultdict(list)
        for i in range(len(xs)):
            for j in range(i + 1, len(xs)):
                a, b = sorted([xs[i], xs[j]])
                if a[0] == b[0] or group_of(table[a[0]]) != group_of(table[b[0]]):
                    continue
                if abs(a[2] - b[2]) > max(5.0, 0.05 * max(abs(a[2]), abs(b[2]))):
                    continue                   # far apart: a different underlying
                seen.add((a[0], b[0]))
                if a[2] == b[2]:
                    eq[(a[0], b[0])].append((a[1], b[1]))
        for k in seen:
            stats[k]["dates"] += 1
            if eq.get(k):
                stats[k]["equal"] += 1
                matches[k] += eq[k]
    good = {k: v for k, v in stats.items() if v["dates"] >= 3 and v["equal"] >= 0.9 * v["dates"]}
    pairs = [p for k in good for p in matches[k]]
    return dict(stats), good, pairs


def replay_lock(markets_by_series, candles, engine, label):
    """Hour by hour through the common life of the markets; paper-buy the first lock once."""
    ms = [m for s, mm in markets_by_series for m in mm]
    pay = Payoffs(ms)
    close = min(max(H.close_of(m) for m in mm) for _, mm in markets_by_series)
    start = max(min(H.open_of(m) for m in mm) for _, mm in markets_by_series)
    start = max(start, close - timedelta(days=HOURLY_WINDOW_DAYS))
    t = (start + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    hours = locks = 0
    first, sizes = None, []
    series_of = {m["ticker"]: s for s, mm in markets_by_series for m in mm}
    while t < close:
        legs = legs_at(ms, candles, t)
        found = best_lock(legs, pay)
        if len(markets_by_series) == 1:
            alt = sell_the_set(markets_by_series[0][1], legs, pay)
            if alt and (not found or alt[0] > found[0]):
                found = alt
        hours += 1
        if found:
            locks += 1
            sizes.append(found[0])
            if first is None:
                first = (t, found)
        t += timedelta(hours=1)
    rows = []
    if first:
        t, (profit, legs) = first
        for m, side, ask, fee in legs:
            other = sum(a + f for mm, s, a, f in legs if (mm["ticker"], s) != (m["ticker"], side))
            rows.append(row(engine, series_of[m["ticker"]], m, side, ask, t, side_won(m, side), fee,
                            model_prob=min(max(1 - other, 0.0), 1.0), value=H.settle_value(m),
                            notes=f"lock={label}; legs={len(legs)}; locked_profit={profit:.2f}; "
                                  f"lock_hours={locks}/{hours}"))
    return rows, {"hours": hours, "lock_hours": locks, "sizes": sizes}


KINDS = {"greater", "greater_or_equal", "less", "less_or_equal", "between"}


def priceable(ms):
    """Every market has a numeric strike (ladders and ranges); one-off yes/no questions do not."""
    return all(m.get("strike_type") in KINDS for m in ms)


def run_consistency(events, table, verbose=True):
    events = {e: v for e, v in events.items() if priceable(v[1])}
    stats, good, pairs = same_settlement_pairs(events, table)
    rows, summary = [], {"pair_stats": {f"{a}|{b}": v for (a, b), v in stats.items()},
                         "pairs_used": [f"{a}|{b}" for a, b in good], "pair": Counter(), "ladder": Counter()}
    jobs = {}
    for a, b in pairs:
        for e in (a, b):
            jobs[e] = (hourly, (events[e][0], e, events[e][1]))
    for e, (s, ms) in events.items():
        if len(ms) >= 2 and table[s].get("frequency") != "hourly":
            jobs[e] = (hourly, (s, e, ms))
    if verbose:
        print(f"  consistency: {len(good)} same-settlement series pairs, {len(pairs)} event pairs, "
              f"{len(jobs)} events to read", flush=True)
    prefetch(list(jobs.values()), verbose=verbose)
    for a, b in pairs:
        (sa, ma), (sb, mb) = events[a], events[b]
        cand = {**hourly(sa, a, ma), **hourly(sb, b, mb)}
        rs, info = replay_lock([(sa, ma), (sb, mb)], cand, "consistency-pair", f"{sa}+{sb}")
        rows += rs
        summary["pair"]["event_pairs"] += 1
        summary["pair"]["hours"] += info["hours"]
        summary["pair"]["lock_hours"] += info["lock_hours"]
        summary["pair"]["events_with_lock"] += bool(rs)
    for e, (s, ms) in events.items():
        if len(ms) < 2 or table[s].get("frequency") == "hourly":
            continue
        rs, info = replay_lock([(s, ms)], hourly(s, e, ms), "consistency-ladder", s)
        rows += rs
        summary["ladder"]["events"] += 1
        summary["ladder"]["hours"] += info["hours"]
        summary["ladder"]["lock_hours"] += info["lock_hours"]
        summary["ladder"]["events_with_lock"] += bool(rs)
    return rows, summary


# ---------------- (b) late-day favourites ----------------

def latefav_rows(series, ms, candles, usual=timedelta(0)):
    rows = []
    for m in ms:
        if early_closed(m, usual):
            continue
        close = H.close_of(m)
        for mins in LATE_MINUTES:
            t = close - timedelta(minutes=mins)
            if not H.existed_at(m, t):
                continue
            bid, ask = H.quote_at(candles.get(m["ticker"], []), t, 3 * 3600)
            for side, price, sbid in (("YES", ask, bid), ("NO", round(1 - bid, 4) if bid else None,
                                                          round(1 - ask, 4) if ask else None)):
                if price and FAV_LO - 1e-9 <= price <= FAV_HI + 1e-9:
                    rows.append(row(f"latefav-{family(series)}{mins}m", series, m, side, price, t, side_won(m, side),
                                    fee1(price), value=H.settle_value(m),
                                    notes=f"favourite {int(round(price * 100))}c {mins} min before close; "
                                          f"side_bid={'' if sbid is None else f'{sbid:.2f}'}"))
    return rows


def run_latefav(events, table, verbose=True):
    use = {e: v for e, v in events.items() if table[v[0]].get("frequency") != "hourly"}
    prefetch([(minutely, (s, e, ms)) for e, (s, ms) in use.items()], verbose=verbose, what="events (minute)")
    rows, gaps = [], usual_gaps(use)
    for e, (s, ms) in use.items():
        rows += latefav_rows(s, ms, minutely(s, e, ms), gaps[s])
    fav_key = lambda r: (r["engine"], int(float(r["market_price"]) * 100) // 3)
    return walk_forward(rows, fav_key), {}


# ---------------- (c) maker and (d) buckets ----------------

def maker_limit(bid, ask):
    """Buy one cent above the best bid without reaching the ask; None if the spread is 1c."""
    if bid is None or ask is None:
        return None
    p = round(bid + 0.01, 2)
    return p if p < ask - 1e-9 else None


def maker_fill(side, limit, lo, hi):
    """Filled only if a later trade printed strictly through our limit.
    Yes buy at L: some Yes trade below L. No buy at L: some Yes trade above 1 - L."""
    if side == "YES":
        return lo is not None and lo < limit - 1e-9
    return hi is not None and hi > 1 - limit + 1e-9


def quote_rows(series, frequency, ms, candles, seed=7, usual=timedelta(0)):
    """Rows for the maker test (c) and both bucket tests (d) at one replay time per event."""
    maker, buckets = [], []
    rng = random.Random(seed)
    usable = []
    for m in ms:
        if early_closed(m, usual):
            continue
        t = replay_time(m, frequency)
        if not H.existed_at(m, t):
            continue
        cs = candles.get(m["ticker"], [])
        bid, ask = H.quote_at(cs, t, 3600)
        if bid is None or ask is None or ask <= bid:
            continue
        lo, hi = H.trades_after(cs, t, H.close_of(m), 3600)
        usable.append((m, t, bid, ask, lo, hi))
    for m, t, bid, ask, lo, hi in usable:
        mid = (bid + ask) / 2
        if NEAR_LO <= mid <= NEAR_HI:
            for side, b, a in (("YES", bid, ask), ("NO", round(1 - ask, 4), round(1 - bid, 4))):
                lim = maker_limit(b, a)
                if lim is None:
                    continue
                filled = maker_fill(side, lim, lo, hi)
                maker.append(row(f"maker-{family(series)}bid+1", series, m, side, lim, t, side_won(m, side), 0.0,
                                 contracts=1 if filled else 0, value=H.settle_value(m),
                                 status="settled" if filled else "void",
                                 notes=f"pricing=maker; maker_fill={'traded-through' if filled else 'none'}; "
                                       f"bid={b:.2f}; ask={a:.2f}"))
    rng.shuffle(usable)
    for m, t, bid, ask, lo, hi in sorted(usable[:BUCKET_MARKETS_PER_EVENT], key=lambda x: x[0]["ticker"]):
        for side, b, a in (("YES", bid, ask), ("NO", round(1 - ask, 4), round(1 - bid, 4))):
            lab = bucket_of(a)
            if lab:
                buckets.append(row(f"bucket-{family(series)}taker-{lab}", series, m, side, a, t, side_won(m, side), fee1(a),
                                   value=H.settle_value(m), notes=f"bid={b:.2f}"))
            lim = maker_limit(b, a)
            lab = bucket_of(lim) if lim else None
            if lab:
                filled = maker_fill(side, lim, lo, hi)
                buckets.append(row(f"bucket-{family(series)}maker-{lab}", series, m, side, lim, t, side_won(m, side), 0.0,
                                   contracts=1 if filled else 0, value=H.settle_value(m),
                                   status="settled" if filled else "void",
                                   notes=f"pricing=maker; maker_fill={'traded-through' if filled else 'none'}"))
    return maker, buckets


def run_quotes(events, table, verbose=True):
    use = {e: v for e, v in events.items() if table[v[0]].get("frequency") != "hourly"}
    prefetch([(hourly, (s, e, ms)) for e, (s, ms) in use.items()], verbose=verbose, what="events (hourly)")
    maker, buckets, gaps = [], [], usual_gaps(use)
    for e, (s, ms) in sorted(use.items()):
        a, b = quote_rows(s, table[s].get("frequency"), ms, hourly(s, e, ms), usual=gaps[s])
        maker += a
        buckets += b
    pkey = lambda r: (r["engine"], int(float(r["market_price"]) * 100) // 10)
    return walk_forward(maker, pkey), walk_forward(buckets, lambda r: r["engine"])


# ---------------- reporting ----------------

def summarize(rows, table):
    """Per engine: calls, settled bets, events, wins, P/L, ROI, edge per contract, bets per week."""
    out = {}
    by = defaultdict(list)
    for r in rows:
        by[r["engine"]].append(r)
    for eng, rs in sorted(by.items()):
        bets = [r for r in rs if r["status"] == "settled" and int(r["paper_contracts"]) > 0]
        card = log.scorecard([r for r in rs if r["status"] == "settled"])
        events = {r["event_ticker"] for r in bets}
        if bets:
            t0 = min(H.parse_time(r["logged_at_utc"]) for r in bets)
            t1 = max(H.parse_time(r["logged_at_utc"]) for r in bets)
            weeks = max((t1 - t0).days / 7, 1)
        else:
            weeks = 1
        staked = sum(float(r["market_price"]) + float(r["fee_per_contract"]) for r in bets)
        groups = Counter(group_of(table.get(r["series"], {"ticker": r["series"]})) for r in bets)
        out[eng] = {"calls": len(rs), "bets": len(bets), "events": len(events),
                    "wins": sum(r["won"] == "1" for r in bets),
                    "avg_price": round(statistics.mean(float(r["market_price"]) for r in bets), 4) if bets else None,
                    "pnl": round(sum(float(r["pnl_after_fees"]) for r in bets), 2),
                    "roi_on_cost": round(sum(float(r["pnl_after_fees"]) for r in bets) / staked, 4) if staked else None,
                    "edge_per_contract": round(sum(float(r["pnl_after_fees"]) for r in bets) / len(bets), 4) if bets else None,
                    "bets_per_week": round(len(bets) / weeks, 1),
                    "brier_model": card["brier_model"], "brier_market": card["brier_market"],
                    "groups": dict(groups)}
        out[eng]["verdict"] = verdict(out[eng])
    return out


def verdict(x):
    """Same gate as the board: 20+ settled events, model Brier < market Brier, P/L > 0 after fees."""
    if x["events"] < 20:
        return f"TOO EARLY ({x['events']}/20 events)"
    beats = x["brier_model"] is not None and x["brier_market"] is not None and x["brier_model"] < x["brier_market"]
    if beats and x["pnl"] > 0:
        return "PASS"
    if x["pnl"] > 0:
        return "PROFIT, BRIER NOT BETTER"
    return "FAIL"


def report(path=REPORT_JSON):
    if not path.exists():
        return "No backtest summary yet: run python -m argus.commodities backtest"
    s = json.loads(path.read_text(encoding="utf-8"))
    L = [f"COMMODITY STRUCTURAL STRATEGIES (backtest {s['ran_at']}, live tier since {s['since']})", "",
         "Engine                     Calls  Bets Events  Win%  Avg px   P/L     ROI  Edge/ct  Bets/wk  Brier strat/mkt  Verdict"]
    for eng, x in s["engines"].items():
        wr = f"{100 * x['wins'] / x['bets']:4.0f}%" if x["bets"] else "   - "
        ap = f"{x['avg_price']:.3f}" if x["avg_price"] is not None else "  -  "
        roi = f"{100 * x['roi_on_cost']:+5.1f}%" if x["roi_on_cost"] is not None else "   -  "
        ed = f"{x['edge_per_contract']:+.4f}" if x["edge_per_contract"] is not None else "   -   "
        bm = lambda v: "  -   " if v is None else f"{v:.4f}"
        L.append(f"{eng[:26]:<26} {x['calls']:>5} {x['bets']:>5} {x['events']:>6} {wr} {ap:>7} {x['pnl']:>+7.2f} {roi} "
                 f"{ed:>8} {x['bets_per_week']:>7}  {bm(x['brier_model'])}/{bm(x['brier_market'])}  {x['verdict']}")
    c = s.get("consistency") or {}
    if c:
        L += ["", f"Same-settlement series pairs found: {', '.join(c.get('pairs_used', [])) or 'none'}",
              f"Pair replay: {dict(c.get('pair', {}))}", f"Ladder replay: {dict(c.get('ladder', {}))}"]
    L.append("\nPaper only. Taker fee on 1 contract per leg; no maker fee on commodity series.")
    return "\n".join(L)


def main(only=None, verbose=True):
    only = set(only or ["consistency", "latefav", "maker", "buckets"])
    table = series_table()
    if verbose:
        print(f"{len(table)} non-15-minute commodity series; listing settled events since {H.LIVE_SINCE.date()}",
              flush=True)
    events = load_events(table, verbose=verbose)
    if verbose:
        print(f"{len(events)} settled events", flush=True)
    old = json.loads(REPORT_JSON.read_text(encoding="utf-8")) if REPORT_JSON.exists() else {}
    engines = dict(old.get("engines", {}))
    summary = {"ran_at": iso(datetime.now(timezone.utc)), "since": H.LIVE_SINCE.date().isoformat(),
               "events": len(events), "consistency": old.get("consistency")}

    def write(name, rows):
        log.write_log(rows, LOGS[name])
        for k in [k for k in engines if any(k.startswith(p) for p in PREFIX[name])]:
            del engines[k]
        engines.update(summarize(rows, table))

    if "consistency" in only:
        rows, info = run_consistency(events, table, verbose)
        write("consistency", rows)
        summary["consistency"] = {k: (dict(v) if isinstance(v, Counter) else v) for k, v in info.items()}
    if "latefav" in only:
        rows, _ = run_latefav(events, table, verbose)
        write("latefav", rows)
    if "maker" in only or "buckets" in only:
        maker, buckets = run_quotes(events, table, verbose)
        if "maker" in only:
            write("maker", maker)
        if "buckets" in only:
            write("buckets", buckets)
    summary["engines"] = engines
    DATA.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    print(report())


PREFIX = {"consistency": ("consistency-",), "latefav": ("latefav-",), "maker": ("maker-",),
          "buckets": ("bucket-",)}
