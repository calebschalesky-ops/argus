"""Reference pricing: read-only HTTP, option maths, no look-ahead, maker fills, Fed mapping."""
import math
from datetime import date

import pytest

from argus.refprice import crypto, fed, http, options


# ---------------- read-only ----------------

@pytest.mark.parametrize("url", [
    "https://api.elections.kalshi.com/trade-api/v2/portfolio/orders",
    "https://api.elections.kalshi.com/trade-api/v2/markets/X/orders",
    "https://www.deribit.com/api/v2/private/buy",
    "https://www.deribit.com/api/v2/private/get_account_summary",
    "https://history.deribit.com/api/v2/private/get_positions",
    "https://example.com/api/v2/public/x",
    "http://www.deribit.com/api/v2/public/get_index_price",
])
def test_refuses_anything_but_public_reads(url):
    with pytest.raises(PermissionError):
        http.check_allowed(url)


@pytest.mark.parametrize("url", [
    "https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
    "https://history.deribit.com/api/v2/public/get_last_trades_by_currency_and_time",
    "https://api.elections.kalshi.com/trade-api/v2/markets/candlesticks",
    "https://fred.stlouisfed.org/graph/fredgraph.csv",
])
def test_allows_public_reads(url):
    http.check_allowed(url)


def test_refused_call_is_logged_and_never_sent(monkeypatch, tmp_path):
    monkeypatch.setattr(http, "CALL_LOG", tmp_path / "calls.csv")
    monkeypatch.setattr(http, "DATA", tmp_path)
    sent = []
    monkeypatch.setattr(http._session, "get", lambda *a, **k: sent.append(a))
    with pytest.raises(PermissionError):
        http.get("https://www.deribit.com/api/v2/private/buy")
    assert not sent and "REFUSED" in (tmp_path / "calls.csv").read_text()


# ---------------- option maths ----------------

def flat(iv):
    return options.Smile((iv, 0.0, 0.0), -3, 3, 10, 10)


def test_flat_smile_digital_is_n_d2():
    S, K, tau, iv = 80000.0, 81000.0, 4 / 8760, 0.45
    sd = iv * math.sqrt(tau)
    d2 = (math.log(S / K) - 0.5 * sd * sd) / sd
    assert options.prob_above(K, S, tau, flat(iv)) == pytest.approx(options._ncdf(d2), abs=2e-4)


def test_skew_moves_the_digital():
    """Put skew (IV higher below spot) makes 'above K' MORE likely than N(d2) says for a flat smile at K's IV."""
    S, K, tau = 80000.0, 80000.0, 1 / 365
    skew = options.Smile((0.45, -0.05, 0.0), -3, 3, 10, 10)
    assert options.prob_above(K, S, tau, skew) > options.prob_above(K, S, tau, flat(0.45))


def test_range_probabilities_add_up():
    S, tau, sm = 80000.0, 4 / 8760, flat(0.5)
    edges = [70000 + 500 * i for i in range(41)]
    total = sum(options.market_prob({"strike_type": "between", "floor_strike": a, "cap_strike": b}, S, tau, sm)
                for a, b in zip(edges, edges[1:]))
    total += options.market_prob({"strike_type": "less", "cap_strike": edges[0]}, S, tau, sm)
    total += options.market_prob({"strike_type": "greater", "floor_strike": edges[-1]}, S, tau, sm)
    assert total == pytest.approx(1.0, abs=1e-3)


def test_averaging_takes_40_seconds_off():
    assert options.kalshi_tau(3600) * options.YEAR == pytest.approx(3560)


def test_fit_smile_recovers_a_quadratic_and_refuses_thin_data():
    pts = [(u / 4, 0.4 + 0.02 * u / 4 + 0.05 * (u / 4) ** 2, 1.0, 1000 + u) for u in range(-8, 9)]
    s = options.fit_smile(pts)
    assert s.coef == pytest.approx((0.4, 0.02, 0.05), abs=1e-6)
    assert options.fit_smile(pts[:3]) is None


def test_upside_down_smile_falls_back():
    pts = [(u / 4, 0.4 - 0.05 * (u / 4) ** 2, 1.0, 1000 + u) for u in range(-8, 9)]
    assert options.fit_smile(pts).coef[2] == 0.0


# ---------------- no look-ahead ----------------

def test_quote_never_uses_a_candle_ending_after_t():
    cs = [{"end_ts": 1000, "bid": 0.4, "ask": 0.42}, {"end_ts": 4600, "bid": 0.9, "ask": 0.92}]
    assert crypto.quote_at(cs, 4599) == (0.4, 0.42)
    assert crypto.quote_at(cs, 4600) == (0.9, 0.92)
    assert crypto.quote_at(cs, 9000) == (None, None)        # older than an hour: stale


def _trade(ts_ms, inst, iv, idx=80000.0):
    return {"timestamp": ts_ms, "instrument_name": inst, "iv": iv, "index_price": idx, "amount": 1.0, "trade_id": str(ts_ms)}


