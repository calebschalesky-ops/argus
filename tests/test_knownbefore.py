"""Known-before engines: METAR parsing, the climate day, and no look-ahead in every replay."""
from datetime import date, datetime, timedelta, timezone

import pytest

from argus.knownbefore import gas, http, obs, quotes, rain, temp
from argus.markets import Contract

UTC = timezone.utc
NY = "America/New_York"


def ob(t, temp_f, **kw):
    return obs.Ob(t, temp_f, **kw)


def hourly(day, temps, tz=NY, minute=51, **kw):
    """One report per hour from the start of the climate day, temps[i] at hour i (+minute)."""
    start, _ = obs.climate_day(day, tz)
    return [ob(start + timedelta(hours=i, minutes=minute), v, **kw) for i, v in enumerate(temps)]


# ---------------- METAR parsing ----------------

def test_parse_metar_tenths_and_six_hour_groups():
    o = obs.parse_metar("2026-10-20 23:51",
                        "KNYC 202351Z AUTO VRB06G17KT 10SM CLR 17/01 A3025 RMK AO2 SLP236 T01670006 10194 20161 53012 $")
    assert o.temp == pytest.approx(16.7 * 9 / 5 + 32)
    assert o.max6 == pytest.approx(19.4 * 9 / 5 + 32)
    assert o.min6 == pytest.approx(16.1 * 9 / 5 + 32)
    assert o.precip is None and not o.wet and not o.cloudy


def test_parse_metar_precip_weather_sky_and_negative_temps():
    o = obs.parse_metar("2026-01-20 05:51",
                        "KNYC 200551Z 2SM -SN BR OVC008 M02/M04 A3023 RMK AO2 P0012 60034 T10221044 11011 21028 $")
    assert o.temp == pytest.approx(-2.2 * 9 / 5 + 32)
    assert o.max6 == pytest.approx(-1.1 * 9 / 5 + 32) and o.min6 == pytest.approx(-2.8 * 9 / 5 + 32)
    assert o.precip == pytest.approx(0.34) and o.wet and o.cloudy


def test_body_temperature_used_when_no_t_group():
    o = obs.parse_metar("2026-10-20 13:25", "KXYZ 201325Z 02011KT 10SM FEW001 14/03 A3027")
    assert o.temp == pytest.approx(14 * 9 / 5 + 32)


# ---------------- climate day ----------------

def test_climate_day_is_local_standard_time_all_year():
    s, e = obs.climate_day(date(2026, 7, 15), NY)          # summer: 1 AM EDT = 05:00Z
    assert s == datetime(2026, 7, 15, 5, tzinfo=UTC) and e - s == timedelta(days=1)
    s, _ = obs.climate_day(date(2026, 1, 15), NY)
    assert s == datetime(2026, 1, 15, 5, tzinfo=UTC)
    s, _ = obs.climate_day(date(2026, 7, 15), "America/Phoenix")
    assert s == datetime(2026, 7, 15, 7, tzinfo=UTC)


def test_wall_clock_uses_daylight_time():
    assert obs.wall_clock(date(2026, 7, 15), 16, NY) == datetime(2026, 7, 15, 20, tzinfo=UTC)


# ---------------- no look-ahead: observations ----------------

def test_state_ignores_reports_after_t_minus_lag():
    day = date(2026, 7, 15)
    o = hourly(day, [70, 71, 72, 74, 76, 78, 80, 82, 84, 85, 86])
    t = o[-1].t + obs.LAG
    base = obs.state_at(o, day, NY, t)
    late = o + [ob(t - timedelta(minutes=5), 99.0)]          # reported, but not yet public at t
    assert obs.state_at(late, day, NY, t).hi == base.hi == 86
    early = o + [ob(t - timedelta(minutes=30), 99.0)]
    assert obs.state_at(early, day, NY, t).hi == 99.0


def test_six_hour_group_reaching_back_before_the_day_is_ignored():
    day = date(2026, 7, 15)
    start, _ = obs.climate_day(day, NY)
    o = hourly(day, [70, 70, 70, 70, 70, 70, 70])
    o.append(ob(start + timedelta(minutes=51), 70, max6=95.0))         # covers yesterday evening
    o.append(ob(start + timedelta(hours=6, minutes=51), 70, max6=75.0))  # fully inside the day
    st = obs.state_at(sorted(o, key=lambda x: x.t), day, NY, start + timedelta(hours=8))
    assert st.hi == 75.0


