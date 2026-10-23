"""Edge factory tests: the bench blocks every known backtest trap, the miner agrees with the
bench, the holdout split never leaks, and the HTTP layer is read-only."""
import sqlite3

import numpy as np
import pytest

from argus import fees
from argus.lab import bench, http, lake, miner, stats

H = 3600
CLOSE = 1_780_000_000 - 1_780_000_000 % H      # an hour boundary


def candle(end, bid, ask, px_lo=None, px_hi=None, vol=0.0):
    return {"end_ts": end, "bid": bid, "ask": ask, "bid_lo": bid, "bid_hi": bid, "ask_lo": ask, "ask_hi": ask,
            "px": px_lo, "px_lo": px_lo, "px_hi": px_hi, "volume": vol}


def market(ticker="S-E1-A", result="yes", close=CLOSE, exp=None, open_ts=None, series="S", **kw):
    m = {"ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0], "series": series, "tier": "live",
         "open_ts": open_ts if open_ts is not None else close - 10 * 86400, "close_ts": close,
         "exp_ts": exp if exp is not None else close, "latest_exp_ts": close + 7 * 86400, "settle_ts": close + 600,
         "can_close_early": 0, "result": result, "settle_value": "1", "volume": 100.0, "open_interest": 5.0,
         "strike_type": "greater", "floor_strike": 1.0, "cap_strike": None, "title": "t", "yes_sub_title": "y",
         "category": "Test", "frequency": "daily", "fee_type": "quadratic", "fee_multiplier": 1.0}
    m.update(kw)
    return m


def always(side="yes", limit=None):
    return lambda m, hist, t: bench.Call(side=side, limit=limit)


# ---------------- trap: look-ahead in the price history ----------------

