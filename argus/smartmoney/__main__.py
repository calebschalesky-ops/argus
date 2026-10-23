"""Command line: python -m argus.smartmoney <freeze|track|bigtrades|settle|score>"""
import argparse
import csv

from . import SMART_DIR, bigtrades, common, score, wallets


def money(x):
    return f"${x:,.0f}" if isinstance(x, (int, float)) else str(x)


def cmd_freeze(a):
    r = wallets.freeze(a.candidates, a.keep, a.min_markets, a.min_events, a.max_share)
    print(f"Checked {r['checked']} leaderboard wallets (top {a.candidates} all-time + top {a.candidates} past month).")
    print(f"Filter: {r['filter']}")
    print(f"Frozen {r['kept']} wallets -> {r['path']}")
    for b in r["wallets"][:15]:
        print(f"  {b['wallet'][:10]}  {b['user_name'][:18]:18}  all-time {money(b['pnl_all']):>12}  "
              f"{b['ended_markets']:4} markets / {b['distinct_events']:4} events  top event {b['top_event_share']:.0%}")
    if r["kept"] > 15:
        print(f"  ... and {r['kept'] - 15} more")
    why = {"markets": 0, "events": 0, "share": 0, "pnl": 0}
    for b in r["rejected"]:
        why["markets"] += b["ended_markets"] < a.min_markets
        why["events"] += b["distinct_events"] < a.min_events
        why["share"] += b["top_event_share"] > a.max_share
        why["pnl"] += b["closed_realized_pnl"] <= 0
    print(f"Rejected {len(r['rejected'])}: too few ended markets {why['markets']}, too few events {why['events']}, "
          f"one event > {a.max_share:.0%} of profit {why['share']}, closed P/L <= 0 {why['pnl']} (a wallet can fail several).")


def cmd_track(a):
    r = wallets.track(a.delay)
    print(f"Frozen list: {r['path'].name} (freeze {r.get('freeze_utc', '?')}), {r['wallets']} wallets.")
    print(f"Delay {a.delay} min + Kalshi taker fee on every entry.")
    for w, (name, n_trades, n_rows) in r["per_wallet"].items():
        if n_trades:
            print(f"  {w[:10]} {name[:18]:18} {n_trades:4} trades since freeze -> {n_rows} new calls")
    print("Counts:", r["counts"] or "no trades since the freeze yet")
    print(f"Logged {len(r['rows'])} new wallet calls -> smartmoney_log.csv")
    for row in r["rows"][:10]:
        n = common.note_fields(row["notes"])
        print(f"  {n['user'][:14]:14} {row['range_label'][:55]:55} fill {float(n['fill_price']):.3f} "
              f"-> ours {row['market_price']} (+fee {row['fee_per_contract']})")


def cmd_bigtrades(a):
    r = bigtrades.bigtrades(a.hours, a.delay, a.size_mult, a.min_notional, a.max_pages)
    c = r["counts"]
    print(f"Kalshi trades, last {a.hours:g} h (read back to {c.get('window_start_utc', '-')}): "
          f"{c.get('fills_read', 0)} fills read, "
          f"{c.get('fills_after_15min_skip', 0)} after skipping 15-minute series, {c.get('orders', 0)} orders.")
    print(f"Rule: block trade, or >= {a.size_mult:g}x the market's median order over 7 days and >= ${a.min_notional:,.0f}.")
    print("Counts:", c)
    for row in r["rows"][:15]:
        n = common.note_fields(row["notes"])
        print(f"  {row['market_ticker'][:40]:40} {row['side']:3} {n['reason']:8} {n['contracts']:>9} ct "
              f"${float(n['notional_usd']):>9,.0f}  x{n['size_x_median']:<6} fill {float(row['anchor_price']):.2f} "
              f"-> ours {row['market_price']}  move after {n['move_after_pts']} pts / {n['move_minutes']} min")
    print(f"Logged {len(r['rows'])} new big-trade calls -> smartmoney_log.csv")


