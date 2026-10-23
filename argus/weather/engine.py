"""The weather engine: price every range of one Kalshi temperature event, build alert and log rows."""
from dataclasses import dataclass, field
from functools import lru_cache
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .. import fees, kalshi
from ..backtest import settled_markets
from ..markets import Contract
from . import model, nbm, stations
from .truth import settled_by_day

ROOT = Path(__file__).resolve().parent.parent.parent
WEATHER_LOG = ROOT / "weather_log.csv"
ALERTS = ROOT / "alerts"
ET = ZoneInfo("America/New_York")
WORTH_A_LOOK_PTS = 10
LONGSHOT = 0.10          # never flag a side priced under 10c (longshot rule)
HISTORY_YEARS = 3        # how far back forecasts are read for the bias/spread fit


@dataclass
class Row:
    c: Contract
    p_yes: float
    side: str = "-"
    price: float | None = None
    gap: float | None = None
    ev: float | None = None
    flag: str = "-"


@dataclass
class WxResult:
    series: str
    event_ticker: str
    title: str
    cli: str
    icao: str
    station: str
    kind: str
    day: date
    close: datetime
    as_of: datetime
    fc: nbm.Fc
    fit: model.Fit
    centre: float
    lead: timedelta
    source_note: str = ""
    nws_point: str = ""
    rows: list = field(default_factory=list)


def pick_side(p_yes, c):
    """The side with the bigger gap: (side, price, gap_pts, ev_per_contract)."""
    opts = []
    if c.yes_ask and 0 < c.yes_ask < 1:
        opts.append(("YES", c.yes_ask, (p_yes - c.yes_ask) * 100, fees.expected_value(p_yes, c.yes_ask)))
    if c.no_ask and 0 < c.no_ask < 1:
        opts.append(("NO", c.no_ask, ((1 - p_yes) - c.no_ask) * 100, fees.expected_value(1 - p_yes, c.no_ask)))
    if not opts:
        return "-", None, None, None
    return max(opts, key=lambda o: o[2])


def flag_for(gap, price):
    """WORTH A LOOK needs a 10+ point gap AND a side priced at 10c or more (longshot rule)."""
    if gap is None or gap < WORTH_A_LOOK_PTS:
        return "-"
    if price < LONGSHOT:
        return "LONGSHOT"
    return "WORTH A LOOK"


def price_event(series, event_ticker, contracts, fc_index, truth, t, title="", source_note=""):
    """Everything the model says about one event as of time t. Returns WxResult or (None, reason)."""
    cli, icao, tz, name, kind = stations.info(series)
    day = stations.target_date(event_ticker)
    fc = nbm.forecast_at(fc_index, kind, day, t)
    if fc is None:
        return None, "no NBM forecast published yet for that day"
    lead = nbm.target_column(kind, day) - fc.runtime
    lead_t = nbm.target_column(kind, day) - t
    errs = model.past_errors(fc_index, truth, kind, day, lead_t, t)
    fit = model.fit(errs, fc.xnd)
    centre = fc.txn + fit.bias
    curve = model.TempCurve(centre, fit.spread)
    close = datetime.fromisoformat(contracts[0].close_time.replace("Z", "+00:00"))
    res = WxResult(series, event_ticker, title or series, cli, icao, name, kind, day, close, t, fc, fit,
                   centre, lead, source_note)
    for c in sorted(contracts, key=Contract.sort_key):
        p = model.temp_prob(c, curve.cdf)
        side, price, gap, ev = pick_side(p, c)
        row = Row(c, p, side, price, gap, ev)
        row.flag = flag_for(gap, price) if price is not None else "-"
        res.rows.append(row)
    return res, ""


