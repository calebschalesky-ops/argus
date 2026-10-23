"""observed-gas: price AAA national gas ladders from the last AAA print already published.

KXAAAGASD (daily) and KXAAAGASW (weekly) pay on AAA's U.S. average regular price for a named day,
"strictly greater than $X". AAA publishes once a day, early morning ET; Kalshi settles about 8:30 AM ET.
The daily market for day D closes 11:59 PM ET on D-1, so at the close the D-1 print is public and only
one day's move is unknown. Retail gas moves about a cent a day and trends, so the last print plus the
recent day-to-day moves is most of the answer.

History of AAA prints = the settlement values of Kalshi's own settled AAA markets (the exact number
each market paid on), each usable only after its market settled (settlement_ts). No other AAA archive
is read. Model: value(D) = last print + an h-day move drawn from past h-day moves (last 180 days, all
public before the call), weighted toward past days whose previous 1-day move was like today's (trend).
Same bet rule as observed-temp (temp.py). Paper only.
"""
import hashlib
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import fees, log
from ..markets import Contract
from ..weather import stations
from . import LOG_PATH, quotes
from .temp import decide, notes, replace_engine, report as _report

ENGINE = "observed-gas"
SERIES = ("KXAAAGASD", "KXAAAGASW")
ET = ZoneInfo("America/New_York")
SLOTS = ((1, 12), (1, 18), (1, 23), (2, 18), (3, 18))   # (days before the settle day, ET hour)
LOOKBACK = 180
K = 25
KERNEL_SD = 0.003        # dollars
FETCH_BAND = 0.08        # only fetch candles for strikes within 8c of the last print (others are 1c/99c)
P_FLOOR = 0.005


def slot_label(s):
    return f"D-{s[0]}@{s[1]}h"


def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def prints(markets):
    """{day: (value, known_at)}: one AAA print per settled event, public from its settlement time."""
    out = {}
    for m in markets:
        try:
            v = float(m.get("expiration_value"))
        except (TypeError, ValueError):
            continue
        d = stations.target_date(m["event_ticker"])
        if d is None:
            continue
        known = _t(m["settlement_ts"]) if m.get("settlement_ts") else _t(m["close_time"]) + timedelta(hours=12)
        if d not in out or known < out[d][1]:
            out[d] = (v, known)
    return out


def known_at(hist, t):
    """Only prints public before t (the no-look-ahead filter): {day: value}."""
    return {d: v for d, (v, k) in hist.items() if k < t}


def predict(hist, day, t, lookback=LOOKBACK, k=K):
    """(last day, last value, h, sorted list of h-day moves to use) or None. Uses prints public before t only."""
    kn = known_at(hist, t)
    past = sorted(d for d in kn if d < day)
    if not past:
        return None
    last = past[-1]
    h = (day - last).days
    if h < 1 or h > 4:
        return None
    prev = kn[last] - kn[last - timedelta(days=1)] if (last - timedelta(days=1)) in kn else None
    cands = []
    for d in past:
        if d < last - timedelta(days=lookback) or (d + timedelta(days=h)) not in kn:
            continue
        mv = kn[d + timedelta(days=h)] - kn[d]
        p = kn[d] - kn[d - timedelta(days=1)] if (d - timedelta(days=1)) in kn else None
        cands.append((abs(p - prev) if (p is not None and prev is not None) else 1.0, mv))
    if len(cands) < k:
        return None
    cands.sort(key=lambda x: x[0])
    return last, kn[last], h, [mv for _, mv in cands[:k]]


def p_above(value, moves, strike, sd=KERNEL_SD):
    p = sum(1 - 0.5 * (1 + math.erf((strike - (value + mv)) / (sd * math.sqrt(2)))) for mv in moves) / len(moves)
    return min(1 - P_FLOOR, max(P_FLOOR, p))


