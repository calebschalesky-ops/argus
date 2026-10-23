"""Price band, maker pricing, maker fee maths, the alert's maker column, and the longshot study helpers."""
from datetime import datetime, timedelta, timezone

import pytest

from argus import alert, buckets, engine, fees, filters
from argus.markets import Contract


# ---------------- fees ----------------

def test_maker_fee_is_quarter_of_taker_rate_rounded_up():
    assert fees.maker_fee(0.50, 100) == pytest.approx(0.44)      # 0.4375 -> 0.44
    assert fees.taker_fee(0.50, 100) == pytest.approx(1.75)
    assert fees.maker_fee(0.50) == pytest.approx(0.01)           # 0.004375 rounds up to a cent
    assert fees.maker_fee(0.90, 100) == pytest.approx(0.16)      # 0.1575 -> 0.16


def test_maker_fee_zero_where_series_does_not_charge_it():
    assert fees.maker_fee(0.50, 100, charged=False) == 0.0


def test_fee_multiplier_scales_both_fees():
    assert fees.maker_fee(0.50, 100, multiplier=2) == pytest.approx(0.88)
    assert fees.taker_fee(0.50, 100, multiplier=0.5) == pytest.approx(0.88)


def test_series_maker_fees_reads_fee_type():
    assert fees.series_maker_fees({"fee_type": "quadratic", "fee_multiplier": 1}) == (False, 1.0)
    assert fees.series_maker_fees({"fee_type": "quadratic_with_maker_fees", "fee_multiplier": 2}) == (True, 2.0)
    assert fees.series_maker_fees(None) == (False, 1.0)


def test_pnl_and_ev_take_an_explicit_fee():
    assert fees.pnl(True, 0.60, 1, fee=0.0) == pytest.approx(0.40)
    assert fees.pnl(False, 0.60, 1, fee=0.01) == pytest.approx(-0.61)
    assert fees.expected_value(0.7, 0.6, 1, fee=0.0) == pytest.approx(0.10)
    assert fees.pnl(True, 0.19) == pytest.approx(0.81 - 0.02)   # default is still the taker fee


# ---------------- band ----------------

def test_default_band_is_50_to_99():
    b = filters.DEFAULT_BAND
    assert b.allows(0.50) and b.allows(0.99) and not b.allows(0.49)
    assert b.reason(0.49) == "UNDER 50c" and b.reason(0.75) == ""


def test_floor_cannot_be_configured_away():
    b = filters.Band(min_price=0.02)
    assert not b.allows(0.09) and b.allows(0.10)
    assert b.reason(0.05) == "UNDER 10c"
    assert not filters.NO_BAND.allows(0.05)


def test_configurable_minimum():
    assert filters.Band(min_price=0.30).allows(0.35)
    assert not filters.Band(min_price=0.70).allows(0.65)


# ---------------- maker price ----------------

def test_maker_price_is_bid_plus_one_cent():
    assert filters.maker_price(0.52, 0.55) == pytest.approx(0.53)


def test_maker_price_never_crosses_the_ask():
    assert filters.maker_price(0.52, 0.53) == pytest.approx(0.52)   # one-tick spread: join the bid


def test_maker_price_with_empty_side():
    assert filters.maker_price(None, 0.05) == pytest.approx(0.01)
    assert filters.maker_price(0.98, None) == pytest.approx(0.99)
    assert filters.maker_price(0.99, None) is None                 # 100c is not a price


def test_side_book_for_no():
    assert filters.side_book("NO", 0.40, 0.45) == (pytest.approx(0.60), pytest.approx(0.55))
    assert filters.side_book("YES", 0.40, 0.45) == (0.45, 0.40)
    assert filters.side_book("NO", None, 0.45) == (None, pytest.approx(0.55))


