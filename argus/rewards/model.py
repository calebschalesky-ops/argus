"""What a small hand-placed quoter would earn from liquidity rewards, net of the fills it takes.

One order = a resting bid on one side of one rewarded market, placed AT the side's Reference Price
(full credit) and at the back of that price's queue (filled last). For a $budget the model buys
contracts greedily where the next dollar earns the most, net of expected fill losses, with a cap on
how many orders one person can place and re-check by hand.

Fill model (an assumption, not a measurement): a share `hit` of the market's 24h volume trades
against this side; it fills everything ahead of us first, then us. Loss per filled contract comes
from the house map (argus.housemap): the average maker P/L at that price, after fees.
"""
import heapq
import math
from functools import lru_cache

from . import score

# scenario knobs: (share of 24h volume hitting our side, competitor size multiplier, uptime, fill loss)
SCENARIOS = {
# drift = ticks our hand-placed order sits below the reference after the book moves (each tick halves credit)
    "high": dict(hit=0.25, crowd=1.0, uptime=1.0, loss="housemap", drift=0),
    "central": dict(hit=0.5, crowd=1.0, uptime=1.0, loss="housemap", drift=0),
    "low": dict(hit=0.75, crowd=3.0, uptime=0.7, loss="full", drift=1),
}
MIN_PAYOUT = 1.00                   # filed rule: a period's reward is paid only if >= $1.00


@lru_cache(maxsize=1)
def maker_pnl_table():
    """[(lo cents, hi cents, maker P/L per contract $)] from the house-map fills (non-15-minute)."""
    import numpy as np
    from ..housemap import analyze
    markets, F = analyze.load(verbose=False)
    main = np.array([markets[i]["group"] == "main" for i in F["m"]])
    mpx = np.round((1 - F["tpx"]) * 100)
    out = []
    for lo, hi in [(1, 1), (2, 2), (3, 5), (6, 9), (10, 29), (30, 49), (50, 69), (70, 89), (90, 99)]:
        k = main & (mpx >= lo) & (mpx <= hi)
        c = F["cnt"][k].sum()
        out.append((lo, hi, float(F["m_pnl"][k].sum() / c) if c else 0.0))
    return out


def maker_pnl(price):
    cents = round(price * 100)
    for lo, hi, v in maker_pnl_table():
        if lo <= cents <= hi:
            return v
    return 0.0


def hours_live(row, now):
    from .data import parse_ts
    end = parse_ts(row["end"]) if isinstance(row["end"], str) else row["end"]
    h = (end - now).total_seconds() / 3600
    if row.get("close"):
        h = min(h, (parse_ts(row["close"]) - now).total_seconds() / 3600)
    return max(0.0, h)


def tick_of(levels):
    """0.001 for markets quoted in tenths of a cent, else 0.01."""
    return 0.001 if any(abs(p * 100 - round(p * 100)) > 1e-6 for p, _ in levels) else 0.01


def sides(rows, now):
    """One candidate per (market, side) where both sides currently meet the Target Size."""
    out = []
    for r in rows:
        if r.get("status") not in (None, "active", "open"):
            continue
        tick = tick_of(r["yes"] + r["no"])
        refs = {s: score.side_scores(r[s], r["target"], r["discount"], tick)[0] for s in ("yes", "no")}
        if None in refs.values():
            continue                      # snapshot would be excluded: nothing pays right now
        live = hours_live(r, now)
        if live <= 0:
            continue
        for s in ("yes", "no"):
            ref = refs[s]
            if not (0 < ref < 1):
                continue
            ahead = sum(sz for p, sz in r[s] if p >= ref - 1e-9)
            out.append({"ticker": r["ticker"], "side": s, "price": ref, "levels": r[s], "target": r["target"],
                        "discount": r["discount"], "per_day": r["per_day"], "hours": live, "ahead": ahead,
                        "vol24": r.get("vol24", 0.0), "title": r.get("title", ""), "label": r.get("label", ""),
                        "series": r["series"], "cap": r.get("cap"), "days": r["days"], "tick": tick})
    return out


