"""Commodities deep dive: map every Kalshi commodity series and test structural strategies.

Run with `python -m argus.commodities <command>` (see __main__.py). Read-only, public data,
no key, every network call logged to data/commodities/api_calls.csv. It never trades.
15-minute series are left to argus/fifteen/ (argus-15m) and skipped here.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "commodities"
CACHE = ROOT / "data" / "cache" / "commodities"
