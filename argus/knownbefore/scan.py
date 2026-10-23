"""Step 1: which Kalshi series settle on a number that is partly public before the market closes.

Reads every series and every open event (with markets) from Kalshi's public API, sorts the series
into families by title/ticker, and measures each family's live liquidity: 24h volume, open interest,
the share of markets with a real two-sided quote (bid within 5c), median spread, and top-of-book size.
When the settlement value becomes visible relative to the close is a property of the source, not of
the API: it is written per family in FAMILIES below (read from the rules text of open markets on
2026-10-22) and repeated in research/known_before_close.md.
"""
import csv
import json
import re
import statistics
from collections import defaultdict
from datetime import date, datetime, timezone

from . import DATA, http

# family -> (title/ticker regex, settlement source, when the value is visible vs the close)
FAMILIES = {
    "temp-daily": (r"^KX(HIGH|LOW)T?[A-Z]{2,5}$", "NWS Daily Climate Report (CLI), via The Weather Company",
                   "Station reports every hour all day; the high is usually set by 4 PM, the market closes 00:59 local daylight time."),
    "rain-daily/monthly": (r"^KXRAIN", "NWS CLI precipitation",
                           "Rain gauge totals reported hourly; a wet day is known once it rains."),
    "snow-monthly": (r"SNOW", "NWS CLI snowfall", "Running monthly total published daily in the CLI."),
    "tsa": (r"TSA", "TSA checkpoint travel numbers (tsa.gov)",
            "TSA posts each day's count the next morning; weekly markets see 6 of 7 days before close."),
    "gas-aaa": (r"AAA|GAS", "AAA national/state average (gasprices.aaa.com)",
                "AAA publishes once a day; the price moves about 1c a day, so the last print is most of the answer."),
    "approval": (r"APPROV|APRPOTUS", "Approval polling average (RCP / Silver Bulletin / etc., per market)",
                 "Average updates through the day as polls come in; the value is near its close days ahead."),
    "rotten-tomatoes": (r"(?i)rotten|tomatomet|^KXRT", "Rotten Tomatoes Tomatometer",
                        "Reviews post before release; the score is visible and moves less as the count grows."),
    "box-office": (r"(?i)box office|opening weekend|^KXBO", "Box Office Mojo / The Numbers",
                   "Thursday previews and Friday estimates published before the weekend total."),
    "music-charts": (r"(?i)spotify|billboard|hot 100|top song|top album|^KXSPOT|^KXBILL", "Spotify Charts / Billboard",
                     "Spotify daily charts are public daily; Billboard is announced Sunday/Monday for the week."),
    "netflix-top10": (r"(?i)netflix", "Netflix Top 10 (tudum)", "Daily top-10 lists are public before the weekly list."),
    "app-store": (r"(?i)app store|top free app|#1 app", "Apple App Store charts", "Ranks are visible live."),
    "post-counts": (r"(?i)tweet|truth social|posts", "The account's public posts", "Counts accumulate in public over the window."),
    "mentions": (r"(?i)mention|say\b|said\b", "The broadcast / transcript", "Visible live while the speech or event runs."),
    "jobless-claims": (r"(?i)jobless|initial claims", "DOL weekly claims", "Released Thursday 8:30 ET; markets close at release."),
}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def family_of(series):
    t, title = series.get("ticker", ""), series.get("title", "")
    for fam, (pat, _, _) in FAMILIES.items():
        if fam == "temp-daily":
            if re.search(pat, t):
                return fam
            continue
        if fam == "mentions" and series.get("category") == "Mentions":
            return fam
        if re.search(pat, t) or re.search(pat, title, re.I):
            return fam
    return None


