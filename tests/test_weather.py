"""Weather engine: range maths, no look-ahead in bias and backtest, settlement parsing."""
from datetime import date, datetime, timedelta, timezone

import pytest

from argus import log
from argus.markets import Contract, contains
from argus.weather import backtest, engine, model, nbm, stations
from argus.weather.truth import Settled, known_before, parse_cli, is_preliminary, settled_by_day

UTC = timezone.utc


def C(kind, low, high, ya=None, na=None, ticker="T"):
    return Contract(ticker, "KXHIGHNY-26OCT23", f"{low}-{high}", kind, low, high,
                    None, ya, None, na, "2026-10-24T05:00:00Z")


# ---------------- range maths ----------------

LADDER = [C("less", None, 57), C("between", 57, 58), C("between", 59, 60),
          C("between", 61, 62), C("between", 63, 64), C("greater", 64, None)]


def test_ranges_sum_to_one():
    cdf = model.TempCurve(59.4, 2.3).cdf
    assert sum(model.temp_prob(c, cdf) for c in LADDER) == pytest.approx(1.0, abs=1e-9)


def test_half_degree_edges():
    cdf = model.TempCurve(57.5, 2.0).cdf
    # "57 to 58" = 56.5 .. 58.5 ; "56 or below" = below 56.5 ; "65 or above" = above 64.5
    assert model.temp_prob(C("between", 57, 58), cdf) == pytest.approx(cdf(58.5) - cdf(56.5))
    assert model.temp_prob(C("less", None, 57), cdf) == pytest.approx(cdf(56.5))
    assert model.temp_prob(C("greater", 64, None), cdf) == pytest.approx(1 - cdf(64.5))


def test_every_whole_degree_lands_in_exactly_one_range():
    # Kalshi's own contains() (used by settle) and the ranges agree on every integer value
    for v in range(40, 80):
        assert sum(contains(c, v) for c in LADDER) == 1


def test_tight_curve_puts_mass_in_right_range():
    cdf = model.TempCurve(60.0, 1.0).cdf
    ps = [model.temp_prob(c, cdf) for c in LADDER]
    assert ps.index(max(ps)) == 2           # 59 to 60


# ---------------- forecast selection: no look-ahead ----------------

def fc(run, day, kind, v, xnd=2.0):
    return nbm.Fc(run, nbm.target_column(kind, day), v, xnd)


def test_target_columns():
    d = date(2026, 10, 23)
    assert nbm.target_column("high", d) == datetime(2026, 10, 24, 0, tzinfo=UTC)
    assert nbm.target_column("low", d) == datetime(2026, 10, 23, 12, tzinfo=UTC)


def test_forecast_at_ignores_runs_not_yet_published():
    d = date(2026, 10, 23)
    early = fc(datetime(2026, 10, 22, 12, tzinfo=UTC), d, "high", 58)
    late = fc(datetime(2026, 10, 22, 18, tzinfo=UTC), d, "high", 70)
    idx = nbm.index([early, late])
    # at 19:00 the 18Z run is only 1 h old (< 2 h lag): not usable yet
    assert nbm.forecast_at(idx, "high", d, datetime(2026, 10, 22, 19, tzinfo=UTC)).txn == 58
    assert nbm.forecast_at(idx, "high", d, datetime(2026, 10, 22, 20, tzinfo=UTC)).txn == 70


def _history(days, err=1.0, target=date(2026, 10, 23), kind="high"):
    """Fake past days: NBM always says 60, settled = 60 + err; one run 30 h ahead of each column."""
    fcs, truth = [], {}
    for k in range(1, days + 1):
        d = target - timedelta(days=k)
        col = nbm.target_column(kind, d)
        fcs.append(nbm.Fc(col - timedelta(hours=30), col, 60.0, 2.0))
        truth[d] = Settled(d, 60.0 + err, col + timedelta(hours=11))
    return fcs, truth


def test_bias_uses_only_results_public_before_t():
    target = date(2026, 10, 23)
    fcs, truth = _history(40, err=2.0)
    t = datetime(2026, 10, 22, 20, tzinfo=UTC)
    # a result that only became public AFTER t, with a wild error, must be ignored
    poison = target - timedelta(days=1)
    truth[poison] = Settled(poison, 200.0, t + timedelta(minutes=1))
    lead = nbm.target_column("high", target) - t
    errs = model.past_errors(nbm.index(fcs), truth, "high", target, lead, t)
    assert poison not in [d for d, _ in errs]
    assert all(e == 2.0 for _, e in errs)
    assert set(known_before(truth, t)) == {d for d in truth if d != poison}


