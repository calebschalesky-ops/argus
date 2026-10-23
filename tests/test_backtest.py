"""Backtest tests. The main job: prove the replay never uses information from after the replay time."""
from datetime import datetime, timezone

import pytest

from argus import backtest as bt
from argus.model import ET


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


T = utc(2026, 10, 14, 14, 0)          # replay time: Wed 2026-10-14 10:00 ET


def candle(end, bid, ask, live=True):
    k = "close_dollars" if live else "close"
    return {"end_period_ts": int(end.timestamp()), "yes_bid": {k: str(bid)}, "yes_ask": {k: str(ask)}}


# ---------------- prices at the replay time ----------------

def test_quote_ignores_candles_that_end_after_replay():
    cs = [candle(utc(2026, 10, 14, 13), 0.40, 0.42),
          candle(utc(2026, 10, 14, 14), 0.45, 0.47),
          candle(utc(2026, 10, 14, 15), 0.90, 0.99)]   # the future: must not be used
    assert bt.quote_at(cs, T) == (0.45, 0.47)


def test_quote_only_future_candles_gives_nothing():
    assert bt.quote_at([candle(utc(2026, 10, 14, 15), 0.9, 0.99)], T) == (None, None)


def test_quote_too_old_gives_nothing():
    assert bt.quote_at([candle(utc(2026, 10, 14, 12), 0.4, 0.5)], T) == (None, None)


def test_quote_reads_historical_tier_format():
    assert bt.quote_at([candle(T, 0.3, 0.35, live=False)], T) == (0.3, 0.35)


def test_candles_request_never_asks_past_replay(monkeypatch):
    seen = {}
    monkeypatch.setattr(bt, "_get", lambda path, params: seen.update(params) or {"candlesticks": []})
    bt.candles_at("KXWTI", {"ticker": "X", "event_ticker": "E", "_tier": "live"}, T)
    assert seen["end_ts"] == int(T.timestamp())
    bt.candles_at("KXWTI", {"ticker": "X", "_tier": "historical"}, T)
    assert seen["end_ts"] == int(T.timestamp())


def test_markets_listed_after_replay_are_not_priced():
    early = {"open_time": "2026-10-13T23:30:00Z", "close_time": "2026-10-14T18:30:00Z"}
    late = {"open_time": "2026-10-14T16:00:00Z", "close_time": "2026-10-14T18:30:00Z"}   # added at noon ET
    assert bt.existed_at(early, T) and not bt.existed_at(late, T)


# ---------------- volatility from the past only ----------------

HIST = [(f"2026-09-{d:02d}T18:30:00Z", 90 + (d % 3), "December 2026") for d in range(1, 31)] + [
    ("2026-10-13T18:30:00Z", 95.0, "December 2026"),
    ("2026-10-14T18:30:00Z", 150.0, "December 2026"),     # same-day settle, after the replay
    ("2026-10-15T18:30:00Z", 30.0, "December 2026"),      # future
]


def test_history_before_drops_same_day_and_future_settles():
    past = bt.history_before(HIST, T)
    assert past[-1][0] == "2026-10-13T18:30:00Z"
    assert all(bt.parse_time(h[0]) < T for h in past)


def test_vol_unchanged_by_future_settles():
    with_future = bt.vol_at(HIST, T)
    without = bt.vol_at([h for h in HIST if h[0] < "2026-10-14"], T)
    assert with_future == without
    assert with_future[2][1] == 95.0           # "last settle" is the prior day's, not today's


def test_fred_fallback_lags_a_week():
    fred = [("2026-10-01", 80.0), ("2026-10-07", 81.0), ("2026-10-08", 82.0), ("2026-10-13", 99.0)]
    assert [d for d, _ in bt.fred_before(fred, T)] == ["2026-10-01", "2026-10-07"]


# ---------------- replay timing ----------------

def test_replay_time_daily_is_10am_et_on_settle_day():
    t = bt.replay_time("KXWTI", utc(2026, 10, 14, 18, 30))
    assert t.astimezone(ET).hour == 10 and t.astimezone(ET).date().isoformat() == "2026-10-14"


def test_replay_time_weekly_is_wednesday_before_friday_close():
    t = bt.replay_time("KXWTIW", utc(2026, 10, 16, 18, 30)).astimezone(ET)
    assert (t.date().isoformat(), t.hour) == ("2026-10-14", 10)
    assert t < utc(2026, 10, 16, 18, 30)


# ---------------- whole-event replay ----------------

def mk(ticker, event, kind, low, high, open_t, close_t, result, value):
    return {"ticker": ticker, "event_ticker": event, "strike_type": kind, "floor_strike": low,
            "cap_strike": high, "yes_sub_title": ticker, "open_time": open_t, "close_time": close_t,
            "result": result, "expiration_value": value, "_tier": "live",
            "yes_bid_dollars": "1.00", "yes_ask_dollars": "1.00",       # final prices: must be ignored
            "custom_strike": {"front_month_contract": "WBS 26Z-ICE"}}


def test_replay_uses_quotes_at_replay_not_final_prices(monkeypatch):
    close = "2026-10-14T18:30:00Z"
    ev = [mk(f"KXWTI-X-T{k}", "KXWTI-X", "greater", k, None, "2026-10-13T23:30:00Z", close,
             "yes" if k < 99 else "no", "99.02") for k in (97.99, 98.99, 99.99, 100.99)]
    quotes = {97.99: (0.80, 0.82), 98.99: (0.55, 0.57), 99.99: (0.30, 0.32), 100.99: (0.10, 0.12)}

    def fake_candles(series, m, t):
        bid, ask = quotes[m["floor_strike"]]
        return [candle(t, bid, ask), candle(utc(2026, 10, 14, 18), 1.0, 1.0)]   # second one is the future
    monkeypatch.setattr(bt, "candles_at", fake_candles)
    res, t, why = bt.replay_event("KXWTI", "KXWTI-X", ev, {"KXWTI-X": ev}, HIST, [])
    assert why == "" and t == T
    asks = sorted(r.c.yes_ask for r in res.rows)
    assert asks == [0.12, 0.32, 0.57, 0.82]
    assert 99.0 < res.anchor < 100.0                 # ladder midpoint at replay, not the 150 settle
    rows = bt.rows_for(res, t, ev)
    assert all(r["engine"] == "oil-backtest" and r["status"] == "settled" for r in rows)
    assert all(r["logged_at_utc"] == "2026-10-14T14:00:00Z" for r in rows)


def test_backtest_never_writes_the_live_log():
    assert bt.BACKTEST_LOG.name == "backtest_log.csv"
    assert bt.BACKTEST_LOG != bt.log.LOG_PATH


def test_refuses_non_read_only_paths():
    with pytest.raises(PermissionError):
        bt._get("/portfolio/orders")
