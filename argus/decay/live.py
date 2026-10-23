"""Today's open by-date markets that the decay rules would bet No on (paper only).

Same rules as the backtest, applied now:
  simple-Kd  today is the K-th day before the deadline (K = 3, 7, 14) and the Yes mid is 10-40c
  hazard     inside the last 25% of the scheduled life, Yes mid 60c or less, and the No edge
             against the constant-hazard fair price (from the Yes mid 7 days ago) is 3c+ after the fee
Prices: the live Yes bid/ask, counted only with a real bid within 5c of the ask. No price = 1 - Yes bid.
`--log` appends each new pick to decay_log.csv (engine decay-<variant>-live, one row per market
per engine, ever). `settle` fills in the results from Kalshi. Argus never trades.
"""
import csv
import uuid
from datetime import datetime, timedelta, timezone

from .. import fees
from .. import log as plog
from . import LOG_PATH
from . import backtest as bt
from . import collect, http

DAY = 86400


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def yes_quote(m):
    """(bid, ask) in dollars from a live market object, or None without a real two-sided book."""
    bid = _f(m.get("yes_bid_dollars"))
    ask = _f(m.get("yes_ask_dollars"))
    if bid is None and m.get("yes_bid") is not None:
        bid = _f(m["yes_bid"]) / 100
    if ask is None and m.get("yes_ask") is not None:
        ask = _f(m["yes_ask"]) / 100
    if bid is None or ask is None or bid < 0.01 or ask >= 1 or ask < bid or ask - bid > bt.MAX_SPREAD + 1e-9:
        return None
    return bid, ask


def open_by_date(series_meta, max_pages=200):
    """Every open by-date market in the kept categories, with its parsed deadline."""
    kept = {s["ticker"]: s for s in collect.kept_series(list(series_meta.values()))}
    events = http.paged("/events", "events", {"status": "open", "with_nested_markets": "true", "limit": 200},
                        max_pages=max_pages)
    out = []
    for e in events:
        s = kept.get(e.get("series_ticker") or e["event_ticker"].split("-", 1)[0])
        if not s:
            continue
        for m in e.get("markets") or []:
            if m.get("status") not in ("active", "open") or not collect.is_by_date(m):
                continue
            d = collect.deadline_of(m)
            if d is None:
                continue
            m["_category"], m["deadline"] = s.get("category") or "Other", d.isoformat()
            m["_series"] = s
            out.append(m)
    return out


def evaluate(m, now, candles=None):
    """The rules that fire on this market today: list of (variant, no_price, model_no, note)."""
    S = bt.parse(m["deadline"])
    opened = bt.parse(m["open_time"])
    if not bt.schedule_ok(S, bt.parse(m.get("latest_expiration_time"))) or S <= now:
        return []
    q = yes_quote(m)
    if q is None:
        return []
    bid, ask = q
    mid = (bid + ask) / 2
    no_ask = round(1 - bid, 4)
    left = (S - now).total_seconds() / DAY
    out = []
    for k in bt.SIMPLE_KS:
        if k - 1 < left <= k and bt.SIMPLE_BAND[0] <= mid <= bt.SIMPLE_BAND[1]:
            out.append((f"simple-{k}d", no_ask, no_ask, f"yes_mid={mid:.3f}"))   # no model: price paid
    life = (S - opened).total_seconds() / DAY
    if 1 <= left <= life * bt.LATE_FRACTION and mid <= bt.HAZARD_MAX_YES and candles is not None:
        t0 = now - timedelta(days=bt.LOOKBACK_DAYS)
        q0 = bt.quote_at(candles, bt.ts(t0)) if t0 >= opened else None
        if q0:
            fair = bt.hazard_fair(sum(q0) / 2, (S - t0).total_seconds(), (S - now).total_seconds())
            _, mult = fees.series_maker_fees(m.get("_series"))
            if (1 - fair) - no_ask - fees.taker_fee(no_ask, multiplier=mult) >= bt.MIN_EDGE:
                out.append(("hazard", no_ask, 1 - fair, f"yes_mid={mid:.3f};yes_mid_7d_ago={sum(q0) / 2:.3f};fair_yes={fair:.3f}"))
    return out


