"""Longshot study: what does each price bucket return on Kalshi, taking the ask vs resting at bid + 1c?

Plain words: we look at settled Kalshi markets, read the quote one day before each closed,
and pretend to buy each side two ways:
  - TAKER: pay the ask, plus the taker fee.
  - MAKER: rest a limit order one cent above the best bid (filters.maker_price), plus the maker
    fee only where the series charges one. Two fill assumptions, both reported:
      * "every order fills" (optimistic: ignores that you only get filled when someone wants to sell),
      * "fills only if a later trade printed at or through our price before close" (we would have
        been first in line at a new best price, so a trade there would have hit us). This keeps
        the adverse selection: the orders that fill are disproportionately the losing ones.
Return per dollar = (payout - price - fee) / (price + fee), the same measure as Burgi, Deng &
Whelan (fee per contract taken from a 100-contract order, as they do).

Sample: random series in every category (fixed seed), no 15-minute, hourly or multi-leg combo
series, markets with 1,000+ contracts traded, open at least a day, closing from 2025-05-01 on
(after the paper's April 2025 cut-off, so this is out of sample). At most a few events per
category per week and a few markets per event, so one busy series cannot dominate.

Also re-scores the commodity/oil backtest logs (their rows hold the replay-time bid and ask).

Run:   python -m argus.buckets              (fetch what is not cached, then report)
       python -m argus.buckets --report-only
Read-only GETs through argus.backtest's cached, rate-limited fetcher. It never trades.
"""
import argparse
import json
import random
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import backtest, fees, filters, kalshi, log
from .model import parse_time

ROOT = Path(__file__).resolve().parent.parent
OBS_FILE = ROOT / "data" / "cache" / "maker_longshot_obs.json"
BUCKETS = [("<10c", 0.00, 0.10), ("10-30c", 0.10, 0.30), ("30-50c", 0.30, 0.50),
           ("50-70c", 0.50, 0.70), ("70-90c", 0.70, 0.90), ("90-99c", 0.90, 1.00)]
SINCE = "2025-05-01"
HORIZON_H = 24
MIN_VOLUME = 1000          # contracts traded over the market's life
SERIES_PER_CAT = 30
EVENTS_PER_WEEK_CAT = 3
MARKETS_PER_EVENT = 3
LOT = 100                  # fee rounding as for a 100-contract order (the paper's method)


def bucket_of(price):
    for label, lo, hi in BUCKETS:
        if lo - 1e-9 <= price < hi - 1e-9:
            return label
    return None


def ret(price, won, fee):
    """Return per dollar put in (price + fee)."""
    cost = price + fee
    return ((1.0 if won else 0.0) - cost) / cost


def lot_fee(price, maker=False, charged=True, mult=1):
    if maker:
        return fees.maker_fee(price, LOT, mult, charged) / LOT
    return fees.taker_fee(price, LOT, mult) / LOT


# ---------------- sampling ----------------

def series_table():
    """ticker -> series object (category, frequency, fee_type, fee_multiplier)."""
    return {s["ticker"]: s for s in kalshi.all_series()}


def skip_series(s):
    t, freq = s.get("ticker", ""), s.get("frequency") or ""
    return (freq in ("fifteen_min", "hourly") or t.endswith("15M") or t.startswith("KXMVE")
            or "combo" in (s.get("fee_type") or ""))


def pick_series(table, per_cat=SERIES_PER_CAT, seed=5):
    rng = random.Random(seed)
    by = defaultdict(list)
    for t, s in table.items():
        if not skip_series(s):
            by[s.get("category") or "Other"].append(t)
    out = []
    for cat in sorted(by):
        ts = sorted(by[cat])
        rng.shuffle(ts)
        out += ts[:per_cat]
    return out


def list_settled(series, max_pages=3):
    """Settled markets of one series from both tiers (cached snapshot)."""
    out = {}
    for path, tier, params in (("/historical/markets", "historical", {"series_ticker": series, "limit": 1000}),
                               ("/markets", "live", {"series_ticker": series, "status": "settled", "limit": 1000})):
        for _ in range(max_pages):
            data = backtest._get(path, params)
            for m in (data or {}).get("markets", []):
                m["_tier"] = tier
                out.setdefault(m["ticker"], m)
            if not (data or {}).get("cursor"):
                break
            params = dict(params, cursor=data["cursor"])
    return list(out.values())


