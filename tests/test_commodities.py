"""Commodities package tests: read-only client, no look-ahead in every replay, lock maths,
maker fill rule, same-settlement pair detection, and the scan's tiering."""
from datetime import datetime, timedelta, timezone

import pytest

from argus.commodities import history as H
from argus.commodities import http, scan
from argus.commodities import strategies as S


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def candle(end, bid=None, ask=None, lo=None, hi=None, vol=0, period=3600):
    c = {"end_period_ts": int(end.timestamp()), "volume_fp": str(vol), "price": {}}
    if bid is not None:
        c["yes_bid"] = {"close_dollars": str(bid)}
    if ask is not None:
        c["yes_ask"] = {"close_dollars": str(ask)}
    if lo is not None:
        c["price"] = {"low_dollars": str(lo), "high_dollars": str(hi if hi is not None else lo)}
    return c


CLOSE = utc(2026, 9, 30, 21, 0)          # 5 PM New York


def mkt(ticker, kind="greater", low=None, high=None, result="no", value=100.0, opened=CLOSE - timedelta(days=1),
        close=CLOSE, event="KXT-26SEP30"):
    return {"ticker": ticker, "event_ticker": event, "strike_type": kind, "floor_strike": low, "cap_strike": high,
            "open_time": iso(opened), "close_time": iso(close), "expected_expiration_time": iso(close),
            "result": result, "expiration_value": str(value), "yes_sub_title": ticker}


# ---------------- read-only client ----------------

def test_http_refuses_trading_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "CALL_LOG", tmp_path / "calls.csv")
    monkeypatch.setattr(http, "DATA", tmp_path)
    for path in ("/portfolio/orders", "/markets/X/orders", "/portfolio/balance", "/exchange/status"):
        with pytest.raises(PermissionError):
            http.get(path)
    assert (tmp_path / "calls.csv").read_text().count("REFUSED") == 4


def test_http_allows_market_data_paths():
    for path in ("/series", "/events", "/markets", "/series/KXGOLDD/events/E/candlesticks",
                 "/historical/markets"):
        http.check_allowed(path)


# ---------------- no look-ahead: quotes and fills ----------------

def test_quote_ignores_candles_after_t():
    t = CLOSE - timedelta(hours=2)
    cs = [candle(t - timedelta(hours=1), 0.40, 0.42), candle(t, 0.45, 0.47), candle(t + timedelta(hours=1), 0.90, 0.92)]
    assert H.quote_at(cs, t, 3600) == (0.45, 0.47)
    # only a future candle: no quote at all
    assert H.quote_at([candle(t + timedelta(minutes=1), 0.9, 0.92)], t, 3600) == (None, None)


def test_quote_too_old_is_refused_and_empty_sides_are_none():
    t = CLOSE - timedelta(hours=2)
    assert H.quote_at([candle(t - timedelta(hours=2), 0.4, 0.42)], t, 3600) == (None, None)
    assert H.quote_at([candle(t, 0.0, 1.0)], t, 3600) == (None, None)


def test_trades_after_uses_only_whole_periods_after_t_and_before_close():
    t = CLOSE - timedelta(hours=3)
    cs = [candle(t, lo=0.10, vol=5),                               # ended at t: before
          candle(t + timedelta(minutes=30), lo=0.11, vol=5),       # began before t
          candle(t + timedelta(hours=1), lo=0.30, hi=0.35, vol=5),  # inside
          candle(t + timedelta(hours=2), lo=0.20, hi=0.60, vol=0),  # no trades
          candle(CLOSE + timedelta(hours=1), lo=0.01, vol=5)]       # after the close
    assert H.trades_after(cs, t, CLOSE, 3600) == (0.30, 0.35)


