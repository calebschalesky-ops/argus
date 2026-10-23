"""Promote holdout survivors to live paper loggers, make forward calls, settle them, run nightly.

  promote   every HOLDOUT PASS rule of the latest cohort (at most MAX_PROMOTED) becomes a
            live paper logger, saved in data/lake/promoted.json.
  live      for each promoted rule, the open markets whose scheduled close is between `hours`
            and `hours + WINDOW_H` from now go through the SAME hypothesis function the bench
            used, with Kalshi's current bid/ask as the newest candle. Calls go to
            lab_live_log.csv (prediction-log columns, engine "lab-live:<rule>"), one per rule per
            market. Taker calls record the ask; maker calls record the limit and are only
            counted if a later trade went strictly through it (checked at settle).
  settle    fills results from Kalshi (GET /markets?tickers=...), checks maker fills on the
            public trade tape, and charges the fee stored on each row.
  nightly   sync (time-boxed) + mine (only if the last cohort is 28+ days old) + settle + live
            + the strategy board. Plain Python: no Claude session, so it costs nothing to run.
Paper only. Argus never trades.
"""
import json
import uuid
from datetime import datetime, timezone

from .. import board, fees, log
from . import LIVE_LOG, PROMOTED, SURVIVORS, bench, http, lake, miner

MAX_PROMOTED = 25
WINDOW_H = 1.0           # a call is allowed while the scheduled close is hours..hours+1 away (run hourly)


def _now():
    return datetime.now(timezone.utc)


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def watch(rule_id, prob=None):
    """Paper-log a rule that did NOT pass the holdout, under its own engine name ("lab-watch:..."),
    so a near-miss gathers forward evidence without being mistaken for a survivor."""
    spec = miner.spec_from_id(rule_id)
    prev = json.loads(PROMOTED.read_text()) if PROMOTED.exists() else []
    if any(p["rule_id"] == rule_id for p in prev):
        return f"Already live: {rule_id}"
    prev.append({"rule_id": rule_id, "spec": spec, "prob": prob if prob is not None else 0.5, "cohort": "watch",
                 "promoted_at": _now().isoformat(), "kind": "watch"})
    PROMOTED.parent.mkdir(parents=True, exist_ok=True)
    PROMOTED.write_text(json.dumps(prev, indent=1))
    return f"Watching (NOT a holdout survivor): {rule_id}"


def promote():
    rows = log.read_log(SURVIVORS)
    if not rows:
        return "No survivors yet: run `python -m argus.lab mine` first."
    cohort = max(r["cohort"] for r in rows)
    ok = sorted((r for r in rows if r["cohort"] == cohort and r["status"] == "HOLDOUT PASS"),
                key=lambda r: float(r["hold_p"]))[:MAX_PROMOTED]
    prev = json.loads(PROMOTED.read_text()) if PROMOTED.exists() else []
    have = {p["rule_id"] for p in prev}
    added = [{"rule_id": r["rule_id"], "spec": json.loads(r["spec_json"]), "prob": float(r["mine_win_rate"]),
              "cohort": cohort, "promoted_at": _now().isoformat()} for r in ok if r["rule_id"] not in have]
    PROMOTED.parent.mkdir(parents=True, exist_ok=True)
    PROMOTED.write_text(json.dumps(prev + added, indent=1))
    if not ok:
        return f"Cohort {cohort}: no rule passed the holdout, nothing to promote."
    return f"Promoted {len(added)} new rule(s) from cohort {cohort} ({len(prev) + len(added)} live):\n" + \
        "\n".join(f"  {a['rule_id']}" for a in added)


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _open_markets(lo_ts, hi_ts):
    out = []
    http.paged("/markets", "markets", {"status": "open", "limit": 1000, "mve_filter": "exclude",
                                       "min_close_ts": lo_ts, "max_close_ts": hi_ts},
               on_page=lambda items, c: out.extend(items))
    return out


def _history(tickers, now_ts, hours_back=30):
    """Recent hourly candles for live markets (batch route), for rules that look back."""
    got = {}
    tickers = list(tickers)
    for i in range(0, len(tickers), 30):
        chunk = tickers[i:i + 30]
        d = http.get("/markets/candlesticks", {"market_tickers": ",".join(chunk), "start_ts": now_ts - hours_back * 3600,
                                               "end_ts": now_ts, "period_interval": 60}) or {}
        for x in d.get("markets", []):
            got[x.get("market_ticker")] = sorted((dict(zip(
                ["end_ts", "bid", "ask", "bid_lo", "bid_hi", "ask_lo", "ask_hi", "px", "px_lo", "px_hi", "volume"],
                lake.candle_row(x.get("market_ticker"), 60, c)[2:13])) for c in x.get("candlesticks") or []),
                key=lambda c: c["end_ts"])
    return got


