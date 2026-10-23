"""Kalshi trading fees (fee schedule checked 2026-10-22).

Taker fee = 0.07 x contracts x P x (1 - P), rounded UP to the next cent.
Maker fee = 0.0175 x contracts x P x (1 - P), rounded UP to the next cent, and ONLY on
series whose fee_type says so ("quadratic_with_maker_fees"). Every other series charges
makers nothing. Measured 2026-10-22: 160 of 15,354 series charge maker fees; none of the
oil or commodity series do (all "quadratic").
P is the price in dollars (0.19 for 19 cents). A series' fee_multiplier scales both fees.
"""
import math

TAKER_RATE = 0.07
MAKER_RATE = 0.0175


def _round_up(raw):
    # round up to the cent; the tiny subtraction stops 0.0300000001 becoming 0.04
    return math.ceil(raw * 100 - 1e-9) / 100


def taker_fee(price, contracts=1, multiplier=1):
    return _round_up(TAKER_RATE * multiplier * contracts * price * (1 - price))


def maker_fee(price, contracts=1, multiplier=1, charged=True):
    """Fee on a resting (limit) order that someone else fills. 0 where the series charges no maker fee."""
    if not charged:
        return 0.0
    return _round_up(MAKER_RATE * multiplier * contracts * price * (1 - price))


def series_maker_fees(series_meta):
    """(maker fee charged?, fee multiplier) from a Kalshi series object."""
    series_meta = series_meta or {}
    charged = "maker" in (series_meta.get("fee_type") or "")
    try:
        mult = float(series_meta.get("fee_multiplier") or 1)
    except (TypeError, ValueError):
        mult = 1.0
    return charged, mult


def expected_value(prob_win, price, contracts=1, fee=None):
    """Average profit of buying `contracts` at `price` when you win with prob_win.

    Win: you get $1 per contract. Either way you paid price + fee (taker fee unless `fee` is given).
    """
    fee = taker_fee(price, contracts) if fee is None else fee
    return contracts * (prob_win - price) - fee


def pnl(won, price, contracts=1, fee=None):
    """Actual profit of a settled paper bet, after fees (taker fee unless `fee` is given)."""
    gross = contracts * ((1 - price) if won else -price)
    fee = taker_fee(price, contracts) if fee is None else fee
    return round(gross - fee, 4)
