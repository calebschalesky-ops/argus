"""Deadline decay: deadlines, no look-ahead, early closes counted as No losses, the hazard maths."""
from datetime import datetime, timedelta, timezone

import pytest

from argus.decay import backtest as bt
from argus.decay import collect, http

UTC = timezone.utc
S = datetime(2026, 6, 1, 4, tzinfo=UTC)          # midnight ET, Jun 1 2026


def candle(t, bid, ask, hi=None):
    return {"end_period_ts": int(t.timestamp()),
            "yes_bid": {"close_dollars": f"{bid:.4f}"}, "yes_ask": {"close_dollars": f"{ask:.4f}"},
            "price": {"close_dollars": f"{ask:.4f}", "high_dollars": f"{(hi or ask):.4f}"}}


def market(result="no", close=None, opened=None, ticker="KXTEST-26-A", event="KXTEST-26"):
    opened = opened or S - timedelta(days=40)
    close = close or S - timedelta(minutes=1)
    m = {"ticker": ticker, "event_ticker": event, "_category": "Politics", "result": result,
         "open_time": opened.isoformat(), "close_time": close.isoformat(),
         "latest_expiration_time": (S + timedelta(days=7)).isoformat(), "deadline": S.isoformat()}
    return bt.prepare(m, {})


def flat(m, bid, ask, until=None):
    """A daily candle every day of the market's life (up to its close) at one price."""
    end = min(until or m["close"], m["close"])
    t, out = m["opened"] + timedelta(days=1), []
    while t <= end:
        out.append(candle(t, bid, ask))
        t += timedelta(days=1)
    return out


# ---------------- reading the deadline ----------------

@pytest.mark.parametrize("text,expect", [
    ("If X happens before Jul 1, 2026, then the market resolves to Yes.", datetime(2026, 7, 1, 4, tzinfo=UTC)),
    ("Will it happen by March 31, 2026?", datetime(2026, 4, 1, 4, tzinfo=UTC)),
    ("before 1 July 2026", datetime(2026, 7, 1, 4, tzinfo=UTC)),
    ("Will Mamdani do it before 2027?", datetime(2027, 1, 1, 5, tzinfo=UTC)),
    ("by the end of 2026", datetime(2027, 1, 1, 5, tzinfo=UTC)),
    ("before July 2026", datetime(2026, 7, 1, 4, tzinfo=UTC)),
    ("before the middle of 2026", None),
])
def test_parse_deadline(text, expect):
    d = collect.parse_deadline(text)
    assert (d.astimezone(UTC) if d else None) == expect


def test_by_date_needs_the_early_close_condition():
    assert collect.is_by_date({"can_close_early": True,
                               "early_close_condition": "This market will close and expire early if the event occurs."})
    assert not collect.is_by_date({"can_close_early": False, "early_close_condition": ""})
    assert not collect.is_by_date({"can_close_early": True, "early_close_condition": ""})


def test_http_is_read_only():
    for bad in ("/portfolio/orders", "/markets/X/orders", "/login"):
        with pytest.raises(PermissionError):
            http.check_allowed(bad)
    http.check_allowed("/historical/markets/X/candlesticks")


# ---------------- no look-ahead ----------------

def test_quote_ignores_candles_that_end_after_t():
    t = S - timedelta(days=5)
    cs = [candle(t - timedelta(days=1), 0.20, 0.22), candle(t + timedelta(hours=1), 0.90, 0.92)]
    assert bt.quote_at(cs, bt.ts(t)) == (0.20, 0.22)


def test_quote_rejects_empty_books_and_stale_candles():
    t = S - timedelta(days=5)
    assert bt.quote_at([candle(t, 0.01, 0.99)], bt.ts(t)) is None            # empty book
    assert bt.quote_at([candle(t, 0.0, 0.03)], bt.ts(t)) is None             # no real bid
    assert bt.quote_at([candle(t - timedelta(days=3), 0.2, 0.22)], bt.ts(t)) is None   # too old


