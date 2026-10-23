"""Backtests on settled Mentions markets, with no look-ahead.

Timing. Kalshi does not publish when an event starts. The anchor is the earlier of the event's
`occurrence_datetime` and the first close of any of its markets (a market closes when its event
ends, or earlier when the word is said); the event cannot have ended after that. Calls are made
HORIZONS before the anchor, using only:
  - the Yes bid/ask of the last hourly candle that ENDED at or before the call time, with a real
    book (a bid within MAX_SPREAD of the ask, both inside 1-99c);
  - base rates from results published before the call time, the target's own event excluded.
Resting (maker) orders count as filled only if a LATER Yes trade printed strictly through the
limit before the anchor; anything after the anchor (the event itself) never fills an order.

Strategies (engine names in mentions_log.csv):
  mentions-baserate     Price = base rate (Fed transcripts for the Fed Chair series, else Kalshi's
                        own past results for the same word in the same series). Buy the side whose
                        ask is 10+ points under the base rate after the taker fee, side price
                        10-95c. Every quoted market is logged; paper bets only on those.
  mentions-fade         "Dramatic word" longshots: Yes ask 2-15c. Buy No as a maker at No bid + 1c
                        (= 1 - Yes ask + 1c), never reaching the No ask; no maker fee on Mentions
                        series (fee_type "quadratic", checked 2026-10-22). Unfilled orders are void.
  mentions-fade-taker   Same picks, No bought at the No ask with the taker fee (reference).
"""
import hashlib
from collections import defaultdict
from datetime import timedelta

from . import LOG_PATH, baserate as B, history as H, transcripts as T, words
from .. import fees
from ..model import parse_time

HORIZONS = {"2h": timedelta(hours=2), "24h": timedelta(hours=24)}
PRIMARY = "2h"                # logged under the plain engine names; other horizons get a suffix
MAX_SPREAD = 0.05
QUOTE_AGE = 3 * 3600
GAP = 0.10
SIDE_LO, SIDE_HI = 0.10, 0.95
FADE_LO, FADE_HI = 0.02, 0.15
FED_SERIES = ("KXFEDMENTION",)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def call_id(*parts):
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:10]


def word_markets(ms):
    return [m for m in ms if words.alternatives(B.strike_of(m))]


def cancelled(ms):
    """An event that did not qualify (cancelled/void) is skipped whole."""
    return any(not words.alternatives(B.strike_of(m)) and m.get("result") == "yes" for m in ms)


def anchor(ms):
    """Latest moment the event can be assumed not yet over: min(occurrence, first close)."""
    ts = [H.close_of(m) for m in ms]
    occ = [parse_time(m["occurrence_datetime"]) for m in ms if m.get("occurrence_datetime")]
    return min(ts + occ)


def row(engine, series, m, side, price, t, won, fee, model_prob, contracts, notes, status="settled"):
    pnl = fees.pnl(won, price, contracts, fee=fee * contracts) if contracts else 0.0
    return {"call_id": call_id(engine, m["ticker"], side, iso(t)), "logged_at_utc": iso(t), "engine": engine,
            "series": series, "event_ticker": m["event_ticker"], "market_ticker": m["ticker"],
            "range_label": B.strike_of(m), "side": side, "model_prob": f"{model_prob:.4f}",
            "market_price": f"{price:.4f}", "gap_pts": f"{(model_prob - price) * 100:.1f}",
            "worth_a_look": "1" if contracts else "0", "paper_contracts": str(contracts),
            "fee_per_contract": f"{fee:.2f}", "anchor_price": "", "anchor_kind": "", "vol_daily": "",
            "horizon_days": "", "event_days": "", "news_shift_pct": "", "close_time_utc": m["close_time"],
            "status": status, "settle_value": "", "won": "1" if won else "0",
            "pnl_after_fees": f"{pnl:.2f}", "notes": notes}


def maker_limit_no(yes_bid, yes_ask):
    """No bid + 1c, where No bid = 1 - Yes ask; None if that would reach the No ask (1 - Yes bid)."""
    lim = round(1 - yes_ask + 0.01, 2)
    return lim if lim < round(1 - yes_bid, 2) - 1e-9 else None


def no_filled(limit, hi):
    """A No buy at L is a Yes sell at 1 - L: filled only if a later Yes trade printed above 1 - L."""
    return hi is not None and hi > 1 - limit + 1e-9


def baserate_call(p, bid, ask):
    """(side, price, fee, p_side, bet?) for the side with the bigger gap after the taker fee."""
    best = None
    for side, price, ps in (("YES", ask, p), ("NO", round(1 - bid, 4), 1 - p)):
        fee = fees.taker_fee(price)
        gap = ps - price - fee
        if best is None or gap > best[-1]:
            best = (side, price, fee, ps, gap)
    side, price, fee, ps, gap = best
    return side, price, fee, ps, (gap >= GAP and SIDE_LO <= price <= SIDE_HI)


