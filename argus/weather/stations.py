"""Which Kalshi weather series Argus prices, and the station each one settles on.

Every daily temperature market names its station in the rules text, e.g.
"the maximum temperature recorded at New York City (CLINYC) for Oct 23, 2026".
CLINYC is the NWS Climate Report (CLI) for NYC; the station is KNYC (Central Park).
The table below was read from the rules text of the open markets on 2026-10-22,
and `station_from_rules` re-reads it from every market so a change is caught.
"""
import re
from datetime import date

# CLI code -> (ICAO station, time zone, plain name)
STATIONS = {
    "NYC": ("KNYC", "America/New_York", "New York City (Central Park)"),
    "MIA": ("KMIA", "America/New_York", "Miami Intl Airport"),
    "LAX": ("KLAX", "America/Los_Angeles", "Los Angeles Intl Airport"),
    "MDW": ("KMDW", "America/Chicago", "Chicago Midway"),
    "ATL": ("KATL", "America/New_York", "Atlanta Hartsfield"),
    "PHL": ("KPHL", "America/New_York", "Philadelphia Intl"),
    "PHX": ("KPHX", "America/Phoenix", "Phoenix Sky Harbor"),
    "AUS": ("KAUS", "America/Chicago", "Austin Bergstrom"),
    "DEN": ("KDEN", "America/Denver", "Denver Intl"),
    "SEA": ("KSEA", "America/Los_Angeles", "Seattle-Tacoma"),
    "MSP": ("KMSP", "America/Chicago", "Minneapolis-St Paul"),
    "BOS": ("KBOS", "America/New_York", "Boston Logan"),
    "SFO": ("KSFO", "America/Los_Angeles", "San Francisco Intl"),
    "DFW": ("KDFW", "America/Chicago", "Dallas-Fort Worth"),
    "HOU": ("KHOU", "America/Chicago", "Houston Hobby"),
    "MSY": ("KMSY", "America/Chicago", "New Orleans Armstrong"),
    "OKC": ("KOKC", "America/Chicago", "Oklahoma City Will Rogers"),
    "DCA": ("KDCA", "America/New_York", "Washington Reagan National"),
    "SAT": ("KSAT", "America/Chicago", "San Antonio Intl"),
    "LAS": ("KLAS", "America/Los_Angeles", "Las Vegas Harry Reid"),
    "SAN": ("KSAN", "America/Los_Angeles", "San Diego Lindbergh"),
    "EWR": ("KEWR", "America/New_York", "Newark Liberty"),
    "TTN": ("KTTN", "America/New_York", "Trenton Mercer"),
    "SDF": ("KSDF", "America/New_York", "Louisville Muhammad Ali"),
}

# Kalshi series -> (CLI code, "high" or "low"). US daily temperature series with open markets
# on 2026-10-22 (research/weather_markets.md). Rain, snow, weekly/monthly and foreign cities are not priced.
SERIES = {
    "KXHIGHNY": ("NYC", "high"), "KXHIGHLAX": ("LAX", "high"), "KXHIGHMIA": ("MIA", "high"),
    "KXHIGHCHI": ("MDW", "high"), "KXHIGHTATL": ("ATL", "high"), "KXHIGHPHIL": ("PHL", "high"),
    "KXHIGHTPHX": ("PHX", "high"), "KXHIGHAUS": ("AUS", "high"), "KXHIGHDEN": ("DEN", "high"),
    "KXHIGHTSEA": ("SEA", "high"), "KXHIGHTMIN": ("MSP", "high"), "KXHIGHTBOS": ("BOS", "high"),
    "KXHIGHTSFO": ("SFO", "high"), "KXHIGHTDAL": ("DFW", "high"), "KXHIGHTHOU": ("HOU", "high"),
    "KXHIGHTNOLA": ("MSY", "high"), "KXHIGHTOKC": ("OKC", "high"), "KXHIGHTDC": ("DCA", "high"),
    "KXHIGHTSATX": ("SAT", "high"), "KXHIGHTSAN": ("SAN", "high"), "KXHIGHTEWR": ("EWR", "high"),
    "KXHIGHTTTN": ("TTN", "high"), "KXHIGHTSDF": ("SDF", "high"),
    "KXLOWTNYC": ("NYC", "low"), "KXLOWTPHIL": ("PHL", "low"), "KXLOWTNOLA": ("MSY", "low"),
    "KXLOWTOKC": ("OKC", "low"), "KXLOWTATL": ("ATL", "low"), "KXLOWTTTN": ("TTN", "low"),
    "KXLOWTMIN": ("MSP", "low"), "KXLOWTAUS": ("AUS", "low"), "KXLOWTEWR": ("EWR", "low"),
    "KXLOWTHOU": ("HOU", "low"), "KXLOWTDAL": ("DFW", "low"), "KXLOWTCHI": ("MDW", "low"),
    "KXLOWTDC": ("DCA", "low"), "KXLOWTSATX": ("SAT", "low"), "KXLOWTBOS": ("BOS", "low"),
    "KXLOWTMIA": ("MIA", "low"), "KXLOWTSDF": ("SDF", "low"), "KXLOWTSFO": ("SFO", "low"),
    "KXLOWTLV": ("LAS", "low"), "KXLOWTLAX": ("LAX", "low"), "KXLOWTSEA": ("SEA", "low"),
}

# The liquid ones (24h volume over ~10k contracts on 2026-10-22): the default backtest set.
LIQUID = ["KXHIGHNY", "KXHIGHLAX", "KXHIGHMIA", "KXHIGHCHI", "KXHIGHTATL", "KXHIGHPHIL",
          "KXHIGHTPHX", "KXHIGHAUS", "KXHIGHDEN", "KXHIGHTSEA", "KXLOWTNYC", "KXLOWTPHIL"]

_RULE = re.compile(r"(maximum|minimum) temperature recorded at (.+?) \(CLI([A-Z]{3})\)", re.I)
_DAYCODE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(?:-|$)")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def station_from_rules(rules):
    """('NYC', 'high') from a market's rules text, or None if it does not name a CLI station."""
    m = _RULE.search(rules or "")
    if not m:
        return None
    return m.group(3).upper(), ("high" if m.group(1).lower() == "maximum" else "low")


def target_date(event_ticker):
    """The day a market is about: KXHIGHNY-26OCT23 -> 2026-10-23 (old tickers HIGHNY-21AUG06 too). None if unreadable."""
    m = _DAYCODE.search(event_ticker.upper())
    if not m or m.group(2) not in _MONTHS:
        return None                      # odd old tickers (e.g. HIGHCHI-2-24FEB28 is fine; junk is not)
    return date(2000 + int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3)))


def series_of(event_ticker):
    return event_ticker.split("-")[0]


def info(series):
    """(cli, icao, tz, name, kind) for a series Argus prices."""
    if series not in SERIES:
        raise ValueError(f"{series} is not a weather series Argus prices. Run `python -m argus.weather markets`.")
    cli, kind = SERIES[series]
    icao, tz, name = STATIONS[cli]
    return cli, icao, tz, name, kind
