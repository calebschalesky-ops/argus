"""Mentions engine tests: word matching by Kalshi's rules, transcript speaker split, base rates
that use only earlier results, and replays with no look-ahead and an honest maker fill rule."""
from datetime import datetime, timedelta, timezone

import pytest

from argus.mentions import backtest as BT
from argus.mentions import baserate as B
from argus.mentions import history as H
from argus.mentions import http, transcripts as T, words as W


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------- word matching ----------------

@pytest.mark.parametrize("strike,text,said", [
    ("Tariff", "We put tariffs on steel.", True),              # plural
    ("Tariff", "The tariff's effect.", True),                  # possessive
    ("Tariff", "Tariffed goods.", False),                       # other inflection: no
    ("Tariff", "tariffication", False),                         # inside a longer word: no
    ("Say", "He said it.", False),                              # tense: no
    ("Economy", "Economies of scale", True),                    # y -> ies
    ("Tax", "Taxation is theft", False),
    ("Tax", "TAXES are high", True),                            # case ignored, -es plural
    ("AI / Artificial Intelligence", "artificial intelligence is here", True),   # slash alternative
    ("AI / Artificial Intelligence", "I said nothing", False),  # 'ai' inside 'said' is not a word
    ("AI / Artificial Intelligence", "AI-driven growth", True), # hyphen splits words
    ("AI / Artificial Intelligence", "the A.I. boom", True),
    ("Slowdown / Slow Down", "things may slow down", True),
    ("Slowdown / Slow Down", "slowing down", False),
    ("Good Afternoon", "good afternoons", True),                # plural of the last word only
    ("Good Afternoon", "goods afternoon", False),
    ("Warsh", "Chairman Warsh’s view", True),              # curly apostrophe possessive
    ("Bank", "the banks' balance sheets", True),                # plural possessive
    ("Event does not qualify", "event does not qualify", False),
])
def test_word_rules(strike, text, said):
    assert W.said(strike, text) is said


def test_count_and_key():
    assert W.count("Tariff", "tariff, tariffs and a tariff's cost") == 3
    assert W.key("Slowdown / Slow Down") == W.key("slow down/SLOWDOWN")
    assert W.key("Tariffs") == W.key("Tariff")
    assert W.key("Event does not qualify") == ""


def test_count_strikes():
    assert W.threshold("Tariff 5+ times") == ("Tariff", 5)
    assert W.threshold("Cut 3+") == ("Cut", 3)
    assert W.said("Tariff 3+ times", "tariff tariffs tariff") and not W.said("Tariff 4+ times", "tariff tariffs tariff")
    assert W.key("Tariff 5+ times") != W.key("Tariff")


# ---------------- transcripts ----------------

FAKE = """September 16, 2026 Chairman Warsh's Press Conference FINAL
Page 1 of 15
CHAIRMAN WARSH.  Good day. Inflation remains elevated.
MICHELLE SMITH.  Nick Timiraos.
NICK TIMIRAOS.  Will tariffs and recession risk change your view?
CHAIRMAN WARSH.  The economy is strong.
"""


def test_only_the_chair_counts():
    chair = T.chair_text(FAKE)
    assert W.said("Inflation", chair) and W.said("Economy", chair)
    assert not W.said("Tariff", chair) and not W.said("Recession", chair)   # only the reporter said them
    assert not W.said("FINAL", chair)                                        # page header dropped


def test_transcript_rate_uses_only_earlier_events():
    tr = [{"held_utc": utc(2026, m, 15), "text": "tariff" if m == 9 else "inflation"} for m in range(1, 10)]
    p, k, n = B.transcript_rate("Tariff", utc(2026, 9, 15), tr)    # the Sep transcript is not before t
    assert (k, n) == (0, 8)
    p2, k2, n2 = B.transcript_rate("Tariff", utc(2026, 9, 16), tr)
    assert (k2, n2) == (1, 8) and p2 > p


# ---------------- Kalshi-history base rate ----------------

