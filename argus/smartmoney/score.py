"""Scorecard per source: each frozen wallet, and the Kalshi big-trade flags.

A wallet or a big trade gives a SIDE, not a probability, so the log stores
model_prob = market_price (our delayed entry price) and gap = 0. The question is then
simply: do their sides win more often than the price we could have paid says they should?

    edge_pts = (wins - sum of entry prices) / n * 100      points of win rate above price
    z        = (wins - sum p) / sqrt(sum p(1-p))            how many standard errors that is
    P/L      = after the delay and after Kalshi's taker fee, 1 contract per call

FOLLOWABLE (all three):
  1. at least MIN_SETTLED = 100 settled calls,
  2. z >= MIN_Z = 3.0,
  3. P/L after fees and delay > 0.

Why 100 and 3.0. A call near 50c wins or loses like a coin, so over n calls the win rate
wobbles by 0.5/sqrt(n) from luck alone: +-5 pts at n=100, +-10 at n=25. We are testing
about 50 wallets at once, and among 50 skill-less wallets one will look like it beats
the market at z=2 by chance alone (each has a 2.3% shot). Raising the bar to z=3 (0.13%
each, about 6% that ANY of 50 gets there by luck) makes a false "followable" unlikely.
At n=100 and z=3 a wallet needs roughly a 15-point edge; at n=400 about 7.5 points.
Below 100 calls a few lucky long shots can carry the P/L, so nothing smaller is judged.
Calls must be independent: one call per wallet per market outcome (repeat fills merged).
"""
import math
from collections import defaultdict

from .. import log
from . import common

MIN_SETTLED = 100
MIN_Z = 3.0


def edge_stats(settled):
    n = len(settled)
    if not n:
        return {"n": 0, "edge_pts": None, "z": None}
    wins = sum(r["won"] == "1" for r in settled)
    ps = [float(r["market_price"]) for r in settled]
    var = sum(p * (1 - p) for p in ps)
    return {"n": n, "wins": wins, "avg_price": sum(ps) / n,
            "edge_pts": (wins - sum(ps)) / n * 100,
            "z": (wins - sum(ps)) / math.sqrt(var) if var > 0 else None}


def followable(card, edge):
    return (card["settled_calls"] >= MIN_SETTLED and edge["z"] is not None
            and edge["z"] >= MIN_Z and card["pnl_after_fees"] > 0)


def source_key(r):
    if r["engine"] == "wallet":
        n = common.note_fields(r["notes"])
        return ("wallet", n.get("wallet", ""), n.get("user", ""))
    n = common.note_fields(r["notes"])
    return ("bigtrade", n.get("reason", ""), "")


def scorecards(rows):
    groups = defaultdict(list)
    for r in rows:
        if r["status"] == "void":      # kept in the log for the record, never scored
            continue
        groups[source_key(r)].append(r)
    out = []
    for key, rs in groups.items():
        card = log.scorecard(rs)
        settled = [r for r in rs if r["status"] == "settled"]
        edge = edge_stats(settled)
        out.append({"engine": key[0], "source": key[1], "name": key[2], "card": card, "edge": edge,
                    "followable": followable(card, edge),
                    "needs": max(0, MIN_SETTLED - card["settled_calls"])})
    out.sort(key=lambda s: (s["engine"], -s["card"]["total_calls"]))
    return out
