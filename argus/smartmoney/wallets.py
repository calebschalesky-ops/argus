"""Polymarket wallets as scored sources: freeze, track, settle.

Why a freeze: a leaderboard shows who WAS lucky or good up to today. To learn which,
we write today's qualifying wallets to smartmoney/frozen_YYYY-MM-DD.csv and then score
ONLY trades they make after that moment. Nothing they did before the freeze can enter
the log, so past luck cannot leak into the score.

Why the filter: one lucky bet is not skill (the all-time #1 wallet made $22M on the 2024
election across 14 markets). A wallet qualifies only with many markets that have ended,
spread over many distinct events, with no single event supplying most of its profit.

Data: Polymarket's official public Data API v2 (data-api.polymarket.com, unauthenticated
GET; v1 retires 2026-10-24). No wallet is connected and nothing is signed.
"""
import csv
import re
from collections import defaultdict
from datetime import datetime, timezone

from .. import fees
from . import SMART_DIR, common, http

FROZEN_COLUMNS = ["frozen_at_utc", "wallet", "user_name", "board", "rank_all", "rank_month",
                  "leaderboard_pnl_all", "volume_all", "ended_markets", "distinct_events",
                  "top_event_share", "closed_realized_pnl", "closed_rows_capped", "filter"]

# Hard rule: skip 15-minute markets. Polymarket's short crypto markets carry it in the slug
# (btc-updown-15m-..., eth-updown-5m-...). Anything 15 minutes or shorter is skipped.
SHORT_MARKET = re.compile(r"(updown|up-or-down)-(1|5|15)m\b|-(1|5|15)m-\d")


def is_short_market(slug):
    return bool(SHORT_MARKET.search(slug or ""))


# ---------------- freeze ----------------

def wallet_profile(closed_positions, as_of_date):
    """Summarise a wallet's closed positions on markets that ended before `as_of_date`.

    Returns ended_markets, distinct_events, top_event_share (share of positive profit
    from the single best event) and closed_realized_pnl.
    """
    ended = [p for p in closed_positions
             if p.get("end_date") and str(p["end_date"])[:10] < as_of_date]
    markets = {p["condition_id"] for p in ended}
    by_event = defaultdict(float)
    for p in ended:
        key = str(p.get("event_id") or p.get("event_slug") or p["condition_id"])
        by_event[key] += float(p.get("realized_pnl") or 0)
    positive = [v for v in by_event.values() if v > 0]
    share = max(positive) / sum(positive) if positive else 1.0
    return {"ended_markets": len(markets), "distinct_events": len(by_event),
            "top_event_share": round(share, 3),
            "closed_realized_pnl": round(sum(by_event.values()), 2)}


def passes(profile, min_markets, min_events, max_share):
    return (profile["ended_markets"] >= min_markets
            and profile["distinct_events"] >= min_events
            and profile["top_event_share"] <= max_share
            and profile["closed_realized_pnl"] > 0)


def leaderboard(period, limit):
    d = http.poly("/v2/leaderboard", {"time_period": period, "sort_by": "PNL", "limit": limit}) or {}
    return d.get("data") or []


