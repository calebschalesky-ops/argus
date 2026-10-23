"""Late favourites, forward paper test: log today's calls, print a hand list, settle later. Paper only.

The backtest (strategies.py, engines latefav-30m / latefav-60m) found a small edge on exchange-priced
ladders (Pyth 1-minute closes and the ICE WTI settle): favourites near the settle won a little more
often than their price. It only counts once it holds on calls logged BEFORE their results exist.

Live rules (fixed 2026-10-22, before the first live call):
  - series: WTI (daily, weekly; ICE settle 2:30 PM ET) and Brent, gold, silver, copper, natgas
    (daily, weekly, monthly; Pyth 1-minute close 5:00 PM ET). No AAA gas, hourly or 15-minute markets.
  - horizon: run 60 and 30 minutes before a settle. Each run takes markets closing 45-65 min away
    (the 60-minute call) or 15-35 min away (the 30-minute call), to allow for a scheduler that starts
    a few minutes late.
  - favourite side priced 87-95c at the ask, with a real bid on that side no more than 5c below it.
  - one paper contract per call, taker fee; the contracts resting at that ask are recorded.

  python -m argus.commodities latefav-live [--at 60|30]   (no --at: chosen from the clock)
  python -m argus.commodities latefav-settle              fill in results from Kalshi

Rows go to commodities_latefav_live_log.csv (engines latefav-live-60m / latefav-live-30m) for
`argus board`; each run's hand list is appended to alerts/YYYY-MM-DD_latefav.md.
"""
from datetime import datetime, timedelta, timezone

from . import http
from .strategies import call_id, fee1, iso
from .. import log
from ..model import ET

LIVE_LOG = log.ROOT / "commodities_latefav_live_log.csv"
ALERTS = log.ROOT / "alerts"
SERIES = ["KXWTI", "KXWTIW", "KXBRENTD", "KXBRENTW", "KXBRENTMON", "KXGOLDD", "KXGOLDW", "KXGOLDMON",
          "KXSILVERD", "KXSILVERW", "KXSILVERMON", "KXCOPPERD", "KXCOPPERW", "KXCOPPERMON",
          "KXNATGASD", "KXNATGASW", "KXNATGASMON"]
WINDOWS = {60: (45, 65), 30: (15, 35)}    # minutes before close a market must be, per horizon
PRICE_LO, PRICE_HI = 0.87, 0.95
MAX_SPREAD = 0.05
SETTLES_ET = ((14, 30), (17, 0))           # WTI; everything else


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def auto_horizon(now):
    """60 or 30, from the minutes left to the next 2:30 PM or 5:00 PM ET settle."""
    local = now.astimezone(ET)
    left = [(local.replace(hour=h, minute=m, second=0, microsecond=0) - local).total_seconds() / 60
            for h, m in SETTLES_ET]
    left = [x for x in left if x > 0]
    if not left:
        return None
    return 60 if min(left) > 45 else 30


def candidates(markets, now, horizon):
    """(market, side, ask, side_bid, minutes_left, size_at_ask) for favourites that meet the rules."""
    lo, hi = WINDOWS[horizon]
    out = []
    for m in markets:
        close = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
        mins = (close - now).total_seconds() / 60
        if not (lo <= mins <= hi):
            continue
        ybid, yask = num(m.get("yes_bid_dollars")), num(m.get("yes_ask_dollars"))
        ybid = ybid if ybid and 0 < ybid < 1 else None
        yask = yask if yask and 0 < yask < 1 else None
        sides = (("YES", yask, ybid, num(m.get("yes_ask_size_fp"))),
                 # buying No at 1 - (Yes bid): the contracts on offer are the Yes bids
                 ("NO", round(1 - ybid, 4) if ybid else None, round(1 - yask, 4) if yask else None,
                  num(m.get("yes_bid_size_fp"))))
        for side, ask, bid, size in sides:
            if ask is None or bid is None:
                continue                                  # no real bid on that side: skip
            if PRICE_LO - 1e-9 <= ask <= PRICE_HI + 1e-9 and ask - bid <= MAX_SPREAD + 1e-9:
                out.append((m, side, ask, bid, int(round(mins)), size))
    return out


