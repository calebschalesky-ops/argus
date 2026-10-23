"""Reference pricing: price Kalshi markets off a deeper professional market's implied distribution.

Plain words: our own forecasts lost to Kalshi everywhere. This package stops forecasting and
borrows the price from a bigger market that prices the same thing:
  - crypto: Deribit's BTC/ETH option chain says how likely every price level is at a given time;
    Kalshi's daily BTC/ETH ladders (KXBTCD, KXETHD) and ranges (KXBTC, KXETH) ask the same question.
  - Fed: an official market-implied rate distribution vs Kalshi's Fed decision markets.
  - indices: only if free, official options data with acceptable terms exists.

Read-only, public data, no key. Every network request is written to data/refprice/api_calls.csv.
It never trades.

    python -m argus.refprice crypto-backtest    replay past 5 PM ET crypto events vs Deribit's archive
    python -m argus.refprice crypto-live        log paper calls on open 5 PM ET crypto events
    python -m argus.refprice crypto-settle      fill in results of live calls
    python -m argus.refprice report             per-strategy scorecard from the refprice logs
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "refprice"                  # api call log (committed)
CACHE = ROOT / "data" / "cache" / "refprice"       # git-ignored downloads
CALL_LOG = DATA / "api_calls.csv"

CRYPTO_BT_LOG = ROOT / "refprice_crypto_backtest_log.csv"
CRYPTO_LIVE_LOG = ROOT / "refprice_crypto_live_log.csv"
FED_BT_LOG = ROOT / "refprice_fed_backtest_log.csv"

# Kalshi series -> Deribit currency. All four settle on the 60-second average of CF Benchmarks'
# real-time index (BRTI / ETH RTI) before the close (rules text, read 2026-10-22).
CRYPTO_SERIES = {"KXBTCD": "BTC", "KXBTC": "BTC", "KXETHD": "ETH", "KXETH": "ETH"}