def market_stats(markets):
    """Liquidity of a set of open markets: volumes, quotes, top-of-book sizes."""
    vol = sum(_f(m.get("volume_24h_fp")) or 0 for m in markets)
    oi = sum(_f(m.get("open_interest_fp")) or 0 for m in markets)
    spreads, ask_sz, bid_sz = [], [], []
    for m in markets:
        b, a = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
        if b and a and 0 < b < a < 1:
            spreads.append(a - b)
            if a - b <= 0.05 + 1e-9:
                ask_sz.append(_f(m.get("yes_ask_size_fp")) or 0)
                bid_sz.append(_f(m.get("yes_bid_size_fp")) or 0)
    return {"markets": len(markets), "vol_24h": round(vol), "open_interest": round(oi),
            "quoted": len(spreads), "tight": len(ask_sz),
            "median_spread_c": round(statistics.median(spreads) * 100, 1) if spreads else None,
            "median_ask_size": round(statistics.median(ask_sz)) if ask_sz else None,
            "median_bid_size": round(statistics.median(bid_sz)) if bid_sz else None}


def run(cache_day=None):
    day = cache_day or date.today().isoformat()
    f_series, f_events = DATA / f"all_series_{day}.json", DATA / f"open_events_{day}.json"
    DATA.mkdir(parents=True, exist_ok=True)
    if f_series.exists():
        series = json.loads(f_series.read_text(encoding="utf-8"))
    else:
        series = (http.kalshi("/series", {}) or {}).get("series", [])
        f_series.write_text(json.dumps(series), encoding="utf-8")
    if f_events.exists():
        events = json.loads(f_events.read_text(encoding="utf-8"))
    else:
        events = http.kalshi_paged("/events", "events", {"status": "open", "with_nested_markets": "true", "limit": 200},
                                   max_pages=400)
        f_events.write_text(json.dumps(events), encoding="utf-8")
    by_series = {s["ticker"]: s for s in series if s.get("frequency") != "fifteen_min"}
    fam_series = defaultdict(list)
    for s in by_series.values():
        fam = family_of(s)
        if fam:
            fam_series[fam].append(s["ticker"])
    open_by_series = defaultdict(list)
    for e in events:
        for m in e.get("markets", []):
            open_by_series[e.get("series_ticker") or m["event_ticker"].split("-")[0]].append(m)
    rows = []
    for fam, tickers in fam_series.items():
        for t in tickers:
            ms = open_by_series.get(t, [])
            if not ms:
                continue
            st = market_stats(ms)
            s = by_series[t]
            closes = sorted(m["close_time"] for m in ms)
            rows.append({"family": fam, "series": t, "title": s.get("title", ""), "frequency": s.get("frequency", ""),
                         "source": "; ".join(x.get("name", "") for x in s.get("settlement_sources") or []),
                         "next_close": closes[0], **st})
    out = DATA / f"scan_{day}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["family"])
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["family"], -r["vol_24h"])))
    return rows


def report(rows):
    fam = defaultdict(list)
    for r in rows:
        fam[r["family"]].append(r)
    L = [f"{'family':<20}{'series':>7}{'open mkts':>10}{'24h vol':>10}{'OI':>11}{'tight':>7}{'med spread':>11}"
         f"{'med ask sz':>11}  top series (24h vol)"]
    for k, rs in sorted(fam.items(), key=lambda kv: -sum(r["vol_24h"] for r in kv[1])):
        rs.sort(key=lambda r: -r["vol_24h"])
        sp = [r["median_spread_c"] for r in rs if r["median_spread_c"] is not None]
        sz = [r["median_ask_size"] for r in rs if r["median_ask_size"] is not None]
        L.append(f"{k:<20}{len(rs):>7}{sum(r['markets'] for r in rs):>10}{sum(r['vol_24h'] for r in rs):>10}"
                 f"{sum(r['open_interest'] for r in rs):>11}{sum(r['tight'] for r in rs):>7}"
                 f"{(statistics.median(sp) if sp else float('nan')):>10.1f}c{(statistics.median(sz) if sz else 0):>11.0f}  "
                 + ", ".join(f"{r['series']}({r['vol_24h']})" for r in rs[:4]))
    return "\n".join(L)
