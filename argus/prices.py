"""Free price data (approved by Caleb 2026-10-22).

- Settlement history: settled Kalshi markets (the exact number each market paid on).
- Long volatility history: FRED daily spot prices (EIA data via the St. Louis Fed).
  FRED is SPOT, about $10 away from the futures contract Kalshi uses, so it is used
  for "how much does it move" only, never for the price level.
- Current price: the 50% point of the live Kalshi daily ladder (see markets.ladder_median).
"""
import csv
import io
import re

import requests

from . import kalshi
from .model import ewma_vol, log_returns, simple_vol

FRED_IDS = {"WTI": "DCOILWTICO", "BRENT": "DCOILBRENTEU"}
SETTLE_SERIES = {"WTI": "KXWTI", "BRENT": "KXBRENTD"}


def fred_series(commodity, last_n=120):
    """Last N daily FRED prices (skips the '.' FRED uses for holidays)."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={FRED_IDS[commodity]}"
    r = requests.get(url, timeout=30, headers={"User-Agent": "argus-research/0.1"})
    r.raise_for_status()
    rows = list(csv.reader(io.StringIO(r.text)))[1:]
    vals = [(d, float(v)) for d, v in rows if v not in (".", "")]
    return vals[-last_n:]


_CONTRACT = re.compile(r"\(([A-Za-z]+ \d{4}) contract\)")


def settlement_history(commodity):
    """[(close_time, value, contract)] from settled Kalshi markets, oldest first."""
    out = []
    for close, value, rules in kalshi.settled_values(SETTLE_SERIES[commodity]):
        m = _CONTRACT.search(rules)
        out.append((close, value, m.group(1) if m else ""))
    return out


def settle_returns(history):
    """Day-to-day log returns between settlements, skipping contract-roll days.

    When Kalshi switches from (say) the December to the January contract, the jump
    is a calendar spread, not a real move, so that pair is skipped.
    """
    rets = []
    for (t1, v1, c1), (t2, v2, c2) in zip(history, history[1:]):
        if c1 and c2 and c1 != c2:
            continue
        if v1 > 0 and v2 > 0:
            rets.append(log_returns([v1, v2])[0])
    return rets


MIN_SETTLE_RETURNS = 15


def estimate_vol(commodity, verbose=False):
    """Daily volatility of the contract Kalshi actually settles on.

    Main source: settle-to-settle returns from Kalshi's own settled markets; we take
    the larger of the plain stdev and the recent-weighted (EWMA) figure, so a calm
    stretch never makes the model overconfident.
    FRED spot is only the fallback when there are fewer than 15 settle returns:
    measured 2026-10-22, spot WTI swung 4.2%/day while the Dec-26 contract swung 2.4%,
    so spot overstates the move by a lot.
    """
    notes = {}
    fred_vol = settle_vol = None
    try:
        fred = fred_series(commodity, 61)
        fred_vol = ewma_vol(log_returns([v for _, v in fred]))
        notes["fred_last"] = fred[-1]
    except Exception as e:                     # network trouble: carry on with what we have
        notes["fred_error"] = str(e)
    try:
        hist = settlement_history(commodity)
        rets = settle_returns(hist)[-40:]
        pair = [v for v in (simple_vol(rets), ewma_vol(rets)) if v]
        settle_vol = max(pair) if len(rets) >= MIN_SETTLE_RETURNS and pair else None
        notes["settle_n"] = len(rets)
        notes["last_settle"] = hist[-1] if hist else None
    except Exception as e:
        notes["settle_error"] = str(e)
    if settle_vol:
        vol, notes["vol_source"] = settle_vol, "Kalshi settlements"
    elif fred_vol:
        vol, notes["vol_source"] = fred_vol, "FRED spot (fallback)"
    else:
        vol, notes["vol_source"] = 0.025, "fixed 2.5% (no data)"
    notes["fred_vol"], notes["settle_vol"] = fred_vol, settle_vol
    return vol, notes
