"""`python -m argus.lab nested`: nested-strike price violations across the whole lake (survey idea 2.2).

Inside one event, "above $100" can never be worth more than "above $95" (and "below $95" never more
than "below $100"): both settle on the same number. If the narrower contract's Yes BID is above the
wider one's Yes ASK by more than both taker fees, buying Yes on the wider one and No on the narrower
one costs less than $1 and pays at least $1 whatever happens.

At every hourly mark, each market's book is its latest candle in that hour (0 < bid < ask < 1); a
violation needs both legs quoted in the same hour. An episode is a run of consecutive hourly marks
with a violation; "lasting 1+ hour" means it was seen on two marks an hour apart. Depth is not in
candles: the smaller leg's volume in that hour is reported as the best available hint.
Pass (written before running): 20+ events with an episode lasting 1+ hour after fees in the holdout
months (decisions from 2026-10-01). Only events whose markets all share one strike type count.
"""
import re
from collections import defaultdict
from datetime import datetime, timezone

from .. import fees
from . import lake, registry

HOLDOUT_FROM = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())
UP = {"greater", "greater_or_equal"}
DOWN = {"less", "less_or_equal"}


def _books(candles):
    """{hour_end: (bid, ask, volume)} for one market: its candle closing in that hour."""
    out = {}
    for c in candles:
        b, a = c.get("bid"), c.get("ask")
        if b is not None and a is not None and 0 < b < a < 1:
            out[c["end_ts"] - c["end_ts"] % 3600 + (3600 if c["end_ts"] % 3600 else 0)] = (b, a, c.get("volume") or 0)
    return out


def best_violation(legs):
    """legs: [(strike, bid, ask, vol)] sorted so that legs[i] is WIDER than legs[j] for i < j.
    Returns (profit per set after fees, wider idx, narrower idx, min volume) or None."""
    best = None
    for i in range(len(legs)):
        for j in range(i + 1, len(legs)):
            _, _, ask_w, vol_w = legs[i]
            _, bid_n, _, vol_n = legs[j]
            no_n = round(1 - bid_n, 4)
            profit = 1 - ask_w - no_n - fees.taker_fee(ask_w) - fees.taker_fee(no_n)
            if profit > 1e-9 and (best is None or profit > best[0]):
                best = (round(profit, 4), i, j, min(vol_w, vol_n))
    return best


def rule_shape(text):
    return re.sub(r"[\d][\d.,]*", "#", text or "").strip()


def nested_groups(ms):
    """Markets of one event split into groups that truly nest: same rules apart from the numbers."""
    by = defaultdict(list)
    for m in ms:
        by[(rule_shape(m.get("rules_primary")), m["strike_type"] in UP)].append(m)
    return [g for g in by.values() if len({m.get("floor_strike") if m["strike_type"] in UP else m.get("cap_strike")
                                             for m in g}) >= 2]


def scan_event(ms, candles_by):
    out = []
    for g in nested_groups(ms):
        out += _scan_group(g, candles_by)
    return out


def _scan_group(ms, candles_by):
    kinds = {m["strike_type"] for m in ms}
    if kinds <= UP:
        key, rev = "floor_strike", False          # wider = lower floor
    elif kinds <= DOWN:
        key, rev = "cap_strike", True             # wider = higher cap
    else:
        return []
    ms = sorted((m for m in ms if m.get(key) is not None), key=lambda m: m[key], reverse=rev)
    seen, uniq = set(), []
    for m in ms:                                   # one market per strike
        if m[key] not in seen:
            seen.add(m[key])
            uniq.append(m)
    ms = uniq
    books = [_books(candles_by.get(m["ticker"], [])) for m in ms]
    hours = sorted(set().union(*[set(b) for b in books])) if books else []
    hits = []
    for h in hours:
        legs = [(m[key],) + b[h] for m, b in zip(ms, books) if h in b]
        v = best_violation(legs)
        if v:
            hits.append((h, v))
    episodes, cur = [], []
    for h, v in hits:
        if cur and h - cur[-1][0] > 3600:
            episodes.append(cur)
            cur = []
        cur.append((h, v))
    if cur:
        episodes.append(cur)
    return [{"event": ms[0]["event_ticker"], "start": ep[0][0], "hours": (ep[-1][0] - ep[0][0]) / 3600,
             "best_profit": max(v[0] for _, v in ep), "min_leg_volume": min(v[3] for _, v in ep)} for ep in episodes]


def run(con=None, chunk=2000):
    con = con or lake.connect()
    ms = lake.markets(con, "m.ticker IN (SELECT ticker FROM candle_done WHERE period=60 AND n > 0) "
                           "AND m.strike_type IN ('greater','greater_or_equal','less','less_or_equal')")
    rules = dict(con.execute("SELECT ticker, rules_primary FROM markets WHERE strike_type IN "
                             "('greater','greater_or_equal','less','less_or_equal')").fetchall())
    by_ev = defaultdict(list)
    for m in ms:
        m["rules_primary"] = rules.get(m["ticker"])
        by_ev[m["event_ticker"]].append(m)
    evs = [e for e, v in by_ev.items() if len(v) >= 2]
    eps = []
    for k in range(0, len(evs), chunk):
        part = evs[k:k + chunk]
        cs = lake.candles_for(con, [m["ticker"] for e in part for m in by_ev[e]])
        for e in part:
            eps += scan_event(by_ev[e], cs)
    lasting = [x for x in eps if x["hours"] >= 1]
    hold = [x for x in lasting if x["start"] >= HOLDOUT_FROM]
    hold_ev = {x["event"] for x in hold}
    passed = len(hold_ev) >= 20
    registry.append([{"kind": "scan", "name": "nested-strike violations (2.2)", "batch": "nested-" + lake.now_iso()[:10],
                      "data_window": "whole lake; pass judged on decisions from 2026-10-01",
                      "n": len(lasting), "events": len({x['event'] for x in lasting}),
                      "p": 0.0 if passed else 1.0, "note": f"{len(hold_ev)} holdout events with a 1h+ episode"}])
    top = sorted(lasting, key=lambda x: -x["best_profit"])[:10]
    L = [f"Nested-strike scan: {len(evs):,} ladder events with 2+ quoted markets",
         f"  violation episodes after both taker fees: {len(eps):,} on {len({x['event'] for x in eps}):,} events",
         f"  lasting 1+ hour: {len(lasting):,} on {len({x['event'] for x in lasting}):,} events; "
         f"in the holdout months: {len(hold):,} on {len(hold_ev)} events -> {'PASS' if passed else 'FAIL'} (needs 20 events)"]
    for x in top:
        L.append(f"  {x['event']}: {x['best_profit'] * 100:.1f}c per set for {x['hours']:.0f}h from "
                 f"{datetime.fromtimestamp(x['start'], timezone.utc):%Y-%m-%d %H:%M} UTC, smaller leg traded {x['min_leg_volume']:.0f} that hour")
    return "\n".join(L)