def cmd_settle(a):
    rows = common.read_log()
    n_w = wallets.settle(rows)
    n_b = bigtrades.settle(rows)
    if n_w or n_b:
        common.write_log(rows)
    print(f"Settled {n_w} wallet calls and {n_b} big-trade calls. "
          f"{sum(r['status'] == 'pending' for r in rows)} still pending.")


def cmd_score(a):
    if not a.no_settle:
        cmd_settle(a)
    cards = score.scorecards(common.read_log())
    print(f"Followable = >= {score.MIN_SETTLED} settled calls AND z >= {score.MIN_Z} AND P/L after fees > 0.")
    print(f"{'source':34} {'calls':>5} {'settled':>7} {'win%':>5} {'avg px':>6} {'edge':>6} {'z':>5} "
          f"{'P/L':>7} {'Brier px':>8}  verdict")
    out = []
    for s in cards:
        c, e = s["card"], s["edge"]
        label = (f"wallet {s['source'][:10]} {s['name'][:12]}" if s["engine"] == "wallet"
                 else f"bigtrade ({s['source']})")
        verdict = "FOLLOWABLE" if s["followable"] else (f"needs {s['needs']} more settled" if s["needs"]
                                                       else "not beating the price")
        f = lambda x, fmt: format(x, fmt) if x is not None else "-"
        print(f"{label:34} {c['total_calls']:5} {c['settled_calls']:7} {f(c['win_rate'] and c['win_rate'] * 100, '5.0f'):>5} "
              f"{f(e.get('avg_price'), '6.2f'):>6} {f(e['edge_pts'], '+6.1f'):>6} {f(e['z'], '5.2f'):>5} "
              f"{c['pnl_after_fees']:7.2f} {f(c['brier_market'], '8.3f'):>8}  {verdict}")
        out.append({"engine": s["engine"], "source": s["source"], "name": s["name"],
                    "calls": c["total_calls"], "settled": c["settled_calls"], "win_rate": c["win_rate"],
                    "avg_price": e.get("avg_price"), "edge_pts": e["edge_pts"], "z": e["z"],
                    "pnl_after_fees": c["pnl_after_fees"], "brier_price": c["brier_market"],
                    "followable": int(s["followable"])})
    if not cards:
        print("No calls logged yet.")
    path = SMART_DIR / f"scorecard_{common.now_utc():%Y-%m-%d}.csv"
    if out:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(out[0]))
            w.writeheader()
            w.writerows(out)
        print(f"Saved -> {path}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m argus.smartmoney",
                                description="Argus Stage 4 smart-money sources (read-only, paper only).")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("freeze", help="save today's qualifying top Polymarket wallets")
    s.add_argument("--candidates", type=int, default=100, help="how deep to read each leaderboard")
    s.add_argument("--keep", type=int, default=50, help="max wallets to freeze")
    s.add_argument("--min-markets", type=int, default=30, help="min closed markets that have ended")
    s.add_argument("--min-events", type=int, default=15, help="min distinct events among them")
    s.add_argument("--max-share", type=float, default=0.5, help="max share of profit from one event")
    s.set_defaults(fn=cmd_freeze)

    s = sub.add_parser("track", help="log frozen wallets' trades made after the freeze")
    s.add_argument("--delay", type=int, default=common.DEFAULT_DELAY_MIN, help="minutes after their trade we could enter")
    s.set_defaults(fn=cmd_track)

    s = sub.add_parser("bigtrades", help="flag and log Kalshi block / outsized trades")
    s.add_argument("--hours", type=float, default=2.0)
    s.add_argument("--delay", type=int, default=common.DEFAULT_DELAY_MIN)
    s.add_argument("--size-mult", type=float, default=10.0)
    s.add_argument("--min-notional", type=float, default=1000.0)
    s.add_argument("--max-pages", type=int, default=80, help="1000 fills per page")
    s.set_defaults(fn=cmd_bigtrades)

    s = sub.add_parser("settle", help="fill in results for settled calls")
    s.set_defaults(fn=cmd_settle)

    s = sub.add_parser("score", help="scorecard per wallet and for big trades (settles first)")
    s.add_argument("--no-settle", action="store_true")
    s.set_defaults(fn=cmd_score)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
