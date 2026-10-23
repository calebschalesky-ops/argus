"""The strategy leaderboard: every strategy Argus runs, side by side, ranked by measured results.

Any file in the project folder named *_log.csv with the prediction-log columns is read
(prediction_log.csv, backtest_log.csv, smartmoney_log.csv, ...). Rows are grouped by
log file and engine, so oil, cross-venue, wallets, big trades, favorites and every
future engine are judged by the same numbers. Money should follow this table, not taste.
"""
from collections import defaultdict
from pathlib import Path

from . import log

GATE_EVENTS = 20   # settled events before a strategy's numbers mean anything


def gather(root=log.ROOT):
    groups = defaultdict(list)
    for path in sorted(Path(root).glob("*_log.csv")):
        for r in log.read_log(path):
            if "status" not in r or "model_prob" not in r:
                continue                       # not a prediction-log file
            groups[(path.stem, r.get("engine") or "?")].append(r)
    return groups


def verdict(card, n_events):
    if n_events < GATE_EVENTS:
        return f"TOO EARLY ({n_events}/{GATE_EVENTS} events)"
    beats = (card["brier_model"] is not None and card["brier_market"] is not None
             and card["brier_model"] < card["brier_market"])
    profitable = card["paper_bets"] > 0 and card["pnl_after_fees"] > 0
    if beats and profitable:
        return "PASS: edge after fees"
    if beats:
        return "BETTER ODDS, NO PROFIT YET"
    return "NO EDGE"


def board(root=log.ROOT):
    rows = []
    for (source, engine), rs in gather(root).items():
        settled = [r for r in rs if r["status"] == "settled"]
        card = log.scorecard(rs)
        n_events = len({r["event_ticker"] for r in settled})
        rows.append({"source": source, "engine": engine, "calls": len(rs), "settled": len(settled),
                     "events": n_events, "bets": card["paper_bets"], "pnl": card["pnl_after_fees"],
                     "roi": card["roi"], "brier_model": card["brier_model"],
                     "brier_market": card["brier_market"], "verdict": verdict(card, n_events)})
    rank = {"PASS: edge after fees": 0, "BETTER ODDS, NO PROFIT YET": 1}
    return sorted(rows, key=lambda x: (rank.get(x["verdict"], 2), -(x["roi"] or -9)))


def format_board(rows):
    f = lambda x: "  n/a " if x is None else f"{x:.4f}"
    L = ["ARGUS STRATEGY BOARD (paper only; money follows this table)", "",
         "Strategy                                 Calls Settled Events Bets   P/L    ROI   Brier model/market  Verdict"]
    for x in rows:
        roi = "  n/a" if x["roi"] is None else f"{x['roi'] * 100:+5.0f}%"
        L.append(f"{(x['source'].replace('_log', '') + ':' + x['engine'])[:40]:<40} {x['calls']:>5} {x['settled']:>7} {x['events']:>6} "
                 f"{x['bets']:>4} {x['pnl']:>+6.2f} {roi}  {f(x['brier_model'])}/{f(x['brier_market'])}   {x['verdict']}")
    if not rows:
        L.append("  (no logs yet)")
    L.append(f"\nPASS needs {GATE_EVENTS}+ settled events, better odds than the market (lower Brier), and paper profit after fees.")
    return "\n".join(L)