def freeze(candidates=100, keep=50, min_markets=30, min_events=15, max_share=0.5, out_dir=None):
    """Pick today's qualifying top wallets and write smartmoney/frozen_YYYY-MM-DD.csv.

    Candidates = the top `candidates` of the all-time AND the past-month profit boards.
    """
    now = common.now_utc()
    today = now.strftime("%Y-%m-%d")
    filt = (f"ended_markets>={min_markets}; distinct_events>={min_events}; "
            f"top_event_share<={max_share}; closed_pnl>0")

    board = {}
    for period in ("all", "month"):
        for row in leaderboard(period, candidates):
            w = row["user_id"].lower()
            b = board.setdefault(w, {"wallet": w, "user_name": row.get("user_name") or "",
                                     "rank_all": "", "rank_month": "", "pnl_all": "", "vol_all": ""})
            if period == "all":
                b.update(rank_all=row["rank"], pnl_all=row["pnl"], vol_all=row["volume"])
            else:
                b["rank_month"] = row["rank"]

    kept = []
    for b in board.values():
        closed = http.poly("/v2/positions", {"user": b["wallet"], "status": "CLOSED", "limit": 1000}) or {}
        rows = closed.get("data") or []
        prof = wallet_profile(rows, today)
        b.update(prof, capped=len(rows) >= 1000)
        b["ok"] = passes(prof, min_markets, min_events, max_share)
        if b["ok"]:
            kept.append(b)

    # all-time profit first; month-board-only wallets after, by month rank
    kept.sort(key=lambda b: (b["pnl_all"] == "", -(b["pnl_all"] or 0), b["rank_month"] or 9999))
    kept = kept[:keep]

    out_dir = out_dir or SMART_DIR
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"frozen_{today}.csv"
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FROZEN_COLUMNS)
        w.writeheader()
        for b in kept:
            w.writerow({"frozen_at_utc": stamp, "wallet": b["wallet"], "user_name": b["user_name"],
                        "board": "all" if b["rank_all"] else "month",
                        "rank_all": b["rank_all"], "rank_month": b["rank_month"],
                        "leaderboard_pnl_all": round(b["pnl_all"], 2) if b["pnl_all"] != "" else "",
                        "volume_all": round(b["vol_all"], 2) if b["vol_all"] != "" else "",
                        "ended_markets": b["ended_markets"], "distinct_events": b["distinct_events"],
                        "top_event_share": b["top_event_share"],
                        "closed_realized_pnl": b["closed_realized_pnl"],
                        "closed_rows_capped": int(b["capped"]), "filter": filt})
    return {"path": path, "checked": len(board), "kept": len(kept), "filter": filt,
            "rejected": [b for b in board.values() if not b["ok"]], "wallets": kept}


def latest_frozen(out_dir=None):
    files = sorted((out_dir or SMART_DIR).glob("frozen_*.csv"))
    if not files:
        raise SystemExit("No frozen wallet list yet. Run: python -m argus.smartmoney freeze")
    return files[-1]