def wm(ticker, event, word, result, settled, opened=None, close=None, occ=None):
    close = close or settled
    return {"ticker": ticker, "event_ticker": event, "custom_strike": {"Word": word}, "yes_sub_title": word,
            "result": result, "settlement_ts": iso(settled), "close_time": iso(close),
            "open_time": iso(opened or close - timedelta(days=1)), "occurrence_datetime": iso(occ) if occ else None,
            "_tier": "live"}


def test_kalshi_rate_no_lookahead():
    ms = [wm(f"S-{i}-T", f"S-{i}", "Tariffs" if i % 2 else "Tariff", "yes" if i < 3 else "no", utc(2026, 9, 1 + i))
          for i in range(6)]
    h = B.KalshiHistory(ms)
    p, k, n = h.rate("Tariff", utc(2026, 9, 4, 12))          # results of Sep 1..4 are public
    assert (k, n) == (3, 4)
    p_late, k_late, n_late = h.rate("Tariff", utc(2026, 9, 30))
    assert (k_late, n_late) == (3, 6) and p_late < p        # later Nos only count once public


def test_kalshi_rate_exact_boundary_and_own_event():
    ms = [wm(f"S-{i}-T", f"S-{i}", "Tariff", "yes", utc(2026, 9, 1 + i)) for i in range(5)]
    h = B.KalshiHistory(ms)
    t = utc(2026, 9, 4)                                       # S-3 settles exactly at t: not public yet
    assert h.rate("Tariff", t)[2] == 3
    assert h.rate("Tariff", utc(2026, 9, 9), exclude_event="S-4")[2] == 4


def test_min_past():
    ms = [wm(f"S-{i}-T", f"S-{i}", "Tariff", "yes", utc(2026, 9, 1 + i)) for i in range(2)]
    assert B.KalshiHistory(ms).rate("Tariff", utc(2026, 10, 1))[0] is None


# ---------------- replay ----------------

def candle(end, bid=None, ask=None, lo=None, hi=None, vol=0):
    c = {"end_period_ts": int(end.timestamp()), "volume_fp": str(vol), "price": {}}
    if bid is not None:
        c["yes_bid"] = {"close_dollars": str(bid)}
    if ask is not None:
        c["yes_ask"] = {"close_dollars": str(ask)}
    if lo is not None:
        c["price"] = {"low_dollars": str(lo), "high_dollars": str(hi if hi is not None else lo)}
    return c


def test_quote_never_uses_a_candle_ending_after_t():
    t = utc(2026, 9, 16, 12)
    cs = [candle(t - timedelta(hours=1), 0.10, 0.12), candle(t + timedelta(minutes=1), 0.90, 0.95)]
    assert H.quote_at(cs, t, 3 * 3600) == (0.10, 0.12)
    assert H.quote_at(cs[1:], t, 3 * 3600) == (None, None)


def test_anchor_is_first_close_or_occurrence():
    occ = utc(2026, 9, 16, 18, 30)
    ms = [wm("E-A", "E", "Tariff", "yes", occ + timedelta(hours=2), close=occ + timedelta(minutes=10), occ=occ),
          wm("E-B", "E", "Oil", "no", occ + timedelta(hours=2), close=occ + timedelta(hours=1), occ=occ)]
    assert BT.anchor(ms) == occ
    for m in ms:
        m["occurrence_datetime"] = None
    assert BT.anchor(ms) == occ + timedelta(minutes=10)


def two_hours():
    return {"2h": BT.HORIZONS["2h"]}