def needs_candles(m, now):
    S, opened = bt.parse(m["deadline"]), bt.parse(m["open_time"])
    left = (S - now).total_seconds() / DAY
    q = yes_quote(m)
    return q is not None and 1 <= left <= (S - opened).total_seconds() / DAY * bt.LATE_FRACTION \
        and sum(q) / 2 <= bt.HAZARD_MAX_YES


def recent_candles(m, now):
    s = m["event_ticker"].split("-", 1)[0]
    params = {"start_ts": bt.ts(now) - 10 * DAY, "end_ts": bt.ts(now), "period_interval": 1440}
    return (http.get(f"/series/{s}/markets/{m['ticker']}/candlesticks", params) or {}).get("candlesticks") or []


def picks(now=None):
    now = now or datetime.now(timezone.utc)
    meta = {s["ticker"]: s for s in http.get("/series", {}).get("series", [])}
    out = []
    for m in open_by_date(meta):
        cs = recent_candles(m, now) if needs_candles(m, now) else None
        for variant, price, model_no, note in evaluate(m, now, cs):
            out.append({"m": m, "variant": variant, "price": price, "model_no": model_no, "note": note,
                        "days_left": (bt.parse(m["deadline"]) - now).total_seconds() / DAY})
    return sorted(out, key=lambda p: (p["variant"], p["days_left"]))


def already_logged(path=LOG_PATH):
    if not path.exists():
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {(r["engine"], r["market_ticker"]) for r in csv.DictReader(f)}


def log_picks(ps, now, path=LOG_PATH):
    seen = already_logged(path)
    rows = []
    for p in ps:
        engine = f"decay-{p['variant']}-live"
        m = p["m"]
        if (engine, m["ticker"]) in seen:
            continue
        _, mult = fees.series_maker_fees(m.get("_series"))
        fee = fees.taker_fee(p["price"], multiplier=mult)
        rows.append({"call_id": uuid.uuid4().hex[:10], "logged_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "engine": engine, "series": m["event_ticker"].split("-", 1)[0],
                     "event_ticker": m["event_ticker"], "market_ticker": m["ticker"], "range_label": "by-date",
                     "side": "NO", "model_prob": f"{p['model_no']:.4f}", "market_price": f"{p['price']:.4f}",
                     "gap_pts": f"{(p['model_no'] - p['price']) * 100:.1f}", "worth_a_look": "1",
                     "paper_contracts": "1", "fee_per_contract": f"{fee:.2f}", "anchor_kind": "no-ask",
                     "horizon_days": f"{p['days_left']:.2f}", "close_time_utc": m.get("close_time", ""),
                     "status": "pending", "notes": f"live; category={m['_category']}; deadline={m['deadline'][:10]}; {p['note']}"})
    if rows:
        plog.append(rows, path)
    return rows


def settle(path=LOG_PATH):
    """Fill in results for pending live rows whose market has settled."""
    rows = plog.read_log(path)
    n = 0
    for r in rows:
        if r["status"] == "settled" or not (r.get("notes") or "").startswith("live"):
            continue
        m = (http.get(f"/markets/{r['market_ticker']}") or {}).get("market") or {}
        if m.get("result") in ("yes", "no"):
            plog.settle_row(r, yes_result=m["result"] == "yes")
            n += 1
    if n:
        plog.write_log(rows, path)
    print(f"settled {n} live decay rows")
    return n


def main(log=False, now=None):
    now = now or datetime.now(timezone.utc)
    ps = picks(now)
    print(f"DEADLINE DECAY - open by-date markets the rules bet No on today ({now:%Y-%m-%d %H:%M} UTC). Paper only.")
    print(f"{'rule':<11} {'days':>5} {'No':>4} {'model':>6}  {'category':<22} market")
    for p in ps:
        m = p["m"]
        print(f"{p['variant']:<11} {p['days_left']:5.1f} {p['price'] * 100:3.0f}c {p['model_no'] * 100:5.0f}%  "
              f"{m['_category'][:22]:<22} {m['ticker']}  {(m.get('title') or '')[:70]}")
    if not ps:
        print("  (none today)")
    if log:
        rows = log_picks(ps, now)
        print(f"{len(rows)} new rows logged to {LOG_PATH.name}")
    return ps