def test_check_grid_comes_from_the_deadline_not_the_close():
    a = market("no")
    b = market("yes", close=S - timedelta(days=3))
    assert bt.check_times(a) == bt.check_times(b)
    assert bt.check_times(a)[-1] == S - timedelta(days=1)
    assert len(bt.check_times(a)) == 10                  # last 25% of a 40-day life


# ---------------- early closes ----------------

def test_early_yes_close_is_a_no_loss_when_open_at_the_check():
    m = market("yes", close=S - timedelta(days=2))       # X happened 2 days before the deadline
    cs = flat(m, 0.20, 0.22)
    [x] = bt.run_simple(m, cs, 7)                         # checked 7 days before the DEADLINE: still open
    assert x["t"] == S - timedelta(days=7)
    assert not x["won"] and x["early_yes"]
    assert x["pnl"] == pytest.approx(-0.80 - x["fee"])


def test_market_already_closed_at_the_check_is_not_traded():
    m = market("yes", close=S - timedelta(days=5))
    cs = flat(m, 0.20, 0.22)
    assert bt.run_simple(m, cs, 3) == []                  # closed (resolved) before S - 3d
    assert len(bt.run_simple(m, cs, 7)) == 1


def test_not_measured_from_the_close():
    """A measure taken from the actual close would trade this early closer at close - 3d;
    measured from the deadline it is checked at S - 3d and S - 7d only."""
    m = market("yes", close=S - timedelta(days=10))
    cs = flat(m, 0.20, 0.22)
    assert all(bt.run_simple(m, cs, k) == [] for k in bt.SIMPLE_KS if k < 10)
    assert len(bt.run_simple(m, cs, 14)) == 1


def test_no_market_wins_after_fee():
    m = market("no")
    [x] = bt.run_simple(m, flat(m, 0.20, 0.22), 3)
    assert x["won"] and x["price"] == pytest.approx(0.80)
    assert x["pnl"] == pytest.approx(0.20 - x["fee"])
    assert x["fee"] > 0


def test_simple_band():
    m = market("no")
    assert bt.run_simple(m, flat(m, 0.05, 0.07), 3) == []
    assert bt.run_simple(m, flat(m, 0.50, 0.52), 3) == []


# ---------------- hazard model ----------------

def test_hazard_fair_maths():
    assert bt.hazard_fair(0.5, 2.0, 1.0) == pytest.approx(1 - 0.5 ** 0.5)
    assert bt.hazard_fair(0.3, 10, 10) == pytest.approx(0.3)
    assert bt.hazard_fair(0.3, 10, 0) == 0.0


def test_hazard_fires_when_price_does_not_decay():
    m = market("no")
    trades = bt.run_hazard(m, flat(m, 0.30, 0.32))      # flat 31c with 10 days left: fair falls well below
    hz = [x for x in trades if x["variant"] == "hazard"]
    assert len(hz) == 1 and hz[0]["model_no"] > hz[0]["market_no"]
    assert hz[0]["t"] == bt.check_times(m)[0]            # first qualifying day, one bet per market


def test_hazard_quiet_when_price_already_decayed():
    m = market("no")
    cs, t = [], m["opened"] + timedelta(days=1)
    while t < m["close"]:
        left = (S - t).total_seconds() / 86400
        y = 1 - (1 - 0.5) ** (left / 39)                  # exactly the constant-hazard path
        cs.append(candle(t, round(y, 2) - 0.01, round(y, 2) + 0.01))
        t += timedelta(days=1)
    assert [x for x in bt.run_hazard(m, cs) if x["variant"] == "hazard"] == []


