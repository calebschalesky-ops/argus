"""Favorites backtest tests: no look-ahead, fee maths, bucket assignment, basket caps."""
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from argus import favorites as fv
from argus.fees import taker_fee


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def candle(end, ask=None, last=None, live=True, bid="auto"):
    k = "close_dollars" if live else "close"
    c = {"end_period_ts": int(end.timestamp())}
    if bid == "auto":
        bid = round((ask if ask is not None else last) - 0.02, 2)
    if bid is not None:
        c["yes_bid"] = {k: str(bid)}
    if ask is not None:
        c["yes_ask"] = {k: str(ask)}
    if last is not None:
        c["price"] = {k: str(last)}
    return c


CLOSE = utc(2026, 9, 30, 20, 0)


def market(result="yes", opened=CLOSE - timedelta(days=40), latest=None, close=CLOSE):
    return {"ticker": "KXTEST-26SEP30-A", "event_ticker": "KXTEST-26SEP30", "_tier": "live",
            "open_time": iso(opened), "close_time": iso(close), "result": result,
            "latest_expiration_time": iso(latest or close + timedelta(days=7))}


# ---------------- no look-ahead ----------------

def test_quote_ignores_candles_ending_after_the_moment():
    t = utc(2026, 9, 30, 19)
    cs = [candle(t - timedelta(hours=1), ask=0.80),
          candle(t, ask=0.85),
          candle(t + timedelta(hours=1), ask=0.99)]        # the future: must not be used
    assert fv.quote_at(cs, int(t.timestamp()), 3600 * 3)[:2] == (0.85, "ask")


def test_quote_only_future_candles_gives_nothing():
    t = utc(2026, 9, 30, 19)
    assert fv.quote_at([candle(t + timedelta(minutes=1), ask=0.99)], int(t.timestamp()), 3600) == (None, None, None)


def test_quote_too_old_gives_nothing():
    t = utc(2026, 9, 30, 19)
    assert fv.quote_at([candle(t - timedelta(hours=4), ask=0.9)], int(t.timestamp()), 3 * 3600)[0] is None


def test_quote_falls_back_to_last_trade_and_reads_historical_format():
    t = utc(2026, 9, 30, 19)
    assert fv.quote_at([candle(t, last=0.91, live=False)], int(t.timestamp()), 3600)[:2] == (0.91, "last")


def test_quote_ignores_an_ask_with_no_real_book():
    t = utc(2026, 9, 30, 19)
    ts = int(t.timestamp())
    assert fv.quote_at([candle(t, ask=0.99, bid=0.01)], ts, 3600)[0] is None      # empty book
    assert fv.quote_at([candle(t, ask=0.97, bid=None)], ts, 3600)[0] is None      # no bid at all
    assert fv.quote_at([candle(t, last=0.96, bid=None)], ts, 3600)[0] is None     # lone trade, no book
    assert fv.quote_at([candle(t, ask=0.97, bid=0.92)], ts, 3600)[0] == 0.97      # 5c spread is fine


def test_observations_use_price_at_each_horizon_not_later():
    m = market()
    hour = [candle(CLOSE - timedelta(hours=25), ask=0.88),   # 1 day before close (1h older)
            candle(CLOSE - timedelta(hours=1), ask=0.93),    # exactly 1 hour before close
            candle(CLOSE - timedelta(minutes=30), ask=0.99)] # after the 1h moment: never used
    day = [candle(CLOSE - timedelta(days=7, hours=5), ask=0.81),
           candle(CLOSE - timedelta(days=6), ask=0.97),      # after the 7d moment: never used
           candle(CLOSE - timedelta(days=30, hours=10), ask=0.72)]
    rows = {r["horizon"]: r for r in fv.observations(m, {"hour": hour, "day": day}, "Test")}
    assert rows["1h"]["price"] == 0.93
    assert rows["1d"]["price"] == 0.88
    assert rows["7d"]["price"] == 0.81
    assert rows["30d"]["price"] == 0.72
    assert rows["30d"]["bucket"] == "70-80" and rows["1h"]["bucket"] == "90-95"


def test_no_observation_before_market_opened():
    m = market(opened=CLOSE - timedelta(days=3))
    day = [candle(CLOSE - timedelta(days=7, hours=1), ask=0.9)]
    rows = fv.observations(m, {"hour": [], "day": day}, "Test")
    assert all(r["horizon"] not in ("7d", "30d") for r in rows)


def test_candle_requests_never_reach_past_one_hour_before_close():
    for name, _, start, end in fv._windows(CLOSE):
        assert end <= int((CLOSE - timedelta(hours=1)).timestamp())
        assert start < end


def test_early_close_flag():
    assert fv.early_closed(market(latest=CLOSE + timedelta(days=400)))
    assert not fv.early_closed(market(latest=CLOSE + timedelta(days=7)))
    # sports games always expire ~2 weeks after close: that is their schedule, not an early close
    assert not fv.early_closed(market(latest=CLOSE + timedelta(days=14)), series_gap=14)
    assert fv.early_closed(market(latest=CLOSE + timedelta(days=21)), series_gap=14)


