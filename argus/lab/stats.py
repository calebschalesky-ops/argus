"""Statistics for the bench and the miner: ROI, Brier, event-clustered bootstrap, p-values, BH.

Plain words: markets in one event move together (one oil price settles a whole ladder), so
40 bets on one event are closer to one bet than to 40. Every interval and p-value here
treats the EVENT as the unit: we resample whole events, and test the average profit per event.
"""
import math

import numpy as np


def roi(pnl, staked):
    return pnl / staked if staked else None


def event_arrays(records):
    """(per-event profit, per-event stake) arrays from bet records."""
    by = {}
    for r in records:
        e = by.setdefault(r["event"], [0.0, 0.0])
        e[0] += r["pnl"]
        e[1] += r["price"]
    a = np.array(list(by.values()), dtype=float).reshape(-1, 2)
    return a[:, 0], a[:, 1]


def bootstrap_ci(pnl_e, stake_e, n_boot=2000, alpha=0.05, seed=7):
    """95% interval for ROI (total profit / total stake), resampling whole events."""
    k = len(pnl_e)
    if k < 2:
        return None, None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, k, size=(n_boot, k))
    p, s = pnl_e[idx].sum(axis=1), stake_e[idx].sum(axis=1)
    r = np.divide(p, s, out=np.zeros_like(p), where=s > 0)
    return float(np.quantile(r, alpha / 2)), float(np.quantile(r, 1 - alpha / 2))


def p_value(pnl_e):
    """One-sided p-value that the average profit per event is above zero (normal approximation)."""
    k = len(pnl_e)
    if k < 2:
        return 1.0
    sd = float(np.std(pnl_e, ddof=1))
    mean = float(np.mean(pnl_e))
    if sd == 0:
        return 0.0 if mean > 0 else 1.0
    z = mean / (sd / math.sqrt(k))
    return 0.5 * math.erfc(z / math.sqrt(2))


def p_from_moments(k, s1, s2):
    """Same one-sided p-value from count, sum and sum of squares (vectorised for the miner)."""
    k = np.asarray(k, dtype=float)
    mean = np.divide(s1, k, out=np.zeros_like(k), where=k > 0)
    var = np.divide(s2 - k * mean ** 2, k - 1, out=np.zeros_like(k), where=k > 1)
    se = np.sqrt(np.maximum(var, 0) / np.maximum(k, 1))
    z = np.divide(mean, se, out=np.where(mean > 0, 50.0, -50.0), where=se > 0)
    from scipy.special import erfc
    return 0.5 * erfc(z / math.sqrt(2))


def p_breakeven(sum_pnl, sum_var, sum_logq, all_won):
    """One-sided p-value against the null "each bet wins exactly at its break-even rate".

    A bet at price P with fee F breaks even when it wins with probability q = P + F; its profit is
    (won - q), so under the null the total profit has mean 0 and variance sum q(1-q). This catches
    what a t-test misses on favourites: 50 straight wins at 97c have almost no sample variance, but
    a 97c favourite wins 50 in a row 22% of the time even with no edge. If every bet won, the
    p-value is at least the chance of that streak at break-even (product of q)."""
    from scipy.special import erfc
    sum_pnl, sum_var = np.asarray(sum_pnl, dtype=float), np.asarray(sum_var, dtype=float)
    z = np.divide(sum_pnl, np.sqrt(sum_var), out=np.where(sum_pnl > 0, 50.0, -50.0), where=sum_var > 0)
    p = 0.5 * erfc(z / math.sqrt(2))
    return np.where(np.asarray(all_won, dtype=bool), np.maximum(p, np.exp(np.asarray(sum_logq, dtype=float))), p)


def honest_p(records):
    """max(event-clustered t-test, break-even null) for bet records."""
    pe, _ = event_arrays(records)
    q = np.clip(np.array([r["price"] + r["fee"] for r in records]), 1e-6, 1 - 1e-6)
    won = np.array([r["won"] for r in records], dtype=float)
    pb = p_breakeven((won - q).sum(), (q * (1 - q)).sum(), np.log(q).sum(), won.all())
    return max(p_value(pe), float(pb))


def benjamini_hochberg(pvals, q=0.05):
    """Boolean array: which hypotheses are discoveries at false-discovery rate q."""
    p = np.asarray(pvals, dtype=float)
    m = len(p)
    if m == 0:
        return np.zeros(0, dtype=bool)
    order = np.argsort(p)
    thresh = q * np.arange(1, m + 1) / m
    passed = p[order] <= thresh
    out = np.zeros(m, dtype=bool)
    if passed.any():
        last = np.max(np.nonzero(passed)[0])
        out[order[:last + 1]] = True
    return out


