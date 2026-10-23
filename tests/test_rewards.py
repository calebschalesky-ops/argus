"""Reward scoring tests: the formula must match the worked rules in the Oct 14, 2026 CFTC filing."""
import pytest

from argus.rewards import score


def test_reference_is_first_level_reaching_a_fifth_of_target():
    # 100 at 50c, 150 at 49c: cumulative reaches 200 (1/5 of 1000) at 49c
    ref, scored = score.side_scores([(0.50, 100), (0.49, 150), (0.40, 900)], target=1000)
    assert ref == 0.49
    # 50c is above the reference (N=0), 49c at it, 40c is 9 ticks below: 900 * 0.5**9
    assert [round(s, 4) for _, _, s in scored] == [100, 150, round(900 * 0.5 ** 9, 4)]


def test_side_short_of_target_does_not_qualify():
    ref, scored = score.side_scores([(0.50, 300), (0.40, 300)], target=1000)
    assert ref is None and scored == []


def test_one_contract_at_reference_gets_pro_rata_share():
    share, ref, ok = score.my_share([(0.54, 929), (0.21, 216)], 0.54, 1, target=1000)
    assert ok and ref == 0.54
    assert share == pytest.approx(1 / 930, rel=1e-3)


def test_big_order_one_tick_better_becomes_reference_and_discounts_others():
    # 200 contracts at 55c sets the reference; the 929 at 54c are one tick below and score half
    share, ref, ok = score.my_share([(0.54, 929), (0.21, 216)], 0.55, 200, target=1000)
    assert ref == 0.55
    assert share == pytest.approx(200 / (200 + 929 * 0.5))   # whole levels are added, so all 929 count


def test_reward_per_day_splits_over_two_sides():
    assert score.reward_per_day(14.0, 0.01, 0.0) == pytest.approx(0.07)
