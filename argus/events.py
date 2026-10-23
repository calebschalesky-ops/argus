"""Scheduled events that make oil jump, and how much each one widens the odds.

Weights are in "extra normal trading days" of movement. Example: an OPEC+ meeting
worth 1.5 means the model spreads the curve as if 1.5 extra days of normal trading
happened. Edit data/events.csv to add or correct one-off events.
"""
import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .model import ET

DATA = Path(__file__).resolve().parent.parent / "data" / "events.csv"

# Federal holidays that move the weekly EIA report (extend each year).
FEDERAL_HOLIDAYS = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 5, 25),
    date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 10, 12),
    date(2026, 11, 11), date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15),
}

DEFAULT_WEIGHTS = {"eia": 0.5, "rigs": 0.1, "opec": 1.5, "fomc": 0.5,
                   "cpi": 0.3, "jobs": 0.4, "election": 0.5, "other": 0.3}


@dataclass
class Event:
    when: datetime      # New York time
    name: str
    kind: str
    weight: float
    confirmed: bool = True
    source: str = ""


def _recurring(start, end):
    """Weekly EIA petroleum report and Baker Hughes rig count between two dates."""
    out = []
    d = start.date() - timedelta(days=7)
    while d <= end.date() + timedelta(days=1):
        if d.weekday() == 2:   # Wednesday
            monday = d - timedelta(days=2)
            eia_day = d + timedelta(days=1) if (monday in FEDERAL_HOLIDAYS or d in FEDERAL_HOLIDAYS) else d
            out.append(Event(datetime.combine(eia_day, time(10, 30), ET),
                             "EIA weekly petroleum report", "eia", DEFAULT_WEIGHTS["eia"],
                             source="eia.gov/petroleum/supply/weekly (schedule)"))
        if d.weekday() == 4:   # Friday
            thanksgiving_week = any(h.month == 11 and 0 <= (h - d).days <= 1 for h in FEDERAL_HOLIDAYS)
            rig_day = d - timedelta(days=2) if thanksgiving_week else d
            out.append(Event(datetime.combine(rig_day, time(13, 0), ET),
                             "Baker Hughes rig count", "rigs", DEFAULT_WEIGHTS["rigs"],
                             source="rigcount.bakerhughes.com"))
        d += timedelta(days=1)
    return out


def load_one_offs(path=DATA):
    out = []
    if not Path(path).exists():
        return out
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row.get("date_et"):
                continue
            hh, mm = (row.get("time_et") or "12:00").split(":")
            when = datetime.combine(date.fromisoformat(row["date_et"]), time(int(hh), int(mm)), ET)
            kind = row.get("kind", "other")
            weight = float(row["weight"]) if row.get("weight") else DEFAULT_WEIGHTS.get(kind, 0.3)
            out.append(Event(when, row["name"], kind, weight,
                             row.get("confirmed", "yes").lower() == "yes", row.get("source", "")))
    return out


def events_between(start, end, path=DATA):
    """Every scheduled event after `start` and before `end` (a market's close), in time order."""
    evs = _recurring(start, end) + load_one_offs(path)
    return sorted((e for e in evs if start < e.when < end), key=lambda e: e.when)


def event_days(evs):
    return sum(e.weight for e in evs)