def test_walk_forward_uses_only_rows_settled_before_the_call():
    rows = []
    for i in range(40):                       # 40 winners that closed on day i
        t = utc(2026, 9, 1) + timedelta(days=i)
        rows.append({"engine": "e", "status": "settled", "paper_contracts": "1", "won": "1",
                     "logged_at_utc": iso(t - timedelta(hours=1)), "close_time_utc": iso(t),
                     "market_price": "0.90", "model_prob": "0.90", "gap_pts": "0", "notes": ""})
    S.walk_forward(rows, lambda r: r["engine"], min_n=30)
    # the 31st call (index 30) sees exactly 30 earlier settled rows; earlier calls keep the price
    assert rows[29]["model_prob"] == "0.90"
    assert rows[30]["model_prob"] == f"{31 / 32:.4f}"
    assert "prior_n=30" in rows[30]["notes"]


def test_replay_time_is_fixed_by_the_calendar_and_before_close():
    m = mkt("A")
    t = S.replay_time(m, "daily")
    assert t == utc(2026, 9, 30, 14, 0) and t < CLOSE           # 10:00 New York, same day
    m2 = mkt("B", close=utc(2026, 9, 30, 14, 0))                 # settles 10:00 New York
    assert S.replay_time(m2, "daily") == utc(2026, 9, 29, 14, 0)
    assert S.replay_time(m, "weekly") == utc(2026, 9, 28, 14, 0)
    assert S.replay_time(m, "monthly") == utc(2026, 9, 23, 14, 0)


def test_early_closed_markets_are_skipped():
    m = mkt("A", close=CLOSE - timedelta(hours=5))
    m["expected_expiration_time"] = iso(CLOSE)
    assert S.early_closed(m)
    assert not S.early_closed(mkt("B"))
    # AAA gas: trading always stops 10 h before the 10 AM reading; that is its schedule, not an early close
    aaa = mkt("AAA", close=CLOSE - timedelta(hours=10))
    aaa["expected_expiration_time"] = iso(CLOSE)
    assert not S.early_closed(aaa, usual=timedelta(hours=10))
    assert S.early_closed(aaa, usual=timedelta(0))


# ---------------- (a) locks ----------------

def test_non_monotone_ladder_is_a_lock_and_a_normal_ladder_is_not():
    lo_k, hi_k = mkt("ABOVE99", low=99.0), mkt("ABOVE100", low=100.0)
    # Above $99 offered at 40c while Above $100 bids 60c: buy Yes 99 + No 100 (at 40c) covers every value
    legs = [(lo_k, "YES", 0.40), (hi_k, "NO", 0.40)]
    found = S.best_lock(legs)
    assert found and found[0] == pytest.approx(1 - (0.40 + 0.02) * 2)
    # sane ladder: Above $99 at 62c, Above $100 No at 52c: costs more than $1
    assert S.best_lock([(lo_k, "YES", 0.62), (hi_k, "NO", 0.52)]) is None
    # wrong way round (Yes 100 + No 99) leaves a gap between 99 and 100: never a lock
    assert S.best_lock([(hi_k, "YES", 0.10), (lo_k, "NO", 0.10)]) is None


def test_ranges_summing_under_a_dollar_after_fees():
    ms = [mkt("LOW", kind="less", high=99.0), mkt("MID", kind="between", low=99.0, high=99.99),
          mkt("HIGH", kind="greater", low=99.99)]
    legs = [(ms[0], "YES", 0.20), (ms[1], "YES", 0.30), (ms[2], "YES", 0.30)]
    found = S.best_lock(legs)
    assert found and len(found[1]) == 3
    assert found[0] == pytest.approx(1 - 0.80 - 3 * 0.02)
    # same set at 31+31+31: 93c + 6c fees = 99c -> exactly the 1c minimum
    legs = [(ms[0], "YES", 0.31), (ms[1], "YES", 0.31), (ms[2], "YES", 0.31)]
    assert S.best_lock(legs)[0] == pytest.approx(0.01)
    legs = [(ms[0], "YES", 0.32), (ms[1], "YES", 0.31), (ms[2], "YES", 0.31)]
    assert S.best_lock(legs) is None


