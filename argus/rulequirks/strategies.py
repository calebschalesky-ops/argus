"""Rule-quirk strategies, replayed on settled Kalshi markets with no look-ahead.

Each one bets that the crowd prices the casual reading instead of the rules. Every bet is a
taker buy at the ask, 1 contract, only with a real book (bid within 5c of the ask), at a fixed
time before the scheduled close, from candles that ended before that time.

  strict     'above X' where the value can land exactly on X (the series has settled on one of
             its own lines BEFORE this market opened). Casual readers count X itself, so Yes
             should be too dear: buy NO when the Yes mid is 15-85c.
  inclusive  'at least X' on the same kind of series: X itself pays, so buy YES at 15-85c.
  tie        'Tie' / 'Draw' outcome markets (regulation-time rules make ties count): buy YES at 5-60c.

Engines: rulequirk-<type>-<horizon> (1h or 1d before close). Markets that closed early (the
event happened, so 'N hours before close' was not knowable) are left out.

model_prob (what the board calls the model) is walk-forward: the strategy's own win rate on
earlier bets that had ALREADY SETTLED at the time of the call (Laplace-smoothed); below 30 such
bets it is just the price paid. So "better Brier than the market" means the track record known
at the time beat the price.
"""
import bisect
import json
import random
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from . import DATA, LOG_PATH, corpus as C
from .classify import boundary_matters, threshold_side
from .mattered import boundary_kind, fifteen_series
from .. import fees, log

HORIZONS = ("1h", "1d")
BANDS = {"strict": (0.15, 0.85), "inclusive": (0.15, 0.85), "tie": (0.05, 0.60)}
RUNGS_PER_EVENT = 2
MAX_EVENTS_PER_SERIES = 40
TIE_PER_SERIES = 150
WALK_MIN_N = 30
TIE_NAMES = re.compile(r"^(?:tie|draw)\b|\bends in a (?:tie|draw)\b|^tie is the result", re.I)
SUMMARY = DATA / "backtest_summary.json"


def landable_since(markets):
    """series -> earliest settlement time at which one of its markets settled exactly on its own line."""
    first = {}
    for m in markets:
        if boundary_kind(m):
            s, t = C.series_of(m), C.parse_time(m.get("settlement_ts") or m["close_time"])
            if s not in first or t < first[s]:
                first[s] = t
    return first


def threshold_universe(markets, kind, skip):
    """Markets of type kind ('strict'/'inclusive') in series already known (before the market opened)
    to land on their lines; a fixed-seed sample of RUNGS_PER_EVENT rungs per event."""
    first = landable_since(markets)
    by_event = defaultdict(list)
    for m in markets:
        s = C.series_of(m)
        if s in skip or s not in first or m["result"] not in ("yes", "no") or C.early_closed(m):
            continue
        side, x = threshold_side(m)
        if side != kind or x is None or not boundary_matters(m, x):
            continue
        if not first[s] < C.parse_time(m["open_time"]):
            continue
        by_event[m["event_ticker"]].append(m)
    rng, per_series, out = random.Random(17), defaultdict(int), []
    for e in sorted(by_event):
        s = e.split("-", 1)[0]
        if per_series[s] >= MAX_EVENTS_PER_SERIES:
            continue
        per_series[s] += 1
        ms = sorted(by_event[e], key=lambda m: m["ticker"])
        rng.shuffle(ms)
        out.extend(ms[:RUNGS_PER_EVENT])
    return out


def tie_universe(markets, skip):
    """Tie / draw outcome markets, a fixed-seed sample of TIE_PER_SERIES per series."""
    per = defaultdict(list)
    for m in sorted(markets, key=lambda m: m["ticker"]):
        if (C.series_of(m) not in skip and m["result"] in ("yes", "no")
                and TIE_NAMES.search((m.get("yes_sub_title") or "").strip()) and not C.early_closed(m)):
            per[C.series_of(m)].append(m)
    rng, out = random.Random(23), []
    for s in sorted(per):
        rng.shuffle(per[s])
        out.extend(per[s][:TIE_PER_SERIES])
    return out


def observe(m, kind, h, cs):
    """One paper bet (or None) for market m at horizon h."""
    bid, ask = C.price_before_close(m, cs, h)
    if bid is None:
        return None
    mid = (bid + ask) / 2
    lo, hi = BANDS[kind]
    if not lo <= mid <= hi:
        return None
    side = "NO" if kind == "strict" else "YES"
    price = round(1 - bid, 4) if side == "NO" else ask
    won = (m["result"] == "no") if side == "NO" else (m["result"] == "yes")
    close = C.parse_time(m["close_time"])
    return {"kind": kind, "h": h, "ticker": m["ticker"], "event": m["event_ticker"], "series": C.series_of(m),
            "side": side, "price": price, "won": won, "t": (close - timedelta(seconds=C.HORIZONS[h])).isoformat(),
            "settled": (m.get("settlement_ts") or m["close_time"]), "value": m.get("expiration_value"),
            "label": (m.get("yes_sub_title") or m["ticker"])[:40], "close": m["close_time"]}


