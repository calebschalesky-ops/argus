"""Weather backtest: replay past settled temperature events with only what was known then.

For each past event (day D) we pretend to run Argus at the replay time (default 4:00 PM
New York on D-1, when the market has been open six hours and the low and high are both
still ahead). At that moment we use:
  - the latest NBM forecast published by then (runtime + 2 h <= replay time);
  - bias and spread from past days whose result was public before then, each scored
    against the forecast made the same number of hours ahead;
  - Kalshi's quote from the hourly candle that ended at or before then
    (argus.backtest's historical price route, imported, not copied).
Each call is then scored against Kalshi's actual result. Output: backtest_weather_log.csv
(engine=weather-backtest), which `python -m argus board` reads.
"""
import hashlib
import time as _time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone

import requests

from .. import backtest as oil_bt
from .. import log
from ..board import verdict
from . import engine, nbm, stations
from .truth import settled_by_day

BACKTEST_LOG = engine.ROOT / "backtest_weather_log.csv"
ENGINE_NAME = "weather-backtest"
REPLAY_HOUR_ET = 16
DEFAULT_SINCE = "2026-05-01"


def replay_time(day, hour=REPLAY_HOUR_ET):
    """4:00 PM New York on the day before the target day."""
    return datetime.combine(day - timedelta(days=1), time(hour), engine.ET).astimezone(timezone.utc)


def _existed(m, t):
    p = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
    return p(m["open_time"]) <= t < p(m["close_time"])


def replay_event(series, event_ticker, markets, fc_index, truth, hour=REPLAY_HOUR_ET):
    day = stations.target_date(event_ticker)
    t = replay_time(day, hour)
    alive = [m for m in markets if _existed(m, t) and m.get("strike_type")]
    if not alive:
        return None, t, "no markets open at replay time"
    contracts = [oil_bt.contract_at(series, m, t) for m in alive]
    if not any(c.yes_ask or c.yes_bid for c in contracts):
        return None, t, "no Kalshi quote at replay time"
    res, why = engine.price_event(series, event_ticker, contracts, fc_index, truth, t)
    return res, t, why


def rows_for(res, markets):
    by = {m["ticker"]: m for m in markets}
    out = []
    for row in engine.log_rows(res, ENGINE_NAME):
        m = by[row["market_ticker"]]
        row["call_id"] = hashlib.sha1(f"{row['market_ticker']}@{row['logged_at_utc']}".encode()).hexdigest()[:10]
        row["notes"] += f"; replay_at={row['logged_at_utc']}"
        try:
            value = float(m.get("expiration_value"))
        except (TypeError, ValueError):
            value = None
        log.settle_row(row, value, m.get("result") == "yes")
        out.append(row)
    return out


def run_series(series, since, until=None, hour=REPLAY_HOUR_ET, verbose=True):
    cli, icao, tz, name, kind = stations.info(series)
    ms = [m for m in oil_bt.settled_markets(series) if m.get("result") in ("yes", "no")]
    truth = settled_by_day(ms)
    events = defaultdict(list)
    for m in ms:
        events[m["event_ticker"]].append(m)
    since_d = date.fromisoformat(since)
    until_d = date.fromisoformat(until) if until else date.today()
    todo = sorted(e for e in events if stations.target_date(e) and since_d <= stations.target_date(e) <= until_d)
    first = min((stations.target_date(e) for e in todo), default=since_d)
    fcs = nbm.index(nbm.history(icao, date(first.year - 3, 1, 1), until_d))
    if verbose:
        print(f"{series} ({icao} {kind}): replaying {len(todo)} settled events since {since} ...", flush=True)

    def one(e):
        for attempt in range(4):
            try:
                res, t, why = replay_event(series, e, events[e], fcs, truth, hour)
                if res is None:
                    return [], (series, e, why)
                return rows_for(res, events[e]), None
            except requests.HTTPError as ex:        # Kalshi rate limit (other sessions read too): wait, retry
                if attempt == 3:
                    return [], (series, e, f"error: {ex}")
                _time.sleep(20 * (attempt + 1))
            except Exception as ex:                 # one bad event never stops the backtest
                return [], (series, e, f"error: {ex.__class__.__name__}: {ex}")

    rows, skipped = [], []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for rs, skip in pool.map(one, todo):
            rows.extend(rs)
            if skip:
                skipped.append(skip)
    return rows, skipped