def order_value(c, k, sc):
    """(reward $/day, fill loss $/day) for k contracts on candidate c under scenario sc."""
    if k <= 0:
        return 0.0, 0.0
    levels = [(p, s * sc["crowd"]) for p, s in c["levels"]]
    share, _, ok = score.my_share(levels, c["price"], k, c["target"], c["discount"], c.get("tick", 0.01))
    reward = score.reward_per_day(c["per_day"], share, 0.0, sc["uptime"]) if ok else 0.0
    reward *= c["discount"] ** sc.get("drift", 0)
    if c.get("cap"):                      # per-account cap, when Kalshi sets one (none did on 2026-10-22)
        reward = min(reward, c["cap"] / max(c["days"], 1 / 24))
    fills = min(k, max(0.0, sc["hit"] * c["vol24"] - c["ahead"]))   # crowd changes scoring, not the queue we saw
    per = -c["price"] if sc["loss"] == "full" else maker_pnl(c["price"])
    return reward, fills * per


def allocate(cands, budget, max_orders=10, scenario="central", steps=60):
    """Greedy: spend `budget` in small steps where the next step adds the most net $/day.

    Then drop any market whose reward over its remaining hours is under the $1 payout floor, and redo.
    Returns a list of orders {ticker, side, price, contracts, cost, reward, fill, net} per day.
    """
    sc = SCENARIOS[scenario]
    pool = list(cands)
    while True:
        orders = _greedy(pool, budget, max_orders, sc, steps)
        by_mkt = {}
        for o in orders:
            by_mkt.setdefault(o["ticker"], 0.0)
            by_mkt[o["ticker"]] += o["reward"] * o["hours"] / 24
        short = {t for t, v in by_mkt.items() if v < MIN_PAYOUT}
        if not short:
            return orders
        pool = [c for c in pool if c["ticker"] not in short]


def _greedy(cands, budget, max_orders, sc, steps):
    step_dollars = budget / steps
    held = {}                                   # index -> contracts
    net_now = {}
    heap = []

    def push(i):
        c = cands[i]
        k0 = held.get(i, 0)
        dk = max(1, math.ceil(step_dollars / c["price"]))
        r, f = order_value(c, k0 + dk, sc)
        gain = (r + f) - net_now.get(i, 0.0)
        if gain > 0:
            heapq.heappush(heap, (-gain / (dk * c["price"]), i, dk, r, f))

    for i in range(len(cands)):
        push(i)
    spent = 0.0
    while heap:
        _, i, dk, r, f = heapq.heappop(heap)
        c = cands[i]
        if held.get(i, 0) == 0 and len(held) >= max_orders:
            continue
        if (r + f) - net_now.get(i, 0.0) <= 0:
            continue
        cost = dk * c["price"]
        if spent + cost > budget + 1e-9:
            continue
        held[i] = held.get(i, 0) + dk
        net_now[i] = r + f
        spent += cost
        push(i)
    out = []
    for i, k in held.items():
        c = cands[i]
        r, f = order_value(c, k, sc)
        out.append({"ticker": c["ticker"], "side": c["side"], "price": c["price"], "contracts": k,
                    "cost": k * c["price"], "reward": r, "fill": f, "net": r + f, "hours": c["hours"],
                    "per_day_pool": c["per_day"], "title": c["title"], "label": c["label"]})
    return sorted(out, key=lambda o: -o["net"])


def summary(orders):
    out = {k: sum(o[k] for o in orders) for k in ("cost", "reward", "fill", "net")}
    # what the periods running now would pay if joined at snapshot time (reward is prorated by hours left)
    out["this_period"] = sum(o["reward"] * min(o["hours"], 24) / 24 for o in orders)
    return out


def one_contract(cands, scenario="central"):
    """Reward per day for a single contract at the reference price, per candidate."""
    sc = SCENARIOS[scenario]
    out = []
    for c in cands:
        r, f = order_value(c, 1, sc)
        out.append(dict(c, reward1=r, fill1=f, per_dollar=(r / c["price"])))
    return out
