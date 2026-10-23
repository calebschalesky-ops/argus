"""Rule quirks: Kalshi markets whose RULES differ from what a casual reader assumes.

Run with `python -m argus.rulequirks <command>` (see __main__.py). Read-only, public data,
no key, every network call logged to data/rulequirks/api_calls.csv. It never trades.
15-minute series are left to argus/fifteen/ and skipped here.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "rulequirks"
CACHE = ROOT / "data" / "cache" / "rulequirks"
# Other threads' caches of answers that can never change (settled-market listings, candles).
# Same cache key scheme (sha1 of path + params), read only, never written.
SIBLING_CACHES = [ROOT / "data" / "cache" / "favorites", ROOT / "data" / "cache" / "commodities"]
LOG_PATH = ROOT / "rulequirks_log.csv"
