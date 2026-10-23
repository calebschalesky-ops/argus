"""The test bench: score any trading idea on the lake with every known backtest trap blocked.

A hypothesis is a small Python function. The bench calls it once per market, at one moment
`t` (a fixed number of hours before the market's SCHEDULED close), with:
  market   what was known about the market at t: tickers, series, category, frequency,
           open time, scheduled close, strike, title. Never the result, the settle value,
           the actual close time or the lifetime volume (all of those are the future).
  history  that market's hourly candles that ENDED at or before t (a copy; later candles
           are never handed over).
  t        the decision time (unix seconds).
It returns None (no bet) or Call(side="yes"|"no", limit=None|price, prob=None|p).
  limit=None   take the ask (taker): pay the side's ask plus the taker fee.
  limit=0.42   rest a buy at 42c (maker): filled only if a LATER trade printed strictly
               through 42c (proved from the trade tape where the lake has it, else from
               later candles' trade low/high); pays the maker fee only on series that charge it.
  prob         optional: the hypothesis' own probability for its side (for the Brier score).

Traps the bench enforces (each has a test in tests/test_lab.py):
  1. Empty books: a price counts only with a real bid within 5c of the ask (MAX_SPREAD).
     Kalshi shows an empty book as a 1c bid / 99c ask, which is not a 99c favourite.
  2. Early-close look-ahead: markets that close when the event happens ("it happened") would
     let "1 hour before close" peek at the result. The bench measures t back from the
     SCHEDULED close (expected expiration minus the series' usual gap), never from an actual
     close that came off schedule; a market already closed at t is skipped.
  3. Last trade instead of the ask: the price paid is always the side's ask (No ask = 1 - Yes bid).
  4. Fees: Kalshi's taker fee (0.07 x P x (1-P), rounded up, times the series multiplier) on
     every taker bet; the maker fee (0.0175x) only where the series' fee_type includes maker.
  5. Assumed maker fills: no fill without a later trade strictly through the limit.
"""
import bisect
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Optional

from .. import fees

MAX_SPREAD = 0.05          # a quote counts only if the bid is within 5c under the ask
ON_SCHEDULE_S = 300        # an actual close within 5 minutes of the schedule is "on schedule"
EARLY_EXPIRY_DAYS = 60     # no expected expiration and closed this long before the latest one: skip
DEPTH_MULT = 3             # an N-contract order needs 3N contracts traded in the hour before t (no book depth in candles)
EXIT_MAX_AGE = 3 * 3600


@dataclass
class Call:
    side: str                       # "yes" or "no"
    limit: Optional[float] = None   # None = taker at the ask; a price = resting maker buy
    prob: Optional[float] = None    # the hypothesis' probability that its side wins
    exit_hours: Optional[float] = None   # sell at the side's bid this many hours later (None = hold to settlement)


@dataclass
class Hypothesis:
    name: str
    hours: float                                   # hours before the scheduled close
    fn: Callable                                   # fn(market, history, t) -> Call | None
    where: Optional[Callable] = None               # cheap pre-filter on the public market dict
    contracts: int = 1                             # contracts per ORDER: Kalshi rounds the fee once per order


PUBLIC_KEYS = ("ticker", "event_ticker", "series", "category", "frequency", "open_ts", "sched_close_ts",
               "strike_type", "floor_strike", "cap_strike", "title", "yes_sub_title", "fee_type",
               "fee_multiplier", "tier")


# ---------------- trap 2: the scheduled close ----------------

def usual_gaps(markets):
    """Median (expected expiration - close) per series: the series' normal schedule."""
    by = defaultdict(list)
    for m in markets:
        if m.get("exp_ts") and m.get("close_ts"):
            by[m["series"]].append(m["exp_ts"] - m["close_ts"])
    return {s: statistics.median(v) for s, v in by.items()}


def scheduled_close(m, usual_gap):
    """The close time anyone could have known in advance, or None if it cannot be told.

    If the actual close is within 5 minutes of the schedule implied by the expected expiration,
    the actual close is used. Otherwise the market closed off schedule (early because the thing
    happened, or late) and the schedule is used instead."""
    if m.get("exp_ts"):
        sched = m["exp_ts"] - (usual_gap or 0)
        return m["close_ts"] if abs(m["close_ts"] - sched) <= ON_SCHEDULE_S else sched
    latest = m.get("latest_exp_ts")
    if latest and latest - m["close_ts"] > EARLY_EXPIRY_DAYS * 86400:
        return None
    return m["close_ts"]