def test_sell_the_set_when_no_asks_are_cheap():
    ms = [mkt("LOW", kind="less", high=99.0), mkt("MID", kind="between", low=99.0, high=99.99),
          mkt("HIGH", kind="greater", low=99.99)]
    pay = S.Payoffs(ms)
    assert pay.exhaustive
    # Yes bids 40/40/40 -> No asks 60/60/60 = $1.80 + fees for a sure $2
    legs = [(m, "NO", 0.60) for m in ms]
    found = S.sell_the_set(ms, legs, pay)
    assert found and found[0] == pytest.approx(2 - 1.80 - 3 * 0.02)


def test_cross_series_lock_daily_ladder_vs_weekly_range():
    daily = mkt("WTI-ABOVE98", low=98.0, event="KXWTI-E")
    weekly = mkt("WTIW-98-98.99", kind="between", low=98.0, high=98.99, event="KXWTIW-E")
    # Yes "above 98" + Yes "98.00-98.99" leaves everything at or below 98.00 unpaid: no lock
    assert S.best_lock([(daily, "YES", 0.30), (weekly, "YES", 0.30)]) is None
    # Yes "above 98" + No on the range: unpaid at exactly 98.00 (in the range, not above it)
    assert S.best_lock([(daily, "YES", 0.40), (weekly, "NO", 0.40)]) is None
    # Yes "above 97.99" + No on the range pays everywhere: a lock across the two series
    above9799 = mkt("WTI-ABOVE97.99", low=97.99, event="KXWTI-E")
    found = S.best_lock([(above9799, "YES", 0.40), (weekly, "NO", 0.40)])
    assert found and found[0] == pytest.approx(1 - 0.84)


def test_replay_lock_buys_first_lock_once_using_only_past_quotes():
    a, b = mkt("ABOVE99", low=99.0, result="yes", value=99.5), mkt("ABOVE100", low=100.0, result="no", value=99.5)
    t1 = CLOSE - timedelta(hours=3)
    candles = {"ABOVE99": [candle(t1, 0.38, 0.40)], "ABOVE100": [candle(t1, 0.60, 0.62)],
               }
    rows, info = S.replay_lock([("KXT", [a, b])], candles, "consistency-ladder", "KXT")
    assert info["lock_hours"] == 1 and len(rows) == 2
    assert {r["logged_at_utc"] for r in rows} == {iso(t1)}
    # settled at 99.50: both legs pay (the lock pays $1 at worst, $2 between the strikes)
    assert sum(float(r["pnl_after_fees"]) for r in rows) == pytest.approx(2 - 0.84)


def test_same_settlement_pairs_found_from_values_not_names():
    table = {"KXWTI": {"ticker": "KXWTI"}, "KXWTIW": {"ticker": "KXWTIW"}, "KXGOLDD": {"ticker": "KXGOLDD"}}
    events = {}
    for i in range(4):
        d = utc(2026, 9, 4) + timedelta(days=7 * i, hours=18, minutes=30)
        events[f"W{i}"] = ("KXWTIW", [mkt("x", value=90 + i, close=d)])
        events[f"D{i}"] = ("KXWTI", [mkt("y", value=90 + i, close=d)])
        events[f"G{i}"] = ("KXGOLDD", [mkt("z", value=4000 + i, close=d)])
    stats, good, pairs = S.same_settlement_pairs(events, table)
    assert set(good) == {("KXWTI", "KXWTIW")}
    assert len(pairs) == 4


# ---------------- (b) late favourites ----------------

def test_latefav_reads_the_quote_before_t_not_after():
    m = mkt("A", result="yes")
    t60 = CLOSE - timedelta(minutes=60)
    cs = {"A": [candle(t60 - timedelta(minutes=5), 0.88, 0.90, period=60),
                candle(t60 + timedelta(minutes=1), 0.98, 0.99, period=60)]}
    rows = S.latefav_rows("KXGOLDD", [m], cs)
    r60 = [r for r in rows if r["engine"] == "latefav-60m"]
    assert len(r60) == 1 and r60[0]["market_price"] == "0.9000" and r60[0]["side"] == "YES"
    # at 30 and 15 minutes the latest quote is 99c: outside 85-97, nothing bought
    assert not [r for r in rows if r["engine"] != "latefav-60m"]


