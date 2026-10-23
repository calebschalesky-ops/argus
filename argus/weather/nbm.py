"""Forecasts: NOAA's National Blend of Models (NBM) station guidance, "NBS" text bulletin.

Plain words: NOAA's National Blend of Models is the forecast the National Weather Service
starts from. Several times a day NOAA publishes, for each weather station, the forecast
daytime high and overnight low (row TXN) and how unsure it is (row XND, a standard
deviation in degrees F). That is exactly the number the Kalshi markets ask about, at
exactly the settlement station, so it is the forecast Argus uses.

Two routes to the same official NOAA product:
  - LIVE: NOAA NOMADS, nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/ (official;
    keeps only the last ~2 days).
  - HISTORY: Iowa Environmental Mesonet (Iowa State University) keeps the archive of the
    same NBS bulletins (mesonet.agron.iastate.edu/cgi-bin/request/mos.py). NOAA itself
    offers no free archive of the text bulletin (checked 2026-10-22: NOMADS 2 days only,
    the NOAA open-data bucket noaa-nbm-pds has no text/ folder).

Which column is the high and which is the low (all U.S. stations are UTC-4 to UTC-8):
  - high for local day D = TXN valid at 00 UTC on D+1 (the max of D's daytime);
  - low  for local day D = TXN valid at 12 UTC on D   (the min of the night into D).
NBM's daytime max is 7am-7pm local; Kalshi settles on the midnight-to-midnight max/min.
The two usually agree; the per-station bias correction absorbs the average difference.
"""
import csv
import io
import json
import re
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE = ROOT / "data" / "weather_cache"
IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/mos.py"
NOMADS = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod"
AVAIL_LAG = timedelta(hours=2)   # a run is treated as usable 2 h after its nominal time
UA = {"User-Agent": "argus-research/0.1 (read-only weather research)"}

_session = requests.Session()
_session.headers.update(UA)


@dataclass(frozen=True)
class Fc:
    runtime: datetime   # UTC, when NOAA ran the forecast
    ftime: datetime     # UTC, the time the TXN value is valid for
    txn: float          # forecast max (00Z column) or min (12Z column), deg F
    xnd: float | None   # NBM's own spread for that value, deg F


def _utc(s):
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def target_column(kind, day):
    """UTC time of the TXN column that forecasts `kind` ('high'/'low') for local day `day`."""
    if kind == "high":
        return datetime.combine(day + timedelta(days=1), datetime.min.time(), timezone.utc)
    return datetime.combine(day, datetime.min.time(), timezone.utc) + timedelta(hours=12)


def forecast_at(fcs, kind, day, as_of, lag=AVAIL_LAG):
    """The latest forecast for (kind, day) that was already published at time `as_of`.

    This is the no-look-ahead rule: a run counts only if runtime + lag <= as_of.
    `fcs` is a dict ftime -> list of Fc (see `index`).
    """
    col = target_column(kind, day)
    best = None
    for f in fcs.get(col, ()):
        if f.runtime + lag <= as_of and (best is None or f.runtime > best.runtime):
            best = f
    return best


def index(fc_list):
    out = {}
    for f in fc_list:
        out.setdefault(f.ftime, []).append(f)
    return out


# ---------------- history (IEM archive of the NBS bulletin) ----------------

def parse_iem_csv(text):
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        if not r.get("txn"):
            continue
        try:
            txn = float(r["txn"])
        except ValueError:
            continue
        xnd = float(r["xnd"]) if r.get("xnd") not in (None, "") else None
        out.append(Fc(_utc(r["runtime"]), _utc(r["ftime"]), txn, xnd))
    return out


def _iem_year(icao, year):
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / ".gitignore").write_text("*\n")
    f = CACHE / f"nbs_{icao}_{year}.json"
    this_year = year >= datetime.now(timezone.utc).year
    if f.exists() and (not this_year or _time.time() - f.stat().st_mtime < 3 * 3600):
        return [Fc(_utc(a), _utc(b), c, d) for a, b, c, d in json.loads(f.read_text())]
    params = {"station": icao, "model": "NBS", "sts": f"{year}-01-01T00:00Z",
              "ets": f"{year + 1}-01-01T00:00Z", "format": "csv"}
    for attempt in range(8):
        r = _session.get(IEM_URL, params=params, timeout=180)
        if r.status_code == 200 and r.text.startswith("runtime"):
            break
        _time.sleep(5 * (attempt + 1))          # IEM asks callers to slow down
    else:
        raise RuntimeError(f"IEM NBS archive did not answer for {icao} {year}: {r.status_code} {r.text[:80]}")
    fcs = parse_iem_csv(r.text)
    f.write_text(json.dumps([(x.runtime.strftime("%Y-%m-%d %H:%M:%S"), x.ftime.strftime("%Y-%m-%d %H:%M:%S"),
                              x.txn, x.xnd) for x in fcs]))
    _time.sleep(2)
    return fcs


