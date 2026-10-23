"""Crypto backtest: every settled 5 PM ET BTC/ETH event, priced off Deribit at 1, 4 and 12 hours out.

For each event and horizon h, the moment priced is t = close - h hours (an hourly mark):
  - Kalshi: the hourly candle ending at t (bid/ask close), only markets already listed and with a
    real book (bid within 5c of the ask);
  - Deribit: option trades in [t - 60 min, t) from the free archive, never after t;
  - result and settle value from the settled Kalshi market; early or off-schedule closes skipped.
Maker fills are proven from later candles (see crypto.maker_filled). Rows go to
refprice_crypto_backtest_log.csv (engines refprice-crypto-bt-<rule>-<h>h), which the board reads.
"""
import math
from collections import defaultdict
from datetime import date, timedelta

from .. import log
from . import CRYPTO_BT_LOG, CRYPTO_SERIES, crypto, http

LIVE_SINCE = date(2026, 8, 23)       # Kalshi's live tier starts here (/historical/cutoff, 2026-10-22)


def run(since=LIVE_SINCE, until=None, series=tuple(CRYPTO_SERIES), horizons=crypto.HORIZONS_H,
        out=CRYPTO_BT_LOG, verbose=True):
    until = until or date.today() - timedelta(days=1)
    rows, skipped, basis = [], defaultdict(int), []
    ref_memo, end_memo = {}, {}
    d = since
    while d <= until:
        close = crypto.scheduled_close(d)
        for s in series:
            cur = CRYPTO_SERIES[s]
            ms = [m for m in crypto.event_markets(s, d) if m.get("result") in ("yes", "no")]
            if not ms:
                skipped["no settled event"] += 1
                continue
            if not all(crypto.on_schedule(m, d) for m in ms):
                skipped["off schedule / early close"] += 1
                continue
            candles = crypto.hourly_candles([m["ticker"] for m in ms], close - (max(horizons) + 2) * 3600, close)
            for h in horizons:
                t = close - h * 3600
                if (cur, t) not in ref_memo:
                    ref_memo[(cur, t)] = crypto.reference_at(cur, t, close)
                ref = ref_memo[(cur, t)]
                if ref is None:
                    skipped[f"deribit too thin ({h}h)"] += 1
                    continue
                priced = crypto.price_event(s, ms, candles, t, ref, close)
                rows += crypto.rows_for(s, priced, t, close, ref, "refprice-crypto-bt", h)
            if (cur, d) not in end_memo:          # diagnostic only: Deribit index vs Kalshi's settle value
                tr = crypto.deribit_trades(cur, close - 300, close)
                end_memo[(cur, d)] = float(tr[-1]["index_price"]) if tr else None
                sv = crypto._f(ms[0].get("expiration_value"))
                if end_memo[(cur, d)] and sv:
                    basis.append((cur, d, math.log(sv / end_memo[(cur, d)]) * 1e4))
        if verbose:
            print(f"  {d}: {len(rows)} rows so far ({http.stats['calls']} calls, {http.stats['429']} x 429)", flush=True)
        d += timedelta(days=1)
    log.write_log(rows, out)
    return rows, dict(skipped), basis
