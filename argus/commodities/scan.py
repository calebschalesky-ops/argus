"""SCAN: map every non-15-minute Commodities series on Kalshi and print today's board.

For each series: what it asks, settlement source and time, shape (ladder / range / one-off),
frequency, liquidity (24h volume, open interest, spread and depth at the top near the
money), fee type, and how many settled events of history exist.

Tradability (plain rule, so it can be argued with): liquidity = the larger of the 24-hour volume
and the lifetime volume of the last settled event (daily series have nothing open between
sessions, so the 24-hour figure alone would hide them). A series drops to tier 2 when its
typical near-the-money spread is over 5c, its top-of-book depth is under 25 contracts, or it
has fewer than 20 settled events of history (too little to backtest). Tier 3: under 1,000
contracts on both measures. Spread and depth come from the nearest open event, or from the
last settled event's final quotes when nothing is open.
"""
import csv
import re
import statistics
from datetime import datetime, timezone

from . import DATA, http
from ..model import ET, parse_time

FIELDS = ["series", "title", "group", "frequency", "shape", "source", "settle_time", "question",
          "open_events", "open_markets", "vol_24h", "last_event_volume", "last_event", "open_interest", "spread_c", "depth_top",
          "settled_events", "first_settled", "fee_type", "tier", "why"]


def is_fifteen(s):
    return s.get("frequency") == "fifteen_min" or s.get("ticker", "").endswith("15M")


def commodity_series():
    """Every Commodities series except the 15-minute ones (argus-15m owns those)."""
    ss = http.get("/series", {"category": "Commodities"})["series"]
    return [s for s in ss if not is_fifteen(s)]


def group_of(s):
    t, title = s["ticker"], (s.get("title") or "").lower()
    tags = " ".join(s.get("tags") or []).lower()
    if "AAAGAS" in t:
        return "gasoline (AAA)"
    if any(k in t for k in ("ERCOT", "PJM", "MISO")):
        return "power"
    if any(k in t for k in ("WTI", "BRENT", "NATGAS", "HOIL")):
        return "energy"
    if any(k in t for k in ("GOLD", "SILVER", "COPPER", "PLATINUM", "PALLADIUM", "NICKEL", "COBALT",
                            "LITHIUM", "STEEL", "ALM", "APT")) or "metal" in tags:
        return "metals"
    if any(k in t for k in ("CORN", "WHEAT", "SOYBEAN", "SUGAR", "COFFEE", "COCOA", "CATTLE", "BEEF", "B65", "B85")):
        return "ags"
    if any(k in t for k in ("GPT", "GEMINI", "OPUS", "B200", "VERA")) or "token" in title:
        return "AI compute/tokens"
    return "other"


def shape_of(markets):
    kinds = {m.get("strike_type") for m in markets}
    if len(markets) <= 1:
        return "one-off"
    if kinds <= {"greater", "greater_or_equal", "less", "less_or_equal"}:
        return "ladder"
    if "between" in kinds:
        return "range"
    return "other:" + ",".join(sorted(k or "?" for k in kinds))


def question_of(m):
    """First sentence of the rules, trimmed: what the market actually asks."""
    q = (m.get("rules_primary") or m.get("title") or "").split(". ")[0]
    return re.sub(r"\s+", " ", q)[:220]


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def near_money_stats(markets):
    """(median spread in cents, median top-of-book depth) over strikes whose Yes mid is 15-85c."""
    spreads, depths = [], []
    for m in markets:
        bid, ask = f(m.get("yes_bid_dollars")), f(m.get("yes_ask_dollars"))
        if ask <= 0 or ask >= 1:
            continue
        mid = (bid + ask) / 2
        if 0.15 <= mid <= 0.85:
            spreads.append(round((ask - bid) * 100))
            depths.append(min(f(m.get("yes_bid_size_fp")), f(m.get("yes_ask_size_fp"))))
    if not spreads:
        return None, None
    return statistics.median(spreads), statistics.median(depths)


def history(series_ticker, frequency):
    pages = 25 if frequency == "hourly" else 10
    evs = http.paged("/events", "events", {"series_ticker": series_ticker, "status": "settled", "limit": 200},
                     max_pages=pages)
    dated = sorted((e.get("strike_date") or "", e["event_ticker"]) for e in evs if e.get("strike_date"))
    capped = len(evs) >= pages * 200
    return len(evs), (dated[0][0][:10] if dated else ""), capped, (dated[-1][1] if dated else None)


def liquidity(row):
    return max(row["vol_24h"], row.get("last_event_volume") or 0)


