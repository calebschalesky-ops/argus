"""Scanner: every open Kalshi market by category (read-only), skipping 15-minute markets.

It also runs one model-free check on events where exactly one outcome wins
("mutually exclusive"): if buying YES on every outcome costs less than $1 after
fees, or the YES bids add up to more than $1, prices are inconsistent.
"""
from collections import defaultdict

from . import fees, kalshi
from .markets import Contract


def fifteen_minute_series():
    return {s["ticker"] for s in kalshi.all_series() if kalshi.is_fifteen_minute(s)}


def scan(category=None, max_pages=150):
    skip = fifteen_minute_series()
    events = kalshi.get_open_events(max_pages=max_pages)
    by_cat = defaultdict(list)
    checks = []
    for e in events:
        if e.get("series_ticker") in skip or e.get("series_ticker", "").endswith("15M"):
            continue
        if category and category.lower() not in (e.get("category") or "").lower():
            continue
        cs = [Contract.from_api(m) for m in e.get("markets", []) if m.get("status") in (None, "active", "open")]
        if not cs:
            continue
        vol = sum(c.volume_24h for c in cs)
        spreads = [c.yes_ask - c.yes_bid for c in cs if c.yes_ask and c.yes_bid is not None and c.yes_ask > 0]
        by_cat[e.get("category") or "Other"].append({
            "event": e["event_ticker"], "title": e.get("title", ""), "markets": len(cs),
            "volume_24h": vol, "avg_spread": sum(spreads) / len(spreads) if spreads else None,
            "close": min(c.close_time for c in cs),
        })
        if e.get("mutually_exclusive") and len(cs) >= 2:
            asks = [c.yes_ask for c in cs]
            if all(a and 0 < a < 1 for a in asks):
                cost = sum(asks) + sum(fees.taker_fee(a) for a in asks)
                if cost < 1.0:
                    checks.append((e["event_ticker"], e.get("title", ""),
                                   f"Buying YES on all {len(cs)} listed outcomes costs ${cost:.2f} after fees (< $1); "
                                   f"money locked until {min(c.close_time for c in cs)[:10]}"))
            bids = [c.yes_bid or 0 for c in cs]
            if sum(bids) > 1.0 + 0.02 * len(cs):
                checks.append((e["event_ticker"], e.get("title", ""),
                               f"YES bids add up to ${sum(bids):.2f} (> $1): the outcomes look overpriced together"))
    return by_cat, checks, len(events)


def format_scan(by_cat, checks, n_events, top=8):
    L = [f"ARGUS SCAN - {n_events} open events read, 15-minute markets skipped", ""]
    for cat in sorted(by_cat, key=lambda k: -sum(x["volume_24h"] for x in by_cat[k])):
        rows = sorted(by_cat[cat], key=lambda x: -x["volume_24h"])
        L.append(f"## {cat} - {len(rows)} events, {sum(x['volume_24h'] for x in rows):,.0f} contracts traded in 24h")
        for x in rows[:top]:
            sp = f"{x['avg_spread'] * 100:.0f}c" if x["avg_spread"] is not None else "-"
            L.append(f"  {x['event'][:34]:<34} vol {x['volume_24h']:>11,.0f}  spread {sp:>4}  "
                     f"closes {x['close'][:10]}  {x['title'][:60]}")
        L.append("")
    L.append("## Price-consistency checks (mutually exclusive events)")
    L += [f"  {t}  {title[:50]} - {msg}" for t, title, msg in checks] or ["  none found"]
    L.append("\nThese are leads to read by hand, not trades. A sum below $1 is only a true arbitrage if the\n"
             "listed outcomes cover every possibility (read the rules for an 'other' outcome).\n"
             "Thin markets often show stale quotes, and money is tied up until the close date.")
    return "\n".join(L)
