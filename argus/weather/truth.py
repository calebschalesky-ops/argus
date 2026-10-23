"""What the markets actually settled on, and the NWS climate report (CLI) they name.

Two sources, both public:
  - Kalshi's own settled markets (`expiration_value`): the exact number each market paid on,
    back to 2021 for NYC. This is the "truth" used for bias and backtests, because it is what
    paid, whatever its upstream source.
  - The NWS Daily Climate Report (CLI) from api.weather.gov/products: used as a cross-check
    when settling (Kalshi's rules say the value comes from The Weather Company, which reports
    the CLI station's max/min).
"""
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import requests

from .stations import target_date

NWS = "https://api.weather.gov"
_session = requests.Session()
_session.headers["User-Agent"] = "argus-research/0.1 (read-only weather research)"
SETTLE_DELAY = timedelta(hours=14)   # fallback: settled ~14 h after the market closes


@dataclass(frozen=True)
class Settled:
    day: date
    value: float
    known_at: datetime   # when the result was public; nothing after this may be used before it


def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def settled_by_day(markets):
    """{day: Settled} from a series' settled Kalshi markets (one value per event)."""
    out = {}
    for m in markets:
        try:
            value = float(m.get("expiration_value"))
        except (TypeError, ValueError):
            continue
        if m.get("settlement_ts"):
            known = _t(m["settlement_ts"])
        else:
            known = _t(m["close_time"]) + SETTLE_DELAY
        d = target_date(m["event_ticker"])
        if d is None:
            continue
        if d not in out or known < out[d].known_at:
            out[d] = Settled(d, value, known)
    return out


def known_before(truth, t):
    """Only results that were already public before time t (the no-look-ahead filter)."""
    return {d: s for d, s in truth.items() if s.known_at < t}


# ---------------- NWS CLI report ----------------

_MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST",
           "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]
_DAY = re.compile(r"CLIMATE SUMMARY FOR\s+([A-Z]+)\s+(\d{1,2})\s+(\d{4})")
_VAL = re.compile(r"^\s*(MAXIMUM|MINIMUM)\s+(-?\d+|M)\b", re.M)


def parse_cli(text):
    """(day, max, min) from an NWS Daily Climate Report. Values are None when missing ('M').

    Only the first MAXIMUM/MINIMUM pair counts: it sits under "YESTERDAY" (or "TODAY" in an
    afternoon preliminary report). Reports that are not a daily summary return None.
    """
    m = _DAY.search(text.upper())
    if not m or m.group(1) not in _MONTHS:
        return None
    day = date(int(m.group(3)), _MONTHS.index(m.group(1)) + 1, int(m.group(2)))
    vals = {}
    for k, v in _VAL.findall(text.upper()):
        vals.setdefault(k, None if v == "M" else float(v))
    return day, vals.get("MAXIMUM"), vals.get("MINIMUM")


def is_preliminary(text):
    """Afternoon CLI reports ('VALID TODAY AS OF ...') are not final."""
    return "VALID TODAY AS OF" in text.upper() or "VALID AS OF" in text.upper()


def nws_cli(cli_code, day, limit=12):
    """The final NWS CLI max/min for a station and day, read from api.weather.gov. None if not found."""
    r = _session.get(f"{NWS}/products", params={"type": "CLI", "location": cli_code, "limit": limit}, timeout=30)
    r.raise_for_status()
    for p in r.json().get("@graph", []):
        t = _session.get(p["@id"], timeout=30).json().get("productText", "")
        got = parse_cli(t)
        if got and got[0] == day and not is_preliminary(t):
            return {"max": got[1], "min": got[2], "issued": p.get("issuanceTime")}
    return None


def utc_now():
    return datetime.now(timezone.utc)
