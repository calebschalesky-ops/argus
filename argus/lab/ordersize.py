"""`python -m argus.lab ordersize`: re-score the near-misses at 1, 5, 10 and 25 contracts per order.

Kalshi rounds the fee up to the cent once per ORDER, not per contract (research/how_to_find_edges.md
section 4.1). One contract at 95c pays a full cent; ten contracts in one order pay 4c, 0.4c each.
Every backtest so far assumed one contract per order, which overcharges cheap-fee favourites.

Two kinds of re-score:
  logs   the exact rows already on the board (same markets, prices and results), with the fee
         recomputed as fees.taker_fee(price, N) / N (maker rows: maker_fee only where the row paid
         one). The logs hold no order-book depth, so these assume the size was there.
  bench  the same rules re-run on the lake with Hypothesis(contracts=N): an N-contract order is
         allowed only if 3N contracts traded in the candle at the decision time (bench.DEPTH_MULT).
Each (strategy, N) is its own engine in lab_ordersize_log.csv, so the board shows them side by side,
and each is a row in the trial registry.
"""
import json
from collections import defaultdict
from datetime import datetime, timezone

from .. import fees, log
from . import ROOT, bench, lake, miner, reference, registry, stats

SIZES = (1, 5, 10, 25)
OUT = ROOT / "lab_ordersize_log.csv"

# (log file, engine, pricing) taken from the board as it stood on 2026-10-22
LOG_TARGETS = [
    ("commodities_buckets_log.csv", "bucket-taker-90-99c", "taker"),
    ("commodities_buckets_log.csv", "bucket-maker-90-99c", "maker"),
    ("commodities_latefav_log.csv", "latefav-15m", "taker"),
    ("commodities_latefav_log.csv", "latefav-30m", "taker"),
    ("commodities_latefav_log.csv", "latefav-60m", "taker"),
    ("fifteen_latefav_log.csv", "latefav-backtest", "taker"),
    ("fifteen_maker_log.csv", "maker-backtest", "maker"),
    ("backtest_filtered_log.csv", "oil-filtered-maker", "maker"),
]
FAV_OBS = ROOT / "data" / "cache" / "favorites_obs.json"