def walk_forward(obs):
    """Fill model_prob: win rate of the same engine's earlier bets already settled at call time."""
    obs.sort(key=lambda o: o["t"])
    done = sorted((C.parse_time(o["settled"]), o["won"]) for o in obs)
    for o in obs:
        t = C.parse_time(o["t"])
        k = bisect.bisect_left(done, (t,))
        if k >= WALK_MIN_N:
            o["model_prob"] = (sum(w for _, w in done[:k]) + 1) / (k + 2)
        else:
            o["model_prob"] = o["price"]
    return obs


def to_row(o):
    fee = fees.taker_fee(o["price"], 1)
    return {"logged_at_utc": o["t"], "engine": f"rulequirk-{o['kind']}-{o['h']}", "series": o["series"],
            "event_ticker": o["event"], "market_ticker": o["ticker"], "range_label": o["label"], "side": o["side"],
            "model_prob": f"{o['model_prob']:.4f}", "market_price": f"{o['price']:.4f}",
            "gap_pts": f"{(o['model_prob'] - o['price']) * 100:+.1f}", "worth_a_look": "", "paper_contracts": 1,
            "fee_per_contract": f"{fee:.2f}", "close_time_utc": o["close"], "status": "settled",
            "settle_value": o["value"] or "", "won": "1" if o["won"] else "0",
            "pnl_after_fees": f"{fees.pnl(o['won'], o['price'], 1):.2f}",
            "notes": f"backtest; taker at ask {o['h']} before close; real book (bid within 5c)"}


def run_kind(kind, ms, workers=4):
    def one(m):
        cs = C.candles(m)
        return [o for h in HORIZONS if (o := observe(m, kind, h, cs))]
    with ThreadPoolExecutor(workers) as ex:
        return [o for os_ in ex.map(one, ms) for o in os_]


def stats(obs):
    if not obs:
        return {"bets": 0}
    pnl = sum(fees.pnl(o["won"], o["price"], 1) for o in obs)
    staked = sum(o["price"] for o in obs)
    return {"bets": len(obs), "events": len({o["event"] for o in obs}), "series": len({o["series"] for o in obs}),
            "won": sum(o["won"] for o in obs), "avg_price": round(staked / len(obs), 3),
            "win_rate": round(sum(o["won"] for o in obs) / len(obs), 3), "pnl": round(pnl, 2),
            "roi": round(pnl / staked, 4) if staked else None,
            "brier_model": round(log.brier([(o["model_prob"], int(o["won"])) for o in obs]), 4),
            "brier_market": round(log.brier([(o["price"], int(o["won"])) for o in obs]), 4)}


def main(markets, series_meta, kinds=("strict", "inclusive", "tie")):
    skip = fifteen_series(series_meta)
    out_rows, summary = [], {}
    for kind in kinds:
        ms = tie_universe(markets, skip) if kind == "tie" else threshold_universe(markets, kind, skip)
        print(f"  {kind}: {len(ms)} markets to price", flush=True)
        obs = run_kind(kind, ms)
        for h in HORIZONS:
            sub = walk_forward([o for o in obs if o["h"] == h])
            summary[f"rulequirk-{kind}-{h}"] = stats(sub)
            out_rows.extend(to_row(o) for o in sub)
    summary["_unpriced_markets"] = len(set(C.UNREACHED))
    if LOG_PATH.exists():
        LOG_PATH.unlink()                    # a backtest log is rebuilt whole from the cache each run
    log.append(out_rows, LOG_PATH)
    DATA.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return report()


def report():
    if not SUMMARY.exists():
        return "No backtest yet: python -m argus.rulequirks backtest"
    from ..board import GATE_EVENTS
    s = json.loads(SUMMARY.read_text(encoding="utf-8"))
    L = ["RULE-QUIRK STRATEGIES (backtest, 1 contract, taker at the ask, fees in)", "",
         f"{'Engine':<26}{'Bets':>6}{'Events':>7}{'Win%':>7}{'AvgPx':>7}{'P/L':>8}{'ROI':>8}  Brier model/market  Verdict"]
    for e, x in s.items():
        if e.startswith("_"):
            continue
        if not x.get("bets"):
            L.append(f"{e:<26}{0:>6}   (no bets)")
            continue
        if x["events"] < GATE_EVENTS:
            v = f"TOO EARLY ({x['events']}/{GATE_EVENTS} events)"
        elif x["brier_model"] < x["brier_market"] and x["pnl"] > 0:
            v = "PASS: edge after fees"
        elif x["brier_model"] < x["brier_market"]:
            v = "BETTER ODDS, NO PROFIT YET"
        else:
            v = "NO EDGE"
        L.append(f"{e:<26}{x['bets']:>6}{x['events']:>7}{x['win_rate'] * 100:>6.1f}%{x['avg_price']:>7.2f}"
                 f"{x['pnl']:>+8.2f}{x['roi'] * 100:>+7.1f}%  {x['brier_model']:.4f}/{x['brier_market']:.4f}   {v}")
    return "\n".join(L)
