"""Official temperature observations, and the climate day they count toward.

Sources (public, no key):
  - METAR reports from the station itself (the same ASOS that the NWS climate report is built
    from). Live: api.weather.gov/stations/{ICAO}/observations (about a week kept). History: Iowa
    State's archive of the same reports (mesonet.agron.iastate.edu, asos.py), raw text, so the
    tenths-of-a-degree "T" group and the 6-hour max/min groups are read directly.
  - The NWS Daily Climate Report (CLI) final max/min, the number Kalshi settles on (via The Weather
    Company; it matched the CLI on 3,834 of 3,841 station-days, research/weather_engine.md).
    History: Iowa State's archive of the CLI products (json/cli.py).

The climate day is midnight to midnight LOCAL STANDARD TIME, all year (NWS rule). In summer
that is 1 AM to 1 AM on the wall clock, which is why Kalshi's markets close at 00:59 daylight time.
"""
import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import http

IEM = "https://mesonet.agron.iastate.edu"
LAG = timedelta(minutes=20)      # a report counts only 20 min after its time (publication delay)

_T = re.compile(r"\bT([01])(\d{3})([01])(\d{3})\b")
_BODY = re.compile(r"\s(M?\d{2})/(M?\d{2})?\s")
_MAX6 = re.compile(r"(?<=\s)1([01])(\d{3})(?=\s|$)")
_MIN6 = re.compile(r"(?<=\s)2([01])(\d{3})(?=\s|$)")
_P1 = re.compile(r"(?<=\s)P(\d{4})(?=\s|$)")          # precip since the last hourly report, 0.01 in
_P6 = re.compile(r"(?<=\s)6(\d{4})(?=\s|$)")          # 3- or 6-hour precip group, 0.01 in
_WX = re.compile(r"(?<=\s)(?:\+|-|VC)?(?:TS|SH|FZ)?(?:RA|DZ|SN|PL|GR|GS|UP|SG)+(?=\s)")
_SKY = re.compile(r"(?<=\s)(?:OVC|BKN)\d{3}")


@dataclass(frozen=True)
class Ob:
    t: datetime              # report time, UTC
    temp: float | None       # degrees F (from tenths of C when the T group is there)
    max6: float | None = None   # 6-hour max ending at t, degrees F
    min6: float | None = None
    precip: float | None = None   # inches: the larger of the hourly (P) and 3/6-hour (6) groups; 0 = trace
    wet: bool = False             # precipitation falling at report time (RA, DZ, SN, TS... in the body)
    cloudy: bool = False          # broken or overcast sky


def c_to_f(c):
    return c * 9 / 5 + 32


def _signed(sign, digits):
    v = int(digits) / 10
    return -v if sign == "1" else v


