"""Tests for argus.smartmoney: freeze filter, no pre-freeze trades, delay/fee maths,
big-trade flags, settlement, and the read-only allowlist."""
import pytest

from argus import fees
from argus.smartmoney import bigtrades, common, http, score, wallets

FREEZE_TS = 1_792_700_000            # 2026-10-22, about 20:13 UTC


@pytest.fixture(autouse=True)
def tmp_logs(tmp_path, monkeypatch):
    """Every test writes to a scratch log and call log, never the real ones."""
    monkeypatch.setattr(common, "LOG_PATH", tmp_path / "smartmoney_log.csv")
    monkeypatch.setattr(http, "CALL_LOG", tmp_path / "api_calls.csv")
    monkeypatch.setattr(http, "SMART_DIR", tmp_path)
    monkeypatch.setattr(http, "PACE_SECONDS", 0)
    return tmp_path


def pos(cond, event, pnl, end="2026-09-01"):
    return {"condition_id": cond, "event_id": event, "realized_pnl": pnl, "end_date": end}


# ---------------- freeze filtering ----------------

def test_one_lucky_event_is_rejected():
    # 14 markets, all one election: the shape of the all-time #1 wallet
    closed = [pos(f"c{i}", "election", 1_000_000) for i in range(14)]
    prof = wallets.wallet_profile(closed, "2026-10-22")
    assert prof["distinct_events"] == 1 and prof["top_event_share"] == 1.0
    assert not wallets.passes(prof, 30, 15, 0.5)


def test_many_markets_but_one_event_carries_the_profit_is_rejected():
    closed = [pos(f"c{i}", f"e{i}", 10) for i in range(40)] + [pos("big", "e_big", 10_000)]
    prof = wallets.wallet_profile(closed, "2026-10-22")
    assert prof["ended_markets"] == 41 and prof["distinct_events"] == 41
    assert prof["top_event_share"] > 0.9
    assert not wallets.passes(prof, 30, 15, 0.5)


def test_spread_out_profitable_wallet_passes():
    closed = [pos(f"c{i}", f"e{i % 20}", 100 if i % 3 else -40) for i in range(60)]
    prof = wallets.wallet_profile(closed, "2026-10-22")
    assert prof["ended_markets"] == 60 and prof["distinct_events"] == 20
    assert wallets.passes(prof, 30, 15, 0.5)


def test_markets_that_have_not_ended_do_not_count():
    closed = [pos(f"c{i}", f"e{i}", 100, end="2026-12-31") for i in range(50)]
    prof = wallets.wallet_profile(closed, "2026-10-22")
    assert prof["ended_markets"] == 0
    assert not wallets.passes(prof, 30, 15, 0.5)


def test_losing_wallet_is_rejected():
    closed = [pos(f"c{i}", f"e{i}", -5) for i in range(50)]
    assert not wallets.passes(wallets.wallet_profile(closed, "2026-10-22"), 30, 15, 0.5)


def test_freeze_writes_only_qualifying_wallets(tmp_path, monkeypatch):
    good = [pos(f"g{i}", f"e{i}", 50) for i in range(40)]
    lucky = [pos(f"l{i}", "one-event", 1e6) for i in range(40)]

    def fake_poly(path, params=None):
        if path == "/v2/leaderboard":
            return {"data": [{"rank": 1, "user_id": "0xLUCKY", "user_name": "lucky", "pnl": 9e6, "volume": 1e7},
                             {"rank": 2, "user_id": "0xGOOD", "user_name": "good", "pnl": 1e6, "volume": 1e7}]}
        if path == "/v2/positions":
            return {"data": lucky if params["user"] == "0xlucky" else good}
        raise AssertionError(path)

    monkeypatch.setattr(http, "poly", fake_poly)
    r = wallets.freeze(out_dir=tmp_path)
    frozen = wallets.read_frozen(r["path"])
    assert [f["wallet"] for f in frozen] == ["0xgood"]
    assert r["path"].name.startswith("frozen_") and frozen[0]["filter"].startswith("ended_markets>=30")


# ---------------- nothing from before the freeze ----------------

def trade(ts, token="t1", side="BUY", price=0.40, slug="will-x-happen", cond="0xc1"):
    return {"timestamp": ts, "token_id": token, "side": side, "price": price, "slug": slug,
            "condition_id": cond, "outcome": "Yes", "outcome_index": 0, "title": "Will X happen?",
            "event_slug": "x", "transaction_hash": f"0x{ts}"}


def fixed_price(p):
    return lambda token, target, ts: (p, target)


def test_no_trade_from_before_or_at_the_freeze_is_logged():
    trades = [trade(FREEZE_TS - 3600, "old"), trade(FREEZE_TS, "edge"), trade(FREEZE_TS + 60, "new")]
    rows, counts = wallets.calls_from_trades(trades, "0xw", "w", FREEZE_TS, set(), 10,
                                             FREEZE_TS + 7200, fixed_price(0.45))
    assert counts["before_freeze"] == 2
    assert [common.note_fields(r["notes"])["token"] for r in rows] == ["new"]