def log_rows(res, engine_name="weather"):
    stamp = res.as_of.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    for r in res.rows:
        if r.side == "-":
            continue
        p_side = r.p_yes if r.side == "YES" else 1 - r.p_yes
        worth = r.flag == "WORTH A LOOK"
        c = r.c
        ym = ((c.yes_bid or 0) + c.yes_ask) / 2 if c.yes_ask else c.yes_bid
        out.append({
            "logged_at_utc": stamp, "engine": engine_name, "series": res.series,
            "event_ticker": res.event_ticker, "market_ticker": c.ticker, "range_label": c.label,
            "side": r.side, "model_prob": f"{p_side:.4f}", "market_price": f"{r.price:.2f}",
            "gap_pts": f"{r.gap:.1f}", "worth_a_look": "1" if worth else "0",
            "paper_contracts": "1" if worth else "0", "fee_per_contract": f"{fees.taker_fee(r.price):.2f}",
            "anchor_price": f"{res.centre:.1f}",
            "anchor_kind": f"NBM {res.fc.runtime:%Y-%m-%d %HZ} {res.kind} {res.fc.txn:.0f}F + bias {res.fit.bias:+.1f}F at {res.icao}",
            "vol_daily": f"{res.fit.spread:.2f}", "horizon_days": f"{(res.lead.total_seconds() / 86400):.2f}",
            "event_days": "0.00", "news_shift_pct": "0.00", "close_time_utc": c.close_time,
            "notes": (f"shape={c.kind}|{c.low}|{c.high}; flag={r.flag}; p_yes={r.p_yes:.4f}"
                      + (f"; yes_mid={ym:.4f}" if ym is not None else "")
                      + f"; nbm_raw={res.fc.txn:.0f}; nbm_xnd={res.fc.xnd}; bias={res.fit.bias:.2f}"
                      f"; spread={res.fit.spread:.2f}; n_err={res.fit.n}; station={res.icao}"
                      + (f"; nws_point={res.nws_point.split('F')[0]}" if res.nws_point[:1].isdigit() else "")),
        })
    return out


# ---------------- live ----------------

def open_events(series):
    evs = {}
    for m in kalshi.get_markets(series_ticker=series):
        evs.setdefault(m["event_ticker"], m["close_time"])
    return sorted(evs.items(), key=lambda kv: kv[1])


def nws_point_forecast(icao, kind, day):
    """NWS's own public point forecast at the station (shown for reference; not used by the model)."""
    try:
        s = requests.get(f"https://api.weather.gov/stations/{icao}", headers=nbm.UA, timeout=20).json()
        lon, lat = s["geometry"]["coordinates"]
        p = requests.get(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}", headers=nbm.UA, timeout=20).json()
        per = requests.get(p["properties"]["forecast"], headers=nbm.UA, timeout=20).json()["properties"]["periods"]
        want_day = kind == "high"
        for x in per:
            if x["startTime"][:10] == day.isoformat() and x["isDaytime"] == want_day:
                return f"{x['temperature']}F ({x['name']})"
            if not want_day and not x["isDaytime"] and x["startTime"][:10] == (day - timedelta(days=1)).isoformat():
                return f"{x['temperature']}F ({x['name']})"
    except Exception as e:
        return f"n/a ({e.__class__.__name__})"
    return "n/a (day not in NWS 7-day forecast)"


def day_not_started(day, tz, now):
    """Live runs price only days that have not begun at the station (local midnight).
    Once the day is under way the temperature so far is visible to traders and not to this model."""
    start = datetime.combine(day, datetime.min.time(), ZoneInfo(tz))
    return now < start


@lru_cache(maxsize=None)
def _truth(series):
    """Settled history of a series, read once per run (both open events share it)."""
    return settled_by_day(settled_markets(series))


def run_event(event_ticker, now=None):
    series = stations.series_of(event_ticker)
    cli, icao, tz, name, kind = stations.info(series)
    s_meta = kalshi.get_series(series)
    if kalshi.is_fifteen_minute(s_meta):
        raise ValueError("Argus skips 15-minute markets.")
    ms = kalshi.get_markets(event_ticker=event_ticker)
    if not ms:
        raise ValueError(f"No open markets in {event_ticker}")
    named = stations.station_from_rules(ms[0].get("rules_primary"))
    if named != (cli, kind):
        raise ValueError(f"Rules name {named}, Argus expected {(cli, kind)}: update stations.py")
    contracts = [Contract.from_api(m) for m in ms]
    now = now or datetime.now(timezone.utc)
    if not day_not_started(stations.target_date(event_ticker), tz, now):
        raise ValueError("target day already under way: the model has forecasts, not observations, so it skips it")
    fcs, note = nbm.forecasts_for(icao, date(now.year - HISTORY_YEARS, 1, 1), now)
    truth = _truth(series)
    res, why = price_event(series, event_ticker, contracts, nbm.index(fcs), truth, now,
                           s_meta.get("title", series), note)
    if res is None:
        raise RuntimeError(f"{event_ticker}: {why}")
    res.nws_point = nws_point_forecast(icao, kind, res.day)
    return res


