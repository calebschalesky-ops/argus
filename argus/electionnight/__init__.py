"""Election night: Kalshi race markets that keep trading while official counts come in.

Run with `python -m argus.electionnight <command>` (see __main__.py). Read-only, public data,
no key, no account. Every network call is logged to data/electionnight/api_calls.csv. It never trades.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "electionnight"
CACHE = ROOT / "data" / "cache" / "electionnight"
LOG_PATH = ROOT / "electionnight_log.csv"
