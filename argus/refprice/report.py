"""Scorecard for the refprice logs: per engine, the board's numbers plus edge per contract,
bets per week, a t-stat on the per-bet P/L and the fair Brier (model P(Yes) vs Kalshi's Yes MIDPOINT,
so the market is not penalised for its spread)."""
import math
from collections import defaultdict
from datetime import datetime

from .. import board, log


def _notes(r):
    return dict(x.split("=", 1) for x in (r.get("notes") or "").split("; ") if "=" in x)


def fair_brier(rows):
    """Brier of P(Yes) for the model and for Kalshi's midpoint, one point per market and moment."""
    seen, pm, pk = set(), [], []
    for r in rows:
        if r["status"] != "settled":
            continue
        n = _notes(r)
        key = (r["market_ticker"], n.get("priced_at"))
        if key in seen or "p_yes" not in n:
            continue
        seen.add(key)
        y = int(r["won"]) if r["side"] == "YES" else 1 - int(r["won"])
        pm.append((float(n["p_yes"]), y))
        pk.append((float(n["yes_mid"]), y))
    return log.brier(pm), log.brier(pk), len(pm)


def summarise(rows):
    settled = [r for r in rows if r["status"] == "settled"]
    bets = [r for r in settled if int(r["paper_contracts"] or 0) > 0]
    card = log.scorecard(rows)
    n_events = len({r["event_ticker"] for r in settled})
    pnls = [float(r["pnl_after_fees"]) for r in bets]
    mean = sum(pnls) / len(pnls) if pnls else None
    sd = (math.sqrt(sum((x - mean) ** 2 for x in pnls) / (len(pnls) - 1)) if len(pnls) > 1 else None)
    # bets cluster inside an event (neighbouring strikes move together): t-stat on per-event P/L
    by_ev = defaultdict(float)
    for r in bets:
        by_ev[r["event_ticker"]] += float(r["pnl_after_fees"])
    ev = list(by_ev.values())
    ev_mean = sum(ev) / len(ev) if ev else None
    ev_sd = math.sqrt(sum((x - ev_mean) ** 2 for x in ev) / (len(ev) - 1)) if len(ev) > 1 else None
    t_ev = ev_mean / (ev_sd / math.sqrt(len(ev))) if ev_sd else None
    closes = [datetime.fromisoformat(r["close_time_utc"].replace("Z", "+00:00")) for r in settled]
    weeks = max((max(closes) - min(closes)).days / 7, 1) if closes else None
    fb_model, fb_mkt, fb_n = fair_brier(rows)
    return {"calls": len(rows), "settled": len(settled), "events": n_events, "bets": len(bets),
            "bet_events": len(by_ev), "wins": card["paper_wins"], "pnl": card["pnl_after_fees"],
            "staked": card["staked"], "roi": card["roi"], "edge_per_bet": mean, "sd_per_bet": sd,
            "t_events": t_ev, "bets_per_week": len(bets) / weeks if weeks else None,
            "brier_model": card["brier_model"], "brier_market": card["brier_market"],
            "fair_brier_model": fb_model, "fair_brier_market": fb_mkt, "fair_n": fb_n,
            "verdict": board.verdict(card, n_events)}


def by_engine(path):
    groups = defaultdict(list)
    for r in log.read_log(path):
        groups[r["engine"]].append(r)
    return {e: summarise(rs) for e, rs in sorted(groups.items())}


def format_table(cards):
    f4 = lambda x: "  n/a " if x is None else f"{x:.4f}"
    L = ["Engine                              Events Bets  Won   P/L     ROI  c/bet  t(ev) bets/wk  Brier board m/k   fair m/k        Verdict"]
    for e, c in cards.items():
        roi = "  n/a" if c["roi"] is None else f"{c['roi'] * 100:+5.1f}%"
        epb = "  n/a" if c["edge_per_bet"] is None else f"{c['edge_per_bet'] * 100:+5.1f}"
        t = "  n/a" if c["t_events"] is None else f"{c['t_events']:+5.1f}"
        bpw = "  n/a" if c["bets_per_week"] is None else f"{c['bets_per_week']:6.1f}"
        L.append(f"{e[:35]:<35} {c['events']:>6} {c['bets']:>4} {c['wins']:>4} {c['pnl']:>+7.2f} {roi} {epb} {t} {bpw}  "
                 f"{f4(c['brier_model'])}/{f4(c['brier_market'])}  {f4(c['fair_brier_model'])}/{f4(c['fair_brier_market'])}  {c['verdict']}")
    return "\n".join(L)


def calibration(path, engine_suffix="-ev3-"):
    """Model P(Yes) bins vs how often Yes happened vs Kalshi's mid in the same bin (one point per market+moment)."""
    rows = [r for r in log.read_log(path) if engine_suffix in r["engine"] and r["status"] == "settled"]
    bins = defaultdict(list)
    for r in rows:
        n = _notes(r)
        y = int(r["won"]) if r["side"] == "YES" else 1 - int(r["won"])
        p = float(n["p_yes"])
        bins[min(int(p * 10), 9)].append((p, float(n["yes_mid"]), y))
    L = ["P(Yes) bin   n     model  kalshi-mid  happened"]
    for b in sorted(bins):
        xs = bins[b]
        L.append(f"{b * 10:>3}-{b * 10 + 10:<3}%  {len(xs):>5}  {sum(x[0] for x in xs) / len(xs):6.3f}  "
                 f"{sum(x[1] for x in xs) / len(xs):9.3f}  {sum(x[2] for x in xs) / len(xs):8.3f}")
    return "\n".join(L)
