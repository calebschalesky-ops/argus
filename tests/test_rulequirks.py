"""Rule-quirks tests: the classifiers (threshold sides, title-vs-rules mismatch, clause types),
the 'did the quirk decide it' detectors, no look-ahead in prices, walk-forward model, read-only client."""
from datetime import datetime, timedelta, timezone

import pytest

from argus.rulequirks import corpus as C
from argus.rulequirks import http, mattered
from argus.rulequirks import strategies as S
from argus.rulequirks.classify import classify, kinds, same, threshold_mismatch, threshold_side


def mk(rules="", title="", sub="", st=None, floor=None, cap=None, secondary="", **kw):
    m = {"ticker": kw.pop("ticker", "KXT-26OCT22-T1"), "event_ticker": kw.pop("event", "KXT-26OCT22"),
         "rules_primary": rules, "rules_secondary": secondary, "title": title, "yes_sub_title": sub,
         "strike_type": st, "floor_strike": floor, "cap_strike": cap}
    m.update(kw)
    return m


# ---------------- thresholds ----------------

def test_strict_from_rules_and_field():
    m = mk("If CPI increases by more than 0.3% (single-decimal) in September, then Yes.",
           "Will CPI rise more than 0.3%?", "Above 0.3%", st="greater", floor=0.3)
    assert threshold_side(m) == ("strict", 0.3)
    assert {"strict_threshold", "rounding"} <= set(kinds(m))


def test_rules_beat_the_strike_field():
    # Kalshi stores "greater than 39.5" but the rules say "at least 40": same contract, inclusive at 40
    m = mk("If Brady Tkachuk scores at least 40 goals during the season, then Yes.",
           "Brady Tkachuk: 40+ goals?", "Brady Tkachuk", st="greater", floor=39.5)
    assert threshold_side(m) == ("inclusive", 40.0)
    assert "inclusive_threshold" in kinds(m)
    assert threshold_mismatch(m) is None


def test_half_point_count_line_is_not_a_quirk():
    m = mk("If Liberty wins by more than 33.5 points, then Yes.", "Liberty wins by over 33.5 points",
           st="greater", floor=33.5)
    assert "strict_threshold" not in kinds(m)


def test_no_more_than_is_inclusive_not_strict():
    m = mk("If out-of-pocket costs are no more than $40 per month, then Yes.", "Insulin cap at no more than $40?")
    assert threshold_side(m)[0] == "inclusive"
    assert threshold_mismatch(m) is None


# ---------------- title vs rules ----------------

def test_mismatch_title_inclusive_rules_strict():
    m = mk("If the close price for Gold on December 31, 2026 is above 5500 USD/t.oz, then Yes.",
           "Will the price of Gold be above 5500 on December 31, 2026?", "5,500 or above", st="greater", floor=5500)
    assert "title counts 5500 itself" in threshold_mismatch(m)
    assert "title_rules_mismatch" in kinds(m)


def test_mismatch_title_strict_rules_inclusive():
    m = mk("If the margin falls within 16% to 100%, inclusive of its lower bound and exclusive of its upper bound, then Yes.",
           "Will the margin of victory be above 16%?", "Raman", st="greater_or_equal", floor=16)
    assert "rules count 16 itself" in threshold_mismatch(m)


@pytest.mark.parametrize("rules,title,sub,st,floor", [
    # off-by-a-hair strikes are the same contract written two ways
    ("If the S&P 500 value is above 7894.9999, then Yes.", "Will the S&P 500 be above 7894.9999?", "7,895 or above",
     "greater_or_equal", 7895),
    ("If Opensea's FDV is above 14999999999.99 at 10:00 AM, then Yes.", "Opensea FDV above $14999999999.99?",
     "$15,000,000,000+", "greater", 14999999999.99),
    # "at or below" is not "below"
    ("If the price is at or below $25 in 2026, then Yes.", "Will the price be at or below $25/MTok?", "$25/MTok or below",
     "less_or_equal", None),
    # "category 3 or above" is a storm class, not the line
    ("If more than 3 hurricanes of category 3 or above occur, then Yes.",
     "Will there be more than 3 hurricanes of category 3 or above?", "Above 3", "greater", 3),
])
def test_no_false_mismatch(rules, title, sub, st, floor):
    assert threshold_mismatch(mk(rules, title, sub, st=st, floor=floor)) is None


def test_same_is_relative_to_size():
    assert not same(0.0000055, 0.000005499)
    assert not same(1e10, 9999999999.99)
    assert same(0.3, 0.1 + 0.2)


# ---------------- clause types ----------------

@pytest.mark.parametrize("text,kind", [
    ("This market is based on the outcome after 90 minutes plus stoppage time.", "regulation_only"),
    ("If the game is postponed but begins within 48 hours, the market remains open.", "postponement_rule"),
    ("If the match is declared a tie, the market will resolve to 50/50 for both competitors.", "tie_rule"),
    ("If the match is declared a tie, the market will resolve to 50/50 for both competitors.", "fair_value_fallback"),
    ("If the game is cancelled, all markets resolve to No.", "cancellation_rule"),
    ("If no data is published, the most recently available published data will be used.", "no_data_rule"),
    ("The exact phrase, or a plural or possessive form, must be used.", "counting_exclusions"),
    ("Corrections or revisions made after the market has settled will not affect the outcome.", "first_release"),
    ("The simple average of the sixty seconds of CF Benchmarks' BTC RTI before 5 PM.", "averaged_value"),
    ("The maximum temperature at Austin (CLIAUS) according to the NWS Climatological Report.", "named_station"),
    ("The daily settlement price for WTI crude oil (December 2026 contract).", "contract_month"),
    ("If views are above 8M at any point during the week, then Yes.", "path_dependent"),
    ('If the event is cancelled, the market for "Event does not qualify" resolves to Yes and all other markets resolve to No.',
     "none_outcome"),
])
def test_clause_classifiers(text, kind):
    assert kind in kinds(mk(text))