def early_close(m):
    """Closed a day or more before it was expected to: an 'it happened' early close.

    Reading a price one day before such a close picks a moment nobody could have chosen in advance."""
    exp = m.get("expected_expiration_time")
    return bool(exp) and parse_time(m["close_time"]) < parse_time(exp) - timedelta(days=1)


def usable(m, since):
    try:
        vol = float(m.get("volume_fp") or m.get("volume") or 0)
    except ValueError:
        return False
    if m.get("result") not in ("yes", "no") or vol < MIN_VOLUME or m.get("mve_collection_ticker"):
        return False
    close, opened = parse_time(m["close_time"]), parse_time(m["open_time"])
    return close >= since and opened <= close - timedelta(hours=HORIZON_H)


def sample(markets, table, seed=7):
    rng = random.Random(seed)
    by_event = defaultdict(list)
    for m in markets:
        by_event[m["event_ticker"]].append(m)
    groups = defaultdict(list)
    for e, ms in by_event.items():
        close = parse_time(ms[0]["close_time"])
        week = (close - timedelta(days=close.weekday())).date().isoformat()
        groups[(week, (table.get(e.split("-")[0]) or {}).get("category") or "Other")].append(e)
    out = []
    for key in sorted(groups):
        evs = sorted(groups[key])
        rng.shuffle(evs)
        for e in evs[:EVENTS_PER_WEEK_CAT]:
            ms = sorted(by_event[e], key=lambda m: m["ticker"])
            rng.shuffle(ms)
            out += ms[:MARKETS_PER_EVENT]
    return out


# ---------------- quotes and fills ----------------

def _f(side, key="close"):
    if not side:
        return None
    v = side.get(f"{key}_dollars", side.get(key))
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def candles(series, m, start, end):
    params = {"start_ts": int(start.timestamp()), "end_ts": int(end.timestamp()), "period_interval": 60}
    if m["_tier"] == "historical":
        d = backtest._get(f"/historical/markets/{m['ticker']}/candlesticks", params)
        return (d or {}).get("candlesticks", [])
    d = backtest._get(f"/series/{series}/events/{m['event_ticker']}/candlesticks", params)
    if not d:
        return []
    return dict(zip(d.get("market_tickers", []), d.get("market_candlesticks", []))).get(m["ticker"], [])


def quote_and_fills(cs, t, close):
    """Yes bid/ask at t (from the candle that ended in the hour up to t), and the lowest and
    highest trade prices printed after t up to the close (None if nothing traded)."""
    ts, cts = int(t.timestamp()), int(close.timestamp())
    before = [c for c in cs if ts - 3600 < c["end_period_ts"] <= ts]
    if not before:
        return None
    c = max(before, key=lambda c: c["end_period_ts"])
    bid, ask = _f(c.get("yes_bid")), _f(c.get("yes_ask"))
    lows, highs = [], []
    for c in cs:
        if ts < c["end_period_ts"] <= cts + 3600 and float(c.get("volume_fp") or c.get("volume") or 0) > 0:
            lo, hi = _f(c.get("price"), "low"), _f(c.get("price"), "high")
            if lo is not None:
                lows.append(lo)
            if hi is not None:
                highs.append(hi)
    return {"yes_bid": bid, "yes_ask": ask, "trade_low_after": min(lows) if lows else None,
            "trade_high_after": max(highs) if highs else None}


def observe(m, table, t=None):
    s = m["event_ticker"].split("-")[0]
    close = parse_time(m["close_time"])
    t = t or close - timedelta(hours=HORIZON_H)
    q = quote_and_fills(candles(s, m, t - timedelta(hours=3), close), t, close)
    if not q:
        return None
    meta = table.get(s) or {}
    charged, mult = fees.series_maker_fees(meta)
    return dict(q, series=s, category=meta.get("category") or "Other", event=m["event_ticker"],
                ticker=m["ticker"], tier=m["_tier"], t=t.isoformat(), close=m["close_time"],
                yes_won=m["result"] == "yes", early=early_close(m), maker_fees=charged, mult=mult)


