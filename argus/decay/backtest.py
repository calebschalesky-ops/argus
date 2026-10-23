"""Replay the deadline-decay rules on settled by-date markets, with no look-ahead.

Every moment is measured back from the market's SCHEDULED deadline S (read from its rules),
never from its actual close. A "by Friday" market closes the minute X happens, so "3 days
before the close" of a market that resolved Yes early is a moment nobody could have picked in
advance. Here, a market that already closed at a check time is simply not traded then (you
would have seen it resolved), and a market still open at the check time is traded with its
real result: if X happens afterwards it closes early as Yes, and the No bet LOSES.

Price at a moment t: the last daily candle that ENDED at or before t (and at most 2 days
before it), counted only with a real Yes bid (1c+) within 5c of the Yes ask. Buying No at the
ask costs 1 - Yes bid. Never the last trade.

Variants (written down before any result was seen):
  hazard        Last 25% of the market's scheduled life, checked once a day. Hazard from the
                Yes mid 7 days earlier: p0 = 1 - exp(-h * T0), T0 = time left then. Fair Yes now
                with no news = 1 - exp(-h * T), T = time left now = 1 - (1 - p0)^(T/T0).
                Take No at the ask on the FIRST day the model's No edge after the taker fee is
                3c+ and the Yes mid is 60c or less. One bet per market.
  hazard-maker  Same signal, but rest a No bid at (No bid + 1c), below the No ask. Counted as
                filled only if a later daily candle printed a Yes trade at or above (1 - our
                No price). Maker fee only where the series charges it. Queue position is not
                proven, so this is an upper bound on fills.
  simple-K      No at the ask exactly K days before the deadline (K = 3, 7, 14) when the
                Yes mid is 10-40c. One bet per market.
"""
import csv
import math
import random
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .. import fees
from ..log import COLUMNS
from . import CACHE, LOG_PATH
from . import collect
from . import http

DAY = 86400
MAX_AGE = 2 * DAY          # a daily candle older than this is not "the price now"
MAX_SPREAD = 0.05
LOOKBACK_DAYS = 7          # hazard: price this many days earlier
LATE_FRACTION = 0.25       # hazard: only the last quarter of the scheduled life
MIN_EDGE = 0.03            # hazard: model No edge after fee, dollars per contract
HAZARD_MAX_YES = 0.60
SIMPLE_KS = (3, 7, 14)
SIMPLE_BAND = (0.10, 0.40)
RECENT_FROM = datetime(2026, 7, 22, tzinfo=timezone.utc)   # "recent 3 months" (today 2026-10-22)
SANITY_WINDOW = (-1, 8)    # latest_expiration_time this many days from the parsed deadline = deadline read right
REPORT = CACHE / "report.txt"


def parse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def ts(dt):
    return int(dt.timestamp())


# ---------------- prices ----------------

def _px(side, key="close"):
    if not side:
        return None
    v = side.get(key + "_dollars", side.get(key))
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v > 1.5:              # some old candles are in cents
        v /= 100
    return v


def quote_at(candles, t):
    """(yes_bid, yes_ask) from the last candle that ended at or before t, or None.

    Needs a real two-sided book: bid of 1c or more, ask within MAX_SPREAD above it."""
    ok = [c for c in candles if t - MAX_AGE < c["end_period_ts"] <= t]
    if not ok:
        return None
    c = max(ok, key=lambda c: c["end_period_ts"])
    bid, ask = _px(c.get("yes_bid")), _px(c.get("yes_ask"))
    if bid is None or ask is None or bid < 0.01 or ask >= 1 or ask - bid > MAX_SPREAD + 1e-9 or ask < bid:
        return None
    return bid, ask


def max_yes_trade_after(candles, t, until):
    """Highest Yes trade printed in a daily candle that STARTED at or after t, ending by `until`."""
    hi = [_px(c.get("price"), "high") for c in candles
          if c["end_period_ts"] - DAY >= t and c["end_period_ts"] <= until + DAY]
    hi = [h for h in hi if h is not None]
    return max(hi) if hi else None