def test_plain_market_has_no_clause_quirks():
    assert kinds(mk("If the Chiefs win the game, then the market resolves to Yes.", "Chiefs win?")) == []


def test_classify_returns_the_phrase():
    q = [q for q in classify(mk("Only regulation time counts for this market.")) if q.kind == "regulation_only"][0]
    assert "regulation" in q.detail


# ---------------- did the quirk decide it ----------------

def test_boundary_strict_decided():
    m = mk("If CPI rises more than 0.6% (single-decimal), then Yes.", st="greater", floor=0.6,
           result="no", expiration_value="0.6")
    assert mattered.boundary_kind(m) == "boundary_strict"


def test_boundary_strict_resolved_yes_means_display_rounding():
    m = mk("If the eggs index is above $2.73, then Yes.", st="greater", floor=2.73, result="yes", expiration_value="2.73")
    assert mattered.boundary_kind(m) is None


def test_strike_zero_is_not_a_quirk():
    m = mk("If precipitation is strictly greater than 0 inches, then Yes.", st="greater", floor=0, result="no",
           expiration_value="0")
    assert mattered.boundary_kind(m) is None


def test_boundary_inclusive_decided():
    m = mk("If the biggest quake is at least 7.0, then Yes.", st="greater_or_equal", floor=7.0, result="yes",
           expiration_value="7.0")
    assert mattered.boundary_kind(m) == "boundary_inclusive"


def test_fallback_and_none_won():
    base = dict(close_time="2026-10-01T00:00:00Z", latest_expiration_time="2026-10-02T00:00:00Z")
    assert "fallback" in mattered.mattered_kinds(mk("tie resolves 50/50", result="scalar", **base))
    assert "none_won" in mattered.mattered_kinds(mk("", sub="Tie", result="yes", **base))
    assert "none_won" not in mattered.mattered_kinds(mk("", sub="Celtic", result="yes", **base))


# ---------------- prices: no look-ahead, real book ----------------

def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def candle(end, bid, ask):
    return {"end_period_ts": int(end.timestamp()), "yes_bid": {"close_dollars": str(bid)},
            "yes_ask": {"close_dollars": str(ask)}}


def test_book_at_ignores_future_candles_and_wide_books():
    t = utc(2026, 10, 1, 12)
    cs = [candle(t - timedelta(minutes=30), 0.40, 0.43), candle(t + timedelta(minutes=1), 0.90, 0.91)]
    assert C.book_at(cs, t, 3600) == (0.40, 0.43)
    assert C.book_at([candle(t, 0.10, 0.90)], t, 3600) == (None, None)       # empty-book quote
    assert C.book_at([candle(t - timedelta(hours=5), 0.4, 0.42)], t, 3600) == (None, None)   # too old


def mkt_for_strategy(result="no"):
    close = utc(2026, 10, 1, 20)
    return {"ticker": "KXHIGHNY-26OCT01-T70", "event_ticker": "KXHIGHNY-26OCT01", "result": result,
            "open_time": "2026-09-29T00:00:00Z", "close_time": "2026-10-01T20:00:00Z",
            "settlement_ts": "2026-10-02T14:00:00Z", "expiration_value": "70", "yes_sub_title": "71 or above"}, close


def test_strict_bet_buys_no_at_one_minus_bid():
    m, close = mkt_for_strategy("no")
    cs = {"hour": [candle(close - timedelta(hours=1, minutes=10), 0.55, 0.58)]}
    o = S.observe(m, "strict", "1h", cs)
    assert o["side"] == "NO" and o["price"] == pytest.approx(0.45) and o["won"]
    cs = {"hour": [candle(close - timedelta(hours=1, minutes=10), 0.95, 0.97)]}
    assert S.observe(m, "strict", "1h", cs) is None                          # outside the 15-85c band


def test_walk_forward_uses_only_bets_settled_before_the_call():
    obs = []
    for i in range(40):
        t = utc(2026, 9, 1) + timedelta(days=i)
        obs.append({"t": t.isoformat(), "settled": (t + timedelta(hours=1)).isoformat(), "won": True, "price": 0.5})
    late = {"t": utc(2026, 9, 1, 0, 30).isoformat(), "settled": utc(2026, 12, 1).isoformat(), "won": False, "price": 0.5}
    S.walk_forward(obs + [late])
    assert late["model_prob"] == 0.5                       # nothing had settled yet: model = price
    assert obs[-1]["model_prob"] == pytest.approx(40 / 41)  # 39 earlier wins settled, Laplace-smoothed


def test_client_is_read_only():
    for bad in ("/portfolio/orders", "/markets/../portfolio/balance", "/login"):
        with pytest.raises(PermissionError):
            http.check_allowed(bad)
    http.check_allowed("/markets")
