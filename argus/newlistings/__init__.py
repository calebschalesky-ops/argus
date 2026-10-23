"""New listings: are a Kalshi market's first prices (1h, 6h, 24h after it opens) worse than its later ones?

Plain words: a new market gets its first prices from a few early quoters. If those prices are
systematically off in some corner (a category, a price band, a big ladder), a rule known at the
time could take the other side. This package measures it on settled markets, backtests rules with
no look-ahead, and lists live paper calls on markets opened in the last few hours.

Run with `python -m argus.newlistings collect | report | live`. Read-only, public data, no key;
every network request is written to data/cache/newlistings/api_calls.csv. It never trades.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE = ROOT / "data" / "cache" / "newlistings"
CALL_LOG = CACHE / "api_calls.csv"
OBS_FILE = CACHE / "obs.json"
LOG_PATH = ROOT / "newlistings_log.csv"
RESEARCH = ROOT / "research" / "new_listings.md"