def live_rows(cands, now, horizon):
    engine = f"latefav-live-{horizon}m"
    rows = []
    for m, side, ask, bid, mins, size in cands:
        rows.append({"call_id": call_id(engine, m["ticker"], side), "logged_at_utc": iso(now), "engine": engine,
                     "series": m["event_ticker"].split("-")[0], "event_ticker": m["event_ticker"],
                     "market_ticker": m["ticker"], "range_label": m.get("yes_sub_title") or m["ticker"],
                     "side": side, "model_prob": f"{ask:.4f}", "market_price": f"{ask:.4f}", "gap_pts": "0.0",
                     "worth_a_look": "1", "paper_contracts": "1", "fee_per_contract": f"{fee1(ask):.2f}",
                     "close_time_utc": m["close_time"], "status": "pending",
                     "notes": f"{mins} min before close; side_bid={bid:.2f}; "
                              f"size_at_ask={'?' if size is None else f'{size:.0f}'}"})
    return rows


def hand_list(rows, now, horizon):
    """Plain list Caleb can act on by hand (paper first; real money only per research/commodities.md)."""
    t = now.astimezone(ET).strftime("%Y-%m-%d %H:%M ET")
    L = [f"## Late favourites, {horizon}-minute run, {t}", ""]
    if not rows:
        L += ["Nothing met the rules (87-95c, bid within 5c, closing in the window).", ""]
        return "\n".join(L)
    L += ["| Market | Side | Price | Contracts at that price | Settles | Max |", "|---|---|---|---|---|---|"]
    for r in rows:
        close = datetime.fromisoformat(r["close_time_utc"].replace("Z", "+00:00")).astimezone(ET).strftime("%H:%M ET")
        size = r["notes"].split("size_at_ask=")[1]
        L.append(f"| {r['market_ticker']} ({r['range_label']}) | {r['side']} | {float(r['market_price']) * 100:.0f}c "
                 f"| {size} | {close} | 1 contract max |")
    L += ["", "Paper calls are logged automatically. Real money only after the forward test passes "
              "(research/commodities.md section 5). Argus never trades.", ""]
    return "\n".join(L)


def save_list(text, now):
    ALERTS.mkdir(exist_ok=True)
    path = ALERTS / f"{now.astimezone(ET).date().isoformat()}_latefav.md"
    new = not path.exists()
    with open(path, "a", encoding="utf-8") as f:
        if new:
            f.write(f"# Late favourites, {now.astimezone(ET).date().isoformat()} (paper; 1 contract max per line)\n\n")
        f.write(text + "\n")
    return path


def run(now=None, horizon=None, path=LIVE_LOG):
    now = now or datetime.now(timezone.utc)
    horizon = horizon or auto_horizon(now)
    if horizon is None:
        return None, [], None
    lo, hi = WINDOWS[horizon]
    markets = []
    for s in SERIES:
        markets += http.paged("/markets", "markets", {
            "series_ticker": s, "status": "open", "limit": 1000,
            "min_close_ts": int((now + timedelta(minutes=lo)).timestamp()) - 60,
            "max_close_ts": int((now + timedelta(minutes=hi)).timestamp()) + 60})
    seen = {r["call_id"] for r in log.read_log(path)}
    rows = [r for r in live_rows(candidates(markets, now, horizon), now, horizon) if r["call_id"] not in seen]
    log.append(rows, path)
    alert = save_list(hand_list(rows, now, horizon), now)
    return horizon, rows, alert


def settle(path=LIVE_LOG):
    rows = log.read_log(path)
    n = 0
    for r in rows:
        if r["status"] != "pending":
            continue
        m = (http.get(f"/markets/{r['market_ticker']}") or {}).get("market") or {}
        if m.get("result") in ("yes", "no"):
            log.settle_row(r, None, m["result"] == "yes")
            r["settle_value"] = m.get("expiration_value") or ""
            n += 1
    log.write_log(rows, path)
    return n


def main(cmd, at=None):
    if cmd in ("live", "latefav-live"):
        horizon, rows, alert = run(horizon=at)
        if horizon is None:
            print("No settle left today (2:30 PM / 5:00 PM ET). Nothing to do.")
            return
        print(hand_list(rows, datetime.now(timezone.utc), horizon))
        print(f"Logged {len(rows)} paper calls to {LIVE_LOG.name}; list saved to {alert}")
    else:
        print(f"Settled {settle()} calls in {LIVE_LOG.name}")