def public_view(m, sched):
    v = {k: m.get(k) for k in PUBLIC_KEYS if k != "sched_close_ts"}
    v["sched_close_ts"] = sched
    return v


# ---------------- trap 1 and 3: the quote at t ----------------

def max_age(hours):
    """How old the latest candle may be and still be 'the book at t'. Kalshi prints a candle
    only when something changed, so a quiet book's last candle can be hours old."""
    return int(max(3.0, min(48.0, hours * 0.5)) * 3600)


def history_until(candles, t, ends=None):
    """Candles that ENDED at or before t (candles are sorted by end_ts; `ends` may be passed in for speed)."""
    if ends is None:
        ends = [c["end_ts"] for c in candles]
    return candles[:bisect.bisect_right(ends, t)]


def quote(history, t, age):
    """(yes_bid, yes_ask) from the latest candle in `history` ending within `age` of t, or (None, None).

    Both sides must be real (0 < bid < ask < 1) and the spread at most MAX_SPREAD."""
    if not history:
        return None, None
    c = history[-1]
    if c["end_ts"] > t or c["end_ts"] <= t - age:
        return None, None
    bid, ask = c.get("bid"), c.get("ask")
    if bid is None or ask is None or not (0 < bid < ask < 1) or ask - bid > MAX_SPREAD + 1e-9:
        return None, None
    return bid, ask


def side_prices(side, bid, ask):
    """(ask, bid) of one side. The No ask is 1 - Yes bid; the No bid is 1 - Yes ask."""
    if side == "yes":
        return ask, bid
    return round(1 - bid, 4), round(1 - ask, 4)


# ---------------- trap 5: proven maker fills ----------------

def filled(side, limit, t, close_ts, candles=None, tape=None):
    """Was a resting buy at `limit` filled after t? Only a later trade strictly through it counts.

    tape: [(ts, taker_yes, yes_px, count)] (preferred when the lake has it);
    candles: hourly candles; only candles whose whole hour starts at or after t are used."""
    through = (lambda px: px < limit - 1e-9) if side == "yes" else (lambda px: px > 1 - limit + 1e-9)
    if tape is not None:
        return any(t < x[0] <= close_ts and through(x[2]) for x in tape)
    for c in candles or ():
        if c["end_ts"] - 3600 < t or c["end_ts"] > close_ts or not c.get("volume"):
            continue
        px = c.get("px_lo") if side == "yes" else c.get("px_hi")
        if px is not None and through(px):
            return True
    return False


def default_maker_limit(side_bid, side_ask):
    """One cent above the side's bid, never reaching its ask; None on a 1c spread."""
    p = round(side_bid + 0.01, 2)
    return p if p < side_ask - 1e-9 else None


# ---------------- one market ----------------

def closing_mid(candles, t, sched, close_ts, side):
    """The side's mid at the last real two-sided quote after t and before the scheduled close, or None.

    Closing-line value (CLV) = this minus the price paid. Its noise is a few cents, against 0-or-$1
    for the bet itself, so it shows an edge with far fewer calls. Caution: for markets that trade
    while the outcome is being decided (games in play), the last quote before close already holds
    most of the result, so CLV there is close to the P/L, not a cleaner signal."""
    end = min(sched, close_ts)
    for c in reversed(candles):
        if c["end_ts"] > end:
            continue
        if c["end_ts"] <= t:
            break
        bid, ask = c.get("bid"), c.get("ask")
        if bid is not None and ask is not None and 0 < bid < ask < 1 and ask - bid <= MAX_SPREAD + 1e-9:
            a, b = side_prices(side, bid, ask)
            return round((a + b) / 2, 4)
    return None


def exit_bid(candles, te, side):
    """The side's bid at time te (latest real quote within EXIT_MAX_AGE), or None."""
    bid, ask = quote(history_until(candles, te), te, EXIT_MAX_AGE)
    if bid is None:
        return None
    return side_prices(side, bid, ask)[1]


