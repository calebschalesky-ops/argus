"""Kalshi large trades as a scored source.

Kalshi's public GetTrades feed (GET /markets/trades) is anonymous: ticker, size, prices,
which side the taker bought, time, and whether it was a block trade. We cannot know WHO
traded, only that someone paid up in size. So each flagged trade is logged as a call on
the taker's side, entered `delay` minutes later after fees, and scored like any source.

A trade is flagged when it is a block trade, or when the order (all fills sharing one
timestamp and taker side) is at least `size_mult` times the median order size in that
market over the past week AND worth at least `min_notional` dollars.

One call per market per side, ever: the first flagged order. A live match can throw up
dozens of big orders in an hour; they are one opinion repeated, not dozens of pieces of
evidence, and counting them separately would make luck look like skill.
"""
from collections import defaultdict
from datetime import datetime, timezone
from statistics import median

import requests

from ..kalshi import is_fifteen_minute
from . import common, http


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def side_price(t, side):
    return float(t["yes_price_dollars"] if side == "yes" else t["no_price_dollars"])


def group_orders(trades):
    """Merge fills that share ticker, timestamp and taker side into one order."""
    orders = {}
    for t in trades:
        side = t.get("taker_side") or t.get("taker_outcome_side")
        key = (t["ticker"], t["created_time"], side)
        o = orders.get(key)
        n = float(t.get("count_fp") or t.get("count") or 0)
        px = side_price(t, side)
        if o is None:
            orders[key] = o = {"ticker": t["ticker"], "created_time": t["created_time"],
                               "ts": parse_ts(t["created_time"]), "side": side, "count": 0.0,
                               "cost": 0.0, "block": False, "order_id": t["trade_id"]}
        o["count"] += n
        o["cost"] += n * px
        o["block"] = o["block"] or bool(t.get("is_block_trade"))
    for o in orders.values():
        o["price"] = o["cost"] / o["count"] if o["count"] else 0.0
    return sorted(orders.values(), key=lambda o: o["ts"])


def flag_orders(orders, size_mult=10.0, min_notional=1000.0, min_history=10):
    """Return [(order, reason, median_count)] for unusually large orders in ONE market."""
    out = []
    med = median(x["count"] for x in orders) if orders else 0.0
    for o in orders:
        if o["block"]:
            out.append((o, "block", med))
        elif (len(orders) > min_history and med > 0 and o["count"] >= size_mult * med
              and o["cost"] >= min_notional):
            out.append((o, "outsized", med))
    return out


def move_after(order, orders, delay_min):
    """Price on the taker's side after the order: at the first trade >= delay later, and latest."""
    later = [x for x in orders if x["ts"] > order["ts"]]
    def px(x):
        return x["price"] if x["side"] == order["side"] else 1 - x["price"]
    at_delay = next((x for x in later if x["ts"] >= order["ts"] + delay_min * 60), None)
    last = later[-1] if later else None
    return (px(at_delay) if at_delay else None,
            (px(last), (last["ts"] - order["ts"]) / 60) if last else (None, None))


def series_frequency():
    d = http.kalshi("/series") or {}
    return {s["ticker"]: s.get("frequency") for s in d.get("series", [])}


def skip_short(ticker, freq):
    s = ticker.split("-")[0]
    return is_fifteen_minute({"ticker": s, "frequency": freq.get(s)})