def replay_event(series, event_ticker, markets, hist, candles, day):
    rows = []
    for slot in SLOTS:
        if series == "KXAAAGASD" and slot[0] > 1:
            continue                                  # the daily market opens on D-1
        t = datetime.combine(day - timedelta(days=slot[0]), time(slot[1]), ET).astimezone(timezone.utc)
        pr = predict(hist, day, t)
        if pr is None:
            continue
        last, value, h, moves = pr
        stamp = t.strftime("%Y-%m-%dT%H:%M:%SZ")
        for m in markets:
            if not (_t(m["open_time"]) <= t < _t(m["close_time"])) or m.get("strike_type") != "greater":
                continue
            q = quotes.quote(candles.get(m["ticker"]), t)
            if q is None:
                continue
            bid, ask, vol = q
            c = Contract.from_api(m)
            p_yes = p_above(value, moves, c.low)
            side, price, p, edge, bet = decide(p_yes, bid, ask)
            row = {
                "call_id": hashlib.sha1(f"{m['ticker']}@{stamp}".encode()).hexdigest()[:10],
                "logged_at_utc": stamp, "engine": ENGINE, "series": series, "event_ticker": event_ticker,
                "market_ticker": m["ticker"], "range_label": f"above {c.low:.4f}", "side": side,
                "model_prob": f"{p:.4f}", "market_price": f"{price:.2f}", "gap_pts": f"{(p - price) * 100:.1f}",
                "worth_a_look": "1" if bet else "0", "paper_contracts": "1" if bet else "0",
                "fee_per_contract": f"{fees.taker_fee(price):.2f}", "anchor_price": f"{value:.4f}",
                "anchor_kind": f"AAA print {last} (public before the call), {h} day(s) to go",
                "vol_daily": f"{KERNEL_SD:.4f}", "horizon_days": f"{(_t(m['close_time']) - t).total_seconds() / 86400:.3f}",
                "event_days": "0.00", "news_shift_pct": "0.00", "close_time_utc": m["close_time"],
                "notes": (f"shape=greater|{c.low}|None; slot={slot_label(slot)}; city={series}; p_yes={p_yes:.4f}; "
                          f"edge={edge:.4f}; yes_bid={bid:.2f}; yes_ask={ask:.2f}; hour_vol={vol:.0f}; "
                          f"h={h}; move_med={sorted(moves)[len(moves) // 2]:+.4f}"),
            }
            log.settle_row(row, None, m.get("result") == "yes")
            row["settle_value"] = m.get("expiration_value") or ""
            rows.append(row)
    return rows


def run(since, until, out_path=LOG_PATH, verbose=True):
    all_ms = {s: [m for m in quotes.settled_markets(s) if m.get("result") in ("yes", "no")] for s in SERIES}
    hist = prints(all_ms["KXAAAGASD"] + all_ms["KXAAAGASW"])
    rows = []
    for series in SERIES:
        events = defaultdict(list)
        for m in all_ms[series]:
            d = stations.target_date(m["event_ticker"])
            if d and since <= d <= until and m["event_ticker"].startswith("KX"):
                events[m["event_ticker"]].append(m)
        if verbose:
            print(f"{series}: {len(events)} settled events {since}..{until}", flush=True)
        for i, (e, mk) in enumerate(sorted(events.items())):
            day = stations.target_date(e)
            ref = known_at(hist, datetime.combine(day - timedelta(days=1), time(12), ET).astimezone(timezone.utc))
            ref_v = ref[max(ref)] if ref else None
            near = [m for m in mk if ref_v is None or m.get("floor_strike") is None
                    or abs(float(m["floor_strike"]) - ref_v) <= FETCH_BAND]
            first = max(s[0] for s in SLOTS if series != "KXAAAGASD" or s[0] == 1)
            start = datetime.combine(day - timedelta(days=first), time(10), ET).astimezone(timezone.utc)
            end = max(_t(m["close_time"]) for m in mk)
            try:
                candles = quotes.event_candles(series, e, near, start, end)
                rows += replay_event(series, e, near, hist, candles, day)
            except Exception as ex:                  # one bad event never stops the backtest
                print(f"  {e}: {ex.__class__.__name__}: {ex}", flush=True)
            if verbose and i % 25 == 24:
                print(f"  {series}: {i + 1}/{len(events)} events, {len(rows)} calls", flush=True)
    replace_engine(ENGINE, rows, out_path)
    return rows


def report(rows):
    return _report(rows, title="observed-gas backtest (AAA national, KXAAAGASD + KXAAAGASW)",
                   slots=[slot_label(s) for s in SLOTS], by_kind=False, slot_unit=" ET")