def run_live(now=None):
    prom = json.loads(PROMOTED.read_text()) if PROMOTED.exists() else []
    if not prom:
        return "No promoted rules: run `python -m argus.lab promote` after a mine with HOLDOUT PASS rules."
    now = now or _now()
    now_ts = int(now.timestamp())
    con = lake.connect()
    smeta = {r[0]: {"category": r[1], "frequency": r[2], "fee_type": r[3], "fee_multiplier": r[4]}
             for r in con.execute("SELECT ticker, category, frequency, fee_type, fee_multiplier FROM series")}
    done = {(r["engine"], r["market_ticker"]) for r in log.read_log(LIVE_LOG)}
    rows = []
    for hours in sorted({p["spec"]["hours"] for p in prom}):
        lo, hi = now_ts + int(hours * 3600), now_ts + int((hours + WINDOW_H) * 3600)
        ms = [m for m in _open_markets(lo, hi)
              if smeta.get(lake.series_of(m["event_ticker"]), {}).get("frequency") != "fifteen_min"]
        rules = [p for p in prom if p["spec"]["hours"] == hours]
        needs_history = any(p["spec"]["family"] == "move" for p in rules)
        hist_by = _history([m["ticker"] for m in ms], now_ts) if needs_history and ms else {}
        for m in ms:
            s = lake.series_of(m["event_ticker"])
            meta = smeta.get(s, {})
            bid, ask = _num(m.get("yes_bid_dollars")), _num(m.get("yes_ask_dollars"))
            if bid is None or ask is None:
                continue
            close_ts = lake._ts(m["close_time"])
            pub = {"ticker": m["ticker"], "event_ticker": m["event_ticker"], "series": s, "tier": "live",
                   "open_ts": lake._ts(m.get("open_time")), "sched_close_ts": close_ts,
                   "strike_type": m.get("strike_type"), "floor_strike": m.get("floor_strike"),
                   "cap_strike": m.get("cap_strike"), "title": m.get("title"), "yes_sub_title": m.get("yes_sub_title"),
                   **meta}
            hist = [c for c in hist_by.get(m["ticker"], []) if c["end_ts"] < now_ts]
            hist.append({"end_ts": now_ts, "bid": bid, "ask": ask, "volume": 0})
            for p in rules:
                h = miner.hypothesis(p["spec"], p["prob"])
                engine = f"lab-{'watch' if p.get('kind') == 'watch' else 'live'}:{p['rule_id']}"
                if (engine, m["ticker"]) in done or not h.where(pub):
                    continue
                call = h.fn(pub, hist, now_ts)
                if call is None:
                    continue
                s_ask, s_bid = bench.side_prices(call.side, bid, ask)
                mult = meta.get("fee_multiplier") or 1.0
                if call.limit is None:
                    price, fee, note = s_ask, fees.taker_fee(s_ask, 1, mult), "mode=taker"
                else:
                    price = round(call.limit, 2)
                    fee = fees.maker_fee(price, 1, mult, "maker" in (meta.get("fee_type") or ""))
                    note = "mode=maker; maker_fill=pending"
                book = _book(m["ticker"])
                rows.append({"call_id": uuid.uuid4().hex[:10], "logged_at_utc": _iso(now_ts), "engine": engine,
                             "series": s, "event_ticker": m["event_ticker"], "market_ticker": m["ticker"],
                             "range_label": m.get("yes_sub_title") or m["ticker"].split("-")[-1],
                             "side": call.side.upper(), "model_prob": f"{p['prob']:.4f}", "market_price": f"{price:.4f}",
                             "paper_contracts": "1", "fee_per_contract": f"{fee:.2f}",
                             "anchor_price": f"{(s_ask + s_bid) / 2:.4f}", "anchor_kind": "side mid at call",
                             "close_time_utc": m["close_time"], "status": "pending",
                             "notes": f"{note}; call_ts={now_ts}; cohort={p['cohort']}; bid={bid}; ask={ask}; "
                                      f"contracts_per_order=1; book={book}"})
                done.add((engine, m["ticker"]))
    log.append(rows, LIVE_LOG)
    return f"Live: {len(prom)} promoted rules, {len(rows)} new paper calls in {LIVE_LOG.name}."


