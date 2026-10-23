"""Shared pieces: the copy-with-delay entry price, fees, and the smart-money log.

smartmoney_log.csv has exactly the same columns as prediction_log.csv, so the same
settle/scorecard logic (argus/log.py) reads it.
"""
from datetime import datetime, timezone

from .. import fees, log
from . import LOG_PATH

DEFAULT_DELAY_MIN = 10


def now_utc():
    return datetime.now(timezone.utc)


def iso(ts):
    """Epoch seconds -> 2026-10-22T19:30:03Z."""
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def entry_after_delay(delayed_price):
    """What following a call costs us: the price available `delay` minutes after the
    source traded, plus Kalshi's taker fee as a conservative stand-in for any venue's
    fee. Returns (market_price, fee_per_contract, all_in_cost) for one contract.

    Prices are clamped to 1..99 cents: nobody can buy below 1c or above 99c.
    """
    p = min(max(round(float(delayed_price), 4), 0.01), 0.99)
    fee = fees.taker_fee(p)
    return p, fee, round(p + fee, 4)


def delay_cost_pts(source_price, delayed_price):
    """How many points worse (positive) our entry is than the source's own fill."""
    return round((float(delayed_price) - float(source_price)) * 100, 1)


def read_log(path=None):
    return log.read_log(path or LOG_PATH)


def append(rows, path=None):
    log.append(rows, path or LOG_PATH)


def write_log(rows, path=None):
    log.write_log(rows, path or LOG_PATH)


def note_fields(notes):
    """Parse 'k=v; k=v' notes into a dict."""
    out = {}
    for part in (notes or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def make_notes(**kw):
    return "; ".join(f"{k}={v}" for k, v in kw.items())
