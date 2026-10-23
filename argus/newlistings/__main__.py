"""python -m argus.newlistings collect | report [--weekly] | live [--h 24 --window 2 --no-log] | settle

collect  read the sample's early candles, 24h-before-close candles and early trades (cached; ~45 min cold)
report   calibration tables + walk-forward backtests; writes the backtest rows to newlistings_log.csv
         (engines newlisting-<variant>; live rows are kept) and the full tables to data/cache/newlistings/report.md.
         --weekly also counts every Kalshi listing of the last 7 days (network) to estimate calls per week.
live     paper calls on markets opened h to h+window hours ago (default h = 6 and 24, pooled band rule;
         logged as newlisting-live-wfband-<h>h)
settle   settle pending live rows from Kalshi's results
"""
import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from .. import favorites as fav
from .. import log
from . import CACHE, LOG_PATH
from . import data as D
from . import live as L
from . import study as S

LIVE_PREFIX = "newlisting-live"


def pct(x, d=1):
    return "  n/a" if x is None else f"{x * 100:+.{d}f}%"


def ci(c):
    return "n/a" if c is None or c[0] is None else f"{c[0] * 100:+.0f}..{c[1] * 100:+.0f}%"


def calib_table(rows, h, by, title):
    t = S.calibration(rows, h, by)
    out = [f"### {title} ({h[1:]}h after open vs 24h before close)", "",
           "| group | n | events | avg early | avg late | won | bias early-won (95% event CI) | Brier early | Brier late | gap (95% CI) |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for k, c in sorted(t.items(), key=lambda kv: (kv[0] != "ALL", -kv[1]["n"])):
        if c["n"] < 20:
            continue
        out.append(f"| {k} | {c['n']} | {c['events']} | {c['early']:.3f} | {c['late']:.3f} | {c['win']:.3f} | "
                   f"{c['bias'] * 100:+.1f} pts ({ci(c['bias_ci'])}) | {c['brier_early']:.4f} | {c['brier_late']:.4f} | "
                   f"{(c['brier_early'] - c['brier_late']):+.4f} ({num_ci(c['gap_ci'])}) |")
    return out + [""]


def num(x):
    return "n/a" if x is None else f"{x:.3f}"


def num_ci(c):
    return "n/a" if c is None or c[0] is None else f"{c[0]:+.4f}..{c[1]:+.4f}"


def strat_lines(name, sp):
    out = [f"**{name}** - verdict: {S.verdict(sp)}", "",
           "| slice | n | events | win | avg price | ROI after fees (95% event CI) | P/L | Brier model / price |", "|---|---|---|---|---|---|---|---|"]
    for k, s in sp.items():
        bm = "n/a" if s["brier_model"] is None else f"{s['brier_model']:.4f} / {s['brier_market']:.4f}"
        out.append(f"| {k} | {s['n']} | {s['events']} | {num(s['win'])} | {num(s['avg_price'])} | "
                   f"{pct(s['roi'])} ({ci(s['ci'])}) | {s['pnl']:+.2f} | {bm} |")
    return out + [""]


def backtest_rows(bets):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [{"logged_at_utc": now, "engine": f"newlisting-{b['variant']}", "series": b["series"],
             "event_ticker": b["event"], "market_ticker": b["ticker"], "side": b["side"].upper(),
             "model_prob": f"{b['p_model']:.4f}", "market_price": f"{b['price']:.2f}",
             "gap_pts": f"{(b['p_model'] - b['price'] - b['fee']) * 100:.1f}", "paper_contracts": "1",
             "fee_per_contract": f"{b['fee']:.2f}", "anchor_kind": "backtest: Yes mid at entry + past cell bias",
             "close_time_utc": b["close"], "status": "settled", "won": "1" if b["won"] else "0",
             "pnl_after_fees": f"{b['pnl']:.2f}",
             "notes": f"entry={b['t']}; category={b['category']}; type={b['type']}; strikes={b['n_in_event']}"
                      + (f"; maker filled_at={datetime.fromtimestamp(b['filled_at'], timezone.utc):%Y-%m-%dT%H:%M}Z"
                         if b.get("filled_at") else "")} for b in bets]


def run_report(weekly=False):
    meta, rows = D.load()
    smeta = fav.series_meta()
    out = ["# New listings - full tables", "", f"Sample: {meta['rows']} settled markets ({meta['sampled']} drawn of "
           f"{meta['in_scope']} in scope; opened {meta['since'][:10]}..{meta['until'][:10]}, lived 48h+, no 15-minute, no combos).", ""]
    cov = S.coverage(rows)
    out += ["Real two-sided book (bid within 5c of ask): " + ", ".join(f"{k}: {v * 100:.0f}%" for k, v in cov.items()), ""]
    ex = sum(r["early_close"] for r in rows)
    out += [f"Early-closed markets excluded from the calibration tables: {ex}.", "", "## Calibration", ""]
    for h in S.HORIZONS:
        out += calib_table(rows, h, lambda r, e: r["category"], "By category")
    out += calib_table(rows, "h1", lambda r, e: S.band_of(e), "By early price band")
    out += calib_table(rows, "h24", lambda r, e: S.band_of(e), "By early price band")
    out += calib_table(rows, "h1", lambda r, e: r["type"], "By market type")
    out += calib_table(rows, "h1", lambda r, e: D.strikes_bin(r["n_in_event"]), "By strikes in the event")
    out += calib_table(rows, "h24", lambda r, e: D.strikes_bin(r["n_in_event"]), "By strikes in the event")

    out += ["## Strategies (walk-forward; learn only from markets settled before each entry)", ""]
    variants = {}
    for h in S.HORIZONS:
        variants[f"wf-{h[1:]}h"] = S.walk_forward(rows, h, smeta)
        variants[f"mk-{h[1:]}h"] = S.walk_forward(rows, h, smeta, maker=True)
        variants[f"wfband-{h[1:]}h"] = S.walk_forward(rows, h, smeta, cell=S.band_cell, tag="wfband")
    variants["mkboth-1h"] = S.maker_both(rows, "h1", smeta)
    variants["mkboth-24h"] = S.maker_both(rows, "h24", smeta)
    strict = {"mk-1h strict": S.walk_forward(rows, "h1", smeta, maker=True, strict=True),
              "mkboth-1h strict": S.maker_both(rows, "h1", smeta, strict=True)}
    summ = {}
    for name, bets in list(variants.items()) + list(strict.items()):
        sp = S.splits(bets)
        summ[name] = sp
        out += strat_lines(name, sp)
    out += [f"Maker-both offers possible at 1h: {S.maker_offers(rows, 'h1')}, filled {len(variants['mkboth-1h'])}; "
            f"at 24h: {S.maker_offers(rows, 'h24')}, filled {len(variants['mkboth-24h'])}.", ""]
    for name in ("wf-1h", "wf-24h"):
        by = defaultdict(list)
        for b in variants[name]:
            by[(b["category"], b["side"])].append(b)
        out += [f"{name} by category and side:", ""] + [
            f"- {k[0]} {k[1]}: n={len(v)}, ROI {pct(S.roi(v))}" for k, v in sorted(by.items(), key=lambda kv: -len(kv[1]))] + [""]

    first = min(D.parse_time(r["open"]) for r in rows)
    weeks = (D.UNTIL - first).days / 7
    out += ["## Rate", "", f"Sample weeks: {weeks:.0f}."]
    for name in variants:
        out.append(f"- {name}: {len(variants[name]) / weeks:.1f} bets/week in the sample")
    if weekly:
        out += [""] + weekly_estimate(rows, variants, smeta)
    keep = [r for r in log.read_log(LOG_PATH) if r["engine"].startswith(LIVE_PREFIX)]
    log.write_log(keep, LOG_PATH)
    log.append(backtest_rows([b for bets in variants.values() for b in bets]), LOG_PATH)
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "report.md").write_text("\n".join(out), encoding="utf-8")
    return summ, out


