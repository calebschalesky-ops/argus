"""The edge factory: a local data lake, a test bench, a rule miner and paper loggers.

Plain words: instead of every research session fetching its own data and writing its own
backtest, this package keeps ONE copy of Kalshi's settled history on disk (data/lake/),
ONE bench that every idea is scored on (with every known backtest trap blocked), and ONE
miner that tries thousands of simple rules with an honest train/holdout split. Rules that
survive become paper loggers that make forward calls, scored on the strategy board.

Run with `python -m argus.lab <command>`; see __main__.py and research/edge_factory.md.
Read-only, public data, no key, every network call logged to data/lake/api_calls.csv.
It never trades.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LAKE = ROOT / "data" / "lake"
DB_PATH = LAKE / "lake.db"
CALL_LOG = LAKE / "api_calls.csv"
SURVIVORS = ROOT / "lab_survivors.csv"
HOLDOUT_LOG = ROOT / "lab_holdout_log.csv"
LIVE_LOG = ROOT / "lab_live_log.csv"
PROMOTED = LAKE / "promoted.json"