def test_latefav_no_side_and_pnl():
    m = mkt("A", result="no")
    t = CLOSE - timedelta(minutes=15)
    rows = S.latefav_rows("KXGOLDD", [m], {"A": [candle(t - timedelta(minutes=1), 0.06, 0.08, period=60)]})
    r = [r for r in rows if r["engine"] == "latefav-15m"][0]
    assert r["side"] == "NO" and r["market_price"] == "0.9400" and r["won"] == "1"
    assert float(r["pnl_after_fees"]) == pytest.approx(0.06 - 0.01)


# ---------------- (c) maker ----------------

def test_maker_limit_never_crosses_the_ask():
    assert S.maker_limit(0.40, 0.45) == 0.41
    assert S.maker_limit(0.40, 0.41) is None


def test_maker_fill_needs_a_trade_strictly_through():
    assert not S.maker_fill("YES", 0.41, 0.41, 0.50)      # traded AT the limit: maybe not us
    assert S.maker_fill("YES", 0.41, 0.40, 0.50)
    assert not S.maker_fill("NO", 0.41, 0.30, 0.59)       # No at 41c = selling Yes at 59c
    assert S.maker_fill("NO", 0.41, 0.30, 0.60)
    assert not S.maker_fill("YES", 0.41, None, None)


def test_quote_rows_fill_from_later_trades_only():
    m = mkt("A", result="no", value=99.0)
    t = S.replay_time(m, "daily")
    cs = {"A": [candle(t, 0.45, 0.50),
                candle(t - timedelta(hours=1), 0.45, 0.50, lo=0.10, vol=9),   # before t: ignored
                candle(t + timedelta(hours=2), 0.40, 0.44, lo=0.43, hi=0.47, vol=3)]}
    maker, buckets = S.quote_rows("KXT", "daily", [m], cs)
    yes = [r for r in maker if r["side"] == "YES"][0]
    no = [r for r in maker if r["side"] == "NO"][0]
    assert yes["market_price"] == "0.4600" and yes["status"] == "settled"   # 0.43 < 0.46: filled
    assert float(yes["pnl_after_fees"]) == pytest.approx(-0.46)              # no maker fee, lost
    assert no["market_price"] == "0.5100" and no["status"] == "void"        # needed a Yes trade > 0.49
    assert {r["engine"] for r in buckets} >= {"bucket-taker-50-70c", "bucket-maker-30-50c", "bucket-maker-50-70c"}


# ---------------- scan ----------------

def test_scan_shapes_and_tiers():
    assert scan.shape_of([{"strike_type": "greater"}, {"strike_type": "greater"}]) == "ladder"
    assert scan.shape_of([{"strike_type": "between"}, {"strike_type": "greater"}]) == "range"
    assert scan.shape_of([{"strike_type": "greater"}]) == "one-off"
    base = {"open_markets": 10, "vol_24h": 50000, "last_event_volume": 0, "spread_c": 2, "depth_top": 100, "settled_events": 200}
    assert scan.tier(base)[0] == 1
    assert scan.tier(dict(base, spread_c=8))[0] == 2
    assert scan.tier(dict(base, vol_24h=10))[0] == 3
    assert scan.tier(dict(base, vol_24h=0, last_event_volume=9000))[0] == 1
    assert scan.is_fifteen({"ticker": "KXGOLD15M", "frequency": "fifteen_min"})


