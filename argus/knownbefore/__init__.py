"""Known-before-close: Kalshi markets whose answer is partly OBSERVABLE before the market closes.

Run with `python -m argus.knownbefore <command>` (see __main__.py). Read-only, public data,
no key, every network call logged to data/knownbefore/api_calls.csv. It never trades.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "knownbefore"
CACHE = ROOT / "data" / "cache" / "knownbefore"
LOG_PATH = ROOT / "knownbefore_log.csv"
