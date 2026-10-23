"""Example idea file for `python -m argus.lab bench argus/lab/ideas/example_longshot_no.py`.

Buy NO on weather longshots (YES ask 3-10c) six hours before the scheduled close, as taker.
Copy this file, change `idea`, and run the bench: it blocks the known traps for you.
"""
from argus.lab.bench import Call, Hypothesis, quote, max_age

HOURS = 6


def idea(market, history, t):
    bid, ask = quote(history, t, max_age(HOURS))   # the book at t (None if empty or stale)
    if bid is None or not (0.03 <= ask <= 0.10):
        return None
    return Call(side="no")                           # limit=None: take the No ask (= 1 - Yes bid)


HYPOTHESIS = Hypothesis("example: weather longshot NO, 6h", HOURS, idea,
                        where=lambda m: m["category"] == "Climate and Weather")
SQL = "s.category = 'Climate and Weather'"         # optional: load only these markets from the lake