def test_past_errors_use_forecast_made_same_lead_ahead():
    target = date(2026, 10, 23)
    fcs, truth = _history(30, err=0.0)
    # add a later (closer) run for every past day that would be "better"; it must not be used at a 30 h lead
    extra = [nbm.Fc(f.ftime - timedelta(hours=3), f.ftime, f.txn + 5, 1.0) for f in fcs]
    t = nbm.target_column("high", target) - timedelta(hours=28)
    lead = nbm.target_column("high", target) - t
    errs = model.past_errors(nbm.index(fcs + extra), truth, "high", target, lead, t)
    assert errs and all(e == 0.0 for _, e in errs)


def test_fit_falls_back_without_history():
    f = model.fit([(date(2026, 1, 1), 3.0)] * 5, nbm_xnd=2.0)
    assert f.bias == 0.0 and f.spread == 2.0
    f = model.fit([(date(2026, 1, 1), e) for e in [1.0, 3.0] * 15])
    assert f.bias == pytest.approx(2.0) and f.spread > 0.9


# ---------------- backtest: replay sees only the past ----------------

def test_replay_time_is_4pm_new_york_day_before():
    assert backtest.replay_time(date(2026, 10, 23)) == datetime(2026, 10, 22, 20, tzinfo=UTC)   # EDT
    assert backtest.replay_time(date(2026, 1, 15)) == datetime(2026, 1, 14, 21, tzinfo=UTC)    # EST


def test_price_event_ignores_target_day_result_and_future_runs():
    target = date(2026, 10, 23)
    fcs, truth = _history(40, err=1.0)
    t = backtest.replay_time(target)
    good = nbm.Fc(t - timedelta(hours=4), nbm.target_column("high", target), 59.0, 2.0)
    contracts = [C("between", 59, 60, ya=0.40, na=0.62, ticker="A"), C("greater", 60, None, ya=0.3, na=0.72, ticker="B")]
    base, _ = engine.price_event("KXHIGHNY", "KXHIGHNY-26OCT23", contracts, nbm.index(fcs + [good]), truth, t)
    # now leak the answer: the target day's result and a run made after t
    leaked = dict(truth)
    leaked[target] = Settled(target, 99.0, t + timedelta(hours=30))
    future = nbm.Fc(t + timedelta(hours=1), nbm.target_column("high", target), 99.0, 0.5)
    again, _ = engine.price_event("KXHIGHNY", "KXHIGHNY-26OCT23", contracts, nbm.index(fcs + [good, future]), leaked, t)
    assert base.centre == again.centre == pytest.approx(60.0)
    assert [r.p_yes for r in base.rows] == [r.p_yes for r in again.rows]


def test_longshot_rule():
    assert engine.flag_for(16.0, 0.06) == "LONGSHOT"
    assert engine.flag_for(16.0, 0.10) == "WORTH A LOOK"
    assert engine.flag_for(9.9, 0.50) == "-"


def test_log_rows_settle_on_whole_degrees():
    target = date(2026, 10, 23)
    fcs, truth = _history(40, err=0.0)
    t = backtest.replay_time(target)
    good = nbm.Fc(t - timedelta(hours=4), nbm.target_column("high", target), 59.0, 2.0)
    contracts = [C("between", 59, 60, ya=0.20, na=0.81, ticker="A")]
    res, _ = engine.price_event("KXHIGHNY", "KXHIGHNY-26OCT23", contracts, nbm.index(fcs + [good]), truth, t)
    row = engine.log_rows(res)[0]
    assert row["side"] == "YES" and row["paper_contracts"] == "1"
    log.settle_row(row, 60.0)
    assert row["won"] == "1"
    row2 = engine.log_rows(res)[0]
    log.settle_row(row2, 61.0)
    assert row2["won"] == "0"


# ---------------- settlement parsing ----------------

CLI_TEXT = """
CDUS41 KOKX 220617
CLINYC

CLIMATE REPORT
NATIONAL WEATHER SERVICE NEW YORK, NY
217 AM EDT THU OCT 22 2026

...THE CENTRAL PARK NY CLIMATE SUMMARY FOR OCTOBER 21 2026...

TEMPERATURE (F)
 YESTERDAY
  MAXIMUM         62    315 PM  84    1920  63     -1       66
  MINIMUM         50    743 AM  31    1871  50      0       50
  AVERAGE         56                        56      0       58
"""


def test_parse_cli_report():
    assert parse_cli(CLI_TEXT) == (date(2026, 10, 21), 62.0, 50.0)
    assert not is_preliminary(CLI_TEXT)
    assert parse_cli(CLI_TEXT.replace("  MINIMUM         50", "  MINIMUM          M"))[2] is None
    assert parse_cli("no summary here") is None


