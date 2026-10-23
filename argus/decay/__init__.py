"""Deadline decay: "Will X happen by <date>?" markets, priced by a constant-hazard clock.

Plain words: if nothing has happened yet, a "by Friday" Yes should lose value every day,
because there is less time left for it to happen. If the crowd is slow to mark that down,
No is cheap late in the window. This package collects settled by-date markets, replays
their prices measured from the SCHEDULED deadline (never the actual close, which leaks
the answer when X happens early), and tests two No rules after Kalshi's fees.

Run with `python -m argus.decay <command>` (see __main__.py). Read-only, public data,
no key, every network call logged to data/cache/decay/api_calls.csv. It never trades.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE = ROOT / "data" / "cache" / "decay"
LOG_PATH = ROOT / "decay_log.csv"