def test_replay_no_lookahead_and_fill_rule():
    a = utc(2026, 9, 16, 18, 30)                          # anchor
    t = a - BT.HORIZONS["2h"]
    m = wm("E-X", "E", "Recession", "no", a + timedelta(hours=1), opened=a - timedelta(days=2),
           close=a + timedelta(hours=1), occ=a)
    past = [wm(f"P{i}-X", f"P{i}", "Recession", "no", a - timedelta(days=30 - i)) for i in range(5)]
    hist = B.KalshiHistory(past + [m])                    # the target's own result is in the history

    def candles_of(mk, end):
        return [candle(t - timedelta(minutes=30), 0.07, 0.10),
                candle(t + timedelta(minutes=30), 0.50, 0.52),            # after t: must not be the quote
                candle(t + timedelta(hours=2), lo=0.10, hi=0.10, vol=5),  # Yes traded 10c: No at 91c filled
                ]
    rows = BT.replay_event("KXTEST", [m], hist, None, candles_of, horizons=two_hours())
    by = {r["engine"]: r for r in rows}
    base = by["mentions-baserate"]
    assert "base=kalshi 0/5" in base["notes"]             # own event excluded, results before t only
    assert base["notes"].endswith("bid=0.07; ask=0.10")   # quote from the candle before t
    fade = by["mentions-fade"]
    assert fade["market_price"] == "0.9100" and fade["status"] == "settled" and fade["paper_contracts"] == "1"
    assert float(fade["fee_per_contract"]) == 0.0 and fade["won"] == "1"
    assert by["mentions-fade-taker"]["market_price"] == "0.9300"


def test_fade_unfilled_is_void_and_event_trades_never_fill():
    a = utc(2026, 9, 16, 18, 30)
    t = a - BT.HORIZONS["2h"]
    m = wm("E-X", "E", "Recession", "yes", a + timedelta(hours=1), opened=a - timedelta(days=2),
           close=a + timedelta(hours=1), occ=a)
    cs = [candle(t - timedelta(minutes=30), 0.07, 0.10),
          candle(t + timedelta(hours=1), lo=0.09, hi=0.09, vol=5),    # 9c is not above 1 - 0.91
          candle(a + timedelta(hours=1), lo=0.99, hi=0.99, vol=50)]   # during the event: never fills
    rows = BT.replay_event("KXTEST", [m], B.KalshiHistory([]), None, lambda mk, end: cs, horizons=two_hours())
    fade = [r for r in rows if r["engine"] == "mentions-fade"][0]
    assert fade["status"] == "void" and fade["paper_contracts"] == "0"


def test_empty_book_is_skipped():
    a = utc(2026, 9, 16, 18, 30)
    t = a - BT.HORIZONS["2h"]
    m = wm("E-X", "E", "Recession", "no", a, opened=a - timedelta(days=2), occ=a)
    cs = [candle(t - timedelta(minutes=30), 0.01, 0.12)]      # bid 11c under the ask: not a real book
    assert BT.replay_event("KXTEST", [m], B.KalshiHistory([]), None, lambda mk, end: cs,
                           horizons=two_hours()) == []


def test_market_not_open_yet_is_skipped():
    a = utc(2026, 9, 16, 18, 30)
    m = wm("E-X", "E", "Recession", "no", a, opened=a - timedelta(hours=1), occ=a)   # opened after t
    cs = [candle(a - timedelta(hours=3), 0.07, 0.10)]
    assert BT.replay_event("KXTEST", [m], B.KalshiHistory([]), None, lambda mk, end: cs,
                           horizons=two_hours()) == []


def test_baserate_call_picks_side_after_fee():
    side, price, fee, ps, bet = BT.baserate_call(0.80, 0.55, 0.57)
    assert side == "YES" and price == 0.57 and bet
    side, price, fee, ps, bet = BT.baserate_call(0.10, 0.40, 0.42)
    assert side == "NO" and price == 0.60 and bet
    assert not BT.baserate_call(0.50, 0.48, 0.50)[-1]


# ---------------- read-only ----------------

@pytest.mark.parametrize("url", [
    "https://api.elections.kalshi.com/trade-api/v2/portfolio/orders",
    "https://api.elections.kalshi.com/trade-api/v2/markets/X/orders",
    "https://www.whitehouse.gov/briefings-statements/",
    "http://www.federalreserve.gov/mediacenter/files/x.pdf",
])
def test_refuses_non_allowlisted(url):
    with pytest.raises(PermissionError):
        http.check_allowed(url)


def test_allows_public_reads():
    http.check_allowed("https://api.elections.kalshi.com/trade-api/v2/markets")
    http.check_allowed("https://www.federalreserve.gov/mediacenter/files/FOMCpresconf20260916.pdf")