def weekly_estimate(rows, variants, smeta):
    """Calls per week Kalshi-wide = per-category bet rate in the sample x that category's listings last week."""
    now = datetime.now(timezone.utc)
    f = CACHE / "weekly_listings.json"
    if f.exists() and now.timestamp() - f.stat().st_mtime < 86400:
        cnt, pages_capped = Counter(json.loads(f.read_text())["cnt"]), json.loads(f.read_text())["capped"]
    else:
        cnt, pages_capped = count_week(now, smeta)
        cnt = Counter(cnt)
        f.write_text(json.dumps({"cnt": dict(cnt), "capped": pages_capped}))
    in_sample = Counter(r["category"] for r in rows if D.parse_time(r["open"]) >= S.RECENT_FROM)
    out = [f"Kalshi listings opened in the last 7 days (48h+ life, no combos, no 15-minute): {sum(cnt.values())}"
           f"{' (LOWER BOUND: page cap hit)' if pages_capped else ''} "
           f"({', '.join(f'{k} {v}' for k, v in cnt.most_common(8))})."]
    for name, bets in variants.items():
        rec = Counter(b["category"] for b in bets if D.parse_time(b["t"]) >= S.RECENT_FROM)
        est = sum(cnt[c] * rec[c] / in_sample[c] for c in rec if in_sample[c])
        out.append(f"- {name}: about {est:.0f} qualifying listings/week Kalshi-wide (recent-3-month rate per category)")
    return out