def hazard_fair(p0, t_left0, t_left):
    """Fair Yes now if nothing happened since: constant hazard implied by p0 with t_left0 to go."""
    if t_left <= 0:
        return 0.0
    p0 = min(max(p0, 0.0), 0.999)
    return 1 - (1 - p0) ** (t_left / t_left0)


# ---------------- the market universe ----------------

def schedule_ok(S, latest):
    if latest is None:
        return False
    d = (latest - S).total_seconds() / DAY
    return SANITY_WINDOW[0] <= d <= SANITY_WINDOW[1]


def prepare(m, series_meta):
    """Attach parsed times and fee info; None when the market cannot be replayed honestly.

    sched_ok: Kalshi's latest_expiration_time (deadline, or deadline + 7 days) sits within
    SANITY_WINDOW of the deadline read from the rules: a check that the deadline was read right.
    NOT expected_expiration_time: on ladders it is a far-off fallback, and on some early Yes closes
    Kalshi overwrote it with the close date, so using it would quietly drop the No losers.
    The report shows the check keeps early Yes closes and No markets at the same rate."""
    if not m.get("deadline"):
        return None
    S, opened, close = parse(m["deadline"]), parse(m["open_time"]), parse(m["close_time"])
    if S <= opened + timedelta(days=1):
        return None
    sched_ok = schedule_ok(S, parse(m.get("latest_expiration_time")))
    s = series_meta.get(m["event_ticker"].split("-", 1)[0], {})
    charged, mult = fees.series_maker_fees(s)
    return dict(m, S=S, opened=opened, close=close, sched_ok=sched_ok,
                maker_charged=charged, fee_mult=mult, yes_won=m["result"] == "yes")


def tradable_at(m, t):
    """Open at t: listed before it and not yet closed (a market already closed is resolved)."""
    return m["opened"] <= t < m["close"]


# ---------------- the rules ----------------

def _trade(m, variant, t, price, fee, model_no, market_no, extra=""):
    won = not m["yes_won"]
    pnl = (1 - price if won else -price) - fee
    return {"variant": variant, "ticker": m["ticker"], "event": m["event_ticker"],
            "category": m["_category"], "t": t, "S": m["S"], "close": m["close"],
            "days_left": (m["S"] - t).total_seconds() / DAY, "price": price, "fee": fee,
            "model_no": model_no, "market_no": market_no, "won": won, "pnl": pnl,
            "early_yes": m["yes_won"] and m["close"] < m["S"] - timedelta(days=1), "extra": extra}


def check_times(m, late_fraction=LATE_FRACTION):
    """Daily check moments in the last `late_fraction` of the scheduled life, oldest first,
    stepping back from S in whole days (so the grid never depends on the actual close)."""
    life_days = (m["S"] - m["opened"]).total_seconds() / DAY
    k_max = int(life_days * late_fraction)
    return [m["S"] - timedelta(days=k) for k in range(k_max, 0, -1)]


def hazard_signal(m, candles, t):
    """(yes_bid, yes_ask, fair_yes) when the hazard rule fires at t, else None."""
    if not tradable_at(m, t):
        return None
    q = quote_at(candles, ts(t))
    t0 = t - timedelta(days=LOOKBACK_DAYS)
    if q is None or t0 < m["opened"]:
        return None
    q0 = quote_at(candles, ts(t0))
    if q0 is None:
        return None
    bid, ask = q
    mid, mid0 = (bid + ask) / 2, (q0[0] + q0[1]) / 2
    if mid > HAZARD_MAX_YES:
        return None
    fair = hazard_fair(mid0, (m["S"] - t0).total_seconds(), (m["S"] - t).total_seconds())
    no_ask = round(1 - bid, 4)
    fee = fees.taker_fee(no_ask, multiplier=m["fee_mult"])
    if (1 - fair) - no_ask - fee < MIN_EDGE:
        return None
    return bid, ask, fair