def parse_metar(valid_utc, text):
    """One Ob from a raw METAR line. None if it carries no temperature at all."""
    t = datetime.strptime(valid_utc, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    rmk = text.split(" RMK ", 1)[1] if " RMK " in text else ""
    temp = None
    m = _T.search(rmk)
    if m:
        temp = c_to_f(_signed(m.group(1), m.group(2)))
    else:
        b = _BODY.search(" " + text.split(" RMK ", 1)[0] + " ")
        if b:
            raw = b.group(1)
            temp = c_to_f(-int(raw[1:]) if raw.startswith("M") else int(raw))
    mx = _MAX6.search(" " + rmk + " ")
    mn = _MIN6.search(" " + rmk + " ")
    max6 = c_to_f(_signed(mx.group(1), mx.group(2))) if mx else None
    min6 = c_to_f(_signed(mn.group(1), mn.group(2))) if mn else None
    body = " " + text.split(" RMK ", 1)[0] + " "
    p1, p6 = _P1.search(" " + rmk + " "), _P6.search(" " + rmk + " ")
    precip = max([int(g.group(1)) / 100 for g in (p1, p6) if g] or [None]) if (p1 or p6) else None
    wet, cloudy = bool(_WX.search(body)), bool(_SKY.search(body))
    if temp is None and max6 is None and min6 is None and precip is None:
        return None
    return Ob(t, temp, max6, min6, precip, wet, cloudy)


def iem_metars(icao, start, end, today=None):
    """Raw METARs (routine + specials) for a station from Iowa State's archive, [start, end) dates.

    Cached when the whole range is in the past (it can no longer change).
    """
    today = today or datetime.now(timezone.utc).date()
    end = min(end, today)              # up to the end of yesterday, so the answer can be cached
    params = [("station", icao[1:] if icao.startswith("K") else icao), ("data", "metar"),
              ("year1", start.year), ("month1", start.month), ("day1", start.day),
              ("year2", end.year), ("month2", end.month), ("day2", end.day),
              ("tz", "Etc/UTC"), ("format", "onlycomma"), ("latlon", "no"),
              ("report_type", 3), ("report_type", 4)]
    text = http.public(f"{IEM}/cgi-bin/request/asos.py", params, cache=True, as_text=True)
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        ob = parse_metar(row["valid"], row.get("metar") or "")
        if ob:
            out.append(ob)
    return sorted(out, key=lambda o: o.t)


def iem_cli_full(icao, year, today=None):
    """{day: {"high", "low", "precip"}} from Iowa State's archive of the NWS CLI report, one station-year.

    Precip "T" (trace) is 0.0, as in Kalshi's rain rules; a missing value is None.
    """
    today = today or datetime.now(timezone.utc).date()
    data = http.public(f"{IEM}/json/cli.py", {"station": icao, "year": year}, cache=year < today.year)
    f = lambda x: float(x) if isinstance(x, (int, float)) else (0.0 if x == "T" else None)
    return {date.fromisoformat(r["valid"]): {"high": f(r.get("high")), "low": f(r.get("low")),
                                             "precip": f(r.get("precip"))}
            for r in (data or {}).get("results", [])}


def iem_cli(icao, year, today=None):
    """{day: (high, low)} from Iowa State's archive of the NWS CLI report for one station-year."""
    return {d: (v["high"], v["low"]) for d, v in iem_cli_full(icao, year, today).items()}


def nws_live_obs(icao, start):
    """Live METARs from api.weather.gov since `start` (UTC). Used by the live command only."""
    data = http.public(f"https://api.weather.gov/stations/{icao}/observations",
                       {"start": start.strftime("%Y-%m-%dT%H:%M:%SZ")})
    out = []
    for f in (data or {}).get("features", []):
        p = f.get("properties", {})
        raw = p.get("rawMessage") or ""
        t = datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00"))
        ob = parse_metar(t.strftime("%Y-%m-%d %H:%M"), raw) if raw else None
        if ob is None and (p.get("temperature") or {}).get("value") is not None:
            ob = Ob(t, c_to_f(p["temperature"]["value"]))
        if ob:
            out.append(ob)
    return sorted(out, key=lambda o: o.t)


# ---------------- the climate day ----------------

def climate_day(day, tz):
    """[start, end) in UTC of the NWS climate day: midnight to midnight local standard time."""
    off = datetime(day.year, 1, 15, tzinfo=ZoneInfo(tz)).utcoffset()
    start = datetime.combine(day, time(0), timezone(off)).astimezone(timezone.utc)
    return start, start + timedelta(days=1)


def wall_clock(day, hour, tz):
    """`hour`:00 on the station's wall clock (daylight time when in force), in UTC."""
    return datetime.combine(day, time(hour), ZoneInfo(tz)).astimezone(timezone.utc)


@dataclass(frozen=True)
class State:
    """What was observable at time t about one climate day."""
    t: datetime
    hi: float          # max so far (F)
    lo: float          # min so far (F)
    now: float         # latest temperature (F)
    trend: float       # latest minus the reading about 2 hours earlier (F)
    n: int             # reports used
    last_t: datetime   # time of the latest report used


def state_at(obs, day, tz, t, lag=LAG):
    """Max/min so far for `day` using only reports public by time t. None if too few reports.

    A report counts only if its time is inside the climate day and at least `lag` before t.
    A 6-hour max/min group counts only if its whole 6 hours lie inside the climate day.
    """
    start, end = climate_day(day, tz)
    cut = min(t - lag, end)
    use = [o for o in obs if start < o.t <= cut]
    temps = [o for o in use if o.temp is not None]
    if len(temps) < 3 or cut - temps[-1].t > timedelta(hours=2):
        return None
    hi = max(o.temp for o in temps)
    lo = min(o.temp for o in temps)
    # The day starts between two hourly reports; the temperature at midnight LST counts toward the
    # day (measured 2026-10-22: NYC's 62F high on Oct 17 was set at 12:08 AM LST while the first
    # report of the day said 57.9F). Estimate it by straight-line between the reports either side.
    before = [o for o in obs if o.temp is not None and start - timedelta(minutes=90) <= o.t <= start]
    if before:
        a, b = before[-1], temps[0]
        w = (start - a.t) / (b.t - a.t) if b.t > a.t else 0
        at_start = a.temp + (b.temp - a.temp) * w
        hi, lo = max(hi, at_start), min(lo, at_start)
    for o in use:
        if o.t - timedelta(hours=6) >= start - timedelta(minutes=10):
            if o.max6 is not None:
                hi = max(hi, o.max6)
            if o.min6 is not None:
                lo = min(lo, o.min6)
    last = temps[-1]
    earlier = [o for o in temps if o.t <= last.t - timedelta(minutes=100)]
    trend = last.temp - earlier[-1].temp if earlier else 0.0
    return State(t, hi, lo, last.temp, trend, len(temps), last.t)