def count_week(now, smeta, max_pages=400):
    info = {}
    ms = L.recent_markets(now, 24 * 7, status=None, max_pages=max_pages, info=info)
    capped = info["capped"]
    cnt = Counter()
    for m in ms:
        s = D.series_of(m["event_ticker"])
        meta = smeta.get(s) or {}
        if fav.is_fifteen_minute(s, meta.get("frequency") or "") or D.life_h(m) < D.MIN_LIFE_H:
            continue
        if D.parse_time(m["open_time"]) < now - timedelta(days=7):
            continue
        cnt[meta.get("category") or "Other"] += 1
    return dict(cnt), capped


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m argus.newlistings")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect")
    rp = sub.add_parser("report")
    rp.add_argument("--weekly", action="store_true")
    lp = sub.add_parser("live")
    lp.add_argument("--h", type=int, nargs="+", default=[6, 24], choices=[1, 6, 24])
    lp.add_argument("--by-category", action="store_true", help="cell = (category, band) instead of the pooled band")
    lp.add_argument("--window", type=float, default=2.0, help="hours after h a market still counts (run at least this often)")
    lp.add_argument("--no-log", action="store_true")
    sub.add_parser("settle")
    a = ap.parse_args(argv)
    if a.cmd == "collect":
        meta, _ = D.collect()
        print({k: v for k, v in meta.items() if k != "scope_by_week"})
    elif a.cmd == "report":
        summ, out = run_report(a.weekly)
        print("\n".join(x for x in out if not x.startswith("|") or "ALL" in x))
    elif a.cmd == "live":
        now = datetime.now(timezone.utc)
        markets, found, logged = L.run(tuple(f"h{h}" for h in a.h), a.window, do_log=not a.no_log, now=now,
                                       pooled=not a.by_category)
        print(f"{len(markets)} markets created recently (combos excluded).")
        print(L.format_calls(found, now))
        print(f"\nLogged {len(logged)} new rows to {LOG_PATH.name}." if not a.no_log else "\n(not logged)")
    elif a.cmd == "settle":
        n, e = L.settle()
        print(f"Settled {n} rows across {e} pending events.")


if __name__ == "__main__":
    main()