def tier(row):
    if liquidity(row) < 1000:
        return 3, "under 1,000 contracts (24h and last event)"
    why = []
    if row["spread_c"] is None or row["spread_c"] > 5:
        why.append("spread > 5c" if row["spread_c"] is not None else "no two-sided quote near the money")
    if row["depth_top"] is None or row["depth_top"] < 25:
        why.append("top depth < 25")
    if row["settled_events"] < 20:
        why.append("< 20 settled events")
    return (2 if why else 1), "; ".join(why) or "liquid, two-sided, history to test"


def scan_series(s):
    t = s["ticker"]
    evs = http.paged("/events", "events", {"series_ticker": t, "status": "open", "with_nested_markets": "true",
                                           "limit": 200}, max_pages=5)
    evs = [e for e in evs if e.get("markets")]
    markets = [m for e in evs for m in e["markets"]]
    # the nearest-to-close event carries the near-the-money quotes people actually trade
    nearest = min(evs, key=lambda e: e.get("strike_date") or "9999") if evs else None
    n_hist, first, capped, last_ev = history(t, s.get("frequency"))
    last_ms = (http.paged("/markets", "markets", {"event_ticker": last_ev, "limit": 1000}, max_pages=3)
               if last_ev else [])
    quote_ms = nearest["markets"] if nearest else last_ms
    spread, depth = near_money_stats(quote_ms) if quote_ms else (None, None)
    src = "; ".join(x.get("name", "") for x in s.get("settlement_sources") or [])
    settle_time = ""
    if nearest and nearest.get("strike_date"):
        settle_time = parse_time(nearest["strike_date"]).astimezone(ET).strftime("%a %b %d %H:%M ET")
    elif last_ms:
        settle_time = "last " + parse_time(last_ms[0]["close_time"]).astimezone(ET).strftime("%a %b %d %H:%M ET")
    row = {"series": t, "title": s.get("title", ""), "group": group_of(s), "frequency": s.get("frequency"),
           "shape": shape_of(quote_ms) if quote_ms else "",
           "source": src, "settle_time": settle_time,
           "question": question_of(quote_ms[0]) if quote_ms else "",
           "open_events": len(evs), "open_markets": len(markets),
           "vol_24h": round(sum(f(m.get("volume_24h_fp")) for m in markets)),
           "last_event_volume": round(sum(f(m.get("volume_fp")) for m in last_ms)), "last_event": last_ev or "",
           "open_interest": round(sum(f(m.get("open_interest_fp")) for m in markets)),
           "spread_c": spread, "depth_top": None if depth is None else round(depth),
           "settled_events": n_hist, "first_settled": first + ("+" if capped else ""),
           "fee_type": s.get("fee_type")}
    row["tier"], row["why"] = tier(row)
    return row


def rank(rows):
    return sorted(rows, key=lambda r: (r["tier"], -liquidity(r), -r["open_interest"]))


def scan(series=None, verbose=True):
    ss = commodity_series()
    if series:
        ss = [s for s in ss if s["ticker"] in series]
    rows = []
    for i, s in enumerate(sorted(ss, key=lambda s: s["ticker"])):
        rows.append(scan_series(s))
        if verbose and i % 10 == 9:
            print(f"  scanned {i + 1}/{len(ss)} series", flush=True)
    return rank(rows)


def save(rows, when=None):
    when = when or datetime.now(timezone.utc)
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / f"series_map_{when.astimezone(ET).date().isoformat()}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in FIELDS})
    return path


def format_board(rows, top=None):
    g = lambda x: "  -" if x is None else f"{x:>3.0f}"
    L = ["ARGUS COMMODITIES BOARD (read-only; paper only)", "",
         "Tier Series           Group           Freq     Shape   Vol 24h LastEvt      OI  Sprd Depth  Hist  Next settle          | Source"]
    for r in rows[:top] if top else rows:
        L.append(f"{r['tier']:>4} {r['series'][:16]:<16} {r['group'][:15]:<15} {(r['frequency'] or '')[:8]:<8} "
                 f"{r['shape'][:7]:<7} {r['vol_24h']:>7,} {r['last_event_volume']:>7,} {r['open_interest']:>7,} "
                 f"{g(r['spread_c'])}c {g(r['depth_top'])}  {r['settled_events']:>4}  {r['settle_time'][:20]:<20} | {r['source'][:36]}")
    L.append("")
    L.append("Tier 1 = near-money spread <= 5c, top depth >= 25, 20+ settled events; ranked by max(24h volume, last event volume).")
    L.append("Sprd/Depth = median over strikes with Yes mid 15-85c in the nearest event. Hist = settled events (all tiers).")
    return "\n".join(L)
