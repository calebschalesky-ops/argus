"""Live forward test: price today's open 5 PM ET BTC/ETH events off Deribit, log paper calls.

Same pricing code and rules as the backtest (crypto.py). Kalshi's current bid/ask come from the
public market list; Deribit trades from the last 60 minutes (live public API, same method as the
archive). For every paper bet, the contracts resting at the best price are read from Kalshi's
public order book, so depth is measured, not assumed. Rows -> refprice_crypto_live_log.csv, engines
refprice-crypto-<rule>-<h>h with h the nearest of 1/4/12 hours. Maker rows stay pending until the
settle step proves (or refutes) the fill from Kalshi's candles.
"""
import math
from datetime import datetime, timedelta, timezone

from .. import log
from . import CRYPTO_LIVE_LOG, CRYPTO_SERIES, crypto, http

ENGINE = "refprice-crypto"


def depth_at(ticker, side, price):
    """Contracts available to buy `side` at `price` (public order book; an offer to sell Yes at p
    is a No bid at 1 - p)."""
    d = (http.kalshi(f"/markets/{ticker}/orderbook") or {}).get("orderbook_fp") or {}
    other = d.get("no_dollars" if side == "YES" else "yes_dollars") or []
    want = round(1 - price, 4)
    return sum(float(q) for p, q in other if abs(float(p) - want) < 1e-6)


def run(now=None):
    now = now or datetime.now(timezone.utc)
    t = int(now.timestamp())
    rows, refs = [], {}
    for day in (now.astimezone(crypto.ET).date(), now.astimezone(crypto.ET).date() + timedelta(days=1)):
        close = crypto.scheduled_close(day)
        if not (t + 1800 <= close <= t + 13 * 3600):
            continue
        h = min(crypto.HORIZONS_H, key=lambda x: abs(x * 3600 - (close - t)))
        for s, cur in CRYPTO_SERIES.items():
            ms = [m for m in crypto.event_markets(s, day, cache=False) if m.get("status") in ("active", "open")]
            if not ms:
                continue
            if cur not in refs:
                tr = crypto.deribit_trades(cur, t - crypto.TRADE_WINDOW_S, t)
                refs[cur] = crypto.reference_at(cur, t, close, trades=tr)
            ref = refs[cur]
            if ref is None:
                print(f"{s}: Deribit too thin in the last hour, nothing priced")
                continue
            candles = {}
            for m in ms:     # the live quote, shaped like a candle ending now
                candles[m["ticker"]] = [{"end_ts": t, "bid": crypto._f(m.get("yes_bid_dollars")),
                                         "ask": crypto._f(m.get("yes_ask_dollars")), "trade_low": None,
                                         "trade_high": None, "volume": 0.0}]
            priced = crypto.price_event(s, ms, candles, t, ref, close)
            rs = crypto.rows_for(s, priced, t, close, ref, ENGINE, h, settled=False)
            for r in rs:
                if r["paper_contracts"]:
                    r["notes"] += f"; depth_at_price={depth_at(r['market_ticker'], r['side'], float(r['market_price'])):.0f}"
            rows += rs
            bets = [r for r in rs if r["paper_contracts"]]
            print(f"{s} {crypto.event_ticker(s, day)}: {len(priced)} markets with a real book, "
                  f"{len(bets)} paper bets ({h}h rule set); Deribit spot {ref['spot']:.2f}, ATM IV {ref['atm_iv'] * 100:.1f}%")
            for r in bets:
                print(f"   {r['engine']:<28} {r['side']:<3} {r['range_label']:<22} at {float(r['market_price']) * 100:.0f}c "
                      f"model {float(r['model_prob']) * 100:.1f}%  {r['notes'].split('; ')[-1]}")
    if rows:
        log.append(rows, CRYPTO_LIVE_LOG)
    print(f"Logged {len(rows)} calls to {CRYPTO_LIVE_LOG.name}. Paper only; Argus never trades.")
    return rows


def settle(path=CRYPTO_LIVE_LOG):
    """Fill results from Kalshi; maker rows get their fill proven from candles after the call."""
    rows = log.read_log(path)
    pending = [r for r in rows if r["status"] == "pending"
               and datetime.fromisoformat(r["close_time_utc"].replace("Z", "+00:00")) < datetime.now(timezone.utc)]
    n = 0
    for ev in {r["event_ticker"] for r in pending}:
        ms = {m["ticker"]: m for m in (http.kalshi("/markets", {"event_ticker": ev, "limit": 1000}) or {}).get("markets", [])}
        evrows = [r for r in pending if r["event_ticker"] == ev]
        need = [r for r in evrows if "maker_fill=pending" in r["notes"]]
        cs = {}
        if need:
            close = crypto.ts(evrows[0]["close_time_utc"])
            cs = crypto.hourly_candles(sorted({r["market_ticker"] for r in need}), close - 14 * 3600, close, cache=False)
        for r in evrows:
            m = ms.get(r["market_ticker"])
            if not m or m.get("result") not in ("yes", "no"):
                continue
            if "maker_fill=pending" in r["notes"]:
                at = crypto.ts(dict(x.split("=", 1) for x in r["notes"].split("; ") if "=" in x)["priced_at"].replace("Z", ":00Z"))
                later = [c for c in cs.get(r["market_ticker"], []) if c["end_ts"] > at]
                filled = crypto.maker_filled(r["side"], float(r["market_price"]), later)
                r["notes"] = r["notes"].replace("maker_fill=pending", f"maker_fill={'proven' if filled else 'no'}")
                if not filled:
                    r["paper_contracts"] = 0
                    r["worth_a_look"] = "0"
            n += crypto.settle_row(r, m)
    log.write_log(rows, path)
    return n
