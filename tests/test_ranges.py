"""Tests for range matching and the daily-ladder maths (markets.py)."""
import pytest

from argus.markets import (Contract, contains, find_range, ladder_implied, ladder_median,
                           ladder_points, ladder_prob_above)


def C(kind, low=None, high=None, yes_bid=None, yes_ask=None, ticker="T"):
    return Contract(ticker, "E", "x", kind, low, high, yes_bid, yes_ask, None, None, "")


WEEKLY = ([C("less", high=87.0, ticker="LOW")]
          + [C("between", float(k), k + 0.99, ticker=f"B{k}") for k in range(87, 112)]
          + [C("greater", low=111.99, ticker="HIGH")])


def test_between_edges_are_inclusive():
    r = C("between", 98.0, 98.99)
    assert contains(r, 98.00)
    assert contains(r, 98.99)
    assert not contains(r, 97.99)
    assert not contains(r, 99.00)


def test_value_is_rounded_to_the_cent():
    r = C("between", 98.0, 98.99)
    assert contains(r, 98.994)        # rounds to 98.99
    assert not contains(r, 98.996)    # rounds to 99.00


def test_tails():
    assert contains(C("less", high=87.0), 86.99)
    assert not contains(C("less", high=87.0), 87.00)
    assert contains(C("greater", low=111.99), 112.00)
    assert not contains(C("greater", low=111.99), 111.99)


def test_every_settle_value_lands_in_exactly_one_weekly_range():
    for cents in range(8000, 12000, 7):
        v = cents / 100
        hits = find_range(WEEKLY, v)
        assert len(hits) == 1, v


def test_find_range_names_the_right_one():
    assert find_range(WEEKLY, 98.73)[0].ticker == "B98"
    assert find_range(WEEKLY, 50.0)[0].ticker == "LOW"


LADDER = [C("greater", low=k, yes_bid=p - 0.005, yes_ask=p + 0.005)
          for k, p in [(97.99, 0.735), (98.49, 0.655), (98.99, 0.565), (99.49, 0.465), (99.99, 0.375)]]


def test_ladder_median_is_the_50pct_crossing():
    pts = ladder_points(LADDER)
    m = ladder_median(pts)
    assert 99.0 < m < 99.5
    assert ladder_prob_above(pts, m - 0.005) == pytest.approx(0.5, abs=1e-9)


def test_ladder_points_flatten_bumps():
    bumpy = LADDER + [C("greater", low=100.49, yes_bid=0.40, yes_ask=0.42)]   # stale quote above a lower one
    pts = ladder_points(bumpy)
    assert all(a[1] >= b[1] for a, b in zip(pts, pts[1:]))


def test_ladder_implied_range():
    pts = ladder_points(LADDER)
    # $99.00-$99.99 = P(above 98.99) - P(above 99.99) = 0.565 - 0.375
    assert ladder_implied(C("between", 99.0, 99.99), pts) == pytest.approx(0.19, abs=1e-9)