def test_maker_needs_a_later_trade_through_our_price():
    m = market("no")
    cs = [dict(c, price={"close_dollars": "0.30", "high_dollars": "0.30"}) for c in flat(m, 0.30, 0.32)]
    # our No bid is 69c = a Yes offer at 31c; Yes only ever traded at 30c, so nothing filled us
    assert [x for x in bt.run_hazard(m, cs) if x["variant"] == "hazard-maker"] == []
    t0 = bt.check_times(m)[0]
    cs = [dict(c, price={"close_dollars": "0.31", "high_dollars": "0.31"})
          if c["end_period_ts"] - 86400 >= bt.ts(t0) else c for c in cs]
    [mk] = [x for x in bt.run_hazard(m, cs) if x["variant"] == "hazard-maker"]
    assert mk["price"] == pytest.approx(0.69) and mk["fee"] == 0.0   # No bid 68c + 1c; no maker fee here


# ---------------- statistics ----------------

def test_cluster_ci_and_worst_cluster():
    def tr(ev, pnl, t):
        return {"event": ev, "pnl": pnl, "price": 0.8, "fee": 0.01, "t": t, "won": pnl > 0,
                "model_no": 0.9, "market_no": 0.8, "early_yes": False}
    xs = [tr(f"E{i}", 0.19, S + timedelta(days=i)) for i in range(30)] + [tr("BAD", -0.81, S), tr("BAD", -0.81, S)]
    s = bt.summary(xs)
    assert s["n"] == 32 and s["events"] == 31
    assert s["worst"][0] == "BAD" and s["worst"][1] == 2
    lo, hi = s["ci"]
    assert lo < s["roi"] < hi


def test_schedule_check_ignores_the_overwritten_expected_expiration():
    """Kalshi rewrote expected_expiration_time to the close date on some early Yes closes
    (KXTARIFFLENGTHCAN-25-APR01). The check must not read it, or it would drop No losers."""
    a = market("yes", close=S - timedelta(days=9))
    a2 = bt.prepare(dict(collect_like(a), expected_expiration_time=a["close"].isoformat()), {})
    b = market("no")
    assert a["sched_ok"] and a2["sched_ok"] and b["sched_ok"]
    assert not bt.schedule_ok(S, S + timedelta(days=30))
    assert not bt.schedule_ok(S, None)


def collect_like(m):
    keep = ["ticker", "event_ticker", "_category", "result", "open_time", "close_time",
            "latest_expiration_time", "deadline"]
    return {k: m[k] for k in keep}


# ---------------- live ----------------

from argus.decay import live  # noqa: E402


def live_market(bid, ask, days_left, life=40):
    now = S - timedelta(days=days_left)
    return now, {"ticker": "KXTEST-26-A", "event_ticker": "KXTEST-26", "status": "active",
                 "open_time": (S - timedelta(days=life)).isoformat(), "deadline": S.isoformat(),
                 "latest_expiration_time": (S + timedelta(days=7)).isoformat(),
                 "yes_bid_dollars": f"{bid:.2f}", "yes_ask_dollars": f"{ask:.2f}", "_category": "Politics"}


def test_live_simple_rule_fires_only_on_the_check_day():
    now, m = live_market(0.20, 0.22, 2.5)
    assert [v for v, *_ in live.evaluate(m, now)] == ["simple-3d"]
    now, m = live_market(0.20, 0.22, 4.5)
    assert live.evaluate(m, now) == []
    now, m = live_market(0.01, 0.30, 2.5)           # no real book
    assert live.evaluate(m, now) == []


def test_live_hazard_uses_the_price_a_week_ago():
    now, m = live_market(0.30, 0.32, 5)
    week_ago = [candle(now - timedelta(days=7, hours=1), 0.30, 0.32)]
    got = {v: (p, mn) for v, p, mn, _ in live.evaluate(m, now, week_ago)}
    assert "hazard" in got and got["hazard"][0] == pytest.approx(0.70) and got["hazard"][1] > 0.75
    assert "hazard" not in {v for v, *_ in live.evaluate(m, now, [])}   # no history, no hazard call


def test_live_skips_markets_whose_schedule_disagrees():
    now, m = live_market(0.20, 0.22, 2.5)
    m["latest_expiration_time"] = (S + timedelta(days=30)).isoformat()
    assert live.evaluate(m, now) == []