def test_check_flags_inside_band_only():
    q = filters.check(p_side=0.75, ask=0.60, bid=0.57)
    assert q.flag() == "WORTH A LOOK" and q.allowed
    cheap = filters.check(p_side=0.30, ask=0.15, bid=0.12)
    assert cheap.gap() >= 10 and cheap.flag() == "UNDER 50c"
    small = filters.check(p_side=0.62, ask=0.60, bid=0.57)
    assert small.flag() == "-"


def test_check_maker_side():
    q = filters.check(p_side=0.65, ask=0.60, bid=0.54, maker_fees=False)
    assert q.maker_price == pytest.approx(0.55) and q.maker_fee == 0.0
    assert q.gap("maker") == pytest.approx(10.0)
    assert q.flag("taker") == "-" and q.flag("maker") == "WORTH A LOOK"
    assert q.maker_ev == pytest.approx(0.10)
    charged = filters.check(p_side=0.65, ask=0.60, bid=0.54, maker_fees=True)
    assert charged.maker_fee == pytest.approx(0.01) and charged.maker_ev == pytest.approx(0.09)


# ---------------- engine + alert ----------------

def _c(label, yes_bid, yes_ask, low):
    return Contract(f"T-{label}", "EV-1", label, "greater", low, None, yes_bid, yes_ask,
                    round(1 - yes_ask, 2) if yes_ask else None, round(1 - yes_bid, 2) if yes_bid else None,
                    "2026-10-23T18:30:00Z")


def _result(rows, pricing="taker", band=filters.DEFAULT_BAND):
    close = datetime.now(timezone.utc) + timedelta(hours=20)
    res = engine.Result("KXWTI", "KXWTI-TEST", "WTI daily", close, 99.0, "test", None, 0.024, 0.9, [], 0.0,
                        vol_notes={"vol_source": "test"}, band=band, pricing=pricing)
    for c, p in rows:
        res.rows.append(engine.price_row(c, p, res))
    return res


def test_engine_flags_favourite_and_blocks_longshot():
    res = _result([(_c("Above $99", 0.58, 0.60, 99.0), 0.75),     # YES 60c, model 75%: in band
                   (_c("Above $105", 0.10, 0.12, 105.0), 0.30)])   # YES 12c, model 30%: longshot
    fav, long_ = res.rows
    assert fav.flag == "WORTH A LOOK" and fav.side == "YES"
    assert long_.flag == "UNDER 50c" and long_.side == "YES"


def test_engine_without_band_still_floors_at_10c():
    res = _result([(_c("Above $110", 0.03, 0.05, 110.0), 0.30)], band=None)
    assert res.rows[0].flag == "UNDER 10c"


def test_engine_maker_pricing_pays_bid_plus_one():
    res = _result([(_c("Above $99", 0.54, 0.60, 99.0), 0.65)], pricing="maker")
    r = res.rows[0]
    assert r.price == pytest.approx(0.55) and r.flag == "WORTH A LOOK"
    rows = engine.log_rows(res)
    assert rows[0]["market_price"] == "0.55" and rows[0]["fee_per_contract"] == "0.00"
    assert "pricing=maker" in rows[0]["notes"] and "maker_fill=assumed" in rows[0]["notes"]


def test_pick_side_default_is_unchanged_taker_rule():
    c = _c("Above $99", 0.58, 0.60, 99.0)
    side, price, gap, ev = engine._pick_side(0.75, c)
    assert (side, price) == ("YES", 0.60) and gap == pytest.approx(15.0)
    assert ev == pytest.approx(fees.expected_value(0.75, 0.60))


def test_alert_shows_maker_price_next_to_ask():
    res = _result([(_c("Above $99", 0.54, 0.60, 99.0), 0.75)])
    text = alert.format_alert(res)
    assert "Yes ask  No ask  Maker" in text
    line = next(x for x in text.splitlines() if x.startswith("Above $99"))
    assert " 60c" in line and " 55c" in line                 # ask 60c, maker 55c
    assert "Maker:     rest a buy at 55c instead of paying 60c" in text
    assert "Filter:    side price 50-99c (never under 10c); priced at the ask (taker)" in text
    assert "Verdict:   WORTH A LOOK" in text