def history(icao, start, end):
    """Every archived NBS max/min forecast for a station with runtime in [start year, end year]."""
    out = []
    for y in range(start.year, end.year + 1):
        out.extend(_iem_year(icao, y))
    return out


# ---------------- live (NOAA NOMADS) ----------------

def parse_nbs_block(text, icao):
    """Read one station's TXN/XND rows out of a NOMADS NBS bulletin (fixed-width text)."""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith(icao + " ") and "NBS GUIDANCE" in ln:
            start = i
            break
    if start is None:
        return []
    head = lines[start]
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{2})00 UTC", head)
    run = datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)), int(m.group(4)), tzinfo=timezone.utc)
    block = {}
    for ln in lines[start + 1:start + 40]:
        key = ln[:5].strip()
        if key in ("FHR", "TXN", "XND") and key not in block:
            block[key] = ln
        if "NBS GUIDANCE" in ln:
            break
    if "FHR" not in block or "TXN" not in block:
        return []

    def cols(line):
        # values are right-aligned in 3-character columns; key them by the column's end position
        return {mm.end(): mm.group() for mm in re.finditer(r"-?\d+", line[5:])}
    fhr = cols(block["FHR"])
    txn = cols(block["TXN"])
    xnd = cols(block.get("XND", ""))
    out = []
    for pos, v in txn.items():
        if pos in fhr:
            x = xnd.get(pos)
            out.append(Fc(run, run + timedelta(hours=int(fhr[pos])), float(v), float(x) if x else None))
    return out


def _nomads_cycles(now=None):
    """Newest-first (day, hour) NBM cycles to try on NOMADS."""
    now = now or datetime.now(timezone.utc)
    out = []
    for back in range(0, 48):
        t = now - timedelta(hours=back)
        out.append((t.strftime("%Y%m%d"), f"{t.hour:02d}"))
    return out


def live(icao, now=None, max_files=2):
    """Latest NBS forecasts for a station straight from NOAA NOMADS (official).

    Each bulletin is ~30 MB (every station worldwide), so it is cached per cycle.
    Returns (forecasts, source_note). Reads up to `max_files` recent bulletins.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    got, notes = [], []
    for day, hh in _nomads_cycles(now):
        if hh not in ("00", "01", "06", "07", "12", "13", "18", "19"):
            continue
        name = f"blend_nbstx.t{hh}z"
        f = CACHE / f"nomads_{day}_{name}"
        if not f.exists():
            r = _session.get(f"{NOMADS}/blend.{day}/{hh}/text/{name}", timeout=300)
            if r.status_code != 200:
                continue
            f.write_bytes(r.content)
            for old in sorted(CACHE.glob("nomads_*"))[:-6]:   # keep the cache small
                old.unlink(missing_ok=True)
        fcs = parse_nbs_block(f.read_text(encoding="latin-1"), icao)
        if fcs:
            got.extend(fcs)
            notes.append(f"NOMADS blend.{day}/{hh}z")
        if len(notes) >= max_files:
            break
    return got, ", ".join(notes)


def forecasts_for(icao, start, now=None, use_live=True):
    """History from the archive plus the newest bulletins from NOMADS, de-duplicated."""
    now = now or datetime.now(timezone.utc)
    fcs = {(f.runtime, f.ftime): f for f in history(icao, start, now.date())}
    note = "IEM archive"
    if use_live:
        try:
            lv, note_live = live(icao, now)
            for f in lv:
                fcs[(f.runtime, f.ftime)] = f
            note = (note_live or "NOMADS unavailable") + " + IEM archive"
        except requests.RequestException as e:
            note = f"IEM archive only (NOMADS failed: {e.__class__.__name__})"
    return list(fcs.values()), note