def test_hypothesis_never_sees_candles_after_t():
    seen = []

    def spy(m, hist, t):
        seen.append(max(c["end_ts"] for c in hist))
        return bench.Call("yes")

    cs = [candle(CLOSE - 5 * H, 0.50, 0.52), candle(CLOSE - 2 * H, 0.60, 0.62), candle(CLOSE - H // 2 - H // 2, 0.97, 0.99)]
    r = bench.run(bench.Hypothesis("spy", 2, spy), [market()], {"S-E1-A": cs})
    assert seen == [CLOSE - 2 * H]
    assert r[0]["price"] == 0.62            # the quote at t, not the later 99c


def test_market_view_has_no_outcome_or_future_fields():
    got = {}

    def spy(m, hist, t):
        got.update(m)
        return None

    bench.run(bench.Hypothesis("spy", 1, spy), [market()], {"S-E1-A": [candle(CLOSE - 2 * H, 0.5, 0.52)]})
    for k in ("result", "settle_value", "settle_ts", "close_ts", "volume", "open_interest", "latest_exp_ts"):
        assert k not in got
    assert got["sched_close_ts"] == CLOSE


# ---------------- trap: early-close look-ahead ----------------

def test_early_close_measured_from_schedule_not_actual_close():
    # Scheduled for CLOSE (expected expiration = CLOSE, usual gap 0) but closed 10 h early because it happened.
    early = market(close=CLOSE - 10 * H, exp=CLOSE)
    cs = [candle(CLOSE - 12 * H, 0.40, 0.42), candle(CLOSE - 11 * H, 0.97, 0.99)]
    # "1 hour before close" measured from the actual close would buy at 99c with the result known;
    # from the schedule, t = CLOSE - 1h is after the market closed, so there is no bet.
    assert bench.run(bench.Hypothesis("h", 1, always()), [early], {early["ticker"]: cs}, gaps={"S": 0}) == []
    # 12 hours before the schedule it was still open, priced at the 42c of that time.
    r = bench.run(bench.Hypothesis("h", 12, always()), [early], {early["ticker"]: [candle(CLOSE - 13 * H, 0.40, 0.42)]},
                  gaps={"S": 0})
    assert r and r[0]["t"] == CLOSE - 12 * H


def test_scheduled_close_uses_series_usual_gap():
    ms = [market(ticker=f"S-E{i}-A", close=CLOSE + i * 86400, exp=CLOSE + i * 86400 + 3 * H) for i in range(5)]
    gaps = bench.usual_gaps(ms)
    assert gaps["S"] == 3 * H
    assert bench.scheduled_close(ms[0], gaps["S"]) == ms[0]["close_ts"]
    off = market(close=CLOSE - 5 * H, exp=CLOSE + 3 * H)
    assert bench.scheduled_close(off, gaps["S"]) == CLOSE


def test_no_expected_expiration_and_far_latest_expiry_is_skipped():
    m = market(exp=None)
    m["exp_ts"] = None
    m["latest_exp_ts"] = CLOSE + 90 * 86400
    assert bench.scheduled_close(m, 0) is None


# ---------------- trap: empty books ----------------

def test_empty_book_is_not_a_price():
    for bid, ask in ((0.01, 0.99), (0.0, 0.97), (0.90, 0.97)):
        r = bench.run(bench.Hypothesis("h", 1, always()), [market()], {"S-E1-A": [candle(CLOSE - 2 * H, bid, ask)]})
        assert r == [], (bid, ask)
    r = bench.run(bench.Hypothesis("h", 1, always()), [market()], {"S-E1-A": [candle(CLOSE - 2 * H, 0.92, 0.97)]})
    assert len(r) == 1


def test_stale_quote_is_not_a_price():
    old = candle(CLOSE - 30 * H, 0.50, 0.52)
    assert bench.run(bench.Hypothesis("h", 1, always()), [market()], {"S-E1-A": [old]}) == []


# ---------------- trap: last trade instead of the ask; fees ----------------

def test_pays_the_ask_plus_taker_fee_not_the_last_trade():
    c = candle(CLOSE - 2 * H, 0.88, 0.91, px_lo=0.85, px_hi=0.85, vol=10)
    r = bench.run(bench.Hypothesis("h", 1, always("yes")), [market()], {"S-E1-A": [c]})[0]
    assert r["price"] == 0.91 and r["fee"] == fees.taker_fee(0.91)
    assert r["pnl"] == pytest.approx(1 - 0.91 - fees.taker_fee(0.91))
    r = bench.run(bench.Hypothesis("h", 1, always("no")), [market()], {"S-E1-A": [c]})[0]
    assert r["price"] == pytest.approx(0.12) and r["won"] == 0
    assert r["pnl"] == pytest.approx(-0.12 - fees.taker_fee(0.12))


def test_fee_multiplier_and_maker_fee_only_where_charged():
    c = [candle(CLOSE - 3 * H, 0.40, 0.45), candle(CLOSE - H, 0.38, 0.42, px_lo=0.39, px_hi=0.40, vol=5)]
    m = market(fee_multiplier=2.0)
    r = bench.run(bench.Hypothesis("h", 2, always("yes")), [m], {m["ticker"]: c})[0]
    assert r["fee"] == fees.taker_fee(0.45, 1, 2.0)
    r = bench.run(bench.Hypothesis("h", 2, always("yes", 0.41)), [m], {m["ticker"]: c})[0]
    assert r["filled"] and r["fee"] == 0.0
    m2 = market(fee_type="quadratic_with_maker_fees")
    r = bench.run(bench.Hypothesis("h", 2, always("yes", 0.41)), [m2], {m2["ticker"]: c})[0]
    assert r["fee"] == fees.maker_fee(0.41, 1, 1.0, True) > 0


# ---------------- trap: assumed maker fills ----------------

def test_maker_fill_needs_a_later_trade_strictly_through_the_limit():
    before = candle(CLOSE - 4 * H, 0.40, 0.45, px_lo=0.30, px_hi=0.30, vol=9)      # a trade BEFORE t: irrelevant
    at_t = candle(CLOSE - 3 * H, 0.40, 0.45)
    touch = candle(CLOSE - H, 0.40, 0.45, px_lo=0.41, px_hi=0.41, vol=3)            # at the limit, not through
    h = bench.Hypothesis("h", 3, always("yes", 0.41))
    r = bench.run(h, [market()], {"S-E1-A": [before, at_t, touch]})[0]
    assert r["filled"] is False and r["pnl"] == 0.0
    through = candle(CLOSE - H, 0.39, 0.45, px_lo=0.40, px_hi=0.40, vol=3)
    r = bench.run(h, [market()], {"S-E1-A": [before, at_t, through]})[0]
    assert r["filled"] is True and r["pnl"] == pytest.approx(1 - 0.41)


def test_maker_fill_from_tape_and_candle_straddling_t_ignored():
    at_t = candle(CLOSE - 3 * H, 0.40, 0.45)
    t = CLOSE - 3 * H
    h = bench.Hypothesis("h", 3, always("no", 0.56))       # a No buy at 56c fills on a Yes trade above 44c
    tape = [(t - 10, 1, 0.50, 5), (t + 100, 0, 0.44, 5)]
    assert bench.run(h, [market()], {"S-E1-A": [at_t]}, {"S-E1-A": tape})[0]["filled"] is False
    tape.append((t + 200, 1, 0.45, 1))
    assert bench.run(h, [market()], {"S-E1-A": [at_t]}, {"S-E1-A": tape})[0]["filled"] is True
    # a candle whose hour began before t may hold a trade from before t: not used
    straddle = candle(t + H // 2, 0.40, 0.45, px_lo=0.30, px_hi=0.60, vol=3)
    h2 = bench.Hypothesis("h", 3, always("yes", 0.41))
    assert bench.run(h2, [market()], {"S-E1-A": [at_t, straddle]})[0]["filled"] is False


def test_maker_limit_at_or_above_ask_is_refused():
    c = [candle(CLOSE - 3 * H, 0.40, 0.45)]
    assert bench.run(bench.Hypothesis("h", 2, always("yes", 0.45)), [market()], {"S-E1-A": c}) == []


def test_fifteen_minute_markets_are_skipped():
    m = market(frequency="fifteen_min")
    assert bench.run(bench.Hypothesis("h", 1, always()), [m], {m["ticker"]: [candle(CLOSE - 2 * H, 0.5, 0.52)]}) == []


# ---------------- statistics ----------------

def test_benjamini_hochberg_known_case():
    p = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216]
    assert list(stats.benjamini_hochberg(p, 0.05)) == [True, True] + [False] * 8


def test_p_from_moments_matches_p_value():
    rng = np.random.default_rng(1)
    x = rng.normal(0.05, 1, 50)
    assert stats.p_from_moments(np.array([50]), np.array([x.sum()]), np.array([(x ** 2).sum()]))[0] == \
        pytest.approx(stats.p_value(x), rel=1e-9)


def test_bootstrap_resamples_events_not_bets():
    # one event of 100 identical winning bets and 9 losing single-bet events: per-bet it looks great,
    # per event the interval must be wide and include losses
    recs = [{"event": "E0", "pnl": 0.1, "price": 0.9} for _ in range(100)]
    recs += [{"event": f"E{i}", "pnl": -0.9, "price": 0.9} for i in range(1, 10)]
    pe, se = stats.event_arrays(recs)
    lo, hi = stats.bootstrap_ci(pe, se)
    assert len(pe) == 10 and lo < 0


# ---------------- the miner agrees with the bench; the split never leaks ----------------

def _toy_lake(tmp_path, n_events=60):
    con = lake.connect(tmp_path / "lake.db")
    con.execute("INSERT INTO series VALUES ('S','Test','daily','t','quadratic',1.0,'[]')")
    rng = np.random.default_rng(3)
    for i in range(n_events):
        close = CLOSE - (n_events - i) * 86400
        for j, strike in enumerate(("A", "B")):
            tk = f"S-E{i}-{strike}"
            yes_p = 0.9 if j == 0 else 0.3
            won = rng.random() < (yes_p + 0.05)
            con.execute(f"INSERT INTO markets VALUES ({','.join('?' * 23)})",
                        (tk, f"S-E{i}", "S", "live", close - 9 * 86400, close, close, close + 86400, close + 60, 0,
                         "yes" if won else "no", "1", 50.0, 1.0, "greater", 1.0, None, "t", "y", "", "", None, "test"))
            rows = []
            for hh in (170, 100, 50, 25, 13, 7, 4, 2.5, 1.5, 0.5):
                end = int(close - hh * H) - int(close - hh * H) % H
                b = float(np.clip(yes_p - 0.01 + rng.integers(-12, 13) / 100, 0.02, 0.96))
                rows.append((tk, 60, end, b, b + 0.02, b, b, b + 0.02, b + 0.02, b, b - 0.02, b + 0.03, 4.0, 1.0))
            lake.insert_candles(con, rows)
            con.execute("INSERT INTO candle_done VALUES (?,?,?,?,?,?)", (tk, 60, close - 9 * 86400, close, len(rows), "x"))
    con.commit()
    return con


def test_miner_numbers_equal_bench_numbers(tmp_path):
    con = _toy_lake(tmp_path)
    snap = miner.build_snapshot(con, verbose=False)
    hold = CLOSE + 10 * 86400          # everything is mining
    rows, _ = miner.mine_rules(snap, hold, verbose=False)
    checked = 0
    for r in rows:
        if not (r["scope"] == "all" or r["scope"].startswith("lowattn")):
            continue
        spec = {k: r[k] for k in ("family", "scope", "hours", "side", "mode", "band", "yes_max", "lookback", "move")
                if k in r}
        recs = [x for x in bench.run_lake([miner.hypothesis(spec)], con)[miner.rule_id(spec)] if x["filled"]]
        assert len(recs) == r["n"], spec
        assert sum(x["pnl"] for x in recs) == pytest.approx(r["pnl"], abs=1e-6), spec
        assert len({x["event"] for x in recs}) == r["events"], spec
        checked += 1
    assert checked > 50
    fams = {r["family"] for r in rows}
    assert {"grid", "fav", "longshot", "move", "movex"} <= fams
    assert any(r["scope"].startswith("lowattn") for r in rows)


def test_mining_never_uses_holdout_markets(tmp_path):
    con = _toy_lake(tmp_path)
    snap = miner.build_snapshot(con, verbose=False)
    hold = CLOSE - 30 * 86400
    before, _ = miner.mine_rules(snap, hold, verbose=False)
    # rewrite every holdout-period result: mining numbers must not change
    con.execute("UPDATE markets SET result = CASE result WHEN 'yes' THEN 'no' ELSE 'yes' END WHERE close_ts >= ?", (hold,))
    con.commit()
    snap2 = miner.build_snapshot(con, verbose=False)
    after, _ = miner.mine_rules(snap2, hold, verbose=False)
    key = lambda r: miner.rule_id(r)
    assert {key(r): (r["n"], round(r["pnl"], 6)) for r in before} == {key(r): (r["n"], round(r["pnl"], 6)) for r in after}


def test_vector_fees_equal_fees_module():
    for p in np.linspace(0.01, 0.99, 99):
        assert miner.taker_fee_v(np.array([p]), np.array([1.0]))[0] == fees.taker_fee(round(p, 2))
        assert miner.maker_fee_v(np.array([p]), np.array([1.0]), np.array([True]))[0] == fees.maker_fee(round(p, 2))


# ---------------- the lake ----------------

def test_lake_drops_combos_and_unsettled():
    assert not lake.usable_market({"ticker": "KXMVEX-1", "event_ticker": "KXMVEX-1", "result": "yes",
                                   "close_time": "2026-01-01T00:00:00Z"})
    assert not lake.usable_market({"ticker": "A-1-B", "event_ticker": "A-1", "result": "",
                                   "close_time": "2026-01-01T00:00:00Z"})
    assert not lake.usable_market({"ticker": "A-1-B", "event_ticker": "A-1", "result": "yes",
                                   "close_time": "2025-01-01T00:00:00Z"})      # before 2025-04-01
    assert lake.usable_market({"ticker": "A-1-B", "event_ticker": "A-1", "result": "no",
                               "close_time": "2026-01-01T00:00:00Z"})


def test_minute_candles_roll_up_to_hourly():
    rows = [lake.candle_row("T", 1, {"end_period_ts": CLOSE - 3600 + 60 * i,
                                     "yes_bid": {"close_dollars": f"{0.40 + i / 100:.2f}"},
                                     "yes_ask": {"close_dollars": f"{0.45 + i / 100:.2f}"},
                                     "price": {"low_dollars": "0.41", "high_dollars": "0.44", "close_dollars": "0.42"},
                                     "volume_fp": "2"}) for i in range(1, 4)]
    out = lake.hourly_from_minutes(rows)
    assert len(out) == 1 and out[0][2] == CLOSE and out[0][3] == pytest.approx(0.43) and out[0][12] == 6
    assert lake.infer_period([{"end_period_ts": CLOSE}, {"end_period_ts": CLOSE + 60}]) == 1
    assert lake.infer_period([{"end_period_ts": CLOSE}, {"end_period_ts": CLOSE + 3600}]) == 60


def test_http_is_read_only():
    for path in ("/portfolio/orders", "/markets/../portfolio", "/orders", "/exchange/status", "/markets/orders"):
        with pytest.raises(PermissionError):
            http.check_allowed(path)
    http.check_allowed("/markets/candlesticks")
    http.check_allowed("/historical/markets")


# ---------------- live paper loggers ----------------

def test_live_calls_and_settle(tmp_path, monkeypatch):
    from argus.lab import live
    con = lake.connect(tmp_path / "lake.db")
    con.execute("INSERT INTO series VALUES ('S','Test','daily','t','quadratic',1.0,'[]')")
    con.commit()
    monkeypatch.setattr(lake, "connect", lambda path=None: con)
    monkeypatch.setattr(live, "LIVE_LOG", tmp_path / "lab_live_log.csv")
    monkeypatch.setattr(live, "PROMOTED", tmp_path / "promoted.json")
    spec = {"family": "grid", "scope": "all", "hours": 2, "side": "yes", "mode": "taker", "band": [0.90, 0.95]}
    spec_m = dict(spec, mode="maker")
    live.PROMOTED.write_text(__import__("json").dumps([
        {"rule_id": miner.rule_id(spec), "spec": spec, "prob": 0.97, "cohort": "c1", "promoted_at": "x"},
        {"rule_id": miner.rule_id(spec_m), "spec": spec_m, "prob": 0.9, "cohort": "c1", "promoted_at": "x"}]))
    now = 1_790_000_000
    close = "2026-09-21T" + "00:00:00Z"
    close_ts = lake._ts(close)
    now = close_ts - int(2.5 * 3600)
    open_m = [{"ticker": "S-E1-A", "event_ticker": "S-E1", "close_time": close, "open_time": "2026-09-01T00:00:00Z",
               "yes_bid_dollars": "0.90", "yes_ask_dollars": "0.92"},
              {"ticker": "S-E1-B", "event_ticker": "S-E1", "close_time": close, "open_time": "2026-09-01T00:00:00Z",
               "yes_bid_dollars": "0.80", "yes_ask_dollars": "0.99"}]          # empty-ish book: no call
    calls = []

    def fake_get(path, params=None, retries=14):
        calls.append((path, dict(params or {})))
        if path == "/markets" and params.get("status") == "open":
            return {"markets": open_m}
        if path == "/markets" and "tickers" in params:
            return {"markets": [{"ticker": "S-E1-A", "result": "yes", "close_time": close, "expiration_value": "1"}]}
        if path == "/markets/trades":
            return {"trades": [{"yes_price_dollars": "0.90"}]}     # at the 91c limit? no: 90 < 91 -> through
        return {}

    monkeypatch.setattr(http, "get", fake_get)
    from datetime import datetime, timezone
    out = live.run_live(now=datetime.fromtimestamp(now, timezone.utc))
    rows = live.log.read_log(live.LIVE_LOG)
    assert "2 new paper calls" in out, out
    by = {r["engine"].split(":", 1)[1]: r for r in rows}
    assert by[miner.rule_id(spec)]["market_price"] == "0.9200"
    assert by[miner.rule_id(spec_m)]["market_price"] == "0.9100" and "maker_fill=pending" in by[miner.rule_id(spec_m)]["notes"]
    assert live.run_live(now=datetime.fromtimestamp(now, timezone.utc)).startswith("Live: 2 promoted rules, 0 new")
    assert all(p != "/portfolio" for p, _ in calls)
    print(live.settle(live.LIVE_LOG))
    rows = {r["engine"].split(":", 1)[1]: r for r in live.log.read_log(live.LIVE_LOG)}
    t = rows[miner.rule_id(spec)]
    assert t["status"] == "settled" and t["won"] == "1" and float(t["pnl_after_fees"]) == pytest.approx(1 - 0.92 - fees.taker_fee(0.92))
    mk = rows[miner.rule_id(spec_m)]
    assert "maker_fill=yes" in mk["notes"] and float(mk["pnl_after_fees"]) == pytest.approx(0.09)


def test_all_win_favourites_are_not_significant_by_luck():
    # 50 bets at 97c (+1c fee), all won: a t-test sees no variance; the break-even null says ~36%
    recs = [{"event": f"E{i}", "pnl": 0.02, "price": 0.97, "fee": 0.01, "won": 1} for i in range(50)]
    assert stats.p_value(stats.event_arrays(recs)[0]) < 1e-10
    assert stats.honest_p(recs) == pytest.approx(0.98 ** 50, rel=0.01)


def test_nightly_skips_mining_when_cohort_is_fresh(tmp_path, monkeypatch):
    import json
    from datetime import datetime, timezone
    monkeypatch.setattr(miner, "COHORTS", tmp_path)
    (tmp_path / "c1.json").write_text(json.dumps({"cohort": "c1", "mined_at": datetime.now(timezone.utc).isoformat()}))
    (tmp_path / "c1_survivors.json").write_text("[]")
    out = miner.run(nightly=True, con=lake.connect(tmp_path / "l.db"))
    assert out.startswith("Mining skipped")


# ---------------- order size, closing-line value, exits ----------------

def test_fee_is_rounded_once_per_order_and_size_needs_volume():
    c = [candle(CLOSE - 2 * H, 0.94, 0.95, vol=40)]
    h1 = bench.Hypothesis("h", 1, always("yes"))
    r1 = bench.run(h1, [market()], {"S-E1-A": c})[0]
    assert r1["fee"] == 0.01
    h10 = bench.Hypothesis("h", 1, always("yes"), contracts=10)
    r10 = bench.run(h10, [market()], {"S-E1-A": c})[0]
    assert r10["fee"] == pytest.approx(fees.taker_fee(0.95, 10) / 10) and r10["fee"] < 0.01
    assert r10["pnl"] == pytest.approx(0.05 - fees.taker_fee(0.95, 10) / 10)
    h25 = bench.Hypothesis("h", 1, always("yes"), contracts=25)     # needs 75 contracts traded that hour
    assert bench.run(h25, [market()], {"S-E1-A": c}) == []


def test_closing_line_value_uses_last_real_quote_after_t():
    cs = [candle(CLOSE - 3 * H, 0.50, 0.52), candle(CLOSE - 2 * H, 0.60, 0.62), candle(CLOSE - H, 0.01, 0.99),
          candle(CLOSE + H, 0.97, 0.99)]          # after close: never used
    r = bench.run(bench.Hypothesis("h", 3, always("yes")), [market()], {"S-E1-A": cs})[0]
    assert r["price"] == 0.52 and r["clv"] == pytest.approx(0.61 - 0.52)   # the 1c/99c book is skipped
    r = bench.run(bench.Hypothesis("h", 3, always("no")), [market()], {"S-E1-A": cs})[0]
    assert r["clv"] == pytest.approx(0.39 - 0.50)


def test_exit_sells_at_the_bid_later_with_a_second_fee():
    cs = [candle(CLOSE - 6 * H, 0.40, 0.42), candle(CLOSE - 3 * H, 0.55, 0.57)]
    r = bench.run(bench.Hypothesis("h", 6, lambda m, h, t: bench.Call("yes", exit_hours=3)), [market(result="no")],
                  {"S-E1-A": cs})[0]
    assert r["exit"] == "3h"
    assert r["pnl"] == pytest.approx(0.55 - 0.42 - fees.taker_fee(0.42) - fees.taker_fee(0.55))


def test_registry_correction_counts_earlier_trials(tmp_path):
    from argus.lab import registry
    path = tmp_path / "trials.csv"
    p_now = [0.001]
    assert registry.bh_with_registry(p_now, 0.05, path=path)[0][0]
    registry.append([{"kind": "miner", "name": f"r{i}", "batch": "old", "p": 0.5} for i in range(200)], path)
    ok, m = registry.bh_with_registry(p_now, 0.05, path=path)
    assert m == 201 and not ok[0]          # 0.001 > 0.05 / 201


def test_nested_violation_found_only_after_fees():
    from argus.lab import nested
    # above $95 (wider) ask 60c; above $100 (narrower) bid 70c: buy Yes 95 + No 100 = 60 + 30 + fees < $1
    v = nested.best_violation([(95, 0.58, 0.60, 10), (100, 0.70, 0.72, 5)])
    assert v and v[0] == pytest.approx(1 - 0.60 - 0.30 - fees.taker_fee(0.60) - fees.taker_fee(0.30))
    assert nested.best_violation([(95, 0.58, 0.60, 10), (100, 0.60, 0.62, 5)]) is None    # 0c gap: fees eat it
    assert nested.best_violation([(95, 0.70, 0.72, 10), (100, 0.50, 0.52, 5)]) is None    # properly nested


def test_nested_groups_need_same_rules_and_different_strikes():
    from argus.lab import nested
    city = lambda c: {"strike_type": "greater", "floor_strike": 0.0, "rules_primary": f"If rain at {c} is greater than 0 inches"}
    assert nested.nested_groups([city("NYC"), city("SEA")]) == []
    lad = lambda k: {"strike_type": "greater", "floor_strike": k, "rules_primary": f"If WTI settles above ${k:.2f} on Oct 1"}
    assert len(nested.nested_groups([lad(95.0), lad(100.0)])) == 1