def test_alert_verdict_when_only_longshots_have_a_gap():
    res = _result([(_c("Above $105", 0.10, 0.12, 105.0), 0.30)])
    text = alert.format_alert(res)
    assert "Verdict:   NO EDGE IN THE BAND" in text and "under 50c" in text


# ---------------- longshot study helpers ----------------

def test_bucket_edges():
    assert buckets.bucket_of(0.09) == "<10c" and buckets.bucket_of(0.10) == "10-30c"
    assert buckets.bucket_of(0.50) == "50-70c" and buckets.bucket_of(0.99) == "90-99c"


def test_return_per_dollar():
    assert buckets.ret(0.50, True, 0.0) == pytest.approx(1.0)
    assert buckets.ret(0.05, False, 0.0) == pytest.approx(-1.0)


def _log_row(side, price, mid, won, notes_extra=""):
    return {"call_id": "x", "logged_at_utc": "2026-09-01T14:00:00Z", "engine": "commodity-backtest",
            "series": "KXGOLDD", "event_ticker": "E", "market_ticker": "M", "side": side,
            "market_price": f"{price:.2f}", "won": won, "close_time_utc": "2026-09-01T21:00:00Z",
            "notes": f"shape=greater|1|None; flag=-; replay_at=x; p_yes=0.5; yes_mid={mid:.4f}{notes_extra}"}


def test_rebuild_quotes_from_backtest_rows():
    o = buckets.obs_from_log_rows([_log_row("YES", 0.60, 0.58, "1")])[0]
    assert o["yes_ask"] == pytest.approx(0.60) and o["yes_bid"] == pytest.approx(0.56) and o["yes_won"]
    o = buckets.obs_from_log_rows([_log_row("NO", 0.45, 0.57, "1")])[0]
    assert o["yes_bid"] == pytest.approx(0.55) and o["yes_ask"] == pytest.approx(0.59) and not o["yes_won"]


def test_rebuild_with_no_yes_seller_gives_no_ask():
    # the backtest writes mid = bid when Yes had no ask; that must not invent a Yes ask
    o = buckets.obs_from_log_rows([_log_row("NO", 0.02, 0.98, "0")])[0]
    assert o["yes_bid"] == pytest.approx(0.98) and o["yes_ask"] is None


def test_maker_fill_needs_a_trade_through_our_price():
    base = {"yes_bid": 0.54, "yes_ask": 0.60, "yes_won": True, "tier": "live", "maker_fees": False, "mult": 1}
    filled = {b[0]: b[5] for b in buckets.bets(dict(base, trade_low_after=0.55, trade_high_after=0.58)) if b[1] == "maker"}
    assert filled == {"YES": True, "NO": False}                # Yes printed at 55c; nobody paid 59c+ for Yes
    none = {b[0]: b[5] for b in buckets.bets(dict(base, trade_low_after=None, trade_high_after=None)) if b[1] == "maker"}
    assert none == {"YES": False, "NO": False}                 # nothing traded: resting orders never fill


def test_filtered_rows_apply_band_and_skip_unfillable_check():
    r = _log_row("YES", 0.60, 0.58, "1").copy()
    r["notes"] = r["notes"].replace("p_yes=0.5", "p_yes=0.8")
    out = {x["engine"]: x for x in buckets.filtered_rows([r], check_fills=False)}
    t, m = out["commodity-filtered-taker"], out["commodity-filtered-maker"]
    assert t["paper_contracts"] == "1" and t["market_price"] == "0.60" and t["pnl_after_fees"] == "0.38"
    assert m["market_price"] == "0.57" and m["fee_per_contract"] == "0.00" and m["pnl_after_fees"] == "0.43"