# ---------------- fee maths ----------------

@pytest.mark.parametrize("price,fee", [(0.95, 0.01), (0.90, 0.01), (0.80, 0.02), (0.70, 0.02), (0.99, 0.01), (0.50, 0.02)])
def test_taker_fee_rounds_up_to_the_cent(price, fee):
    assert taker_fee(price) == fee


def test_profit_after_fee():
    assert fv.profit(0.95, True) == pytest.approx(0.05 - 0.01)
    assert fv.profit(0.95, False) == pytest.approx(-0.95 - 0.01)
    assert fv.profit(0.80, True) == pytest.approx(0.20 - 0.02)


def test_break_even_and_roi_in_cell_stats():
    t, c = CLOSE - timedelta(days=1), CLOSE
    rows = [{"price": 0.95, "won": w, "event": f"E{i}", "t": t.isoformat(), "settle": c.isoformat()}
            for i, w in enumerate([True] * 19 + [False])]
    s = fv.cell_stats(rows)
    assert s["n"] == 20 and s["win"] == pytest.approx(0.95)
    assert s["breakeven"] == pytest.approx(0.96)                 # 95c + 1c fee
    assert s["profit"] == pytest.approx((19 * 0.04 - 0.96) / 20)  # loses the fee on average
    assert s["roi"] == pytest.approx(s["profit"] / 0.96)
    assert s["days"] == pytest.approx(1)
    assert s["ann"] == pytest.approx(s["roi"] * 365)


# ---------------- bucket assignment ----------------

@pytest.mark.parametrize("price,label", [
    (0.69, None), (0.70, "70-80"), (0.79, "70-80"), (0.80, "80-85"), (0.84, "80-85"),
    (0.85, "85-90"), (0.89, "85-90"), (0.90, "90-95"), (0.94, "90-95"), (0.95, "95-99"),
    (0.99, "95-99"), (1.0, None), (None, None), (0.8499999, "85-90"), (0.7000001, "70-80")])  # float noise rounds to the cent
def test_bucket_of(price, label):
    assert fv.bucket_of(price) == label


# ---------------- basket ----------------

def obs_row(i, cat, close_day, won=True, price=0.95):
    t = utc(2026, 9, 14, 12)                                       # a Monday
    return {"ticker": f"T{i}", "event": f"E{i}", "category": cat, "bucket": "95-99", "horizon": "7d",
            "t": t.isoformat(), "close": (utc(2026, 9, close_day, 20)).isoformat(),
            "settle": (utc(2026, 9, close_day, 21)).isoformat(), "price": price, "won": won, "early": False}


def test_basket_caps_per_category_and_close_date():
    rows = [obs_row(i, "Sports", 21) for i in range(10)] + [obs_row(100 + i, "Crypto", 20 + i % 3) for i in range(10)]
    got = fv.basket(rows, "95-99", "7d", cap_category=5, cap_close_date=3)
    sports = [b for b in got if b["category"] == "Sports"]
    assert len(sports) == 3                                        # close-date cap bites first
    crypto = [b for b in got if b["category"] == "Crypto"]
    assert len(crypto) <= 5
    per_day = {}
    for b in got:
        per_day[b["close"][:10]] = per_day.get(b["close"][:10], 0) + 1
    assert max(per_day.values()) <= 3


def test_basket_skips_early_closed_and_other_buckets():
    rows = [obs_row(1, "A", 21), dict(obs_row(2, "B", 22), early=True), dict(obs_row(3, "C", 23), bucket="70-80")]
    assert [b["ticker"] for b in fv.basket(rows, "95-99", "7d")] == ["T1"]


def test_basket_summary_worst_day_and_month():
    rows = [obs_row(1, "A", 21, won=False), obs_row(2, "B", 22), obs_row(3, "C", 23)]
    s = fv.basket_summary(fv.basket(rows, "95-99", "7d"))
    assert s["contracts"] == 3 and s["wins"] == 2
    assert s["pl"] == pytest.approx(2 * 0.04 - 0.96)
    assert s["worst_day"][0] == "2026-09-21"
    assert s["worst_month"][0] == "2026-09"


# ---------------- read-only ----------------

def test_refuses_non_public_paths():
    with pytest.raises(PermissionError):
        fv._get("/portfolio/orders")


# ---------------- live paper basket ----------------

NOW = utc(2026, 10, 22, 18)


def live(ticker, event, yes_ask, yes_bid, vol=1000, close_day=25):
    return {"ticker": ticker, "event_ticker": event, "close_time": iso(utc(2026, 10, close_day, 20)),
            "yes_ask_dollars": str(yes_ask), "yes_bid_dollars": str(yes_bid),
            "no_ask_dollars": str(round(1 - yes_bid, 2)), "no_bid_dollars": str(round(1 - yes_ask, 2)),
            "volume_24h_fp": str(vol), "yes_sub_title": "x"}


