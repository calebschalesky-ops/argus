"""Calibration of early vs later prices, and walk-forward strategies with honest fills.

Everything here is local maths over the collected rows (data.collect); no network.

No look-ahead, by construction:
- a price at "1h after open" comes from an hourly candle that ENDED at or before that moment;
- a rule used for a trade at time t learns only from markets that SETTLED before t;
- a maker order fills only if a later taker trade crossed its limit inside the window.
"""
import math
import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .. import fees
from . import data as D

BANDS = [(0, 5), (5, 15), (15, 30), (30, 50), (50, 70), (70, 85), (85, 95), (95, 100)]
MIN_HIST = 40           # settled markets in a cell before its bias is trusted
MIN_HIST_EVENTS = 15    # ... from at least this many events
EDGE = 0.03             # model edge over price + fee before a paper bet
RECENT_FROM = datetime(2026, 7, 20, tzinfo=timezone.utc)   # "recent 3 months" of entries
HORIZONS = ("h1", "h6", "h24")


def band_of(p):
    c = p * 100
    for lo, hi in BANDS:
        if lo <= c < hi or (hi == 100 and c <= 100):
            return f"{lo}-{hi}"
    return None


def entry_time(r, h):
    return D.parse_time(r["open"]) + timedelta(hours=int(h[1:]))


# ---------------- calibration ----------------

def brier(pairs):
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs) if pairs else None


def paired(rows, h, exclude_early_close=True):
    """Markets with a real book both at h and 24h before close: (row, early mid, late mid)."""
    out = []
    for r in rows:
        if exclude_early_close and r["early_close"]:
            continue
        e, l = D.mid(r["books"][h]), D.mid(r["books"]["late"])
        if e is not None and l is not None:
            out.append((r, e, l))
    return out


def cluster_ci(items, stat, key=lambda x: x[0]["event"], n_boot=1000, seed=5):
    """95% range of stat(items) resampling whole events (markets of one event move together)."""
    by = defaultdict(list)
    for it in items:
        by[key(it)].append(it)
    groups = list(by.values())
    if len(groups) < 5:
        return None, None
    rng = random.Random(seed)
    vals = []
    for _ in range(n_boot):
        pick = [x for _ in groups for x in rng.choice(groups)]
        v = stat(pick)
        if v is not None:
            vals.append(v)
    vals.sort()
    return vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]


def calib_cell(items):
    """items = [(row, early, late)]. Brier early/late, bias (mean early - win rate) with event CI."""
    n = len(items)
    won = [int(r["won"]) for r, _, _ in items]
    b_e = brier([(e, y) for (_, e, _), y in zip(items, won)])
    b_l = brier([(l, y) for (_, _, l), y in zip(items, won)])
    bias = lambda its: sum(e - int(r["won"]) for r, e, _ in its) / len(its)
    gap = lambda its: (brier([(e, int(r["won"])) for r, e, _ in its]) - brier([(l, int(r["won"])) for r, _, l in its]))
    return {"n": n, "events": len({r["event"] for r, _, _ in items}), "early": sum(e for _, e, _ in items) / n,
            "late": sum(l for _, _, l in items) / n, "win": sum(won) / n, "brier_early": b_e, "brier_late": b_l,
            "bias": bias(items), "bias_ci": cluster_ci(items, bias), "gap_ci": cluster_ci(items, gap)}


def calibration(rows, h, by):
    """{group value: calib_cell} for the markets paired at horizon h; by(row, early) -> group."""
    groups = defaultdict(list)
    for it in paired(rows, h):
        groups[by(it[0], it[1])].append(it)
        groups["ALL"].append(it)
    return {k: calib_cell(v) for k, v in groups.items()}


def coverage(rows):
    """Share of markets with a real two-sided book at each moment."""
    n = len(rows)
    return {h: sum(D.real_book(r["books"][h]) for r in rows) / n for h in HORIZONS + ("late",)} if n else {}


# ---------------- walk-forward rule ----------------

class History:
    """Settled markets known so far, by cell: (settle time, early mid, won, event). Learns in time order."""

    def __init__(self, rows, h, cell):
        self.items = sorted(((D.parse_time(r["settle"]), D.mid(r["books"][h]), int(r["won"]), r["event"], cell(r, D.mid(r["books"][h])))
                             for r in rows if D.mid(r["books"][h]) is not None), key=lambda x: x[0])

    def bias_before(self, t, cell_key):
        """Mean (won - early mid) in the cell over markets settled before t, or None if too few."""
        xs = [(m, w, e) for s, m, w, e, c in self.items if s < t and c == cell_key]
        if len(xs) < MIN_HIST or len({e for _, _, e in xs}) < MIN_HIST_EVENTS:
            return None, len(xs)
        return sum(w - m for m, w, _ in xs) / len(xs), len(xs)


def default_cell(r, m):
    return (r["category"], band_of(m)) if m is not None else None


def band_cell(r, m):
    """Pooled over categories: 'early prices in band X are too high/low'."""
    return band_of(m) if m is not None else None


def decide(book, bias, maker=False, fee_mult=1.0, maker_charged=False):
    """(side, price, fee, p_model) for the best side with edge >= EDGE after fees, else None.

    p_model(Yes) = current mid + the cell's past bias (won - mid), clipped to [0.01, 0.99].
    Taker pays the ask; maker rests at bid + 1c on that side (only if it stays under the ask).
    """
    m = D.mid(book)
    if m is None or bias is None:
        return None
    p_yes = min(0.99, max(0.01, m + bias))
    best = None
    for side, p, bid, ask in (("yes", p_yes, book["bid"], book["ask"]), ("no", 1 - p_yes, 1 - book["ask"], 1 - book["bid"])):
        if maker:
            price = round(bid + 0.01, 2)
            if price >= ask - 1e-9:
                continue
            fee = fees.maker_fee(price, 1, fee_mult, maker_charged)
        else:
            price = round(ask, 2)
            fee = fees.taker_fee(price, 1, fee_mult)
        edge = p - price - fee
        if edge >= EDGE and (best is None or edge > best[4]):
            best = (side, price, fee, round(p, 4), edge)
    return best[:4] if best else None


