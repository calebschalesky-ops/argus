"""Backtest: replay the oil engine on past, already-settled Kalshi events.

Plain words: for each past event we go back to one moment before it closed
(the "replay time"), rebuild what Argus would have known at that moment, and
price every range with the same model the live engine uses. Then we compare
with the prices Kalshi was actually showing at that moment and score the
call against the known result.

No look-ahead, on purpose:
  - Prices come from Kalshi's public hourly candles, and only the candle that
    ENDS at the replay time is used (its closing bid/ask = the quote then).
  - Only markets that already existed at the replay time are priced.
  - Volatility uses only settlements that closed BEFORE the replay time, and
    FRED spot (fallback only) lags a week, roughly when it is published.
  - The anchor is the ladder midpoint at the replay time.
  - No news shift (there is no record of the headlines Argus would have read).

Run:   python -m argus.backtest                 (all four series, since 2026-03-01)
       python -m argus.backtest --series KXWTI --since 2026-08-01
Output: backtest_log.csv (same columns as prediction_log.csv, engine=oil-backtest).
It never touches prediction_log.csv. Read-only GETs, public data, no key.
"""
import argparse
import hashlib
import json
import threading
import time as _time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from . import engine, kalshi, log
from .events import event_days, events_between
from .markets import Contract, ladder_median, ladder_points, model_prob
from .model import ET, PriceCurve, ewma_vol, log_returns, parse_time, simple_vol, trading_days
from .prices import _CONTRACT, MIN_SETTLE_RETURNS, settle_returns

ROOT = Path(__file__).resolve().parent.parent
BACKTEST_LOG = ROOT / "backtest_log.csv"
CACHE = ROOT / "data" / "backtest_cache"
ENGINE_NAME = "oil-backtest"
DEFAULT_SINCE = "2026-03-01"     # the current market layout ("Above $X" daily ladders) starts here
# Every series the backtest can replay: where its anchor ladder lives, which daily series
# gives the settlement history for volatility, and whether the oil event calendar applies.
# Oil = the four live Argus series. The others widen the sample with the same model
# (Caleb, 2026-10-22): each uses its own daily settlements for volatility.
BT_SERIES = {s: {"ladder": v["ladder"], "vol": "KXWTI" if v["commodity"] == "WTI" else "KXBRENTD",
                 "fred": v["commodity"], "oil": True} for s, v in engine.SERIES.items()}
for _c in ("GOLD", "SILVER", "COPPER", "NATGAS"):
    BT_SERIES[f"KX{_c}D"] = {"ladder": f"KX{_c}D", "vol": f"KX{_c}D", "fred": None, "oil": False}
    BT_SERIES[f"KX{_c}W"] = {"ladder": f"KX{_c}W", "vol": f"KX{_c}D", "fred": None, "oil": False}
WEEKLY = {s for s in BT_SERIES if s.endswith("W")}
OIL = [s for s in BT_SERIES if BT_SERIES[s]["oil"]]
MAX_RPS = 8                      # stay under Kalshi's public rate limit
REPLAY_HOUR_ET = 10              # 10:00 AM New York: after the open, before any 2:30 PM settle
FRED_LAG_DAYS = 7                # FRED posts spot prices about a week late

# Read-only paths this module may GET. /historical holds markets settled before Kalshi's cutoff.
_ALLOWED = ("/series/", "/markets", "/historical/markets", "/historical/cutoff")


# ---------------- read-only fetching (cached) ----------------

_lock = threading.Lock()
_last = [0.0]


def _throttled_get(path, params):
    """GET with a shared pace limit and patient retries on HTTP 429 (rate limited)."""
    for attempt in range(12):
        with _lock:
            wait = _last[0] + 1 / MAX_RPS - _time.monotonic()
            if wait > 0:
                _time.sleep(wait)
            _last[0] = _time.monotonic()
        r = kalshi._session.get(kalshi.BASE + path, params=params, timeout=30)
        if r.status_code != 429:
            return r
        _time.sleep(min(2 * (attempt + 1), 15))
    return r