def replay_event(series, ms, hist, fed, candles_of, horizons=HORIZONS):
    """Rows for every strategy and horizon for one settled event. `candles_of(m, end)` returns the
    market's hourly candles up to `end`; tests pass a fake."""
    out = []
    a = anchor(ms)
    ev = ms[0]["event_ticker"]
    for hname, h in horizons.items():
        t = a - h
        sfx = "" if hname == PRIMARY else f"-{hname}"
        for m in word_markets(ms):
            if not (H.open_of(m) <= t):
                continue
            cs = candles_of(m, a)
            bid, ask = H.quote_at(cs, t, QUOTE_AGE)
            if bid is None or ask is None or ask <= bid or ask - bid > MAX_SPREAD + 1e-9:
                continue
            yes = m["result"] == "yes"
            strike = B.strike_of(m)
            # ---- base rate
            src, (p, k, n) = "kalshi", hist.rate(strike, t, exclude_event=ev)
            if series in FED_SERIES and fed:
                tp = B.transcript_rate(strike, t, fed)
                if tp[0] is not None:
                    src, (p, k, n) = "fed-transcript", tp
            if p is not None:
                side, price, fee, ps, bet = baserate_call(p, bid, ask)
                won = yes if side == "YES" else not yes
                out.append(row("mentions-baserate" + sfx, series, m, side, price, t, won, fee, ps, 1 if bet else 0,
                               f"h={hname}; base={src} {k}/{n}; p_yes={p:.3f}; bid={bid:.2f}; ask={ask:.2f}"))
            # ---- fade cheap Yes
            if FADE_LO <= ask <= FADE_HI:
                won = not yes
                mp = 1 - p if p is not None else round(1 - ask, 4)
                lim = maker_limit_no(bid, ask)
                lo, hi = H.trades_after(cs, t, a, 3600)
                if lim is not None:
                    filled = no_filled(lim, hi)
                    out.append(row("mentions-fade" + sfx, series, m, "NO", lim, t, won, 0.0, mp, 1 if filled else 0,
                                   f"h={hname}; pricing=maker; maker_fill={'traded-through' if filled else 'none'}; "
                                   f"yes_bid={bid:.2f}; yes_ask={ask:.2f}", status="settled" if filled else "void"))
                price = round(1 - bid, 4)
                out.append(row("mentions-fade-taker" + sfx, series, m, "NO", price, t, won, fees.taker_fee(price), mp, 1,
                               f"h={hname}; yes_bid={bid:.2f}; yes_ask={ask:.2f}"))
    return out


# ---------------- running ----------------

def _candles_factory(start_pad=timedelta(days=3)):
    """Hourly candles per market up to the anchor, fetched once per event (live tier) or per
    market (historical tier). Everything is cached on disk."""
    memo = {}

    def candles_of(m, end):
        if m["ticker"] in memo:
            return memo[m["ticker"]]
        start = max(H.open_of(m), end - max(HORIZONS.values()) - start_pad)
        if m.get("_tier") == "historical":
            memo[m["ticker"]] = H.market_candles(m, start, end)
        else:
            series = m["event_ticker"].split("-")[0]
            got = H.event_candles(series, m["event_ticker"], start, end)
            for tk, cs in got.items():
                memo.setdefault(tk, cs)
            memo.setdefault(m["ticker"], [])
        return memo[m["ticker"]]
    return candles_of


def walk_forward(rows, min_n=30):
    """Fade rows have no model of their own: their model_prob becomes the smoothed win rate of
    earlier fade bets (same engine) that had settled before this call; below min_n, the price.
    Base-rate rows keep the base rate."""
    by = defaultdict(list)
    for r in rows:
        if r["engine"].startswith("mentions-fade"):
            by[r["engine"]].append(r)
    for eng, rs in by.items():
        done = sorted((r for r in rs if r["status"] == "settled"), key=lambda r: r["close_time_utc"])
        for r in rs:
            past = [d["won"] == "1" for d in done if d["close_time_utc"] < r["logged_at_utc"]]
            if len(past) >= min_n:
                p = (sum(past) + 1) / (len(past) + 2)
                r["model_prob"] = f"{p:.4f}"
                r["gap_pts"] = f"{(p - float(r['market_price'])) * 100:.1f}"
            else:
                r["model_prob"] = r["market_price"]
                r["gap_pts"] = "0.0"
    return rows


def select_events(allm, min_events=3):
    """Series with at least `min_events` settled events; events that were cancelled are dropped."""
    out = {}
    for s, ms in allm.items():
        evs = H.by_event(H.settled(ms))
        evs = {e: v for e, v in evs.items() if not cancelled(v) and word_markets(v)}
        if len(evs) >= min_events:
            out[s] = evs
    return out


def top_series(allm, top, min_events=8):
    """The `top` series by traded volume among those with min_events+ settled events."""
    vol = {}
    for s, ms in allm.items():
        if len(select_events({s: ms}, min_events)) == 1:
            vol[s] = sum(H.num(m.get("volume_fp")) or 0 for m in ms)
    return sorted(vol, key=lambda s: -vol[s])[:top]


def run(series=None, since=None, max_events=None, top=None, verbose=True):
    """Replay the most recent `max_events` settled events of each chosen series (all series, a
    list, or the `top` by volume). Kalshi's historical tier needs one candle request per market,
    so the full category (~79,000 markets) is not replayed in one go."""
    from .. import log
    allm = H.load_all()
    if top:
        series = top_series(allm, top)
    if series:
        allm = {s: v for s, v in allm.items() if s in series}
    picks = select_events(allm)
    fed = T.fed_transcripts(2022) if any(s in FED_SERIES for s in picks) else []
    candles_of = _candles_factory()
    rows, n_ev = [], 0
    for s, evs in sorted(picks.items()):
        hist = B.KalshiHistory(H.settled(allm[s]))
        order = sorted(evs.items(), key=lambda kv: anchor(kv[1]))
        if since:
            order = [kv for kv in order if anchor(kv[1]) >= since]
        if max_events:
            order = order[-max_events:]
        for e, ms in order:
            rows += replay_event(s, ms, hist, fed, candles_of)
            n_ev += 1
        if verbose:
            print(f"  {s}: {len(order)} events, {len(rows)} rows so far", flush=True)
    walk_forward(rows)
    rows.sort(key=lambda r: (r["logged_at_utc"], r["market_ticker"], r["engine"]))
    log.write_log(rows, LOG_PATH)
    if verbose:
        print(f"{n_ev} events replayed, {len(rows)} rows -> {LOG_PATH}")
    return rows