def evaluate_market(h, m, candles, tape, gap):
    """One bet record for hypothesis h on market m, or None. Money fields are per contract;
    fees are charged once per order of h.contracts contracts (Kalshi rounds per order)."""
    if m.get("frequency") == "fifteen_min" or m.get("result") not in ("yes", "no"):
        return None
    sched = scheduled_close(m, gap)
    if sched is None:
        return None
    t = int(sched - h.hours * 3600)
    if not (m["open_ts"] <= t < m["close_ts"]):
        return None
    pub = public_view(m, sched)
    if h.where is not None and not h.where(pub):
        return None
    hist = history_until(candles, t)
    call = h.fn(pub, list(hist), t)
    if call is None:
        return None
    bid, ask = quote(hist, t, max_age(h.hours))
    if bid is None:
        return None
    n = max(1, int(h.contracts))
    if n > 1 and (hist[-1].get("volume") or 0) < DEPTH_MULT * n:
        return None                     # the size was probably not there
    s_ask, s_bid = side_prices(call.side, bid, ask)
    mult = m.get("fee_multiplier") or 1.0
    rec = {"ticker": m["ticker"], "event": m["event_ticker"], "series": m["series"],
           "category": m.get("category"), "t": t, "close_ts": m["close_ts"], "side": call.side,
           "won": int(m["result"] == call.side), "mid_side": round((s_ask + s_bid) / 2, 4),
           "prob": call.prob, "hypothesis": h.name, "contracts": n}
    if call.limit is None:
        price = s_ask
        fee = fees.taker_fee(price, n, mult)
        rec.update(mode="taker", price=price, filled=True)
    else:
        price = round(call.limit, 2)
        if not (0 < price < s_ask - 1e-9):
            return None                 # a "maker" price at or above the ask would be a taker order
        charged = "maker" in (m.get("fee_type") or "")
        fee = fees.maker_fee(price, n, mult, charged)
        later = [c for c in candles if c["end_ts"] > t]
        until = m["close_ts"] if call.exit_hours is None else min(m["close_ts"], t + int(call.exit_hours * 3600))
        rec.update(mode="maker", price=price,
                   filled=filled(call.side, price, t, until, later, tape),
                   fill_proof="tape" if tape is not None else "candles")
    rec["fee"] = round(fee / n, 6)
    total = fees.pnl(bool(rec["won"]), price, n, fee)
    rec["exit"] = "settle"
    if call.exit_hours is not None and rec["filled"]:
        te = t + int(call.exit_hours * 3600)
        xb = exit_bid(candles, te, call.side) if te < min(sched, m["close_ts"]) else None
        if xb is not None:
            xfee = fees.taker_fee(xb, n, mult)
            total = round(n * (xb - price) - fee - xfee, 4)
            rec["exit"] = f"{call.exit_hours:g}h"
            rec["fee"] = round((fee + xfee) / n, 6)
    rec["pnl"] = round(total / n, 6) if rec["filled"] else 0.0
    cm = closing_mid(candles, t, sched, m["close_ts"], call.side)
    rec["clv"] = None if cm is None else round(cm - price, 4)
    return rec


def run(h, markets, candles_by, tapes_by=None, gaps=None):
    """Bet records of hypothesis h over many markets (dicts from lake.markets)."""
    gaps = gaps if gaps is not None else usual_gaps(markets)
    tapes_by = tapes_by or {}
    out = []
    for m in markets:
        r = evaluate_market(h, m, candles_by.get(m["ticker"], []), tapes_by.get(m["ticker"]),
                            gaps.get(m["series"], 0))
        if r is not None:
            out.append(r)
    return out


def run_lake(hyps, con, where="1=1", params=(), chunk=3000, gaps=None):
    """Run several hypotheses over the lake's markets, loading candles a chunk at a time
    (the whole lake does not fit in memory as Python objects). Returns {name: records}."""
    from . import lake
    base = "m.ticker IN (SELECT ticker FROM candle_done WHERE period=60 AND n > 0)"
    if gaps is None:
        gaps = usual_gaps(lake.markets(con, base))
    ms = lake.markets(con, f"{base} AND ({where})", params)
    complete = {r[0] for r in con.execute("SELECT ticker FROM tape_done WHERE complete=1")}
    out = {h.name: [] for h in hyps}
    for k in range(0, len(ms), chunk):
        part = ms[k:k + chunk]
        cs = lake.candles_for(con, [m["ticker"] for m in part])
        tp = lake.trades_for(con, [m["ticker"] for m in part if m["ticker"] in complete])
        tapes = {m["ticker"]: tp.get(m["ticker"], []) for m in part if m["ticker"] in complete}
        for h in hyps:
            out[h.name] += run(h, part, cs, tapes, gaps)
    return out