def _get(path, params=None):
    """One public GET, cached on disk so a re-run costs nothing."""
    if not path.startswith(_ALLOWED) or "/portfolio" in path or "/orders" in path:
        raise PermissionError(f"Argus is read-only; refusing path {path}")
    params = params or {}
    key = hashlib.sha1((path + json.dumps(params, sort_keys=True)).encode()).hexdigest()
    f = CACHE / f"{key}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    r = _throttled_get(path, params)
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    data = r.json()
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / ".gitignore").write_text("*\n")
    f.write_text(json.dumps(data), encoding="utf-8")
    return data


def settled_markets(series):
    """Every settled market of a series, from both the live and the historical tier.

    The lists are read fresh each run (new results keep arriving); candles, which
    never change once a market has settled, are cached.
    """
    out = {}
    for path, params in (("/historical/markets", {"series_ticker": series, "limit": 1000}),
                         ("/markets", {"series_ticker": series, "status": "settled", "limit": 1000})):
        data = _paged_fresh(path, "markets", params)
        for m in data:
            m["_tier"] = "historical" if path.startswith("/historical") else "live"
            out[m["ticker"]] = m
    return list(out.values())


def _paged_fresh(path, key, params):
    """Like _paged, but the market lists are read fresh each run (results keep arriving)."""
    params, out = dict(params), []
    for _ in range(200):
        r = _throttled_get(path, params)
        r.raise_for_status()
        data = r.json()
        out.extend(data.get(key, []))
        if not data.get("cursor"):
            break
        params["cursor"] = data["cursor"]
    return out


def _ts(dt):
    return int(dt.timestamp())


def candles_at(series, market, t):
    """Hourly candles for one market up to (and ending at) time t. Never later than t."""
    params = {"start_ts": _ts(t) - 3 * 3600, "end_ts": _ts(t), "period_interval": 60}
    if market["_tier"] == "historical":
        # Kalshi only offers per-market candles in the historical tier
        data = _get(f"/historical/markets/{market['ticker']}/candlesticks", params)
        return data.get("candlesticks", []) if data else []
    # live tier: one event-level request returns every market of the event at once
    data = _get(f"/series/{series}/events/{market['event_ticker']}/candlesticks", params)
    if not data:
        return []
    by = dict(zip(data.get("market_tickers", []), data.get("market_candlesticks", [])))
    return by.get(market["ticker"], [])


def _px(side):
    """Candle close price; the live tier says close_dollars, the historical tier says close."""
    if not side:
        return None
    v = side.get("close_dollars", side.get("close"))
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def quote_at(candles, t):
    """(yes_bid, yes_ask) at time t: the close of the latest candle that ended at or before t.

    A candle ending after t would contain prices from the future, so it is never used.
    The candle must also end within the last hour, or the quote is too old to trust.
    """
    ok = [c for c in candles if _ts(t) - 3600 < c["end_period_ts"] <= _ts(t)]
    if not ok:
        return None, None
    last = max(ok, key=lambda c: c["end_period_ts"])
    return _px(last.get("yes_bid")), _px(last.get("yes_ask"))


def contract_at(series, market, t):
    """A Contract with the bid/ask Kalshi showed at time t (not the final prices)."""
    c = Contract.from_api(market)
    bid, ask = quote_at(candles_at(series, market, t), t)
    c.yes_bid = bid if bid else None
    c.yes_ask = ask if ask and ask < 1 else None
    c.no_ask = round(1 - bid, 4) if bid else None
    c.no_bid = round(1 - ask, 4) if ask and ask < 1 else None
    return c


# ---------------- what was known at the replay time ----------------

def existed_at(market, t):
    return parse_time(market["open_time"]) <= t < parse_time(market["close_time"])


