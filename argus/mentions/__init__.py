"""Mentions engine: Kalshi "Will X say 'Y' during event Z?" markets.

Run with `python -m argus.mentions <command>` (see __main__.py). Read-only, public data,
no key, every network call logged to data/mentions/api_calls.csv. It never trades.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "mentions"
CACHE = ROOT / "data" / "cache" / "mentions"
LOG_PATH = ROOT / "mentions_log.csv"