SMETA = {"KXA": {"category": "Sports", "fee_type": "quadratic", "frequency": "daily"},
         "KXB": {"category": "Economics", "fee_type": "quadratic_with_maker_fees", "frequency": "monthly"},
         "KXC15M": {"category": "Crypto", "fee_type": "quadratic", "frequency": "fifteen_min"}}


def test_candidates_band_volume_side_and_15_minute_rule():
    ms = [live("KXA-1-A", "KXA-1", 0.90, 0.88),            # Yes favorite at 90c
          live("KXA-2-A", "KXA-2", 0.12, 0.10),            # No favorite at 90c
          live("KXA-3-A", "KXA-3", 0.97, 0.95),            # 97c: outside the band
          live("KXA-4-A", "KXA-4", 0.90, 0.88, vol=100),   # too thin
          live("KXC15M-1-A", "KXC15M-1", 0.90, 0.88)]      # 15-minute market: never
    got = {(c["m"]["ticker"], c["side"], c["ask"]) for c in fv.candidates(ms, SMETA)}
    assert got == {("KXA-1-A", "YES", 0.90), ("KXA-2-A", "NO", 0.90)}
    wide = [live("KXA-5-A", "KXA-5", 0.90, 0.80)]                 # 10c spread: no real book
    assert fv.candidates(wide, SMETA) == []


def test_pick_caps_category_close_date_and_event():
    ms = [live(f"KXA-{i}-A", f"KXA-{i}", 0.90, 0.88, vol=1000 + i, close_day=23 + i % 3) for i in range(9)]
    ms += [live(f"KXB-E-{i}", "KXB-E", 0.85, 0.83, close_day=27) for i in range(4)]
    got = fv.pick(fv.candidates(ms, SMETA))
    assert sum(c["category"] == "Sports" for c in got) == 3
    assert sum(c["m"]["event_ticker"] == "KXB-E" for c in got) == 2
    per_day = Counter(c["m"]["close_time"][:10] for c in got)
    assert max(per_day.values()) <= 2


def test_maker_price_never_crosses_the_ask():
    assert fv.maker_price(0.90, 0.85) == 0.86
    assert fv.maker_price(0.90, 0.89) == 0.89       # 1c spread: stay on the bid
    assert fv.maker_price(0.90, None) is None


def test_basket_rows_charge_maker_fee_only_where_series_does():
    ms = [live("KXA-1-A", "KXA-1", 0.90, 0.85), live("KXB-1-A", "KXB-1", 0.90, 0.85)]
    rows = fv.basket_rows(fv.pick(fv.candidates(ms, SMETA)), SMETA, NOW, {})
    fee = {(r["series"], r["engine"]): r["fee_per_contract"] for r in rows}
    assert fee[("KXA", fv.ENGINE_TAKER)] == "0.01" and fee[("KXB", fv.ENGINE_TAKER)] == "0.01"
    assert fee[("KXA", fv.ENGINE_MAKER)] == "0.00"
    assert fee[("KXB", fv.ENGINE_MAKER)] == "0.01"     # 0.0175 x .86 x .14 rounds up to 1c
    assert {r["market_price"] for r in rows if r["engine"] == fv.ENGINE_MAKER} == {"0.86"}


def test_settle_log_uses_the_fee_on_each_row(tmp_path, monkeypatch):
    from argus import log
    path = tmp_path / "favorites_log.csv"
    ms = [live("KXA-1-A", "KXA-1", 0.90, 0.85), live("KXA-2-A", "KXA-2", 0.12, 0.10)]
    log.append(fv.basket_rows(fv.pick(fv.candidates(ms, SMETA)), SMETA, NOW, {}), path)
    results = {"KXA-1": [{"ticker": "KXA-1-A", "result": "yes"}], "KXA-2": [{"ticker": "KXA-2-A", "result": "yes"}]}
    monkeypatch.setattr(fv, "_get", lambda p, params, cache=True: {"markets": results[params["event_ticker"]]})
    n, _ = fv.settle_log(path)
    rows = {(r["market_ticker"], r["engine"]): r for r in log.read_log(path)}
    assert n == 4
    assert rows[("KXA-1-A", fv.ENGINE_TAKER)]["pnl_after_fees"] == "0.09"     # 1 - .90 - .01
    assert rows[("KXA-1-A", fv.ENGINE_MAKER)]["pnl_after_fees"] == "0.14"     # 1 - .86, no maker fee
    assert rows[("KXA-2-A", fv.ENGINE_TAKER)]["won"] == "0"                   # bought No, Yes won
    assert rows[("KXA-2-A", fv.ENGINE_TAKER)]["pnl_after_fees"] == "-0.91"