def _book(ticker, levels=3):
    """Top levels of the public order book at call time, compact: 'yes:px@size,...|no:px@size,...'.
    Candles hide whether the ask had any size; this records it (survey 3.5)."""
    try:
        raw = http.get(f"/markets/{ticker}/orderbook", {"depth": levels}) or {}
        d = raw.get("orderbook_fp") or raw.get("orderbook") or {}
    except Exception:                       # a missing book must not stop the logger
        return "unavailable"
    parts = []
    for side in ("yes", "no"):
        lv = d.get(f"{side}_dollars") or d.get(side) or []
        best = sorted(lv, key=lambda x: -float(x[0]))[:levels]
        parts.append(f"{side}:" + ",".join(f"{float(px):.2f}@{float(q):g}" for px, q in best))
    return "|".join(parts)


def _clv(row, close_ts):
    """Closing-line value: the side's mid at the last real two-sided hourly quote before close, minus the price."""
    t = int(_note(row, "call_ts"))
    d = http.get("/markets/candlesticks", {"market_tickers": row["market_ticker"], "start_ts": t,
                                           "end_ts": close_ts, "period_interval": 60}) or {}
    cs = []
    for x in d.get("markets", []):
        cs = [dict(zip(["end_ts", "bid", "ask"], lake.candle_row(row["market_ticker"], 60, c)[2:5]))
              for c in x.get("candlesticks") or []]
    cm = bench.closing_mid(sorted(cs, key=lambda c: c["end_ts"]), t, close_ts, close_ts, row["side"].lower())
    return None if cm is None else round(cm - float(row["market_price"]), 4)


def _note(row, key):
    for part in row["notes"].split(";"):
        k, _, v = part.strip().partition("=")
        if k == key:
            return v
    return None


def _maker_filled(row, close_ts):
    t = int(_note(row, "call_ts"))
    limit = float(row["market_price"])
    params = {"ticker": row["market_ticker"], "min_ts": t, "max_ts": close_ts, "limit": 1000}
    trades = http.paged("/markets/trades", "trades", params, max_pages=50)
    for x in trades:
        px = _num(x.get("yes_price_dollars"))
        if px is None:
            continue
        if (row["side"] == "YES" and px < limit - 1e-9) or (row["side"] == "NO" and px > 1 - limit + 1e-9):
            return True
    return False


def settle(path=LIVE_LOG):
    rows = log.read_log(path)
    pending = [r for r in rows if r["status"] == "pending"]
    if not pending:
        return "Settle: nothing pending."
    tickers = sorted({r["market_ticker"] for r in pending})
    res = {}
    for i in range(0, len(tickers), 50):
        d = http.get("/markets", {"tickers": ",".join(tickers[i:i + 50]), "limit": 1000}) or {}
        for m in d.get("markets", []):
            res[m["ticker"]] = m
    n = 0
    for r in pending:
        m = res.get(r["market_ticker"])
        if not m or m.get("result") not in ("yes", "no"):
            continue
        won = (m["result"] == "yes") == (r["side"] == "YES")
        contracts = int(r["paper_contracts"] or 0)
        if _note(r, "maker_fill") == "pending":
            filled = _maker_filled(r, lake._ts(m["close_time"]))
            r["notes"] = r["notes"].replace("maker_fill=pending", f"maker_fill={'yes' if filled else 'no'}")
            contracts = 1 if filled else 0
            r["paper_contracts"] = str(contracts)
        clv = _clv(r, lake._ts(m["close_time"]))
        if clv is not None and "clv=" not in r["notes"]:
            r["notes"] += f"; clv={clv:+.4f}"
        r["status"], r["won"] = "settled", "1" if won else "0"
        r["settle_value"] = m.get("expiration_value") or ""
        r["pnl_after_fees"] = (f"{fees.pnl(won, float(r['market_price']), contracts, float(r['fee_per_contract']) * contracts):.2f}"
                               if contracts else "0.00")
        n += 1
    log.write_log(rows, path)
    return f"Settle: {n} of {len(pending)} pending calls settled."


def nightly(minutes=90):
    from . import sync
    out = [f"Argus edge factory nightly run, {_now():%Y-%m-%d %H:%M} UTC"]
    rep = sync.run(minutes=minutes, steps=("series", "import", "recent", "candles", "backfill", "candles"),
                   verbose=False)
    out.append(f"Sync: {rep['api_calls']} calls ({rep['http_429']} rate-limited), {rep['minutes']} min; "
               f"lake {rep['counts']['markets']:,} markets, {rep['counts']['candles']:,} candles, {rep['disk_mb']} MB")
    out.append(miner.run(nightly=True, verbose=False))
    if "HOLDOUT PASS" in (SURVIVORS.read_text(encoding="utf-8") if SURVIVORS.exists() else ""):
        out.append(promote())
    out.append(settle())
    out.append(run_live())
    out.append(board.format_board([r for r in board.board() if r["source"].startswith("lab_")]))
    text = "\n".join(out)
    (lake.LAKE / "nightly_last.txt").write_text(text, encoding="utf-8")
    return text