def gather(since=SINCE, verbose=True):
    table = series_table()
    picked = pick_series(table)
    since_dt = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
    if verbose:
        print(f"Listing settled markets of {len(picked)} series ...", flush=True)
    with ThreadPoolExecutor(8) as pool:
        lists = list(pool.map(lambda s: _safe(list_settled, s) or [], picked))
    markets = [m for ms in lists for m in ms if usable(m, since_dt)]
    chosen = sample(markets, table)
    if verbose:
        print(f"{len(markets)} usable markets; sampled {len(chosen)}. Reading quotes ...", flush=True)
    with ThreadPoolExecutor(8) as pool:
        obs = [o for o in pool.map(lambda m: _safe(observe, m, table), chosen) if o]
    OBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    OBS_FILE.write_text(json.dumps(obs), encoding="utf-8")
    return obs


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as ex:            # one bad series or market never stops the study
        print(f"  skipped {a[0] if isinstance(a[0], str) else a[0].get('ticker')}: {ex}", flush=True)
        return None


# ---------------- backtest logs -> observations ----------------

def obs_from_log_rows(rows):
    """Rebuild the replay-time Yes bid/ask of every market in a backtest log.

    Each row stores the chosen side's ask (market_price) and the Yes midpoint in `notes`, which
    together give both sides: YES rows -> ask = market_price, bid = 2*mid - ask;
    NO rows -> bid = 1 - market_price, ask = 2*mid - bid. The backtest writes mid = bid when
    no Yes ask existed (and mid = ask / 2 when no bid existed); those sides come back as None.
    """
    out = []
    for r in rows:
        n = dict(x.split("=", 1) for x in r["notes"].split("; ") if "=" in x)
        if "yes_mid" not in n or r.get("won") not in ("0", "1"):
            continue
        mid, px = float(n["yes_mid"]), float(r["market_price"])
        if r["side"] == "YES":
            ask, bid = px, round(2 * mid - px, 4)
            yes_won = r["won"] == "1"
        else:
            bid = round(1 - px, 4)
            # the backtest stores mid = bid when there was no Yes ask (no seller): no ask then
            ask = None if abs(mid - bid) < 1e-6 else round(2 * mid - bid, 4)
            yes_won = r["won"] == "0"
        out.append({"series": r["series"], "category": "Commodities", "event": r["event_ticker"],
                    "ticker": r["market_ticker"], "yes_bid": bid if bid > 0 else None,
                    "yes_ask": ask if ask and 0 < ask < 1 else None, "yes_won": yes_won, "early": False,
                    "maker_fees": False, "mult": 1, "trade_low_after": None, "trade_high_after": None,
                    "t": r["logged_at_utc"], "close": r["close_time_utc"]})
    return out


# ---------------- scoring ----------------

def bets(o):
    """Every way to buy this market: (side, mode, price, fee, won, filled)."""
    out = []
    for side in ("YES", "NO"):
        ask, bid = filters.side_book(side, o["yes_bid"], o["yes_ask"])
        won = o["yes_won"] if side == "YES" else not o["yes_won"]
        if ask and 0 < ask < 1:
            out.append((side, "taker", ask, lot_fee(ask, mult=o.get("mult", 1)), won, True))
        mp = filters.maker_price(bid, ask)
        if mp:
            filled = None
            lo, hi = o.get("trade_low_after"), o.get("trade_high_after")
            if side == "YES" and (lo is not None or hi is not None):
                filled = lo is not None and lo <= mp + 1e-9
            if side == "NO" and (lo is not None or hi is not None):
                filled = hi is not None and hi >= 1 - mp - 1e-9
            if lo is None and hi is None and o.get("tier"):
                filled = False             # nothing traded after t: a resting order never filled
            out.append((side, "maker", mp, lot_fee(mp, True, o.get("maker_fees", False), o.get("mult", 1)),
                        won, filled))
    return out


def _boot_ci(per_event, n_boot=400, seed=1):
    """95% interval of the mean return, resampling whole events (bets in one event move together)."""
    evs = list(per_event.values())
    if len(evs) < 5:
        return None, None
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        pick = [rng.choice(evs) for _ in evs]
        tot, n = sum(sum(x) for x in pick), sum(len(x) for x in pick)
        means.append(tot / n if n else 0)
    means.sort()
    return means[int(0.025 * n_boot)], means[int(0.975 * n_boot) - 1]