def run(series_list, since, until=None, out_path=BACKTEST_LOG, hour=REPLAY_HOUR_ET):
    all_rows, skipped = [], []
    for s in series_list:
        try:
            rows, sk = run_series(s, since, until, hour)
        except Exception as ex:                      # one bad series never stops the rest
            rows, sk = [], [(s, "-", f"series failed: {ex.__class__.__name__}: {ex}")]
        all_rows.extend(rows)
        skipped.extend(sk)
        log.write_log(all_rows, out_path)            # saved after every series
    return all_rows, skipped


# ---------------- report ----------------

def _notes(r):
    return dict(x.split("=", 1) for x in r["notes"].split("; ") if "=" in x)


def station_bias(rows):
    """Average (settled - raw NBM) per event, i.e. the station bias measured over the backtest."""
    seen = {}
    for r in rows:
        n = _notes(r)
        if r["event_ticker"] in seen or not r.get("settle_value") or "nbm_raw" not in n:
            continue
        seen[r["event_ticker"]] = float(r["settle_value"]) - float(n["nbm_raw"])
    v = list(seen.values())
    if not v:
        return None, None, 0
    mae = sum(abs(x) for x in v) / len(v)
    return sum(v) / len(v), mae, len(v)


def corrected_mae(rows):
    seen = {}
    for r in rows:
        if r["event_ticker"] in seen or not r.get("settle_value"):
            continue
        seen[r["event_ticker"]] = abs(float(r["settle_value"]) - float(r["anchor_price"]))
    return sum(seen.values()) / len(seen) if seen else None


def report(rows, skipped=()):
    pct = lambda x: "n/a" if x is None else f"{x * 100:.1f}%"
    b = lambda x: "n/a" if x is None else f"{x:.4f}"
    L = ["ARGUS WEATHER BACKTEST (replayed at 4 PM ET the day before; paper only)"]
    groups = [(s, [r for r in rows if r["series"] == s]) for s in sorted({r["series"] for r in rows})]
    groups.append(("ALL WEATHER", rows))
    for s, rs in groups:
        if not rs:
            continue
        card = log.scorecard(rs)
        fm, fk, n = oil_bt.fair_brier(rs)
        w, ne = oil_bt.event_wins(rs)
        bias, mae, nb = station_bias(rs)
        cm = corrected_mae(rs)
        L += [f"\n== {s} ==  verdict vs gate: {verdict(card, ne)}",
              f"  Events: {ne}   calls: {card['settled_calls']}   model beat market (Brier) in {w}/{ne} events",
              f"  Station bias (settled - NBM): {'n/a' if bias is None else f'{bias:+.2f}F'}   NBM miss {'n/a' if mae is None else f'{mae:.2f}F'}"
              f"  after correction {'n/a' if cm is None else f'{cm:.2f}F'} (avg abs, n={nb})",
              f"  Brier, chosen side vs its ask:   model {b(card['brier_model'])}  market {b(card['brier_market'])}",
              f"  Brier, Yes prob vs Yes midpoint: model {b(fm)}  market {b(fk)}   (n={n}; lower is better)",
              f"  Paper bets: {card['paper_bets']}  won {card['paper_wins']} ({pct(card['win_rate'])})  "
              f"P/L after fees ${card['pnl_after_fees']:+.2f} on ${card['staked']:.2f} staked (ROI {pct(card['roi'])})"]
        if s == "ALL WEATHER":
            L.append("  Calibration   n    predicted  won")
            for x in card["calibration"]:
                L.append(f"  {x['bin']:<10} {x['n']:>5}   {x['avg_predicted'] * 100:6.1f}%  {x['actual_win_rate'] * 100:6.1f}%")
    if skipped:
        L.append(f"\nSkipped events: {len(skipped)}")
        for s, e, why in list(skipped)[:15]:
            L.append(f"  {s} {e}: {why}")
    return "\n".join(L)