def replay_time(series, close):
    """The moment we pretend to run Argus.

    Daily events: 10:00 AM New York on the settle day.
    Weekly events: 10:00 AM New York on the Wednesday of settle week.
    """
    d = close.astimezone(ET).date()
    if series in WEEKLY:
        d = d - timedelta(days=(d.weekday() - 2) % 7)
    return datetime.combine(d, time(REPLAY_HOUR_ET), ET).astimezone(timezone.utc)


def settle_history(markets):
    """[(close_time, value, contract)] one per event, oldest first; contract read like prices.py does."""
    by_event = {}
    for m in markets:
        v = m.get("expiration_value")
        try:
            value = float(v)
        except (TypeError, ValueError):
            continue
        g = _CONTRACT.search(m.get("rules_primary") or "")
        by_event[m["event_ticker"]] = (m["close_time"], value, g.group(1) if g else "")
    return sorted(by_event.values(), key=lambda h: h[0])


def history_before(hist, t):
    """Only settlements that had already happened before time t."""
    return [h for h in hist if parse_time(h[0]) < t]


def fred_before(fred, t):
    """FRED (date, price) rows that would have been published by time t."""
    cutoff = (t.astimezone(ET) - timedelta(days=FRED_LAG_DAYS)).date().isoformat()
    return [r for r in fred if r[0] <= cutoff]


def vol_at(hist, t, fred=()):
    """Same rule as prices.estimate_vol, but only with data known before t."""
    past = history_before(hist, t)
    rets = settle_returns(past)[-40:]
    pair = [v for v in (simple_vol(rets), ewma_vol(rets)) if v]
    if len(rets) >= MIN_SETTLE_RETURNS and pair:
        return max(pair), "Kalshi settlements", (past[-1] if past else None)
    spot = fred_before(list(fred), t)[-61:]
    fv = ewma_vol(log_returns([v for _, v in spot])) if len(spot) > 2 else None
    if fv:
        return fv, "FRED spot (fallback)", (past[-1] if past else None)
    return 0.025, "fixed 2.5% (no data)", (past[-1] if past else None)


def _fred(commodity):
    try:
        from .prices import fred_series
        return fred_series(commodity, last_n=5000)
    except Exception:
        return []


# ---------------- the replay ----------------

def anchor_at(ladder_series, ladder_events, close, t):
    """Ladder midpoint at time t. Prefer the ladder that settles at the same moment;
    if that one is not open yet (weekly events on a Wednesday), use the soonest-closing
    open ladder, which tracks the same futures contract today."""
    live = [(e, ms) for e, ms in ladder_events.items()
            if ms and parse_time(ms[0]["close_time"]) > t
            and any(existed_at(m, t) for m in ms)]
    if not live:
        return None, "none", ""
    same = [x for x in live if ms_close(x[1]) == close]
    e, ms = same[0] if same else min(live, key=lambda x: ms_close(x[1]))
    pts = ladder_points([contract_at(ladder_series, m, t) for m in ms if existed_at(m, t)])
    mid = ladder_median(pts)
    kind = (f"Kalshi {ladder_series} ladder 50% point at replay"
            + ("" if same else f" ({e}, nearest open ladder)"))
    return mid, kind, _contract_of(ms[0])


def _contract_of(m):
    return (m.get("custom_strike") or {}).get("front_month_contract", "")


def ms_close(ms):
    return parse_time(ms[0]["close_time"])


