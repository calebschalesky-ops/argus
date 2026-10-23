"""Crypto reference pricing: Kalshi's 5 PM ET BTC/ETH ladders vs Deribit's option-implied distribution.

Markets: KXBTCD / KXETHD ("BTC above $X at 5 PM ET") and KXBTC / KXETH (ranges), the 5 PM New
York event of each day (event ticker ends in 17). Other hours of the same series and the
15-minute series are left out. All settle on the 60-second average of CF Benchmarks' real-time
index before the close.

Reference: Deribit BTC/ETH option trades in the 60 minutes BEFORE the moment priced (from the
free archive history.deribit.com, the same call live), each with Deribit's own implied
volatility and index price. See options.py for the maths.

Rules (written down in research/reference_pricing.md before the backtest was run):
  spec    taker at the ask; filters.check() says WORTH A LOOK (gap 10+ pts, side ask 50-99c)
  ev3     taker at the ask; EV after the taker fee >= 3c; side ask 10-90c
  maker3  resting buy at bid + 1c; EV after the maker fee (none on these series) >= 3c; side
          price 10-90c; counted only if FILLED: a later hourly candle before the close printed a
          trade strictly through the order (Yes at q: trade low < q; No at q: Yes trade high > 1 - q)
Every market needs a real book at that moment: a bid, and the ask within 5c of it.
"""
import math
import uuid
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import fees, filters
from . import CRYPTO_SERIES, http, options

ET = ZoneInfo("America/New_York")
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
HORIZONS_H = (1, 4, 12)          # hours before the 5 PM ET close
TRADE_WINDOW_S = 3600            # Deribit trades used: the hour before the moment priced
MAX_SPREAD = 0.05                # a real book: bid within 5c of the ask
EV_MIN = 0.03
EV_BAND = filters.Band(min_price=0.10, max_price=0.90)
RULES = ("spec", "ev3", "maker3")
MAX_EXPIRY_AFTER_S = 3 * 86400   # Deribit expiries used: from Kalshi's close to 3 days after