def run_hazard(m, candles):
    out = []
    for t in check_times(m):
        sig = hazard_signal(m, candles, t)
        if not sig:
            continue
        bid, ask, fair = sig
        no_ask = round(1 - bid, 4)
        mid_no = 1 - (bid + ask) / 2
        out.append(_trade(m, "hazard", t, no_ask, fees.taker_fee(no_ask, multiplier=m["fee_mult"]),
                          1 - fair, mid_no, f"yes_mid={(bid + ask) / 2:.3f};fair_yes={fair:.3f}"))
        # maker: rest No one cent above the No bid (= 1 - Yes ask), still below the No ask
        no_bid = round(1 - ask, 4)
        my_no = round(min(no_bid + 0.01, no_ask - 0.01), 4)
        if my_no > 0 and my_no >= no_bid:
            hi = max_yes_trade_after(candles, ts(t), ts(min(m["close"], m["S"])))
            if hi is not None and hi >= 1 - my_no - 1e-9:
                fee = fees.maker_fee(my_no, multiplier=m["fee_mult"], charged=m["maker_charged"])
                out.append(_trade(m, "hazard-maker", t, my_no, fee, 1 - fair, mid_no,
                                  f"rest_no={my_no:.2f};yes_trade_hi={hi:.2f}"))
        break
    return out


def run_simple(m, candles, k):
    t = m["S"] - timedelta(days=k)
    if not tradable_at(m, t):
        return []
    q = quote_at(candles, ts(t))
    if q is None:
        return []
    bid, ask = q
    mid = (bid + ask) / 2
    if not SIMPLE_BAND[0] <= mid <= SIMPLE_BAND[1]:
        return []
    no_ask = round(1 - bid, 4)
    # no model here: the "model" probability is the price paid, so the board credits no Brier edge
    return [_trade(m, f"simple-{k}d", t, no_ask, fees.taker_fee(no_ask, multiplier=m["fee_mult"]),
                   no_ask, 1 - mid, f"yes_mid={mid:.3f}")]


def replay(markets, candles_of, require_sched=True):
    trades = []
    for m in markets:
        if require_sched and not m["sched_ok"]:
            continue
        cs = candles_of(m["ticker"])
        if not cs:
            continue
        trades += run_hazard(m, cs)
        for k in SIMPLE_KS:
            trades += run_simple(m, cs, k)
    return trades


# ---------------- statistics ----------------

def roi(trades):
    stake = sum(x["price"] + x["fee"] for x in trades)
    return sum(x["pnl"] for x in trades) / stake if stake else None


def cluster_ci(trades, n_boot=2000, seed=5):
    """95% range of ROI when whole events are resampled (bets on one event move together)."""
    by = defaultdict(list)
    for x in trades:
        by[x["event"]].append(x)
    keys = list(by)
    if len(keys) < 2:
        return None, None
    rng = random.Random(seed)
    sums = {k: (sum(x["pnl"] for x in by[k]), sum(x["price"] + x["fee"] for x in by[k])) for k in keys}
    out = []
    for _ in range(n_boot):
        p = s = 0.0
        for _ in keys:
            a, b = sums[keys[rng.randrange(len(keys))]]
            p += a
            s += b
        out.append(p / s if s else 0.0)
    out.sort()
    return out[int(0.025 * n_boot)], out[int(0.975 * n_boot) - 1]


def worst_cluster(trades):
    by = defaultdict(list)
    for x in trades:
        by[x["event"]].append(x)
    if not by:
        return None
    k = min(by, key=lambda k: sum(x["pnl"] for x in by[k]))
    return k, len(by[k]), sum(x["pnl"] for x in by[k])


def brier(pairs):
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs) if pairs else None


def summary(trades):
    if not trades:
        return {"n": 0}
    trades = sorted(trades, key=lambda x: x["t"])
    half = len(trades) // 2
    lo, hi = cluster_ci(trades)
    recent = [x for x in trades if x["t"] >= RECENT_FROM]
    return {
        "n": len(trades), "events": len({x["event"] for x in trades}),
        "wins": sum(x["won"] for x in trades), "pnl": sum(x["pnl"] for x in trades),
        "roi": roi(trades), "ci": (lo, hi),
        "first_half": roi(trades[:half]), "second_half": roi(trades[half:]),
        "split_at": trades[half]["t"] if trades else None,
        "recent_n": len(recent), "recent_roi": roi(recent),
        "worst": worst_cluster(trades),
        "early_yes": sum(x["early_yes"] for x in trades),
        "avg_price": sum(x["price"] for x in trades) / len(trades),
        "brier_model": brier([(x["model_no"], x["won"]) for x in trades]),
        "brier_market": brier([(x["market_no"], x["won"]) for x in trades]),
    }


