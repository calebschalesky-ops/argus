"""SCAN: classify the rule quirks of every open, non-15-minute Kalshi market.

Reads the saved download of open markets (fetch_open.py; downloads it first if today's is
missing), runs classify() on each, and writes:
  data/rulequirks/scan_<date>.csv          one row per (series, quirk kind): markets, events, an example
  data/rulequirks/scan_<date>_summary.json counts per kind (markets, events, series), by category
and prints the catalogue. Counting is per market, per event and per series because one rule text
is copied onto every strike of a ladder: 400 weather strikes are one rule written once.
"""
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime

from . import DATA, fetch_open
from .classify import CATALOGUE, classify
from ..model import ET


def is_fifteen(series_meta, ticker):
    return (series_meta or {}).get("frequency") == "fifteen_min" or ticker.endswith("15M")


def load_open(day=None):
    p = fetch_open.path_for(day)
    if not p.exists():
        print("Downloading every open market (a few minutes)...")
        fetch_open.download(day)
    return json.loads(p.read_text(encoding="utf-8"))


def scan(day=None):
    data = load_open(day)
    smeta = data["series"]
    per = defaultdict(lambda: {"markets": 0, "events": set(), "example": None})
    by_kind = defaultdict(lambda: {"markets": 0, "events": set(), "series": set()})
    by_cat = defaultdict(Counter)
    n_markets, n_events, n_series, skipped = 0, set(), set(), 0
    for m in data["markets"]:
        s = m["event_ticker"].split("-", 1)[0]
        if is_fifteen(smeta.get(s), s):
            skipped += 1
            continue
        n_markets += 1
        n_events.add(m["event_ticker"])
        n_series.add(s)
        cat = (smeta.get(s) or {}).get("category") or "Other"
        for q in classify(m):
            k = by_kind[q.kind]
            k["markets"] += 1
            k["events"].add(m["event_ticker"])
            k["series"].add(s)
            by_cat[q.kind][cat] += 1
            row = per[(s, q.kind)]
            row["markets"] += 1
            row["events"].add(m["event_ticker"])
            if row["example"] is None:
                row["example"] = (m["ticker"], m.get("title") or "", q.detail, cat)
    summary = {"markets": n_markets, "events": len(n_events), "series": len(n_series), "skipped_15min": skipped,
               "fetched_utc": data.get("fetched_utc"),
               "kinds": {k: {"markets": v["markets"], "events": len(v["events"]), "series": len(v["series"]),
                             "by_category": dict(by_cat[k].most_common())}
                         for k, v in sorted(by_kind.items(), key=lambda kv: -len(kv[1]["series"]))}}
    rows = [{"series": s, "kind": k, "markets": v["markets"], "events": len(v["events"]), "category": v["example"][3],
             "example_ticker": v["example"][0], "example_title": v["example"][1][:120], "detail": v["example"][2][:200]}
            for (s, k), v in sorted(per.items())]
    return summary, rows


def save(summary, rows, day=None):
    day = day or datetime.now(ET).strftime("%Y-%m-%d")
    DATA.mkdir(parents=True, exist_ok=True)
    p = DATA / f"scan_{day}.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["series"])
        w.writeheader()
        w.writerows(rows)
    (DATA / f"scan_{day}_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return p


def format_summary(summary, rows, examples=2):
    L = [f"RULE QUIRKS in {summary['markets']:,} open markets ({summary['events']:,} events, {summary['series']:,} series; "
         f"{summary['skipped_15min']:,} 15-minute markets skipped)", "",
         f"{'Quirk':<36}{'Series':>7}{'Events':>8}{'Markets':>9}  Casual reading -> rules"]
    for k, v in summary["kinds"].items():
        label, naive, rule = CATALOGUE[k]
        L.append(f"{label:<36}{v['series']:>7}{v['events']:>8}{v['markets']:>9}  '{naive}' -> {rule}")
    L.append("")
    by_kind = defaultdict(list)
    for r in rows:
        by_kind[r["kind"]].append(r)
    for k in summary["kinds"]:
        top = sorted(by_kind[k], key=lambda r: -r["events"])[:examples]
        for r in top:
            L.append(f"  [{k}] {r['series']}: {r['example_title'][:70]}  ->  {r['detail'][:110]}")
    return "\n".join(L)