def test_midnight_value_is_interpolated_from_the_reports_either_side():
    day = date(2026, 10, 17)
    start, _ = obs.climate_day(day, NY)
    o = [ob(start - timedelta(minutes=9), 64.0)] + hourly(day, [58, 56, 54, 52])
    st = obs.state_at(o, day, NY, start + timedelta(hours=5))
    assert 63 < st.hi < 64


# ---------------- no look-ahead: analogs and quotes ----------------

def test_analogs_on_or_after_the_target_day_are_never_used():
    day = date(2026, 7, 15)
    pool = [temp.Analog(day - timedelta(days=i), 1.0, 0.0, 0.0) for i in range(-5, 40)]
    chosen = temp.pick(pool, day, 1.0, 0.0, k=100)
    assert chosen and all(a.day < day for a in chosen)


def candle(end, bid, ask, vol=10):
    return {"end_period_ts": int(end.timestamp()), "yes_bid": {"close": f"{bid}"}, "yes_ask": {"close": f"{ask}"},
            "volume": f"{vol}"}


def test_quote_never_uses_a_candle_that_ended_after_t():
    t = datetime(2026, 7, 15, 20, tzinfo=UTC)
    cs = [candle(t - timedelta(hours=1), 0.40, 0.42), candle(t, 0.50, 0.52), candle(t + timedelta(hours=1), 0.97, 0.98)]
    assert quotes.quote(cs, t)[:2] == (0.50, 0.52)
    assert quotes.quote(cs[:1], t - timedelta(minutes=1))[:2] == (0.40, 0.42)


def test_quote_needs_a_real_bid_within_5c():
    t = datetime(2026, 7, 15, 20, tzinfo=UTC)
    assert quotes.quote([candle(t, 0.40, 0.46)], t) is None
    assert quotes.quote([candle(t, 0.0, 0.03)], t) is None
    assert quotes.quote([candle(t, 0.97, 1.0)], t) is None


def market(ticker, kind, low, high, result, close):
    return {"ticker": ticker, "event_ticker": "KXHIGHNY-26JUL15", "strike_type": kind, "floor_strike": low,
            "cap_strike": high, "open_time": "2026-07-14T14:00:00Z", "close_time": close, "result": result,
            "expiration_value": "88", "yes_sub_title": f"{low}-{high}", "_tier": "historical"}


def test_replay_is_unchanged_by_anything_after_the_call():
    """Plant future reports and future prices; the logged calls at earlier slots must not move."""
    day = date(2026, 7, 15)
    close = "2026-07-16T04:59:00Z"
    mk = [market("A", "less", None, 85, "no", close), market("B", "between", 85, 86, "no", close),
          market("C", "between", 87, 88, "yes", close), market("D", "greater", 88, None, "no", close)]
    hist_days = sorted({day - timedelta(days=365 * y + j) for y in range(3) for j in range(-30, 31)} - {day}
                       - {day + timedelta(days=j) for j in range(1, 31)})
    temps = [70, 70, 71, 72, 73, 75, 77, 79, 81, 83, 84, 85, 86, 87, 88, 88, 87, 86, 84, 82, 80, 78, 76, 75]
    metars = []
    for d in hist_days + [day]:
        metars += hourly(d, temps)
    finals = {d: (88.0, 70.0) for d in hist_days}
    analogs = {s: temp.build_analogs(metars, finals, NY, "high", s) for s in temp.SLOTS}
    t13 = obs.wall_clock(day, 13, NY)
    candles = {m["ticker"]: [candle(t13, 0.30, 0.33)] for m in mk}
    base = temp.replay_event("KXHIGHNY", "KXHIGHNY-26JUL15", mk, metars, analogs, NY, "high", candles)
    assert base
    future_metars = metars + [ob(t13 + timedelta(hours=2), 105.0)]
    future_candles = {k: v + [candle(t13 + timedelta(hours=1), 0.97, 0.99)] for k, v in candles.items()}
    future_finals = {**finals, day: (105.0, 70.0)}
    analogs2 = {s: temp.build_analogs(future_metars, future_finals, NY, "high", s) for s in temp.SLOTS}
    again = temp.replay_event("KXHIGHNY", "KXHIGHNY-26JUL15", mk, future_metars, analogs2, NY, "high", future_candles)
    at13 = lambda rows: [(r["market_ticker"], r["model_prob"], r["market_price"]) for r in rows if "slot=13;" in r["notes"]]
    assert at13(base) and at13(base) == at13(again)


