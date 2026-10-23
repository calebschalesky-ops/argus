"""Print and save the alert in the exact format from SPEC.md section 8."""
from pathlib import Path

from .engine import WORTH_A_LOOK_PTS
from .model import ET, now_utc

ROOT = Path(__file__).resolve().parent.parent
ALERTS = ROOT / "alerts"
SETTLES = {
    "KXWTIW": "ICE WTI daily settle, nearest contract, 2:30 PM ET",
    "KXWTI": "ICE WTI daily settle, nearest contract, 2:30 PM ET",
    "KXBRENTW": "Pyth Brent index, 1-minute close at 5:00 PM ET",
    "KXBRENTD": "Pyth Brent index, 1-minute close at 5:00 PM ET",
}


def _c(x):
    return f"{round(x * 100):>3d}c" if x else "   -"


def format_alert(res):
    now = now_utc()
    hours = (res.close - now).total_seconds() / 3600
    close_et = res.close.astimezone(ET).strftime("%a %b %d %I:%M %p ET")
    last = (f"${res.last_settle[1]:.2f} ({res.last_settle[0][:10]})" if res.last_settle else "n/a")
    ev_text = "; ".join(f"{e.name} @ {e.when.strftime('%a %b %d %I:%M %p')} ET"
                        + ("" if e.confirmed else " (unconfirmed)") for e in res.events) or "none"
    L = []
    L.append("==================== ARGUS ALERT ====================")
    L.append(f"Market:    {res.event_ticker} - {res.title}")
    L.append(f"Closes:    {close_et}   ({hours:.1f} h left)")
    L.append(f"Settles:   {SETTLES.get(res.series, '')}")
    L.append(f"Anchor:    ${res.anchor:.2f} ({res.anchor_kind})   Last settle: {last}")
    L.append(f"Vol:       {res.vol * 100:.1f}%/day ({res.vol_notes.get('vol_source', '')})   Horizon: {res.days:.2f} trading days   Event days: +{res.ev_days:.1f}")
    L.append(f"Events:    {ev_text}")
    L.append(f"News:      {res.news_shift * 100:+.1f}% total  ({len(res.news)} headlines)")
    for s in res.news:
        L.append(f"  {s['shift'] * 100:+.1f}%  {s['headline'][:70]}"
                 + (f"   (top range {s['move_pts']:+.1f} pts)" if 'move_pts' in s else ""))
    L.append("-----------------------------------------------------")
    L.append("Range               Model  Yes ask  No ask  Maker   Gap  Side  EV/ct    Flag")
    for r in res.rows:
        gap = f"{r.gap:+5.1f}" if r.gap is not None else "    -"
        ev = f"${r.ev:+.2f}" if r.ev is not None else "    -"
        mk = _c(r.quote.maker_price) if r.quote else "   -"
        L.append(f"{r.c.label[:18]:<18}  {r.p_yes * 100:4.0f}%   {_c(r.c.yes_ask)}    {_c(r.c.no_ask)}   {mk}  "
                 f"{gap}  {r.side:<4}  {ev:<7}  {r.flag}")
    L.append("-----------------------------------------------------")
    flagged = [r for r in res.rows if r.flag == "WORTH A LOOK"]
    best = max(flagged or [r for r in res.rows if r.gap is not None], key=lambda r: r.gap, default=None)
    if best:
        p_side = best.p_yes if best.side == "YES" else 1 - best.p_yes
        how = "maker bid+1c" if res.pricing == "maker" else "ask"
        L.append(f"Top call:  {best.side} \"{best.c.label}\" at {round(best.price * 100)}c ({how}) - model {p_side * 100:.0f}% "
                 f"- gap {best.gap:+.0f} pts - EV after fees ${best.ev:+.2f}/contract")
        q = best.quote
        if q and q.ask is not None and q.maker_price is not None:
            L.append(f"Maker:     rest a buy at {round(q.maker_price * 100)}c instead of paying {round(q.ask * 100)}c - "
                     f"EV ${q.maker_ev:+.2f}/contract (maker fee ${q.maker_fee:.2f}; fills only if someone sells to you)")
        if flagged:
            verdict = "WORTH A LOOK"
        elif best.gap >= WORTH_A_LOOK_PTS:
            verdict = f"NO EDGE IN THE BAND (best gap is on a side priced {best.flag.lower()})"
        else:
            verdict = f"NO EDGE (best gap < {WORTH_A_LOOK_PTS} pts)"
        L.append("Verdict:   " + verdict)
    else:
        L.append("Top call:  none (no buyable side)")
        L.append(f"Verdict:   NO EDGE (best gap < {WORTH_A_LOOK_PTS} pts)")
    if any(r.flag == "CONSISTENCY" for r in res.rows):
        L.append("Note:      CONSISTENCY = the weekly range and the daily ladder (same settlement) disagree by 10+ pts.")
    L.append("Filter:    " + (res.band.describe() if res.band else "no price band")
             + ("; priced as a maker at bid+1c (fill assumed)" if res.pricing == "maker" else "; priced at the ask (taker)"))
    L.append("Paper only. Argus never trades.")
    L.append("=====================================================")
    return "\n".join(L)


def save_alert(text, res):
    ALERTS.mkdir(exist_ok=True)
    stamp = now_utc().astimezone(ET).strftime("%Y-%m-%d_%H%M")
    path = ALERTS / f"{stamp}_{res.event_ticker}.md"
    path.write_text(f"# Argus alert — {res.event_ticker}\n\n```\n{text}\n```\n", encoding="utf-8")
    return path