def replay_event(series, event_ticker, markets, ladder_events, hist, fred):
    """Price one past event as of its replay time. Returns (Result | None, replay_time, reason)."""
    info = BT_SERIES[series]
    close = ms_close(markets)
    t = replay_time(series, close)
    alive = [m for m in markets if existed_at(m, t)]
    if not alive:
        return None, t, "no markets open at replay time"
    contracts = sorted((contract_at(series, m, t) for m in alive), key=Contract.sort_key)
    anchor, kind, a_contract = anchor_at(info["ladder"], ladder_events, close, t)
    if a_contract and _contract_of(markets[0]) and a_contract != _contract_of(markets[0]):
        kind += f" [contract {a_contract} vs event {_contract_of(markets[0])}]"
    vol, vol_src, last = vol_at(hist, t, fred)
    if anchor is None:
        if not last:
            return None, t, "no anchor"
        anchor, kind = last[1], f"last official settle {last[0][:10]}"
    days = trading_days(t, close)
    evs = events_between(t.astimezone(ET), close.astimezone(ET)) if info["oil"] else []
    ev_days = event_days(evs)
    curve = PriceCurve(anchor, vol, days, ev_days, 0.0)
    res = engine.Result(series, event_ticker, series, close, anchor, kind, last, vol, days,
                        evs, ev_days, vol_notes={"vol_source": vol_src})
    for c in contracts:
        p = model_prob(c, curve.cdf)
        side, price, gap, ev = engine._pick_side(p, c)
        row = engine.Row(c, p, side, price, gap, ev)
        if gap is not None and gap >= engine.WORTH_A_LOOK_PTS:
            row.flag = "WORTH A LOOK"
        res.rows.append(row)
    return res, t, ""


def rows_for(res, t, markets):
    """Log rows for a replayed event, already settled against the known results."""
    by_ticker = {m["ticker"]: m for m in markets}
    mids = {r.c.ticker: r.c for r in res.rows}
    out = []
    for row in engine.log_rows(res):
        m = by_ticker[row["market_ticker"]]
        c = mids[row["market_ticker"]]
        row["engine"] = ENGINE_NAME if res.series in OIL else "commodity-backtest"
        row["logged_at_utc"] = t.strftime("%Y-%m-%dT%H:%M:%SZ")
        row["call_id"] = hashlib.sha1(f"{row['market_ticker']}@{row['logged_at_utc']}".encode()).hexdigest()[:10]
        ym = ((c.yes_bid or 0) + c.yes_ask) / 2 if c.yes_ask else (c.yes_bid or "")
        p_yes = float(row["model_prob"]) if row["side"] == "YES" else 1 - float(row["model_prob"])
        row["notes"] += f"; replay_at={row['logged_at_utc']}; p_yes={p_yes:.4f}" + (
            f"; yes_mid={ym:.4f}" if ym != "" else "")
        try:
            value = float(m.get("expiration_value"))
        except (TypeError, ValueError):
            value = None
        log.settle_row(row, value, m.get("result") == "yes")
        out.append(row)
    return out


def run(series_list, since, out_path=BACKTEST_LOG, verbose=True):
    all_rows, skipped = [], []
    cache = {}

    def markets_of(s):
        if s not in cache:
            cache[s] = settled_markets(s)
        return cache[s]

    since_dt = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
    for s in series_list:
        info = BT_SERIES[s]
        ms = [m for m in markets_of(s) if m.get("result") in ("yes", "no")]
        events = defaultdict(list)
        for m in ms:
            events[m["event_ticker"]].append(m)
        ladder = defaultdict(list)
        for m in markets_of(info["ladder"]):
            ladder[m["event_ticker"]].append(m)
        hist = settle_history(markets_of(info["vol"]))
        fred = _fred(info["fred"]) if info["fred"] else []
        todo = sorted((e for e, x in events.items() if ms_close(x) >= since_dt), key=lambda e: ms_close(events[e]))
        if verbose:
            print(f"{s}: replaying {len(todo)} settled events since {since} ...", flush=True)

        def one(e):
            try:
                res, t, why = replay_event(s, e, events[e], ladder, hist, fred)
                if res is None:
                    return [], (e, why)
                return rows_for(res, t, events[e]), None
            except Exception as ex:          # one bad event never stops the backtest
                return [], (e, f"error: {ex}")

        with ThreadPoolExecutor(max_workers=8) as pool:
            for rows, skip in pool.map(one, todo):
                all_rows.extend(rows)
                if skip:
                    skipped.append((s,) + skip)
    log.write_log(all_rows, out_path)
    return all_rows, skipped


