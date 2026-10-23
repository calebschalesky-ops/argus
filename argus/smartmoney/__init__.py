"""Argus Stage 4: smart-money sources (SPEC section 11).

Every followed wallet or big-trade flag is a SCORED SOURCE, never an automatic copy.
Nothing in this package can trade, connect a wallet, or send a key.

    python -m argus.smartmoney freeze      save today's qualifying top Polymarket wallets
    python -m argus.smartmoney track       log those wallets' trades made AFTER the freeze
    python -m argus.smartmoney bigtrades   log Kalshi block trades and outsized fills
    python -m argus.smartmoney settle      fill in results for settled calls
    python -m argus.smartmoney score       scorecard per wallet and for big trades
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SMART_DIR = ROOT / "smartmoney"
LOG_PATH = ROOT / "smartmoney_log.csv"
