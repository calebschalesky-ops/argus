"""Base rates: how often a listed word was actually said at past events, known at a given time.

Two sources, both strictly from events BEFORE the moment of the call:
  1. transcript  Official transcript (Fed press conferences): the share of the last N press
                 conferences, held before t, in which the Chair said the word by Kalshi's rules.
  2. kalshi      Kalshi's own settled results for the same word in the same series: the share of
                 earlier markets with that word that resolved Yes, counting only markets whose
                 result was published (settlement time) before t. Works for every series that
                 repeats words (Trump speeches, briefings, earnings calls, sports broadcasts).
Both are shrunk toward the series' overall Yes rate known at t (prior weight PRIOR_N), because
two or three past events say little on their own.
"""
from bisect import bisect_left
from collections import defaultdict

from . import words
from ..model import parse_time

PRIOR_N = 2.0          # pseudo-events of the series-wide Yes rate mixed into each word's rate
MIN_PAST = 3           # fewer past observations of a word than this: no base rate
FED_LAST_N = 8         # Fed transcripts used: the last 8 press conferences (one year)


def settled_at(m):
    """When a market's result became public (settlement time, else close time)."""
    return parse_time(m.get("settlement_ts") or m["close_time"])


def shrink(k, n, prior, prior_n=PRIOR_N):
    return (k + prior_n * prior) / (n + prior_n)


class KalshiHistory:
    """Past results of one series, indexed by word key and by the time each became public."""

    def __init__(self, markets):
        self.by_key = defaultdict(list)          # key -> sorted [(settled_at, yes, event)]
        self.all = []                            # sorted [(settled_at, yes)]
        for m in markets:
            if m.get("result") not in ("yes", "no"):
                continue
            k = words.key(strike_of(m))
            if not k:
                continue
            row = (settled_at(m), m["result"] == "yes", m["event_ticker"])
            self.by_key[k].append(row)
            self.all.append(row[:2])
        for v in self.by_key.values():
            v.sort(key=lambda r: r[0])
        self.all.sort(key=lambda r: r[0])
        self._all_t = [r[0] for r in self.all]
        self._cum = [0]
        for _, y in self.all:
            self._cum.append(self._cum[-1] + y)

    def series_rate(self, t):
        """(Yes share, n) of every word market of the series settled strictly before t."""
        i = bisect_left(self._all_t, t)
        return (self._cum[i] / i if i else None), i

    def rate(self, strike, t, exclude_event=None):
        """(base rate, k said, n past) for a word at time t; (None, k, n) below MIN_PAST.
        Only results public strictly before t count; the target's own event is always excluded."""
        past = [r for r in self.by_key.get(words.key(strike), []) if r[0] < t and r[2] != exclude_event]
        k, n = sum(r[1] for r in past), len(past)
        prior, _ = self.series_rate(t)
        if n < MIN_PAST or prior is None:
            return None, k, n
        return shrink(k, n, prior), k, n


def strike_of(m):
    cs = m.get("custom_strike") or {}
    return cs.get("Word") or m.get("yes_sub_title") or ""


def transcript_rate(strike, t, transcripts, last_n=FED_LAST_N):
    """(base rate, k, n) from official transcripts held strictly before t (the last `last_n`),
    smoothed as (k + 0.5) / (n + 1) so 0 of 8 gives 6%, not 0%."""
    past = sorted((x for x in transcripts if x["held_utc"] < t), key=lambda x: x["held_utc"])[-last_n:]
    if len(past) < MIN_PAST:
        return None, 0, len(past)
    k = sum(words.said(strike, x["tokens"] if "tokens" in x else x["text"]) for x in past)
    return (k + 0.5) / (len(past) + 1), k, len(past)