def ts(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------- Kalshi ----------------

def event_ticker(series, d):
    return f"{series}-{d:%y}{MONTHS[d.month - 1]}{d:%d}17"


def scheduled_close(d):
    """5 PM New York on date d, as a UTC timestamp."""
    return int(datetime.combine(d, dtime(17, 0), ET).timestamp())


def event_markets(series, d, cache=True):
    """Markets of one 5 PM event (settled lists never change, so they may be cached)."""
    data = http.kalshi("/markets", {"event_ticker": event_ticker(series, d), "limit": 1000}, cache=cache) or {}
    return data.get("markets", [])


def parse_candle(c):
    p, a, b = c.get("price") or {}, c.get("yes_ask") or {}, c.get("yes_bid") or {}
    return {"end_ts": int(c["end_period_ts"]),
            "bid": _f(b.get("close_dollars", b.get("close"))),
            "ask": _f(a.get("close_dollars", a.get("close"))),
            "trade_low": _f(p.get("low_dollars", p.get("low"))),
            "trade_high": _f(p.get("high_dollars", p.get("high"))),
            "volume": _f(c.get("volume_fp", c.get("volume"))) or 0.0}


def hourly_candles(tickers, start_ts, end_ts, cache=True):
    """{ticker: [candles]} from Kalshi's batch route GET /markets/candlesticks (hourly)."""
    out = {}
    for i in range(0, len(tickers), 100):
        chunk = tickers[i:i + 100]
        data = http.kalshi("/markets/candlesticks", {"market_tickers": ",".join(chunk), "start_ts": start_ts,
                                                     "end_ts": end_ts, "period_interval": 60}, cache=cache) or {}
        for m in data.get("markets", []):
            out[m.get("market_ticker")] = sorted((parse_candle(c) for c in m.get("candlesticks", [])),
                                                 key=lambda c: c["end_ts"])
    return out


def quote_at(candles, t):
    """(bid, ask) of the latest candle ending at or before t (and within the hour). Never later."""
    ok = [c for c in candles or [] if t - 3600 < c["end_ts"] <= t]
    if not ok:
        return None, None
    c = max(ok, key=lambda c: c["end_ts"])
    return c["bid"], c["ask"]


def real_book(bid, ask):
    return bid is not None and ask is not None and 0 < bid < ask < 1 and ask - bid <= MAX_SPREAD + 1e-9


def on_schedule(m, d):
    """Excludes early closes and odd events: close must be 5 PM ET and settlement soon after."""
    if ts(m["close_time"]) != scheduled_close(d):
        return False
    st = m.get("settlement_ts")
    return st is None or ts(st) - ts(m["close_time"]) < 6 * 3600


# ---------------- Deribit ----------------

def parse_instrument(name):
    """'BTC-23OCT26-82000-C' -> (expiry ts at 08:00 UTC, strike, 'C'/'P'); None if not a plain option."""
    parts = name.split("-")
    if len(parts) != 4 or parts[3] not in ("C", "P"):
        return None
    try:
        d = datetime.strptime(parts[1].upper(), "%d%b%y").replace(hour=8, tzinfo=timezone.utc)
        return int(d.timestamp()), float(parts[2].replace("d", ".")), parts[3]
    except ValueError:
        return None


def deribit_trades(currency, start_ts, end_ts):
    """Option trades on Deribit in [start_ts, end_ts) (seconds), from the free history archive.
    Pages of a finished window are cached; the trade_id removes overlaps between pages."""
    cache = end_ts < datetime.now(timezone.utc).timestamp() - 600
    seen, out = set(), []
    start_ms, end_ms = int(start_ts * 1000), int(end_ts * 1000) - 1
    for _ in range(200):
        res = http.deribit("get_last_trades_by_currency_and_time",
                           {"currency": currency, "kind": "option", "start_timestamp": start_ms,
                            "end_timestamp": end_ms, "count": 1000, "sorting": "asc"},
                           cache=cache, history=True)
        trades = res.get("trades", [])
        for tr in trades:
            if tr["trade_id"] not in seen:
                seen.add(tr["trade_id"])
                out.append(tr)
        if not res.get("has_more") or not trades:
            break
        nxt = trades[-1]["timestamp"]
        if nxt <= start_ms:
            nxt = start_ms + 1
        start_ms = nxt
    return sorted(out, key=lambda tr: tr["timestamp"])


def reference_at(currency, t, settle_ts, trades=None):
    """What Deribit said in the hour before t about the price at settle_ts.

    Returns dict(spot, smile, n_trades, atm_iv, expiries) or None if too thin. Only trades
    strictly before t are used (timestamp alignment: the reference never comes after the quote)."""
    if trades is None:
        trades = deribit_trades(currency, t - TRADE_WINDOW_S, t)
    trades = [tr for tr in trades if tr["timestamp"] < t * 1000]
    if not trades:
        return None
    spot = float(trades[-1]["index_price"])
    pts, exps = [], set()
    for tr in trades:
        inst = parse_instrument(tr["instrument_name"])
        if not inst or not tr.get("iv"):
            continue
        exp, K, cp = inst
        if not (settle_ts <= exp <= settle_ts + MAX_EXPIRY_AFTER_S):
            continue
        S = float(tr["index_price"])
        if (cp == "C" and K < S) or (cp == "P" and K > S):
            continue                     # in-the-money options: thin, noisy IVs
        tau = (exp - tr["timestamp"] / 1000) / options.YEAR
        if tau <= 0:
            continue
        pts.append((math.log(K / S) / math.sqrt(tau), float(tr["iv"]) / 100, float(tr.get("amount") or 1), K))
        exps.add(exp)
    smile = options.fit_smile(pts)
    if smile is None:
        return None
    return {"spot": spot, "smile": smile, "n_trades": len(pts), "atm_iv": smile.atm, "expiries": sorted(exps)}


# ---------------- pricing one moment ----------------

def side_quotes(p_yes, bid, ask, maker_charged=False, mult=1):
    """Both sides through filters.check: {side: Quote}."""
    out = {}
    for side, p in (("YES", p_yes), ("NO", 1 - p_yes)):
        s_ask, s_bid = filters.side_book(side, bid, ask)
        out[side] = filters.check(p, s_ask, s_bid, band=filters.NO_BAND, maker_fees=maker_charged,
                                  fee_multiplier=mult)
    return out


def decide(rule, quotes):
    """(side, price, fee, p_side, bet?) for one rule; side = the better side under that pricing."""
    pricing = "maker" if rule == "maker3" else "taker"
    best = max(quotes, key=lambda s: (quotes[s].ev(pricing) if quotes[s].ev(pricing) is not None else -9))
    q = quotes[best]
    price, fee, ev = q.price(pricing), q.fee(pricing), q.ev(pricing)
    if price is None:
        return best, None, None, q.p_side, False
    if rule == "spec":
        bet = filters.check(q.p_side, q.ask, q.bid).flag() == "WORTH A LOOK"
    else:
        bet = ev is not None and ev >= EV_MIN - 1e-9 and EV_BAND.allows(price)
    return best, price, fee, q.p_side, bet


def maker_filled(side, price, later_candles):
    """A resting buy is proven filled only by a later trade strictly through its price."""
    for c in later_candles:
        if side == "YES" and c["trade_low"] is not None and c["trade_low"] < price - 1e-9:
            return True
        if side == "NO" and c["trade_high"] is not None and c["trade_high"] > 1 - price + 1e-9:
            return True
    return False


def price_event(series, markets, candles, t, ref, close_ts):
    """One row-dict per market with a real book at t: p_yes, quotes and the three rules' decisions."""
    tau = options.kalshi_tau(close_ts - t)
    out = []
    for m in markets:
        if ts(m["open_time"]) > t:
            continue                                   # not listed yet at t
        cs = candles.get(m["ticker"], [])
        bid, ask = quote_at(cs, t)
        if not real_book(bid, ask):
            continue
        p = options.market_prob(m, ref["spot"], tau, ref["smile"])
        if p is None:
            continue
        quotes = side_quotes(p, bid, ask)
        later = [c for c in cs if t < c["end_ts"] <= close_ts]
        out.append({"market": m, "p_yes": p, "bid": bid, "ask": ask, "quotes": quotes, "later": later, "tau": tau})
    return out


def rows_for(series, priced, t, close_ts, ref, engine_prefix, horizon_h, settled=True):
    """Prediction-log rows (one per market per rule) for the board."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for x in priced:
        m = x["market"]
        for rule in RULES:
            side, price, fee, p_side, bet = decide(rule, x["quotes"])
            if price is None:
                continue
            fill = ""
            if rule == "maker3" and bet:
                filled = maker_filled(side, price, x["later"]) if settled else None
                fill = "proven" if filled else ("no" if filled is False else "pending")
                bet = bet and filled is not False
            row = {
                "call_id": uuid.uuid4().hex[:12], "logged_at_utc": now,
                "engine": f"{engine_prefix}-{rule}-{horizon_h}h", "series": series,
                "event_ticker": m["event_ticker"], "market_ticker": m["ticker"],
                "range_label": m.get("yes_sub_title") or "", "side": side,
                "model_prob": round(p_side, 4), "market_price": round(price, 4),
                "gap_pts": round((p_side - price) * 100, 1), "worth_a_look": "1" if bet else "0",
                "paper_contracts": 1 if bet else 0, "fee_per_contract": round(fee or 0, 4),
                "anchor_price": round(ref["spot"], 2), "anchor_kind": "deribit-index",
                "vol_daily": round(ref["atm_iv"] / math.sqrt(365), 5),
                "horizon_days": round(x["tau"] * 365, 4), "event_days": 0, "news_shift_pct": 0,
                "close_time_utc": m["close_time"], "status": "pending", "settle_value": "", "won": "",
                "pnl_after_fees": "",
                "notes": (f"p_yes={x['p_yes']:.4f}; yes_mid={(x['bid'] + x['ask']) / 2:.4f}; yes_bid={x['bid']}; "
                          f"yes_ask={x['ask']}; priced_at={datetime.fromtimestamp(t, timezone.utc):%Y-%m-%dT%H:%MZ}; "
                          f"atm_iv={ref['atm_iv']:.4f}; smile={','.join(f'{c:.4f}' for c in ref['smile'].coef)}; "
                          f"deribit_pts={ref['n_trades']}; rule={rule}"
                          + (f"; pricing=maker; maker_fill={fill}" if rule == "maker3" else "; pricing=taker")),
            }
            if settled:
                settle_row(row, m)
            rows.append(row)
    return rows


def settle_row(row, m):
    """Fill the result from the Kalshi market, charging the fee stored on the row."""
    res = m.get("result")
    if res not in ("yes", "no"):
        return False
    won = (res == "yes") == (row["side"] == "YES")
    row["status"] = "settled"
    row["settle_value"] = m.get("expiration_value") or ""
    row["won"] = "1" if won else "0"
    n = int(row["paper_contracts"] or 0)
    row["pnl_after_fees"] = fees.pnl(won, float(row["market_price"]), n, fee=float(row["fee_per_contract"]) * n) if n else 0.0
    return True