def table_rows(obs, max_spread=0.20):
    """Per bucket: taker, maker (all fill), maker (fill-checked) mean returns with event-bootstrap CIs."""
    cells = defaultdict(lambda: defaultdict(list))
    fills = defaultdict(lambda: [0, 0])
    for o in obs:
        if o["yes_bid"] and o["yes_ask"] and o["yes_ask"] - o["yes_bid"] > max_spread + 1e-9:
            continue
        for side, mode, price, fee, won, filled in bets(o):
            b = bucket_of(price)
            if not b:
                continue
            r = ret(price, won, fee)
            if mode == "taker":
                cells[(b, "taker")][o["event"]].append(r)
            else:
                cells[(b, "maker_all")][o["event"]].append(r)
                if filled is not None:
                    fills[b][1] += 1
                    if filled:
                        fills[b][0] += 1
                        cells[(b, "maker_filled")][o["event"]].append(r)
    out = []
    for label, _, _ in BUCKETS + [("ALL", 0, 0)]:
        row = {"bucket": label}
        for mode in ("taker", "maker_all", "maker_filled"):
            if label == "ALL":
                per = defaultdict(list)
                for (b, md), d in cells.items():
                    if md == mode:
                        for e, xs in d.items():
                            per[e] += xs
            else:
                per = cells.get((label, mode), {})
            xs = [x for v in per.values() for x in v]
            lo, hi = _boot_ci(per)
            row[mode] = {"n": len(xs), "mean": sum(xs) / len(xs) if xs else None, "lo": lo, "hi": hi}
        f = fills[label] if label != "ALL" else [sum(v[0] for v in fills.values()), sum(v[1] for v in fills.values())]
        row["fill_rate"] = f[0] / f[1] if f[1] else None
        out.append(row)
    return out


def format_table(rows, title):
    pct = lambda x: "   n/a" if x is None else f"{x * 100:+6.1f}%"
    ci = lambda c: "" if c["lo"] is None else f" [{c['lo'] * 100:+.0f},{c['hi'] * 100:+.0f}]"
    L = [title, "Bucket   | Taker (ask + fee)             | Maker bid+1c, every order fills | Maker, fills only if traded through",
         "         |   n    return [95% CI]        |   n    return [95% CI]          | fill%    n    return [95% CI]"]
    for r in rows:
        t, a, f = r["taker"], r["maker_all"], r["maker_filled"]
        fr = "  n/a" if r["fill_rate"] is None else f"{r['fill_rate'] * 100:4.0f}%"
        L.append(f"{r['bucket']:<8} | {t['n']:>5} {pct(t['mean'])}{ci(t):<14} | {a['n']:>5} {pct(a['mean'])}{ci(a):<16} | "
                 f"{fr} {f['n']:>5} {pct(f['mean'])}{ci(f)}")
    return "\n".join(L)


def report(obs, commodity_rows=None):
    parts = []
    main = [o for o in obs if not o.get("early")]
    cats = defaultdict(int)
    for o in main:
        cats[o["category"]] += 1
    parts.append(f"Broad sample: {len(main)} markets ({len(obs) - len(main)} early-closed left out), "
                 f"{len({o['event'] for o in main})} events, {len({o['series'] for o in main})} series. "
                 "By category: " + ", ".join(f"{k} {v}" for k, v in sorted(cats.items(), key=lambda kv: -kv[1])))
    parts.append(format_table(table_rows(main), "\nALL CATEGORIES, quote 24 h before close, spread <= 20c"))
    if commodity_rows:
        cobs = obs_from_log_rows(commodity_rows)
        parts.append(format_table(table_rows(cobs),
                                  f"\nCOMMODITY + OIL BACKTEST QUOTES ({len(cobs)} markets at the replay time; "
                                  "maker fill not checkable from the logs)"))
    return "\n".join(parts)


# ---------------- the backtests again, through the filter ----------------

FILTERED_LOG = ROOT / "backtest_filtered_log.csv"
HIST_CUTOFF = "2026-08-23T00:00:00Z"   # Kalshi moved markets settled before this to /historical (read 2026-10-22)


def _fill_after(o, t, close):
    """Trade prints after the replay time for one backtest market (for the maker fill check)."""
    m = {"ticker": o["ticker"], "event_ticker": o["event"], "close_time": close,
         "_tier": "historical" if close < HIST_CUTOFF else "live"}
    q = quote_and_fills(candles(o["series"], m, t - timedelta(hours=3), parse_time(close)), t, parse_time(close))
    return q or {}


