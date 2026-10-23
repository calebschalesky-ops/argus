"""Where a quirk DECIDED a settled market, and whether Kalshi's price beforehand knew it.

"Mattered" means the rules' answer differed from the casual reading's answer. That can only be
seen from Kalshi's own data for these quirk types:

  boundary_strict     'above X' / 'more than X' settled at exactly X -> No (casual reading: Yes)
  boundary_inclusive  'at least X' / 'X or more' settled at exactly X -> Yes (casual: a cut-off to beat)
  boundary_mismatch   title and rules disagree about X itself, and it settled at exactly X
  fallback            settled at a price between 0 and $1 (tie / cancellation / delay / fair-value clause)
  none_won            the 'Tie' / 'None' / 'Does not qualify' outcome won (casual: a named outcome wins)
  early_close         closed long before its scheduled expiry (the 'can close early' clause) -- counted only:
                      prices 'N hours before close' there look into the future (the look-ahead trap)

Station, contract-month, averaging, first-release and clock-time quirks need the outside data the
casual reader would have used (another airport, the revised number, spot vs futures), so they are
catalogued but not scored here. The weather and commodities threads measured those sources.

Price check: the Yes bid/ask 1 hour and 1 day before close, from candles that ended before that
moment, only with a real book (bid within 5c of the ask). "rule side" = the side the rules paid.
The rule-side mid at 1 hour is compared with a CONTROL: the next rung of the same event that
settled the same way but not on its boundary.
"""
import json
import random
import re
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

from . import DATA, corpus as C
from .classify import RX, same, threshold_mismatch, threshold_side

NONE_NAMES = re.compile(r"^(?:tie|draw|none|no one|nobody|no winner|does not qualify|event does not qualify|other|neither)\b", re.I)
CAP_PER_TYPE = 400          # price checks per type (fixed-seed sample) to keep calls polite
CAP_PER_SERIES = 40


def fifteen_series(series_meta):
    return {s for s, v in series_meta.items() if (v or {}).get("frequency") == "fifteen_min" or s.endswith("15M")}


def boundary_kind(m):
    """'boundary_strict' / 'boundary_inclusive' when the settle value sat exactly on the threshold, else None.

    A strict market at exactly X that still resolved Yes means Kalshi showed a rounded value
    (e.g. eggs at $2.73 for $2.7312); those are dropped, not counted. Strike 0 ('any rain') is
    not a quirk: nobody reads 'more than 0 inches' as including a dry day."""
    side, x = threshold_side(m)
    v = C.num(m.get("expiration_value"))
    if side is None or x is None or v is None:
        return None
    if not same(v, x) or abs(x) < 1e-12:
        return None
    if side == "strict" and m["result"] == "no":
        return "boundary_strict"
    if side == "inclusive" and m["result"] == "yes":
        return "boundary_inclusive"
    return None


def mattered_kinds(m):
    out = []
    b = boundary_kind(m)
    if b:
        out.append(b)
        if threshold_mismatch(m):
            out.append("boundary_mismatch")
    if m["result"] == "scalar":
        out.append("fallback")
    elif m["result"] == "yes" and NONE_NAMES.search((m.get("yes_sub_title") or "").strip()):
        out.append("none_won")
    if C.early_closed(m):
        out.append("early_close")
    return out


def clause_of(m):
    """Which written clause a fallback settlement came from (first match wins)."""
    t = " ".join(str(m.get(k) or "") for k in ("rules_primary", "rules_secondary"))
    for k in ("tie_rule", "cancellation_rule", "postponement_rule", "no_data_rule", "fair_value_fallback"):
        if RX[k].search(t):
            return k
    return "unstated"


def find(markets, series_meta):
    skip = fifteen_series(series_meta)
    hits = defaultdict(list)
    for m in markets:
        if C.series_of(m) in skip:
            continue
        for k in mattered_kinds(m):
            hits[k].append(m)
    return hits


def control_for(m, by_event):
    """Next rung of the same event, same side and same result, whose value was NOT on its boundary."""
    side, x = threshold_side(m)
    best = None
    for o in by_event.get(m["event_ticker"], []):
        if o["ticker"] == m["ticker"] or o["result"] != m["result"]:
            continue
        s2, x2 = threshold_side(o)
        if s2 != side or x2 is None or o.get("strike_type") != m.get("strike_type") or boundary_kind(o):
            continue
        d = abs(x2 - x)
        if best is None or d < best[0]:
            best = (d, o)
    return best[1] if best else None