def verdict(s):
    if s["n"] == 0:
        return "NO TRADES"
    if s["events"] < 20:
        return f"TOO EARLY ({s['events']}/20 events)"
    lo, hi = s["ci"]
    if s["roi"] > 0 and lo is not None and lo > 0 and (s["first_half"] or 0) > 0 and (s["second_half"] or 0) > 0:
        return "PASS (backtest): positive, CI above 0, both halves positive"
    if s["roi"] > 0:
        return "NOT PROVEN: positive but CI includes 0 or a half lost"
    return "FAIL: loses after fees"


# ---------------- report and log ----------------

def _p(x, d=1):
    return "  n/a" if x is None else f"{x * 100:+.{d}f}%"


def fmt_summary(name, s):
    if s["n"] == 0:
        return f"{name:<14} n=0"
    lo, hi = s["ci"]
    w = s["worst"]
    return (f"{name:<14} n={s['n']:>4} ev={s['events']:>4} win={s['wins'] / s['n']:.0%} avgNo={s['avg_price']:.2f} "
            f"ROI {_p(s['roi'])} CI[{_p(lo)},{_p(hi)}] halves {_p(s['first_half'])}/{_p(s['second_half'])} "
            f"recent(n={s['recent_n']}) {_p(s['recent_roi'])} worst {w[0]} ({w[1]} bets, ${w[2]:+.2f}) "
            f"earlyYes={s['early_yes']}  -> {verdict(s)}")


def by_category(trades):
    by = defaultdict(list)
    for x in trades:
        by[x["category"]].append(x)
    return {c: summary(v) for c, v in sorted(by.items())}


def calibration(markets, candles_of, ks=(1, 3, 7, 14, 30)):
    """Is Yes overpriced late? Yes mid vs how often Yes happened, K days before the deadline."""
    rows = []
    bands = [(0.02, 0.10), (0.10, 0.25), (0.25, 0.40), (0.40, 0.60), (0.60, 0.90)]
    for k in ks:
        for lo, hi in bands:
            pts = []
            for m in markets:
                if not m["sched_ok"]:
                    continue
                t = m["S"] - timedelta(days=k)
                if not tradable_at(m, t):
                    continue
                cs = candles_of(m["ticker"])
                q = quote_at(cs or [], ts(t))
                if q is None:
                    continue
                mid = sum(q) / 2
                if lo <= mid < hi:
                    pts.append((mid, m["yes_won"], m["event_ticker"]))
            if pts:
                rows.append((k, f"{lo * 100:.0f}-{hi * 100:.0f}c", len(pts), len({p[2] for p in pts}),
                             sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)))
    return rows