def test_track_end_to_end_ignores_pre_freeze_trades(tmp_path, monkeypatch):
    frozen = tmp_path / "frozen_2026-10-22.csv"
    frozen.write_text("frozen_at_utc,wallet,user_name\n" f"{common.iso(FREEZE_TS)},0xw,w\n")
    # even if the API ignored our `start` filter and returned old trades
    monkeypatch.setattr(http, "poly_paged", lambda *a, **k: [trade(FREEZE_TS - 86400), trade(FREEZE_TS - 1)])
    r = wallets.track(frozen_path=frozen)
    assert r["rows"] == [] and r["counts"]["before_freeze"] == 2
    assert common.read_log() == []


def test_sells_short_markets_and_repeat_fills_are_not_separate_calls():
    trades = [trade(FREEZE_TS + 100), trade(FREEZE_TS + 200), trade(FREEZE_TS + 300),   # 3 fills, 1 bet
              trade(FREEZE_TS + 100, "t2", side="SELL"),
              trade(FREEZE_TS + 100, "t3", slug="btc-updown-15m-1792698300"),
              trade(FREEZE_TS + 100, "t4", slug="eth-updown-5m-1792698300")]
    rows, counts = wallets.calls_from_trades(trades, "0xw", "w", FREEZE_TS, set(), 10,
                                             FREEZE_TS + 7200, fixed_price(0.45))
    assert len(rows) == 1 and counts["sells"] == 1 and counts["short_market"] == 2
    assert common.note_fields(rows[0]["notes"])["trade_utc"] == common.iso(FREEZE_TS + 100)


def test_hourly_market_is_not_treated_as_short():
    assert not wallets.is_short_market("solana-up-or-down-october-22-2026-3pm-et")
    assert wallets.is_short_market("sol-updown-15m-1792698300")


# ---------------- delay and fee maths ----------------

def test_entry_after_delay_adds_kalshi_taker_fee():
    mp, fee, cost = common.entry_after_delay(0.55)
    assert mp == 0.55
    assert fee == 0.02                  # 0.07 * .55 * .45 = 0.0173 -> rounds UP to 2c
    assert cost == 0.57
    assert common.entry_after_delay(0.10) == (0.10, 0.01, 0.11)   # 0.0063 -> 1c


def test_entry_price_is_clamped_to_tradable_range():
    assert common.entry_after_delay(0.999)[0] == 0.99
    assert common.entry_after_delay(0.0)[0] == 0.01


def test_delay_cost_points():
    assert common.delay_cost_pts(0.40, 0.46) == 6.0       # leader moved it 6 pts before we could follow
    assert common.delay_cost_pts(0.40, 0.38) == -2.0


def test_call_uses_price_after_delay_not_leaders_fill():
    seen = {}

    def price_fn(token, target, ts):
        seen["target"], seen["ts"] = target, ts
        return 0.52, target + 30

    rows, _ = wallets.calls_from_trades([trade(FREEZE_TS + 100, price=0.40)], "0xw", "w", FREEZE_TS,
                                        set(), 10, FREEZE_TS + 7200, price_fn)
    assert seen["target"] == FREEZE_TS + 100 + 600          # 10 minutes later
    r = rows[0]
    assert r["market_price"] == "0.5200" and r["anchor_price"] == "0.4000"
    assert r["fee_per_contract"] == "0.02"
    assert common.note_fields(r["notes"])["delay_cost_pts"] == "12.0"


def test_trade_younger_than_delay_waits():
    rows, counts = wallets.calls_from_trades([trade(FREEZE_TS + 100)], "0xw", "w", FREEZE_TS, set(), 10,
                                             FREEZE_TS + 300, fixed_price(0.5))
    assert rows == [] and counts["waiting_for_delay"] == 1


def test_settled_pnl_includes_fee():
    rows = [{"engine": "wallet", "status": "pending", "market_ticker": "0xC1", "market_price": "0.5200",
             "paper_contracts": "1", "notes": "outcome_index=0"},
            {"engine": "wallet", "status": "pending", "market_ticker": "0xC1", "market_price": "0.4800",
             "paper_contracts": "1", "notes": "outcome_index=1"}]
    import argus.smartmoney.wallets as w
    orig = w.http.poly
    w.http.poly = lambda path, params=None: {"data": [{"condition_id": "0xc1", "status": "resolved",
                                                        "price": "1000000000000000000"}]}
    try:
        assert wallets.settle(rows) == 2
    finally:
        w.http.poly = orig
    assert rows[0]["won"] == "1" and float(rows[0]["pnl_after_fees"]) == round(0.48 - fees.taker_fee(0.52), 2)
    assert rows[1]["won"] == "0" and float(rows[1]["pnl_after_fees"]) == round(-0.48 - fees.taker_fee(0.48), 2)