def test_near_money_stats_uses_only_strikes_between_15_and_85():
    ms = [{"yes_bid_dollars": "0.48", "yes_ask_dollars": "0.50", "yes_bid_size_fp": "100", "yes_ask_size_fp": "40"},
          {"yes_bid_dollars": "0.01", "yes_ask_dollars": "0.03", "yes_bid_size_fp": "9", "yes_ask_size_fp": "9"}]
    assert scan.near_money_stats(ms) == (2, 40.0)


def test_one_off_markets_are_left_out_of_consistency():
    assert S.priceable([mkt("A"), mkt("B", kind="between", low=1, high=2)])
    one_off = mkt("C")
    one_off["strike_type"] = ""
    assert not S.priceable([one_off])


def test_half_cent_gap_on_aaa_strikes_is_not_a_lock():
    # AAA settles to 3 decimals: No "above 3.8900" + Yes "above 3.8950" leaves 3.891-3.895 unpaid
    a, b = mkt("A389", low=3.89), mkt("A3895", low=3.895)
    assert S.best_lock([(a, "NO", 0.22), (b, "YES", 0.56)]) is None
    assert S.pays_yes(S.contract(a), 3.893) and not S.pays_yes(S.contract(b), 3.893)


def test_aaa_gas_is_reported_as_its_own_engine():
    m = mkt("A", result="yes")
    t = CLOSE - timedelta(minutes=60)
    cs = {"A": [candle(t - timedelta(minutes=5), 0.88, 0.90, period=60)]}
    assert S.latefav_rows("KXAAAGASDWI", [m], cs)[0]["engine"] == "latefav-aaa-60m"
    assert S.latefav_rows("KXGOLDD", [m], cs)[0]["engine"] == "latefav-60m"


def test_live_candidates_rules_window_band_spread():
    from argus.commodities import live
    now = utc(2026, 10, 23, 20, 30)               # 4:30 PM ET: 30 minutes before the 5 PM settle
    def m(t, mins, bid, ask):
        return {"ticker": t, "event_ticker": "KXGOLDD-26OCT2317", "close_time": iso(now + timedelta(minutes=mins)),
                "yes_bid_dollars": str(bid), "yes_ask_dollars": str(ask), "yes_ask_size_fp": "40",
                "yes_bid_size_fp": "12", "yes_sub_title": t}
    cands = live.candidates([m("IN-YES", 30, 0.88, 0.90), m("IN-NO", 25, 0.07, 0.09),
                             m("TOO-SOON", 10, 0.88, 0.90), m("TOO-FAR", 50, 0.88, 0.90),
                             m("TOO-SURE", 30, 0.95, 0.96), m("TOO-CHEAP", 30, 0.84, 0.86),
                             m("WIDE", 30, 0.80, 0.90), m("NO-BID", 30, 0, 0.90)], now, 30)
    assert [(c[0]["ticker"], c[1], c[2], c[5]) for c in cands] == [("IN-YES", "YES", 0.90, 40.0),
                                                                    ("IN-NO", "NO", 0.93, 12.0)]
    rows = live.live_rows(cands, now, 30)
    assert rows[0]["engine"] == "latefav-live-30m" and rows[0]["status"] == "pending"
    assert "size_at_ask=40" in rows[0]["notes"] and rows[1]["fee_per_contract"] == "0.01"
    text = live.hand_list(rows, now, 30)
    assert "1 contract max" in text and "IN-YES" in text and "17:00 ET" in text


def test_live_auto_horizon_from_the_clock():
    from argus.commodities import live
    assert live.auto_horizon(utc(2026, 10, 23, 17, 30)) == 60   # 1:30 PM ET, WTI at 2:30
    assert live.auto_horizon(utc(2026, 10, 23, 18, 0)) == 30    # 2:00 PM ET
    assert live.auto_horizon(utc(2026, 10, 23, 20, 0)) == 60    # 4:00 PM ET, 5 PM settle
    assert live.auto_horizon(utc(2026, 10, 23, 20, 30)) == 30
    assert live.auto_horizon(utc(2026, 10, 23, 22, 0)) is None  # 6 PM ET: nothing left