def log_rows(trades, now=None):
    now = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for x in trades:
        rows.append({
            "call_id": uuid.uuid4().hex[:10], "logged_at_utc": now, "engine": f"decay-{x['variant']}",
            "series": x["event"].split("-", 1)[0], "event_ticker": x["event"], "market_ticker": x["ticker"],
            "range_label": "by-date", "side": "NO", "model_prob": f"{x['model_no']:.4f}",
            "market_price": f"{x['price']:.4f}", "gap_pts": f"{(x['model_no'] - x['price']) * 100:.1f}",
            "worth_a_look": "1", "paper_contracts": "1", "fee_per_contract": f"{x['fee']:.2f}",
            "anchor_price": f"{x['market_no']:.4f}", "anchor_kind": "no-mid",
            "horizon_days": f"{x['days_left']:.2f}", "close_time_utc": x["close"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "settled", "won": "1" if x["won"] else "0", "pnl_after_fees": f"{x['pnl']:.4f}",
            "notes": (f"backtest; category={x['category']}; t={x['t']:%Y-%m-%d}; deadline={x['S']:%Y-%m-%d}; "
                      f"early_yes_close={int(x['early_yes'])}; {x['extra']}"),
        })
    return rows


def write_log(trades, path=LOG_PATH):
    """Replace the backtest rows (notes start with 'backtest') and keep every live row."""
    keep = []
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            keep = [r for r in csv.DictReader(f) if not (r.get("notes") or "").startswith("backtest")]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in keep + log_rows(trades):
            w.writerow({k: r.get(k, "") for k in COLUMNS})


def load_universe():
    meta = {s["ticker"]: s for s in http.get("/series", {}, cache=True)["series"]}
    raw = collect.load_markets()
    ms = [p for p in (prepare(m, meta) for m in raw) if p]
    return raw, ms


def main(log=True):
    raw, ms = load_universe()
    cache = {}

    def candles_of(t):
        if t not in cache:
            cache[t] = collect.load_candles(t)
        return cache[t]

    with_dl = [m for m in raw if m.get("deadline")]
    ok = [m for m in ms if m["sched_ok"]]
    traded = [m for m in ok if candles_of(m["ticker"])]
    L = ["DEADLINE DECAY BACKTEST (paper only; measured from the scheduled deadline)", "",
         f"Settled by-date markets found: {len(raw)}; deadline read: {len(with_dl)}; "
         f"deadline agrees with Kalshi's listed schedule: {len(ok)}; with price history: {len(traded)}",
         f"  of those, resolved Yes: {sum(m['yes_won'] for m in traded)}; Yes that closed 1+ day before the deadline: "
         f"{sum(m['yes_won'] and m['close'] < m['S'] - timedelta(days=1) for m in traded)}",
         "", ""]
    early = [m for m in ms if m["yes_won"] and m["close"] < m["S"] - timedelta(days=1)]
    nos = [m for m in ms if not m["yes_won"]]
    rate = lambda xs: sum(m["sched_ok"] for m in xs) / len(xs) if xs else 0
    L.insert(4, f"  schedule check keeps {rate(early):.0%} of early Yes closes and {rate(nos):.0%} of No markets "
                f"(must be similar, or the check is leaking the result)")
    cats = defaultdict(int)
    for m in traded:
        cats[m["_category"]] += 1
    L[-2] = f"  events: {len({m['event_ticker'] for m in traded})}; markets by category: " + ", ".join(
        f"{c} {n}" for c, n in sorted(cats.items(), key=lambda x: -x[1]))
    trades = replay(ms, candles_of)
    by_var = defaultdict(list)
    for x in trades:
        by_var[x["variant"]].append(x)
    L.append("Per variant (ROI = P/L after fees / money paid incl. fees; CI = 95% event-clustered bootstrap):")
    sums = {}
    for v in ["hazard", "hazard-maker"] + [f"simple-{k}d" for k in SIMPLE_KS]:
        sums[v] = summary(by_var[v])
        L.append("  " + fmt_summary(v, sums[v]))
    s = sums["hazard"]
    if s["n"]:
        L.append(f"  hazard Brier (No side): model {s['brier_model']:.4f} vs market mid {s['brier_market']:.4f}")
    L.append("")
    L.append("By category:")
    for v in ["hazard"] + [f"simple-{k}d" for k in SIMPLE_KS]:
        L.append(f"  {v}:")
        for c, cs in by_category(by_var[v]).items():
            L.append("    " + fmt_summary(c[:14], cs))
    L.append("")
    L.append("Calibration: Yes mid K days before the deadline vs how often Yes happened (markets still open then)")
    L.append("  K   band     n  events  avg Yes mid  Yes rate")
    for k, band, n, ev, mid, rate in calibration(ms, candles_of):
        L.append(f"  {k:>2}  {band:<7} {n:>4} {ev:>6}     {mid * 100:5.1f}c     {rate * 100:5.1f}%")
    text = "\n".join(L)
    print(text)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(text + "\n", encoding="utf-8")
    if log:
        write_log(trades)
        print(f"\n{len(trades)} backtest rows written to {LOG_PATH.name}")
    return sums, trades
