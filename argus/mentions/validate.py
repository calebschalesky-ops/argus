"""Check the word matcher against Kalshi's own rulings: every settled Fed Chair mention market
whose press-conference transcript is posted, transcript says-it vs Kalshi's result."""
from collections import defaultdict

from . import baserate as B, history as H, transcripts as T, words as W

MONTHS = {m: f"{i:02d}" for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def event_month(event_ticker):
    code = event_ticker.split("-")[1]
    return "20" + code[:2] + MONTHS[code[2:5]]


def fed_check(markets, transcripts):
    by_month = {t["date"][:6]: t for t in transcripts}
    per, misses = defaultdict(lambda: [0, 0]), []
    for m in markets:
        s = B.strike_of(m)
        if m.get("result") not in ("yes", "no") or not W.alternatives(s):
            continue
        t = by_month.get(event_month(m["event_ticker"]))
        if not t:
            continue
        said, yes = W.said(s, t["text"]), m["result"] == "yes"
        per[t["date"]][0] += said == yes
        per[t["date"]][1] += 1
        if said != yes:
            misses.append((t["date"], s, m["result"], W.count(s, t["text"])))
    return dict(per), misses


def main():
    allm = H.load_all()
    ms = allm.get("KXFEDMENTION") or H.series_markets("KXFEDMENTION")
    per, misses = fed_check(ms, T.fed_transcripts(2024))
    ok, n = sum(v[0] for v in per.values()), sum(v[1] for v in per.values())
    print(f"Fed transcripts vs Kalshi results: {ok}/{n} agree ({ok / n:.1%}) over {len(per)} press conferences")
    for d in sorted(per):
        print(f"  {d}: {per[d][0]}/{per[d][1]}")
    for x in misses:
        print(f"  MISS {x[0]} '{x[1]}': Kalshi {x[2]}, transcript count {x[3]}")