def bigtrades(hours=2.0, delay_min=common.DEFAULT_DELAY_MIN, size_mult=10.0, min_notional=1000.0,
              max_pages=80, history_days=7):
    now = common.now_utc().timestamp()
    freq = series_frequency()
    feed = http.kalshi_paged("/markets/trades", "trades",
                             {"limit": 1000, "min_ts": int(now - hours * 3600)}, max_pages=max_pages)
    counts = defaultdict(int)
    counts["fills_read"] = len(feed)
    if feed:
        counts["window_start_utc"] = min(t["created_time"] for t in feed)[:19] + "Z"
    feed = [t for t in feed if not skip_short(t["ticker"], freq)]
    counts["fills_after_15min_skip"] = len(feed)
    orders = group_orders(feed)
    counts["orders"] = len(orders)
    candidates = {o["ticker"] for o in orders if o["block"] or o["cost"] >= min_notional}
    counts["candidate_markets"] = len(candidates)

    prior = [r for r in common.read_log() if r["engine"] == "bigtrade"]
    logged = {common.note_fields(r["notes"]).get("order_id") for r in prior}
    taken = {(r["market_ticker"], r["side"]) for r in prior if r["status"] != "void"}
    rows, flagged_all = [], []
    for ticker in sorted(candidates):
        try:
            hist = http.kalshi_paged("/markets/trades", "trades",
                                     {"ticker": ticker, "limit": 1000,
                                      "min_ts": int(now - history_days * 86400)}, max_pages=3)
        except requests.HTTPError:     # still rate limited after backing off: skip, count it
            counts["skipped_rate_limited"] += 1
            continue
        horders = group_orders(hist)
        window = [x for x in flag_orders(horders, size_mult, min_notional)
                  if x[0]["ts"] >= now - hours * 3600]
        if not window:
            continue
        try:
            market = (http.kalshi(f"/markets/{ticker}") or {}).get("market", {})
        except requests.HTTPError:
            counts["skipped_rate_limited"] += 1
            continue
        for o, reason, med in window:
            flagged_all.append((o, reason))
            if o["order_id"] in logged:
                counts["already_logged"] += 1
                continue
            if (ticker, o["side"].upper()) in taken:
                counts["repeat_same_market_side"] += 1
                continue
            if o["ts"] + delay_min * 60 > now:
                counts["waiting_for_delay"] += 1
                continue
            p_delay, (p_last, mins_last) = move_after(o, horders, delay_min)
            source = "next trade after delay"
            if p_delay is None:       # nothing traded since: use the current ask on that side
                ask = market.get(f"{o['side']}_ask_dollars")
                p_delay, source = (float(ask), "current ask") if ask not in (None, "") else (None, "")
            if p_delay is None or p_delay <= 0:
                counts["no_price"] += 1
                continue
            mp, fee, _ = common.entry_after_delay(p_delay)
            rows.append({
                "logged_at_utc": common.iso(now), "engine": "bigtrade",
                "series": ticker.split("-")[0], "event_ticker": market.get("event_ticker", ""),
                "market_ticker": ticker,
                "range_label": (market.get("yes_sub_title") or market.get("title") or "")[:70],
                "side": o["side"].upper(), "model_prob": f"{mp:.4f}", "market_price": f"{mp:.4f}",
                "gap_pts": "0.0", "worth_a_look": "0", "paper_contracts": "1",
                "fee_per_contract": f"{fee:.2f}", "anchor_price": f"{o['price']:.4f}",
                "anchor_kind": "big-trade fill price", "close_time_utc": market.get("close_time", ""),
                "status": "pending",
                "notes": common.make_notes(
                    order_id=o["order_id"], reason=reason, traded_utc=o["created_time"],
                    contracts=round(o["count"], 2), notional_usd=round(o["cost"], 2),
                    median_order=round(med, 2), size_x_median=round(o["count"] / med, 1) if med else "",
                    delay_min=delay_min, entry_source=source,
                    delay_cost_pts=common.delay_cost_pts(o["price"], mp),
                    move_after_pts=round((p_last - o["price"]) * 100, 1) if p_last is not None else "",
                    move_minutes=round(mins_last, 1) if mins_last is not None else ""),
            })
            logged.add(o["order_id"])
            taken.add((ticker, o["side"].upper()))
    counts["flagged"] = len(flagged_all)
    counts["logged"] = len(rows)
    if rows:
        common.append(rows)
    return {"rows": rows, "counts": dict(counts), "flagged": flagged_all}


def settle(rows):
    """Fill results for pending bigtrade rows from Kalshi's public market results."""
    from .. import log
    n = 0
    cache = {}
    for r in rows:
        if r["engine"] != "bigtrade" or r["status"] in ("settled", "void"):
            continue
        t = r["market_ticker"]
        if t not in cache:
            cache[t] = (http.kalshi(f"/markets/{t}") or {}).get("market", {})
        result = cache[t].get("result")
        if result in ("yes", "no"):
            log.settle_row(r, None, result == "yes")
            n += 1
    return n