def bet(r, side, price, fee, p_model, t, variant, filled_at=None):
    won = r["won"] if side == "yes" else not r["won"]
    return {"variant": variant, "ticker": r["ticker"], "event": r["event"], "series": r["series"],
            "category": r["category"], "type": r["type"], "n_in_event": r["n_in_event"], "side": side,
            "price": price, "fee": fee, "p_model": p_model, "t": t.isoformat(),
            "filled_at": filled_at, "close": r["close"], "won": won,
            "pnl": fees.pnl(won, price, 1, fee)}


def walk_forward(rows, h, smeta, cell=default_cell, maker=False, strict=False, tag=None):
    """Paper bets of the walk-forward rule at horizon h. Taker at the ask, or maker at bid+1c with honest fills."""
    hist = History(rows, h, cell)
    variant = f"{tag or ('mk' if maker else 'wf')}-{h[1:]}h"
    out = []
    for r in rows:
        book = r["books"][h]
        m = D.mid(book)
        if m is None:
            continue
        t = entry_time(r, h)
        if t >= D.parse_time(r["close"]):
            continue
        bias, _ = hist.bias_before(t, cell(r, m))
        charged, mult = fees.series_maker_fees(smeta.get(r["series"]))
        d = decide(book, bias, maker, mult, charged)
        if not d:
            continue
        side, price, fee, p = d
        filled = None
        if maker:
            filled = D.maker_fill(r["fills"], side, price, t.timestamp(), t.timestamp() + D.FILL_WINDOW_H * 3600, strict)
            if filled is None:
                continue
        out.append(bet(r, side, price, fee, p, t, variant, filled))
    return out


def maker_both(rows, h, smeta, strict=False):
    """Plain market making at listing: rest Yes at bid+1c AND No at no-bid+1c; each filled leg is a bet."""
    out = []
    for r in rows:
        book = r["books"][h]
        if D.mid(book) is None:
            continue
        t = entry_time(r, h)
        charged, mult = fees.series_maker_fees(smeta.get(r["series"]))
        for side, bid, ask in (("yes", book["bid"], book["ask"]), ("no", 1 - book["ask"], 1 - book["bid"])):
            price = round(bid + 0.01, 2)
            if price >= ask - 1e-9 or price <= 0:
                continue
            f = D.maker_fill(r["fills"], side, price, t.timestamp(), t.timestamp() + D.FILL_WINDOW_H * 3600, strict)
            if f is None:
                continue
            out.append(bet(r, side, price, fees.maker_fee(price, 1, mult, charged), round(price, 4), t,
                           f"mkboth-{h[1:]}h", f))
    return out


def maker_offers(rows, h):
    """How many maker-both offers were possible at h (for the fill rate)."""
    n = 0
    for r in rows:
        b = r["books"][h]
        if D.mid(b) is None:
            continue
        n += (round(b["bid"] + 0.01, 2) < b["ask"] - 1e-9) + (round(1 - b["ask"] + 0.01, 2) < 1 - b["bid"] - 1e-9)
    return n


# ---------------- scoring ----------------

def roi(bets):
    cost = sum(b["price"] + b["fee"] for b in bets)
    return sum(b["pnl"] for b in bets) / cost if cost else None


def summary(bets):
    if not bets:
        return {"n": 0, "events": 0, "roi": None, "ci": (None, None), "pnl": 0.0, "win": None, "avg_price": None,
                "brier_model": None, "brier_market": None}
    lo, hi = cluster_ci([(b,) for b in bets], lambda its: roi([x[0] for x in its]), key=lambda x: x[0]["event"])
    return {"n": len(bets), "events": len({b["event"] for b in bets}), "roi": roi(bets), "ci": (lo, hi),
            "pnl": round(sum(b["pnl"] for b in bets), 2), "win": sum(b["won"] for b in bets) / len(bets),
            "avg_price": sum(b["price"] for b in bets) / len(bets),
            "brier_model": brier([(b["p_model"], int(b["won"])) for b in bets]),
            "brier_market": brier([(b["price"], int(b["won"])) for b in bets])}


def splits(bets):
    """All, first half, second half (by entry time), recent 3 months."""
    bs = sorted(bets, key=lambda b: b["t"])
    half = len(bs) // 2
    return {"all": summary(bs), "first half": summary(bs[:half]), "second half": summary(bs[half:]),
            "recent 3m": summary([b for b in bs if D.parse_time(b["t"]) >= RECENT_FROM])}


def verdict(s):
    """Board-style: PASS needs 20+ events, model Brier better than the price, profit after fees,
    and here also an event-clustered 95% range above zero and both halves positive."""
    a = s["all"]
    if a["events"] < 20:
        return f"TOO EARLY ({a['events']} events)"
    beats = a["brier_model"] is not None and a["brier_model"] < a["brier_market"]
    if beats and a["roi"] > 0 and a["ci"][0] is not None and a["ci"][0] > 0 \
            and all((s[k]["roi"] or -1) > 0 for k in ("first half", "second half")):
        return "PASS"
    if a["roi"] is not None and a["roi"] > 0:
        return "POSITIVE, NOT PROVEN"
    return "NO EDGE"


def per_week(bets, weeks):
    return len(bets) / weeks if weeks else None
