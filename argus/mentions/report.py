"""Summaries of mentions_log.csv: per strategy, and by price bucket."""
from collections import defaultdict
from datetime import datetime

from . import LOG_PATH
from .. import board, log

BUCKETS = [(0, 0.10), (0.10, 0.30), (0.30, 0.50), (0.50, 0.70), (0.70, 0.90), (0.90, 1.0)]


def _weeks(rows):
    ts = sorted(r["logged_at_utc"] for r in rows)
    if len(ts) < 2:
        return 1.0
    a, b = (datetime.fromisoformat(x.replace("Z", "+00:00")) for x in (ts[0], ts[-1]))
    return max((b - a).days / 7, 1.0)


def strategy_table(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["engine"]].append(r)
    out = []
    for eng, rs in sorted(by.items()):
        card = log.scorecard(rs)
        bets = [r for r in rs if r["status"] == "settled" and int(r["paper_contracts"] or 0) > 0]
        ev = len({r["event_ticker"] for r in bets})
        out.append({"engine": eng, "calls": len(rs), "bets": len(bets), "events": ev,
                    "wins": card["paper_wins"], "pnl": card["pnl_after_fees"], "staked": card["staked"],
                    "roi": card["roi"], "brier_model": card["brier_model"], "brier_market": card["brier_market"],
                    "bets_per_week": len(bets) / _weeks(rs),
                    "verdict": board.verdict(card, len({r["event_ticker"] for r in rs if r["status"] == "settled"}))})
    return out


def bucket_table(rows, engine):
    out = []
    rs = [r for r in rows if r["engine"] == engine and r["status"] == "settled"]
    for lo, hi in BUCKETS:
        b = [r for r in rs if lo <= float(r["market_price"]) < hi]
        if not b:
            continue
        bets = [r for r in b if int(r["paper_contracts"] or 0) > 0]
        staked = sum(float(r["market_price"]) for r in bets)
        out.append({"bucket": f"{int(lo * 100)}-{int(hi * 100)}c", "calls": len(b),
                    "avg_price": sum(float(r["market_price"]) for r in b) / len(b),
                    "avg_model": sum(float(r["model_prob"]) for r in b) / len(b),
                    "won": sum(r["won"] == "1" for r in b) / len(b),
                    "bets": len(bets), "pnl": sum(float(r["pnl_after_fees"]) for r in bets),
                    "roi": sum(float(r["pnl_after_fees"]) for r in bets) / staked if staked else None})
    return out


def fmt(rows):
    L = ["Strategy                      Calls  Bets Events  Wins     P/L    ROI  Brier model/mkt  Bets/wk  Verdict"]
    f = lambda x: " n/a " if x is None else f"{x:.3f}"
    for x in strategy_table(rows):
        roi = "  n/a" if x["roi"] is None else f"{x['roi'] * 100:+5.1f}%"
        L.append(f"{x['engine']:<28} {x['calls']:>6} {x['bets']:>5} {x['events']:>6} {x['wins']:>5} {x['pnl']:>+7.2f} {roi}"
                 f"  {f(x['brier_model'])}/{f(x['brier_market'])}  {x['bets_per_week']:>7.1f}  {x['verdict']}")
    for eng in sorted({r["engine"] for r in rows}):
        L.append(f"\n{eng} by side price (all settled calls; bets = paper bets in the bucket)")
        L.append("bucket     calls  price  model   won   bets     P/L    ROI")
        for b in bucket_table(rows, eng):
            roi = "  n/a" if b["roi"] is None else f"{b['roi'] * 100:+5.1f}%"
            L.append(f"{b['bucket']:<9} {b['calls']:>6} {b['avg_price']:.3f}  {b['avg_model']:.3f} {b['won']:.3f} "
                     f"{b['bets']:>6} {b['pnl']:>+7.2f} {roi}")
    return "\n".join(L)


def main():
    print(fmt(log.read_log(LOG_PATH)))