def test_station_and_date_from_kalshi():
    rules = ("If the maximum temperature recorded at New York City (CLINYC) for Oct 23, 2026, is greater "
             "than 64° fahrenheit according to The Weather Company, then the market resolves to Yes.")
    assert stations.station_from_rules(rules) == ("NYC", "high")
    assert stations.station_from_rules(rules.replace("maximum", "minimum").replace("CLINYC", "CLIMDW")) == ("MDW", "low")
    assert stations.target_date("KXHIGHNY-26OCT23") == date(2026, 10, 23)
    assert stations.target_date("HIGHNY-21AUG06") == date(2021, 8, 6)
    for s, (cli, kind) in stations.SERIES.items():
        assert cli in stations.STATIONS


def test_settled_by_day_known_time():
    ms = [{"event_ticker": "KXHIGHNY-26OCT21", "expiration_value": "62.00", "close_time": "2026-10-22T05:00:00Z",
           "settlement_ts": "2026-10-22T11:18:25.048158Z"},
          {"event_ticker": "HIGHNY-21AUG06", "expiration_value": "88", "close_time": "2021-08-07T03:59:00Z"},
          {"event_ticker": "KXHIGHNY-26OCT22", "expiration_value": "", "close_time": "2026-10-23T05:00:00Z"}]
    t = settled_by_day(ms)
    assert t[date(2026, 10, 21)].value == 62.0
    assert t[date(2026, 10, 21)].known_at == datetime(2026, 10, 22, 11, 18, 25, 48158, tzinfo=UTC)
    assert t[date(2021, 8, 6)].known_at == datetime(2021, 8, 7, 17, 59, tzinfo=UTC)
    assert date(2026, 10, 22) not in t


# ---------------- NBM text parsing ----------------

NBS_BLOCK = """ KNYC    NBM V5.0 NBS GUIDANCE    10/22/2026  1900 UTC
 DT   /OCT  23                /OCT  24                /OCT  25
 UTC  00 03 06 09 12 15 18 21 00 03 06 09 12 15 18 21 00 03 06 09 12 15 18
 FHR  05 08 11 14 17 20 23 26 29 32 35 38 41 44 47 50 53 56 59 62 65 68 71
 TXN              47          59          42          61          42
 XND               2           2           2           2           4
 TMP  58 55 52 49 48 54 58 58 52 47 45 43 44 54 60 61 53 48 45 42 43 56 63
 KLGA    NBM V5.0 NBS GUIDANCE    10/22/2026  1900 UTC
"""


def test_parse_nomads_block():
    got = nbm.parse_nbs_block(NBS_BLOCK, "KNYC")
    run = datetime(2026, 10, 22, 19, tzinfo=UTC)
    assert {(f.ftime, f.txn, f.xnd) for f in got} == {
        (run + timedelta(hours=17), 47.0, 2.0), (run + timedelta(hours=29), 59.0, 2.0),
        (run + timedelta(hours=41), 42.0, 2.0), (run + timedelta(hours=53), 61.0, 2.0),
        (run + timedelta(hours=65), 42.0, 4.0)}
    # 59 is the high for Oct 23 (valid 00Z Oct 24), 47 the low for Oct 23 (12Z Oct 23)
    idx = nbm.index(got)
    assert nbm.forecast_at(idx, "high", date(2026, 10, 23), run + timedelta(hours=2)).txn == 59
    assert nbm.forecast_at(idx, "low", date(2026, 10, 23), run + timedelta(hours=2)).txn == 47
    assert nbm.parse_nbs_block(NBS_BLOCK, "KXYZ") == []


def test_parse_iem_csv():
    text = ("runtime,ftime,model,tmp,txn,xnd\n"
            "2026-09-01 12:00:00,2026-09-02 00:00:00,NBS,78,82,2\n"
            "2026-09-01 12:00:00,2026-09-01 15:00:00,NBS,78,,\n")
    got = nbm.parse_iem_csv(text)
    assert got == [nbm.Fc(datetime(2026, 9, 1, 12, tzinfo=UTC), datetime(2026, 9, 2, 0, tzinfo=UTC), 82.0, 2.0)]


def test_live_skips_days_already_under_way():
    tz = "America/New_York"
    d = date(2026, 10, 23)
    assert engine.day_not_started(d, tz, datetime(2026, 10, 23, 3, 59, tzinfo=UTC))      # 11:59 PM EDT Oct 22
    assert not engine.day_not_started(d, tz, datetime(2026, 10, 23, 4, 1, tzinfo=UTC))
