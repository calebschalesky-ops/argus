"""Live paper calls: markets opened in the last few hours, priced with the walk-forward rule's cell bias.

A market qualifies when it is not a 15-minute market or a combo, will live 48h+, opened between
h and h + window hours ago (h = the rule's horizon), has a real book (bid within 5c of the ask),
and its cell (price band) has a past bias that leaves EDGE after the taker fee.
The bias comes from every settled market in the backtest sample (all of it is in the past now).
Default cell = the price band pooled over categories (the wfband rule, the only one with a positive
backtest); --by-category uses (category, band). Calls go to newlistings_log.csv
(engine newlisting-live-wfband-<h>h or newlisting-live-wf-<h>h, 1 paper contract). Never trades.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .. import fees, log
from .. import favorites as fav
from . import LOG_PATH
from . import data as D
from . import study as S


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def recent_markets(now, hours, status="open", max_pages=60, info=None):
    """Non-combo markets created in the last `hours`, read fresh. info['capped'] says if pages ran out first."""
    params = {"limit": 1000, "mve_filter": "exclude", "min_created_ts": int((now - timedelta(hours=hours)).timestamp())}
    if status:
        params["status"] = status
    out, cursor = [], None
    for _ in range(max_pages):
        d = D.get("/markets", params, cache=False)
        cursor = d.get("cursor")
        out.extend(m for m in d.get("markets", []) if not m["ticker"].startswith("KXMVE") and not m.get("mve_collection_ticker"))
        if not d.get("cursor"):
            break
        params = dict(params, cursor=d["cursor"])
    if info is not None:
        info["capped"] = bool(cursor)
    return out


def book_now(m):
    def p(k):
        v = _f(m.get(k))
        return v if v is not None and 0 < v < 1 else None
    return {"bid": p("yes_bid_dollars"), "ask": p("yes_ask_dollars"), "last": p("last_price_dollars"), "age_h": 0}


def biases(rows, h, cell=S.default_cell):
    """cell -> past bias (won - early mid) over all settled sample markets, where trusted."""
    g = defaultdict(list)
    for r in rows:
        m = D.mid(r["books"][h])
        if m is not None:
            g[cell(r, m)].append((m, int(r["won"]), r["event"]))
    return {k: sum(w - m for m, w, _ in v) / len(v) for k, v in g.items()
            if len(v) >= S.MIN_HIST and len({e for _, _, e in v}) >= S.MIN_HIST_EVENTS}


CAP_PER_EVENT = 3      # the backtest sample takes at most 3 markets per event; correlated calls beyond that add nothing


def candidates(markets, smeta, now, h, window_h, bias_by_cell, cap_per_event=CAP_PER_EVENT, pooled=True):
    out, per_event = [], defaultdict(int)
    hh = int(h[1:])
    for m in markets:
        s = D.series_of(m["event_ticker"])
        meta = smeta.get(s) or {}
        if fav.is_fifteen_minute(s, meta.get("frequency") or ""):
            continue
        o, c = D.parse_time(m["open_time"]), D.parse_time(m["close_time"])
        age = (now - o).total_seconds() / 3600
        if not hh <= age < hh + window_h or (c - o).total_seconds() / 3600 < D.MIN_LIFE_H:
            continue
        book = book_now(m)
        mid = D.mid(book)
        if mid is None:
            continue
        cat = meta.get("category") or "Other"
        cell = S.band_of(mid) if pooled else (cat, S.band_of(mid))
        charged, mult = fees.series_maker_fees(meta)
        d = S.decide(book, bias_by_cell.get(cell), False, mult, charged)
        if d and per_event[m["event_ticker"]] < cap_per_event:
            per_event[m["event_ticker"]] += 1
            out.append({"m": m, "series": s, "category": cat, "book": book, "age_h": age, "cell": cell,
                        "bias": bias_by_cell[cell], "side": d[0], "price": d[1], "fee": d[2], "p": d[3]})
    return out


def log_rows(cands, now, h, tag="wfband"):
    rows = []
    for c in cands:
        m = c["m"]
        rows.append({
            "logged_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "engine": f"newlisting-live-{tag}-{h[1:]}h",
            "series": c["series"], "event_ticker": m["event_ticker"], "market_ticker": m["ticker"],
            "range_label": (m.get("yes_sub_title") or m.get("title") or "")[:80], "side": c["side"].upper(),
            "model_prob": f"{c['p']:.4f}", "market_price": f"{c['price']:.2f}",
            "gap_pts": f"{(c['p'] - c['price'] - c['fee']) * 100:.1f}", "worth_a_look": "",
            "paper_contracts": "1", "fee_per_contract": f"{c['fee']:.2f}", "anchor_price": f"{D.mid(c['book']):.3f}",
            "anchor_kind": f"Yes mid now + past bias of the cell {c['cell']}", "close_time_utc": m["close_time"],
            "horizon_days": f"{(D.parse_time(m['close_time']) - now).total_seconds() / 86400:.2f}", "status": "pending",
            "notes": (f"category={c['category']}; cell={c['cell']}; bias={c['bias']:+.3f}; bid={c['book']['bid']}; "
                      f"ask={c['book']['ask']}; age_h={c['age_h']:.1f}; pricing=taker at ask"),
        })
    return rows


def run(horizons=("h6", "h24"), window_h=2.0, hours=None, do_log=True, now=None, path=LOG_PATH, pooled=True):
    now = now or datetime.now(timezone.utc)
    _, rows = D.load()
    smeta = fav.series_meta()
    look = hours or max(int(h[1:]) for h in horizons) + window_h + 48   # created can precede open
    markets = recent_markets(now, look)
    found, logged = [], []
    for h in horizons:
        cell = S.band_cell if pooled else S.default_cell
        cands = candidates(markets, smeta, now, h, window_h, biases(rows, h, cell), pooled=pooled)
        found.extend((h, c) for c in cands)
        new = log_rows(cands, now, h, "wfband" if pooled else "wf")
        if do_log and new:
            pending = {(r["market_ticker"], r["engine"]) for r in log.read_log(path)}
            new = [r for r in new if (r["market_ticker"], r["engine"]) not in pending]
            log.append(new, path)
            logged.extend(new)
    return markets, found, logged


def settle(path=LOG_PATH):
    """Settle pending live rows from Kalshi's results, charging the fee stored on each row."""
    rows = log.read_log(path)
    events = sorted({r["event_ticker"] for r in rows if r["status"] != "settled"})
    n = 0
    for ev in events:
        ms = D.get("/markets", {"event_ticker": ev, "limit": 1000}, cache=False).get("markets", [])
        done = {m["ticker"]: m["result"] == "yes" for m in ms if m.get("result") in ("yes", "no")}
        for r in rows:
            if r["event_ticker"] != ev or r["status"] == "settled" or r["market_ticker"] not in done:
                continue
            won = done[r["market_ticker"]] if r["side"] == "YES" else not done[r["market_ticker"]]
            k = int(r["paper_contracts"] or 0)
            r.update(status="settled", won="1" if won else "0",
                     pnl_after_fees=f"{fees.pnl(won, float(r['market_price']), k, fee=float(r['fee_per_contract']) * k):.2f}")
            n += 1
    log.write_log(rows, path)
    return n, len(events)


def format_calls(found, now):
    L = [f"NEW LISTINGS - paper calls {now:%Y-%m-%d %H:%M} UTC (Argus never trades)", ""]
    if not found:
        L.append("  No qualifying listings right now.")
    for h, c in found:
        m = c["m"]
        L.append(f"  [{h[1:]}h rule] {m['ticker']:<45} {c['side'].upper():<3} at {c['price'] * 100:.0f}c "
                 f"(fee {c['fee'] * 100:.0f}c)  model {c['p'] * 100:.0f}%  mid {D.mid(c['book']) * 100:.0f}c  "
                 f"{c['category']} cell {c['cell']}  opened {c['age_h']:.1f}h ago  closes {m['close_time'][:16]}")
    return "\n".join(L)