def rule_side_mid(m, bid, ask):
    mid = (bid + ask) / 2
    if m["result"] == "scalar":
        return mid
    return mid if m["result"] == "yes" else 1 - mid


def sample(ms, n, seed=5):
    per = defaultdict(list)
    for m in sorted(ms, key=lambda m: m["ticker"]):
        per[C.series_of(m)].append(m)
    rng, out = random.Random(seed), []
    for s in sorted(per):
        xs = per[s]
        rng.shuffle(xs)
        out.extend(xs[:CAP_PER_SERIES])
    rng.shuffle(out)
    return sorted(out[:n], key=lambda m: m["ticker"])


def price_rows(kind, ms, by_event, workers=4):
    def one(m):
        cs = C.candles(m)
        row = {"kind": kind, "ticker": m["ticker"], "series": C.series_of(m), "result": m["result"],
               "value": m.get("expiration_value"), "settled_at": m.get("settlement_value_dollars"),
               "title": (m.get("title") or "")[:90], "tier": m["_tier"]}
        if kind == "fallback":
            row["clause"] = clause_of(m)
        for h in ("1h", "1d"):
            bid, ask = C.price_before_close(m, cs, h)
            row[f"bid_{h}"], row[f"ask_{h}"] = bid, ask
            row[f"rule_mid_{h}"] = None if bid is None else round(rule_side_mid(m, bid, ask), 4)
        if kind.startswith("boundary"):
            c = control_for(m, by_event)
            if c is not None:
                bid, ask = C.price_before_close(c, C.candles(c), "1h")
                row["control"] = c["ticker"]
                row["control_mid_1h"] = None if bid is None else round(rule_side_mid(c, bid, ask), 4)
        return row
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(one, ms))


def verdict_of(rows, kind):
    """Plain summary of one type's price check."""
    xs = [r["rule_mid_1h"] for r in rows if r.get("rule_mid_1h") is not None]
    out = {"checked": len(rows), "priced_1h": len(xs)}
    if not xs:
        return out
    if kind == "fallback":
        diffs = [abs(r["rule_mid_1h"] - C.num(r["settled_at"])) for r in rows
                 if r.get("rule_mid_1h") is not None and C.num(r["settled_at"]) is not None]
        out["median_gap_to_settle_1h"] = round(statistics.median(diffs), 3) if diffs else None
        out["within_10c"] = sum(d <= 0.10 for d in diffs)
        return out
    out["rule_side_median_1h"] = round(statistics.median(xs), 3)
    out["rule_side_favoured_1h"] = sum(x > 0.5 for x in xs)
    out["rule_side_under_20c_1h"] = sum(x < 0.2 for x in xs)
    ys = [r["rule_mid_1d"] for r in rows if r.get("rule_mid_1d") is not None]
    if ys:
        out["priced_1d"] = len(ys)
        out["rule_side_median_1d"] = round(statistics.median(ys), 3)
        out["rule_side_favoured_1d"] = sum(y > 0.5 for y in ys)
    pairs = [(r["rule_mid_1h"], r["control_mid_1h"]) for r in rows
             if r.get("rule_mid_1h") is not None and r.get("control_mid_1h") is not None]
    if pairs:
        out["paired_with_control"] = len(pairs)
        out["boundary_median_1h"] = round(statistics.median(p for p, _ in pairs), 3)
        out["control_median_1h"] = round(statistics.median(c for _, c in pairs), 3)
    return out


def run(markets, series_meta, kinds=("boundary_strict", "boundary_inclusive", "boundary_mismatch", "fallback", "none_won")):
    hits = find(markets, series_meta)
    by_event = defaultdict(list)
    for m in markets:
        by_event[m["event_ticker"]].append(m)
    summary = {"corpus_markets": len(markets),
               "counts": {k: {"markets": len(v), "series": len({C.series_of(m) for m in v}),
                              "top_series": Counter(C.series_of(m) for m in v).most_common(8)}
                          for k, v in hits.items()}}
    summary["fallback_clauses"] = Counter(clause_of(m) for m in hits.get("fallback", [])).most_common()
    all_rows = []
    for k in kinds:
        ms = sample(hits.get(k, []), CAP_PER_TYPE)
        print(f"  price check {k}: {len(ms)} markets", flush=True)
        rows = price_rows(k, ms, by_event)
        summary["counts"].setdefault(k, {})["price_check"] = verdict_of(rows, k)
        all_rows.extend(rows)
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "mattered_rows.json").write_text(json.dumps(all_rows, indent=0), encoding="utf-8")
    (DATA / "mattered_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary, all_rows