def test_range_prob_whole_degree_edges():
    cdf = temp.cdf_of(86.0, [temp.Analog(date(2026, 1, 1), 0, 0, 0)] * 10, "high", sd=0.3)
    assert temp.range_prob(Contract("x", "e", "", "between", 85, 86, None, None, None, None, ""), cdf) > 0.9
    assert temp.range_prob(Contract("x", "e", "", "greater", 86, None, None, None, None, None, ""), cdf) < 0.1


def test_bet_rule_needs_5c_after_fee_and_10c_price():
    side, price, p, edge, bet = temp.decide(0.99, 0.90, 0.92)
    assert side == "YES" and bet and edge == pytest.approx(0.99 - 0.92 - 0.01)
    assert not temp.decide(0.15, 0.01, 0.03)[4]                    # 3c side: longshot rule
    assert not temp.decide(0.50, 0.46, 0.48)[4]                    # 2c edge


# ---------------- rain ----------------

def test_rain_counts_only_precip_windows_inside_the_day():
    day = date(2026, 7, 15)
    start, _ = obs.climate_day(day, NY)
    o = hourly(day, [70] * 8)
    t = start + timedelta(hours=8)
    early = sorted(o + [ob(start + timedelta(minutes=51), 70, precip=0.20)], key=lambda x: x.t)  # 05:51Z 6-hr group
    assert rain.state_class(early, day, NY, t) == "clear"
    inside = sorted(o + [ob(start + timedelta(hours=3, minutes=51), 70, precip=0.02)], key=lambda x: x.t)
    assert rain.state_class(inside, day, NY, t) == "measured"
    future = sorted(o + [ob(t - timedelta(minutes=5), 70, precip=0.50)], key=lambda x: x.t)
    assert rain.state_class(future, day, NY, t) == "clear"


def test_rain_history_uses_only_earlier_days():
    day = date(2026, 7, 15)
    hist = [(day + timedelta(days=i), "clear", True) for i in range(0, 20)]      # today and later: all rain
    hist += [(day - timedelta(days=i), "clear", False) for i in range(1, 20)]
    p, n = rain.prob_rain(hist, day, "clear")
    assert n == 19 and p < 0.1


# ---------------- gas ----------------

def test_gas_uses_only_prints_public_before_the_call():
    day = date(2026, 7, 15)
    t = datetime(2026, 7, 14, 22, tzinfo=UTC)
    hist = {day - timedelta(days=i): (4.00 + 0.001 * i, datetime(2026, 7, 14, 12, tzinfo=UTC) - timedelta(days=i - 1))
            for i in range(1, 60)}
    base = gas.predict(hist, day, t, k=10)
    assert base[0] == day - timedelta(days=1) and base[2] == 1
    hist[day] = (9.99, datetime(2026, 7, 15, 12, tzinfo=UTC))                       # tomorrow's print, not yet public
    hist[day - timedelta(days=1)] = (hist[day - timedelta(days=1)][0], t + timedelta(minutes=1))   # yesterday's: late
    again = gas.predict(hist, day, t, k=10)
    assert again[0] == day - timedelta(days=2) and again[2] == 2


# ---------------- read-only client ----------------

def test_client_refuses_order_paths_and_unknown_hosts():
    with pytest.raises(PermissionError):
        http.check_kalshi("/portfolio/orders")
    with pytest.raises(PermissionError):
        http.check_kalshi("/markets/X/orders")
    with pytest.raises(PermissionError):
        http.check_host("https://example.com/x")
    http.check_kalshi("/markets/X/orderbook")
    http.check_host("https://mesonet.agron.iastate.edu/json/cli.py")
