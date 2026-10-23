"""Map of the Mentions category: every series, how much it trades, how often it repeats,
how often its words come true, and what its live books look like.

Writes data/mentions/series_map.csv (one row per series) and prints the top of the table.
"""
import csv
import statistics
from collections import Counter

from . import DATA, history as H, words

MAP_FILE = DATA / "series_map.csv"


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def summarize(series_meta, markets):
    word_ms = [m for m in markets if words.alternatives((m.get("custom_strike") or {}).get("Word")
                                                        or m.get("yes_sub_title"))]
    st = H.settled(word_ms)
    opn = [m for m in word_ms if m["_tier"] == "open"]
    spreads = [_f(m["yes_ask_dollars"]) - _f(m["yes_bid_dollars"]) for m in opn
               if 0 < _f(m.get("yes_bid_dollars")) < _f(m.get("yes_ask_dollars")) < 1]
    keys = Counter(words.key((m.get("custom_strike") or {}).get("Word") or m.get("yes_sub_title")) for m in st)
    closes = sorted(m["close_time"] for m in st)
    return {"series": series_meta["ticker"], "title": series_meta.get("title", ""),
            "terms": (series_meta.get("contract_terms_url") or "").rsplit("/", 1)[-1],
            "tags": ";".join(series_meta.get("tags") or []), "frequency": series_meta.get("frequency"),
            "settled_events": len({m["event_ticker"] for m in st}), "settled_markets": len(st),
            "yes_rate": round(sum(m["result"] == "yes" for m in st) / len(st), 3) if st else "",
            "volume_contracts": round(sum(_f(m.get("volume_fp")) for m in st + opn)),
            "repeat_word_share": round(sum(c for c in keys.values() if c > 1) / len(st), 3) if st else "",
            "first_close": closes[0][:10] if closes else "", "last_close": closes[-1][:10] if closes else "",
            "open_events": len({m["event_ticker"] for m in opn}), "open_markets": len(opn),
            "open_median_spread_c": round(statistics.median(spreads) * 100, 1) if spreads else ""}


def run(refresh=False):
    allm = H.load_all(refresh=refresh)
    meta = {s["ticker"]: s for s in H.mention_series()}
    rows = [summarize(meta[s], ms) for s, ms in allm.items() if s in meta]
    rows.sort(key=lambda r: -r["volume_contracts"])
    DATA.mkdir(parents=True, exist_ok=True)
    with open(MAP_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} Mentions series; {sum(r['settled_events'] for r in rows)} settled events, "
          f"{sum(r['settled_markets'] for r in rows)} settled word markets -> {MAP_FILE}")
    print(f"{'series':28} {'events':>6} {'mkts':>6} {'yes%':>5} {'volume':>12} {'repeat':>6} {'open':>5} {'spread':>6}")
    for r in rows[:40]:
        print(f"{r['series'][:28]:28} {r['settled_events']:>6} {r['settled_markets']:>6} "
              f"{(r['yes_rate'] or 0) * 100:>5.0f} {r['volume_contracts']:>12,} {r['repeat_word_share'] or 0:>6} "
              f"{r['open_markets']:>5} {r['open_median_spread_c']!s:>6}")
    return rows