def filtered_rows(rows, band=filters.DEFAULT_BAND, check_fills=True):
    """Re-score backtest log rows with the band, as taker and as maker (bid + 1c).

    Same model probabilities, same replay-time quotes; only the side choice, the price paid and
    which rows count as paper bets change. Maker bets count only if a later trade printed at or
    through the resting price before the close (else the row stays, with no bet: "unfilled")."""
    out = []
    for r in rows:
        n = dict(x.split("=", 1) for x in r["notes"].split("; ") if "=" in x)
        o = obs_from_log_rows([r])
        if not o or "p_yes" not in n:
            continue
        o, p_yes = o[0], float(n["p_yes"])
        kind = "oil" if r["engine"] == "oil-backtest" else "commodity"
        for pricing in ("taker", "maker"):
            best = None
            for side in ("YES", "NO"):
                ask, bid = filters.side_book(side, o["yes_bid"], o["yes_ask"])
                q = filters.check(p_yes if side == "YES" else 1 - p_yes, ask, bid, band)
                if q.price(pricing) is None or (pricing == "taker" and q.ask is None):
                    continue
                if best is None or q.gap(pricing) > best[1].gap(pricing):
                    best = (side, q)
            if not best:
                continue
            side, q = best
            flag = q.flag(pricing)
            bet = flag == "WORTH A LOOK"
            note = f"pricing={pricing}; flag={flag}; ask={q.ask}; maker_price={q.maker_price}; band={band.describe()}"
            if bet and pricing == "maker" and check_fills:
                f = _fill_after(o, parse_time(r["logged_at_utc"]), r["close_time_utc"])
                lo, hi = f.get("trade_low_after"), f.get("trade_high_after")
                mp = q.maker_price
                filled = (lo is not None and lo <= mp + 1e-9) if side == "YES" else (hi is not None and hi >= 1 - mp - 1e-9)
                note += f"; maker_fill={'yes' if filled else 'no'}"
                bet = filled
            won = (o["yes_won"] if side == "YES" else not o["yes_won"])
            price, fee = q.price(pricing), q.fee(pricing)
            row = dict(r)
            row.update({
                "call_id": f"{r['call_id']}-{pricing[0]}", "engine": f"{kind}-filtered-{pricing}", "side": side,
                "model_prob": f"{q.p_side:.4f}", "market_price": f"{price:.2f}", "gap_pts": f"{q.gap(pricing):.1f}",
                "worth_a_look": "1" if flag == "WORTH A LOOK" else "0", "paper_contracts": "1" if bet else "0",
                "fee_per_contract": f"{fee:.2f}", "won": "1" if won else "0",
                "pnl_after_fees": f"{fees.pnl(won, price, 1, fee):.2f}" if bet else "0.00",
                "notes": r["notes"].split("; flag=")[0] + "; " + note,
            })
            out.append(row)
    return out


def run_filtered(out_path=FILTERED_LOG):
    rows = []
    for name in ("backtest_log.csv", "backtest_commodities_log.csv"):
        rows += log.read_log(ROOT / name)
    out = filtered_rows(rows)
    log.write_log(out, out_path)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m argus.buckets", description=__doc__.split("\n")[0])
    p.add_argument("--since", default=SINCE)
    p.add_argument("--report-only", action="store_true")
    p.add_argument("--filtered", action="store_true",
                   help="re-score the oil and commodity backtests through the band (taker and maker) "
                        "into backtest_filtered_log.csv, then print their scorecards")
    a = p.parse_args(argv)
    if a.filtered:
        from . import board
        out = run_filtered()
        print(f"Wrote {len(out)} rows to {FILTERED_LOG.name}")
        rs = [x for x in board.board() if x["source"] == FILTERED_LOG.stem]
        print(board.format_board(rs))
        return
    obs = json.loads(OBS_FILE.read_text(encoding="utf-8")) if a.report_only else gather(a.since)
    logs = []
    for name in ("backtest_log.csv", "backtest_commodities_log.csv"):
        logs += log.read_log(ROOT / name)
    print(report(obs, logs))


if __name__ == "__main__":
    main()