# ---------------- report ----------------

def fair_brier(rows):
    """Brier of YES probabilities: model p_yes vs the Yes MIDPOINT (no spread penalty on the market)."""
    pm, pk = [], []
    for r in rows:
        n = dict(x.split("=", 1) for x in r["notes"].split("; ") if "=" in x)
        if "p_yes" not in n or "yes_mid" not in n:
            continue
        r_yes = int(r["won"]) if r["side"] == "YES" else 1 - int(r["won"])
        pm.append((float(n["p_yes"]), r_yes))
        pk.append((float(n["yes_mid"]), r_yes))
    return log.brier(pm), log.brier(pk), len(pm)


def event_wins(rows):
    """Per event: did the model's Brier beat the market's (side-ask basis)?"""
    by = defaultdict(list)
    for r in rows:
        by[r["event_ticker"]].append(r)
    wins = 0
    for rs in by.values():
        bm = log.brier([(float(r["model_prob"]), int(r["won"])) for r in rs])
        bk = log.brier([(float(r["market_price"]), int(r["won"])) for r in rs])
        wins += bm < bk
    return wins, len(by)


def report(rows, skipped=()):
    pct = lambda x: "n/a" if x is None else f"{x * 100:.1f}%"
    b = lambda x: "n/a" if x is None else f"{x:.4f}"
    lines = ["ARGUS OIL BACKTEST (replayed, paper only, no news)"]
    groups = [(s, [r for r in rows if r["series"] == s]) for s in BT_SERIES]
    oil = [r for r in rows if r["series"] in OIL]
    groups += [("ALL OIL", oil)] if oil and len(oil) != len(rows) else []
    groups += [("ALL", rows)]
    for s, rs in groups:
        if not rs:
            continue
        card = log.scorecard(rs)
        fm, fk, n = fair_brier(rs)
        w, ne = event_wins(rs)
        lines += [
            f"\n== {s} ==",
            f"  Events replayed: {ne}   calls: {card['settled_calls']}   model beat market (Brier) in {w}/{ne} events",
            f"  Brier, chosen side vs its ask:   model {b(card['brier_model'])}  market {b(card['brier_market'])}",
            f"  Brier, Yes prob vs Yes midpoint: model {b(fm)}  market {b(fk)}   (n={n}; lower is better)",
            f"  Paper bets: {card['paper_bets']}  won {card['paper_wins']} ({pct(card['win_rate'])})  "
            f"P/L after fees ${card['pnl_after_fees']:+.2f} on ${card['staked']:.2f} staked (ROI {pct(card['roi'])})",
            "  Calibration   n    predicted  won",
        ]
        for x in card["calibration"]:
            lines.append(f"  {x['bin']:<10} {x['n']:>5}   {x['avg_predicted'] * 100:6.1f}%  {x['actual_win_rate'] * 100:6.1f}%")
    if skipped:
        lines.append(f"\nSkipped events: {len(skipped)}")
        for s, e, why in skipped[:15]:
            lines.append(f"  {s} {e}: {why}")
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m argus.backtest",
                                description="Replay the oil engine on past settled Kalshi events (paper only).")
    p.add_argument("--series", nargs="*", default=OIL, choices=list(BT_SERIES),
                   help="default: the four oil series; add e.g. KXGOLDD KXGOLDW to widen the sample")
    p.add_argument("--out", default=str(BACKTEST_LOG), help="where to write the replayed calls")
    p.add_argument("--since", default=DEFAULT_SINCE, help="first settle date to replay (YYYY-MM-DD)")
    p.add_argument("--report-only", action="store_true", help="just print the scorecard of backtest_log.csv")
    a = p.parse_args(argv)
    if a.report_only:
        print(report(log.read_log(a.out)))
        return
    rows, skipped = run(a.series, a.since, a.out)
    print(f"\nWrote {len(rows)} replayed calls to {a.out}\n")
    print(report(rows, skipped))


if __name__ == "__main__":
    main()