def brier(pairs):
    pairs = [(p, o) for p, o in pairs if p is not None]
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs) if pairs else None


def summary(records, recent_days=90, now_ts=None):
    """The bench's report card for a list of bet records (filled bets only count as bets)."""
    bets = [r for r in records if r.get("filled", True)]
    out = {"calls": len(records), "n": len(bets), "events": len({r["event"] for r in bets})}
    if not bets:
        return out
    staked = sum(r["price"] for r in bets)
    pnl = sum(r["pnl"] for r in bets)
    pe, se = event_arrays(bets)
    lo, hi = bootstrap_ci(pe, se)
    out.update(wins=sum(r["won"] for r in bets), win_rate=sum(r["won"] for r in bets) / len(bets),
               avg_price=staked / len(bets), fees=round(sum(r["fee"] for r in bets), 2),
               pnl=round(pnl, 2), roi=roi(pnl, staked), ci_lo=lo, ci_hi=hi, p=honest_p(bets),
               brier_market=brier([(r["mid_side"], r["won"]) for r in bets]),
               brier_model=brier([(r.get("prob"), r["won"]) for r in bets]))
    clv = [r["clv"] for r in bets if r.get("clv") is not None]
    if clv:
        a = np.array(clv)
        net = np.array([r["clv"] - r["fee"] for r in bets if r.get("clv") is not None])
        out["clv"] = {"n": len(a), "mean": float(a.mean()), "se": float(a.std(ddof=1) / math.sqrt(len(a))) if len(a) > 1 else None,
                      "share_pos": float((a > 0).mean()), "mean_net_of_fee": float(net.mean())}
    if any(r.get("mode") == "maker" for r in records):
        out["fill_rate"] = len(bets) / len(records)
    ts = sorted(r["t"] for r in bets)
    mid = ts[len(ts) // 2]
    for name, part in (("first_half", [r for r in bets if r["t"] < mid]),
                       ("second_half", [r for r in bets if r["t"] >= mid])):
        st = sum(r["price"] for r in part)
        out[name] = {"n": len(part), "events": len({r["event"] for r in part}),
                     "roi": roi(sum(r["pnl"] for r in part), st)}
    cut = (now_ts or ts[-1]) - recent_days * 86400
    rec = [r for r in bets if r["t"] >= cut]
    out["recent_3m"] = {"n": len(rec), "events": len({r["event"] for r in rec}),
                        "roi": roi(sum(r["pnl"] for r in rec), sum(r["price"] for r in rec))}
    cats = {}
    for r in bets:
        c = cats.setdefault(r.get("category") or "?", [0, set(), 0.0, 0.0])
        c[0] += 1
        c[1].add(r["event"])
        c[2] += r["pnl"]
        c[3] += r["price"]
    out["by_category"] = {k: {"n": v[0], "events": len(v[1]), "roi": roi(v[2], v[3])}
                          for k, v in sorted(cats.items(), key=lambda kv: -kv[1][0])}
    return out


def fmt_pct(x):
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def format_summary(name, s):
    if not s.get("n"):
        return f"{name}: no bets ({s.get('calls', 0)} calls)"
    L = [f"{name}",
         f"  bets {s['n']} on {s['events']} events, won {s['win_rate'] * 100:.1f}% at avg price {s['avg_price'] * 100:.1f}c"
         + (f", fill rate {s['fill_rate'] * 100:.0f}%" if "fill_rate" in s else ""),
         f"  P/L after fees ${s['pnl']:+.2f}  ROI {fmt_pct(s['roi'])}  95% CI [{fmt_pct(s['ci_lo'])}, {fmt_pct(s['ci_hi'])}]"
         f"  p={s['p']:.3g}",
         f"  Brier market {s['brier_market']:.4f}" + (f"  model {s['brier_model']:.4f}" if s.get('brier_model') is not None else "")
         + (f"  CLV {s['clv']['mean'] * 100:+.2f}c (se {(s['clv']['se'] or 0) * 100:.2f}c, {s['clv']['share_pos'] * 100:.0f}% positive,"
            f" net of fee {s['clv']['mean_net_of_fee'] * 100:+.2f}c, n={s['clv']['n']})" if s.get("clv") else ""),
         f"  first half {fmt_pct(s['first_half']['roi'])} ({s['first_half']['events']} ev), second half "
         f"{fmt_pct(s['second_half']['roi'])} ({s['second_half']['events']} ev), last 3 months "
         f"{fmt_pct(s['recent_3m']['roi'])} ({s['recent_3m']['events']} ev)",
         "  by category: " + ", ".join(f"{k} {fmt_pct(v['roi'])} ({v['events']} ev)" for k, v in list(s["by_category"].items())[:8])]
    return "\n".join(L)
