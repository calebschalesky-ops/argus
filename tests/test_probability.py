"""Tests for the probability maths (model.py, markets.model_prob)."""
import math
from datetime import datetime, timezone

import pytest

from argus.markets import Contract, model_prob
from argus.model import PriceCurve, ewma_vol, log_returns, simple_vol, trading_days


def C(kind, low=None, high=None):
    return Contract("T", "E", "x", kind, low, high, None, None, None, None, "")


def test_curve_is_centred_on_anchor():
    curve = PriceCurve(anchor=100, vol_daily=0.02, days=1)
    assert curve.cdf(100) == pytest.approx(0.5, abs=1e-9)


def test_curve_is_monotonic_and_bounded():
    curve = PriceCurve(100, 0.02, 1)
    xs = [80, 90, 95, 99, 100, 101, 105, 110, 120]
    ps = [curve.cdf(x) for x in xs]
    assert ps == sorted(ps)
    assert 0 <= ps[0] and ps[-1] <= 1


def test_all_ranges_add_up_to_one():
    """Below-tail + every $1 range + above-tail covers every price exactly once."""
    curve = PriceCurve(99.3, 0.024, 0.96, event_days=0.1)
    contracts = [C("less", high=87.0)]
    contracts += [C("between", low=float(k), high=k + 0.99) for k in range(87, 112)]
    contracts += [C("greater", low=111.99)]
    total = sum(model_prob(c, curve.cdf) for c in contracts)
    assert total == pytest.approx(1.0, abs=1e-6)


def test_more_time_or_events_widens_the_curve():
    narrow = PriceCurve(100, 0.02, 1)
    wide_time = PriceCurve(100, 0.02, 4)
    wide_event = PriceCurve(100, 0.02, 1, event_days=1.5)
    middle = C("between", 99.5, 100.49)
    assert model_prob(middle, wide_time.cdf) < model_prob(middle, narrow.cdf)
    assert model_prob(middle, wide_event.cdf) < model_prob(middle, narrow.cdf)


def test_news_shift_moves_the_centre_up():
    up = PriceCurve(100, 0.02, 1, shift=0.015)
    assert up.centre == pytest.approx(101.511, abs=1e-3)
    assert 1 - up.cdf(100) > 0.5


def test_student_t_spread_matches_vol():
    """Scaled t should have the same standard deviation as the vol we asked for."""
    curve = PriceCurve(100, 0.03, 4)
    sd = curve.scale * math.sqrt(curve.df / (curve.df - 2))
    assert sd == pytest.approx(0.06, rel=1e-9)


def test_greater_strike_is_strictly_above():
    curve = PriceCurve(100, 0.02, 1)
    # "Above $99.99" pays only from 100.00 up, so it is P(price >= 99.995)
    assert model_prob(C("greater", low=99.99), curve.cdf) == pytest.approx(1 - curve.cdf(99.995))


def test_volatility_helpers():
    prices = [100, 101, 99.5, 100.5, 102, 101]
    rets = log_returns(prices)
    assert len(rets) == 5
    assert simple_vol(rets) > 0
    assert ewma_vol(rets) > 0
    assert ewma_vol([0.01]) is None


def test_trading_days_skip_weekends():
    fri = datetime(2026, 10, 23, 16, 0, tzinfo=timezone.utc)   # Friday noon ET
    mon = datetime(2026, 10, 26, 16, 0, tzinfo=timezone.utc)   # Monday noon ET
    assert trading_days(fri, mon) == pytest.approx(1.0, abs=0.05)
    assert trading_days(mon, fri) == 0.0