# ---------------- alert ----------------

def _c(x):
    return f"{round(x * 100):>3d}c" if x else "   -"


def format_alert(res, now=None):
    now = now or datetime.now(timezone.utc)
    hours = (res.close - now).total_seconds() / 3600
    what = "maximum" if res.kind == "high" else "minimum"
    L = ["==================== ARGUS ALERT ====================",
         f"Market:    {res.event_ticker} - {res.title}",
         f"Closes:    {res.close.astimezone(ET):%a %b %d %I:%M %p ET}   ({hours:.1f} h left)",
         f"Settles:   {what} temp {res.day:%b %d} at {res.station} (CLI{res.cli} / {res.icao}), per The Weather Company",
         f"Forecast:  NOAA NBM run {res.fc.runtime:%b %d %HZ}: {res.fc.txn:.0f}F (NBM's own spread {res.fc.xnd}F)   "
         f"NWS point forecast: {res.nws_point or 'n/a'}",
         f"Bias:      {res.fit.bias:+.1f}F (settled minus NBM at {res.lead.total_seconds() / 3600:.0f} h lead; {res.fit.source})",
         f"Centre:    {res.centre:.1f}F   Spread: {res.fit.spread:.1f}F (stdev of those past misses)",
         "-----------------------------------------------------",
         "Range               Model  Yes ask  No ask   Gap  Side  EV/ct    Flag"]
    for r in res.rows:
        gap = f"{r.gap:+5.1f}" if r.gap is not None else "    -"
        ev = f"${r.ev:+.2f}" if r.ev is not None else "    -"
        label = r.c.label.replace("°", "")
        L.append(f"{label[:18]:<18}  {r.p_yes * 100:4.0f}%   {_c(r.c.yes_ask)}    {_c(r.c.no_ask)}  "
                 f"{gap}  {r.side:<4}  {ev:<7}  {r.flag}")
    L.append("-----------------------------------------------------")
    elig = [r for r in res.rows if r.gap is not None and r.price is not None and r.price >= LONGSHOT]
    best = max(elig, key=lambda r: r.gap, default=None)
    if best:
        p_side = best.p_yes if best.side == "YES" else 1 - best.p_yes
        L.append(f"Top call:  {best.side} \"{best.c.label.replace(chr(176), '')}\" at {round(best.price * 100)}c - model "
                 f"{p_side * 100:.0f}% - gap {best.gap:+.0f} pts - EV after fees ${best.ev:+.2f}/contract")
        L.append("Verdict:   " + ("WORTH A LOOK" if best.flag == "WORTH A LOOK"
                                  else f"NO EDGE (best gap < {WORTH_A_LOOK_PTS} pts)"))
    else:
        L += ["Top call:  none (no side priced 10c or more)", f"Verdict:   NO EDGE"]
    L.append("Rule:      sides priced under 10c are never flagged (LONGSHOT).")
    L.append("Paper only. Argus never trades.")
    L.append("=====================================================")
    return "\n".join(L)


def save_alert(text, res, now=None):
    ALERTS.mkdir(exist_ok=True)
    now = now or datetime.now(timezone.utc)
    path = ALERTS / f"{now.astimezone(ET):%Y-%m-%d_%H%M}_{res.event_ticker}.md"
    path.write_text(f"# Argus weather alert - {res.event_ticker}\n\n```\n{text}\n```\n", encoding="utf-8")
    return path