def test_reference_ignores_trades_at_or_after_t():
    t = 1792656000 - 86400            # one day before a 22OCT26 08:00 UTC expiry
    settle = t + 3600 * 4
    good = [_trade((t - 600 + i) * 1000, f"BTC-22OCT26-{k}-{'C' if k >= 80000 else 'P'}", 45.0)
            for i, k in enumerate((76000, 78000, 79000, 81000, 82000, 84000))]
    future = [_trade(t * 1000, "BTC-22OCT26-80000-C", 90.0, idx=90000.0)]
    ref = crypto.reference_at("BTC", t, settle, trades=good + future)
    assert ref["spot"] == 80000.0 and ref["atm_iv"] == pytest.approx(0.45)


def test_reference_skips_expiries_before_settlement_and_itm_options():
    t = 1792656000 - 86400
    settle = 1792656000 + 3600         # Kalshi settles after the 22OCT expiry: only later expiries count
    trades = [_trade((t - 600 + i) * 1000, f"BTC-22OCT26-{k}-C", 45.0) for i, k in enumerate((81000, 82000, 83000, 84000))]
    assert crypto.reference_at("BTC", t, settle, trades=trades) is None
    itm = [_trade((t - 600 + i) * 1000, f"BTC-23OCT26-{k}-C", 45.0) for i, k in enumerate((70000, 72000, 74000, 76000))]
    assert crypto.reference_at("BTC", t, settle, trades=itm) is None


def test_parse_instrument():
    exp, k, cp = crypto.parse_instrument("BTC-23OCT26-82000-C")
    assert (k, cp) == (82000.0, "C") and exp % 86400 == 8 * 3600
    assert crypto.parse_instrument("BTC-PERPETUAL") is None


def test_real_book_needs_a_bid_within_5c():
    assert crypto.real_book(0.40, 0.44)
    assert not crypto.real_book(0.01, 0.99)
    assert not crypto.real_book(None, 0.5)
    assert not crypto.real_book(0.40, 0.46)


def test_off_schedule_close_is_excluded():
    d = date(2026, 10, 21)
    ok = {"close_time": "2026-10-21T21:00:00Z", "settlement_ts": "2026-10-21T21:02:22Z"}
    early = {"close_time": "2026-10-21T19:00:00Z"}
    assert crypto.on_schedule(ok, d) and not crypto.on_schedule(early, d)


# ---------------- rules and fills ----------------

def test_maker_fill_needs_a_trade_strictly_through():
    later = [{"trade_low": 0.40, "trade_high": 0.45}]
    assert not crypto.maker_filled("YES", 0.40, later)
    assert crypto.maker_filled("YES", 0.41, later)
    assert not crypto.maker_filled("NO", 0.55, later)     # needs a Yes trade above 0.45
    assert crypto.maker_filled("NO", 0.56, later)
    assert not crypto.maker_filled("YES", 0.5, [{"trade_low": None, "trade_high": None}])


def test_rules():
    q = crypto.side_quotes(0.70, 0.55, 0.57)     # model 70% Yes, Yes ask 57c
    side, price, fee, p, bet = crypto.decide("spec", q)
    assert side == "YES" and price == 0.57 and bet         # gap 13 pts, inside 50-99c
    assert crypto.decide("ev3", q)[4]
    side, price, *_ = crypto.decide("maker3", q)
    assert side == "YES" and price == 0.56
    q = crypto.side_quotes(0.20, 0.08, 0.09)     # cheap Yes at 9c: under every band
    assert not crypto.decide("ev3", q)[4] or crypto.decide("ev3", q)[0] == "NO"


def test_settle_charges_the_stored_fee():
    row = {"side": "YES", "paper_contracts": 1, "market_price": 0.56, "fee_per_contract": 0.0}
    crypto.settle_row(row, {"result": "yes", "expiration_value": "1"})
    assert row["won"] == "1" and row["pnl_after_fees"] == pytest.approx(0.44)


# ---------------- Fed mapping ----------------

def test_prob_between_uniform_inside_bins():
    bins = [(350, 375, 0.5), (375, 400, 0.5)]
    assert fed.prob_between(bins, 362.5, 387.5) == pytest.approx(0.5)


def test_post_meeting_map_and_decisions():
    # window: 40% of days before M at 387.5; post-M rate 362.5 (a cut) -> average 372.5
    a, r_now = 0.4, 387.5
    avg = a * r_now + (1 - a) * 362.5
    bins = [(avg - 1, avg + 1, 1.0)]
    p = fed.decision_probs(fed.post_meeting_bins(bins, a, r_now), r_now)
    assert p["C25"] == pytest.approx(1.0) and p["H0"] == 0


def test_window_shares_and_pick():
    ws = date(2026, 6, 17)
    a, c = fed.window_shares(ws, date(2026, 7, 29), date(2026, 9, 16))
    assert a == pytest.approx(42 / 91) and c == 0.0
    # a meeting right before the next IMM date with another meeting 6 weeks later: contaminated
    assert fed.pick_window([date(2026, 9, 16)], date(2026, 9, 15), date(2026, 10, 28)) is None


def test_tails_recovered_from_cut_and_hike():
    w = {"range": (375, 400), "bins": [(375, 400, 0.6), (400, 425, 0.3)], "cut": 0.05, "hike": 0.35}
    b = fed.with_tails(w)
    assert b[0] == (350, 375, pytest.approx(0.05)) and b[-1] == (425, 450, pytest.approx(0.05))