def _ts(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def _fee(price, n, pricing, paid_fee):
    if pricing == "maker":
        return fees.maker_fee(price, n) if paid_fee > 0 else 0.0
    return fees.taker_fee(price, n)


def log_records(path, engine, pricing):
    """Settled, filled bets of one engine as bench-style records (per contract), plus a fee check."""
    out, mismatch = [], 0
    for r in log.read_log(ROOT / path):
        if r["engine"] != engine or r["status"] != "settled" or int(float(r["paper_contracts"] or 0)) <= 0:
            continue
        price = float(r["market_price"])
        paid = float(r["fee_per_contract"] or 0)
        if abs(_fee(price, 1, pricing, paid) - paid) > 1e-9:
            mismatch += 1
        out.append({"ticker": r["market_ticker"], "event": r["event_ticker"], "series": r["series"],
                    "category": "", "t": _ts(r["logged_at_utc"]), "close_ts": _ts(r["close_time_utc"]),
                    "side": r["side"].lower(), "won": int(r["won"]), "price": price, "paid_fee": paid,
                    "pricing": pricing, "mid_side": price})
    return out, mismatch


def favorites_records():
    """argus/favorites' 95-99c Yes asks 1 hour before close (early closers excluded), from its saved observations."""
    if not FAV_OBS.exists():
        return []
    obs = json.loads(FAV_OBS.read_text(encoding="utf-8"))["obs"]
    return [{"ticker": o["ticker"], "event": o["event"], "series": o["series"], "category": o["category"],
             "t": _ts(o["t"]), "close_ts": _ts(o["close"]), "side": "yes", "won": int(o["won"]), "price": o["price"],
             "paid_fee": fees.taker_fee(o["price"]), "pricing": "taker", "mid_side": o["price"]}
            for o in obs if o["horizon"] == "1h" and o["bucket"] == "95-99" and not o["early"] and o["src"] == "ask"]


def at_size(recs, n):
    out = []
    for r in recs:
        fee = _fee(r["price"], n, r["pricing"], r["paid_fee"])
        out.append(dict(r, contracts=n, fee=fee / n, pnl=fees.pnl(bool(r["won"]), r["price"], n, fee) / n))
    return out


def log_rows(name, recs, n):
    return [{"logged_at_utc": datetime.fromtimestamp(r["t"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "engine": f"{name}@{n}", "series": r["series"], "event_ticker": r["event"], "market_ticker": r["ticker"],
             "side": r["side"].upper(), "model_prob": f"{r['mid_side']:.4f}", "market_price": f"{r['price']:.4f}",
             "paper_contracts": str(n), "fee_per_contract": f"{r['fee']:.4f}",
             "close_time_utc": datetime.fromtimestamp(r["close_ts"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "status": "settled", "won": str(r["won"]), "pnl_after_fees": f"{r['pnl'] * n:.4f}",
             "notes": f"contracts_per_order={n}" + ("" if r.get("clv") is None else f"; clv={r['clv']:+.4f}")}
            for r in recs]


def bench_hypotheses():
    """The near-misses that the lake can replay, each at every order size."""
    com = reference.where
    base = [
        ("latefav-60m (commodities, 85-97c)", 1, reference.latefav, com),
        ("favourites 95-99c at 1h (all categories)",
         1, miner.hypothesis({"family": "fav", "scope": "all", "hours": 1, "side": "fav", "mode": "taker",
                              "band": [0.95, 1.0]}).fn, None),
    ]
    for mode in ("taker", "maker"):
        spec = {"family": "fav", "scope": "category:Commodities", "hours": 6, "side": "fav", "mode": mode, "band": [0.90, 1.0]}
        base.append((f"commodities bucket 90-99c {mode} at 6h (approx. 10 AM ET replay)", 6,
                     miner.hypothesis(spec).fn, com))
    return [bench.Hypothesis(f"{name}@{n}", hrs, fn, where, contracts=n) for name, hrs, fn, where in base for n in SIZES]


def run(con=None):
    con = con or lake.connect()
    lines, rows, trials = [], [], []
    batch = f"ordersize-{datetime.now(timezone.utc):%Y%m%d%H%M}"
    lines.append("Order-size re-score (fee rounded once per order). ROI after fees per contract; CI resamples events.")
    lines.append("")
    lines.append(f"{'strategy':<58} {'N':>3} {'bets':>6} {'ev':>5} {'fee/ct':>7} {'ROI':>7}  {'95% CI':<18} {'p':>8}  CLV net")
    targets = [(f"{path.replace('_log.csv', '')}:{eng}", log_records(path, eng, pricing)) for path, eng, pricing in LOG_TARGETS]
    targets.append(("favorites:95-99c 1h (backtest obs)", (favorites_records(), 0)))
    for name, (recs, mismatch) in targets:
        for n in SIZES:
            rs = at_size(recs, n)
            s = stats.summary(rs)
            lines.append(_line(f"log  {name}", n, s) + (f"   ({mismatch} rows' stored fee differs)" if mismatch and n == 1 else ""))
            rows += log_rows(f"log:{name}", rs, n)
            trials.append(_trial(batch, f"log:{name}@{n}", s, "fee re-score of logged rows; no depth check"))
    got = bench.run_lake(bench_hypotheses(), con)
    for h in bench_hypotheses():
        rs = got[h.name]
        s = stats.summary(rs)
        lines.append(_line(f"lake {h.name.rsplit('@', 1)[0]}", h.contracts, s))
        rows += log_rows(f"bench:{h.name.rsplit('@', 1)[0]}", rs, h.contracts)
        trials.append(_trial(batch, f"bench:{h.name}", s, f"depth: {bench.DEPTH_MULT}N traded in the hour"))
    if OUT.exists():
        OUT.unlink()                       # a re-score replaces the previous one (the registry keeps every try)
    log.append(rows, OUT)
    registry.append(trials)
    return "\n".join(lines)


def _line(name, n, s):
    if not s.get("n"):
        return f"{name[:58]:<58} {n:>3}      0"
    ci = f"[{s['ci_lo'] * 100:+.1f}%, {s['ci_hi'] * 100:+.1f}%]" if s.get("ci_lo") is not None else ""
    fee = s["fees"] / s["n"]
    clv = f"{s['clv']['mean_net_of_fee'] * 100:+.2f}c" if s.get("clv") else "n/a"
    return (f"{name[:58]:<58} {n:>3} {s['n']:>6} {s['events']:>5} {fee * 100:>6.2f}c {s['roi'] * 100:>+6.2f}%  "
            f"{ci:<18} {s['p']:>8.2g}  {clv}")


def _trial(batch, name, s, note):
    return {"kind": "ordersize", "name": name, "batch": batch, "data_window": "as logged / whole lake",
            "n": s.get("n", 0), "events": s.get("events", 0), "roi": s.get("roi"), "p": s.get("p", 1.0), "note": note}