def test_payout_for_unresolved_or_split():
    assert wallets.payout_for({"status": "proposed", "price": "0"}, 0) is None
    assert wallets.payout_for({"status": "resolved", "price": "500000000000000000"}, 0) == 0.5


# ---------------- big trades ----------------

def fill(ticker, t, count, yes, side="yes", block=False, tid=None):
    return {"ticker": ticker, "created_time": t, "count_fp": str(count), "yes_price_dollars": f"{yes:.4f}",
            "no_price_dollars": f"{1 - yes:.4f}", "taker_side": side, "is_block_trade": block,
            "trade_id": tid or f"{ticker}{t}{count}"}


def test_fills_with_same_time_and_side_are_one_order():
    orders = bigtrades.group_orders([fill("M", "2026-10-22T20:00:00Z", 100, 0.40),
                                     fill("M", "2026-10-22T20:00:00Z", 300, 0.42),
                                     fill("M", "2026-10-22T20:01:00Z", 5, 0.42)])
    assert len(orders) == 2
    assert orders[0]["count"] == 400 and abs(orders[0]["price"] - 0.415) < 1e-9


def test_flags_block_and_outsized_but_not_normal_or_small_notional():
    small = [fill("M", f"2026-10-22T19:{i:02d}:00Z", 10, 0.5) for i in range(20)]
    big = fill("M", "2026-10-22T19:30:00Z", 5000, 0.5)                       # 500x median, $2,500
    big_cheap = fill("M", "2026-10-22T19:31:00Z", 500, 0.01, side="yes")     # 50x median but $5
    block = fill("M", "2026-10-22T19:32:00Z", 10, 0.5, block=True)
    flags = bigtrades.flag_orders(bigtrades.group_orders(small + [big, big_cheap, block]))
    got = sorted((o["count"], reason) for o, reason, _ in flags)
    assert got == [(10.0, "block"), (5000.0, "outsized")]


def test_no_side_price_is_used_for_no_taker():
    o = bigtrades.group_orders([fill("M", "2026-10-22T20:00:00Z", 10, 0.30, side="no")])[0]
    assert abs(o["price"] - 0.70) < 1e-9


def test_fifteen_minute_series_skipped():
    freq = {"KXBTC15M": "fifteen_min", "KXWTI": "daily"}
    assert bigtrades.skip_short("KXBTC15M-26OCT221600-00", freq)
    assert not bigtrades.skip_short("KXWTI-26OCT23-T99", freq)


# ---------------- scorecard ----------------

def test_followable_needs_enough_settled_calls():
    def rows(n, wins, p=0.5):
        return [{"status": "settled", "won": "1" if i < wins else "0", "market_price": str(p)} for i in range(n)]
    e = score.edge_stats(rows(40, 36))                          # huge edge, too few calls
    assert not score.followable({"settled_calls": 40, "pnl_after_fees": 10}, e)
    e = score.edge_stats(rows(100, 70))                         # 20-pt edge, z = 4
    assert round(e["z"], 1) == 4.0
    assert score.followable({"settled_calls": 100, "pnl_after_fees": 15}, e)
    e = score.edge_stats(rows(100, 58))                         # 8-pt edge, z = 1.6: could be luck
    assert not score.followable({"settled_calls": 100, "pnl_after_fees": 3}, e)


# ---------------- read-only allowlist ----------------

@pytest.mark.parametrize("url", [
    "https://api.elections.kalshi.com/trade-api/v2/portfolio/orders",
    "https://api.elections.kalshi.com/trade-api/v2/portfolio/balance",
    "https://api.elections.kalshi.com/trade-api/v2/markets/orders",
    "https://clob.polymarket.com/order",
    "https://data-api.polymarket.com/v2/approvals",
    "https://data-api.polymarket.com/v1/accounting/snapshot",
    "https://gamma-api.polymarket.com/markets",
    "http://data-api.polymarket.com/v2/trades",
    "https://evil.example.com/v2/trades",
])
def test_allowlist_refuses_everything_else(url, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("a refused URL must never reach the network")
    monkeypatch.setattr(http._session, "get", no_network)
    with pytest.raises(PermissionError):
        http.get(url)
    assert "REFUSED" in http.CALL_LOG.read_text()


@pytest.mark.parametrize("url", [
    "https://data-api.polymarket.com/v2/trades",
    "https://data-api.polymarket.com/v2/leaderboard",
    "https://api.elections.kalshi.com/trade-api/v2/markets/trades",
    "https://api.elections.kalshi.com/trade-api/v2/markets/KXBALANCEPOWERCOMBO-27FEB-RR",
])
def test_allowlist_allows_public_reads(url):
    http.check_allowed(url)


def test_http_module_has_no_write_methods():
    import inspect
    src = inspect.getsource(http)
    for verb in (".post(", ".put(", ".delete(", ".patch("):
        assert verb not in src