def read_frozen(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def epoch(stamp):
    return int(datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


# ---------------- track ----------------

def delayed_price(token_id, target_ts, trade_ts):
    """Price of `token_id` at the first observation at or after `target_ts`.

    Falls back to the latest observation at or before target_ts, but only if it was
    taken after the source's trade (otherwise we would be using a price from before
    they moved it). Returns (price, observed_ts), or None when no usable point exists yet.
    """
    d = http.poly("/v2/prices-history", {"token_id": token_id, "start": target_ts,
                                         "end": target_ts + 3600, "bucket_seconds": 60}) or {}
    pts = sorted((p for p in (d.get("data") or []) if p["timestamp"] >= target_ts),
                 key=lambda p: p["timestamp"])
    if pts:
        return float(pts[0]["price"]), pts[0]["timestamp"]
    d = http.poly("/v2/prices-history", {"token_id": token_id, "as_of": target_ts}) or {}
    pts = [p for p in (d.get("data") or []) if trade_ts < p["timestamp"] <= target_ts]
    if pts:
        return float(pts[-1]["price"]), pts[-1]["timestamp"]
    return None


def calls_from_trades(trades, wallet, user_name, freeze_ts, already, delay_min, now_ts, price_fn):
    """Turn a wallet's raw trades into paper calls.

    - only trades strictly AFTER the freeze;
    - BUYs only (a buy opens a position on that outcome; a sell is an exit);
    - one call per wallet per outcome token: twenty fills on one market are one bet,
      not twenty pieces of evidence;
    - skip 15-minute-or-shorter markets;
    - our entry = price `delay_min` minutes later, plus Kalshi's taker fee.
    Returns (rows, counts).
    """
    counts = defaultdict(int)
    first = {}
    for t in trades:
        if int(t["timestamp"]) <= freeze_ts:
            counts["before_freeze"] += 1
            continue
        if t.get("side") != "BUY":
            counts["sells"] += 1
            continue
        if is_short_market(t.get("slug")):
            counts["short_market"] += 1
            continue
        key = (wallet, t["token_id"])
        if key in already:
            counts["already_logged"] += 1
            continue
        if key not in first or int(t["timestamp"]) < int(first[key]["timestamp"]):
            first[key] = t

    rows = []
    for key, t in first.items():
        ts = int(t["timestamp"])
        target = ts + delay_min * 60
        if target > now_ts:
            counts["waiting_for_delay"] += 1
            continue
        got = price_fn(t["token_id"], target, ts)
        if got is None:
            counts["no_price_yet"] += 1
            continue
        price, seen_ts = got
        mp, fee, _ = common.entry_after_delay(price)
        outcome = t.get("outcome") or ""
        rows.append({
            "logged_at_utc": common.iso(now_ts), "engine": "wallet", "series": "polymarket",
            "event_ticker": t.get("event_slug") or "", "market_ticker": t["condition_id"],
            "range_label": f"{outcome}: {(t.get('title') or '')[:70]}",
            "side": outcome.upper() if outcome.upper() in ("YES", "NO") else outcome,
            "model_prob": f"{mp:.4f}", "market_price": f"{mp:.4f}", "gap_pts": "0.0",
            "worth_a_look": "0", "paper_contracts": "1", "fee_per_contract": f"{fee:.2f}",
            "anchor_price": f"{float(t['price']):.4f}", "anchor_kind": "wallet fill price",
            "close_time_utc": "", "status": "pending",
            "notes": common.make_notes(
                wallet=wallet, user=user_name, pm_condition=t["condition_id"], token=t["token_id"],
                outcome_index=t.get("outcome_index"), fill_price=t["price"], trade_utc=common.iso(ts),
                delay_min=delay_min, price_seen_utc=common.iso(seen_ts),
                delay_cost_pts=common.delay_cost_pts(t["price"], mp), slug=t.get("slug") or "",
                tx=t.get("transaction_hash") or ""),
        })
        already.add(key)
        counts["logged"] += 1
    return rows, counts


def track(delay_min=common.DEFAULT_DELAY_MIN, frozen_path=None, max_pages=4):
    """Log every frozen wallet's post-freeze buys as paper calls in smartmoney_log.csv."""
    path = frozen_path or latest_frozen()
    frozen = read_frozen(path)
    if not frozen:
        return {"path": path, "rows": [], "counts": {}, "wallets": 0, "per_wallet": {}}
    freeze_ts = min(epoch(r["frozen_at_utc"]) for r in frozen)
    already = set()
    for r in common.read_log():
        if r["engine"] == "wallet":
            n = common.note_fields(r["notes"])
            already.add((n.get("wallet"), n.get("token")))
    now_ts = int(common.now_utc().timestamp())
    all_rows, total, per_wallet = [], defaultdict(int), {}
    for fw in frozen:
        trades = http.poly_paged("/v2/trades", {"user": fw["wallet"], "start": freeze_ts + 1,
                                                "taker_only": "false", "limit": 500},
                                 max_pages=max_pages)
        rows, counts = calls_from_trades(trades, fw["wallet"], fw["user_name"], freeze_ts, already,
                                         delay_min, now_ts, delayed_price)
        per_wallet[fw["wallet"]] = (fw["user_name"], len(trades), len(rows))
        all_rows.extend(rows)
        for k, v in counts.items():
            total[k] += v
    if all_rows:
        common.append(all_rows)
    return {"path": path, "freeze_utc": common.iso(freeze_ts), "rows": all_rows,
            "counts": dict(total), "wallets": len(frozen), "per_wallet": per_wallet}


# ---------------- settle ----------------

def payout_for(resolution, outcome_index):
    """Polymarket/UMA resolution price: 1e18 = first outcome (Yes) won, 0 = second won."""
    if resolution.get("status") != "resolved" or resolution.get("price") in (None, ""):
        return None
    p = int(resolution["price"]) / 1e18
    return p if int(outcome_index) == 0 else 1 - p


def settle(rows):
    """Fill results for pending wallet rows from /v2/resolutions. Returns how many settled."""
    pending = [r for r in rows if r["engine"] == "wallet" and r["status"] not in ("settled", "void")]
    conds = sorted({r["market_ticker"].lower() for r in pending})
    res = {}
    for i in range(0, len(conds), 20):
        d = http.poly("/v2/resolutions", {"condition": ",".join(conds[i:i + 20])}) or {}
        for x in d.get("data") or []:
            res[x["condition_id"].lower()] = x
    n = 0
    for r in pending:
        x = res.get(r["market_ticker"].lower())
        if not x:
            continue
        pay = payout_for(x, common.note_fields(r["notes"]).get("outcome_index") or 0)
        if pay not in (0.0, 1.0):        # unresolved, or a 50/50 split: leave pending
            continue
        won = pay == 1.0
        r.update(status="settled", settle_value=f"{pay:.2f}", won="1" if won else "0",
                 pnl_after_fees=f"{fees.pnl(won, float(r['market_price']), int(r['paper_contracts'] or 1)):.2f}")
        n += 1
    return n
