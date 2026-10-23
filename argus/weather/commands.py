"""Command line for the weather engine: python -m argus.weather <command>

  markets              open Kalshi temperature events Argus prices, with volume and station
  run <event>          price one event (e.g. KXHIGHNY-26OCT23), print + save the alert, log the calls
  run --all-liquid     run every open event of the liquid series
  settle --auto        settle pending rows of weather_log.csv from Kalshi's results (NWS CLI cross-check)
  backtest             replay past settled events -> backtest_weather_log.csv
  backtest --report-only
Paper only. Argus never trades.
"""
import argparse
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

import requests

from .. import kalshi, log
from . import backtest, engine, stations
from .truth import nws_cli


def _out(s):
    try:
        print(s, flush=True)
    except UnicodeEncodeError:
        print(s.encode("ascii", "replace").decode(), flush=True)


def cmd_markets(a):
    series = stations.LIQUID if not a.all else list(stations.SERIES)
    _out(f"{'Event':<22} {'Station':<6} {'Kind':<4} {'Closes (UTC)':<20} {'Vol 24h':>9}  Ranges")
    for s in series:
        try:
            ms = kalshi.get_markets(series_ticker=s)
        except Exception as e:
            _out(f"{s:<22} error {e.__class__.__name__}")
            continue
        by = defaultdict(list)
        for m in ms:
            by[m["event_ticker"]].append(m)
        cli, icao, tz, name, kind = stations.info(s)
        for e, xs in sorted(by.items()):
            vol = sum(float(m.get("volume_24h_fp") or 0) for m in xs)
            _out(f"{e:<22} {icao:<6} {kind:<4} {xs[0]['close_time']:<20} {vol:>9.0f}  {len(xs)}")


def _patient(fn, *args, tries=5):
    """Call fn, waiting out Kalshi's rate limit (HTTP 429) when other sessions are reading too."""
    for attempt in range(tries):
        try:
            return fn(*args)
        except requests.HTTPError as e:
            if attempt == tries - 1 or e.response is None or e.response.status_code != 429:
                raise
            time.sleep(15 * (attempt + 1))


def _run_one(ev, no_log=False):
    res = _patient(engine.run_event, ev)
    text = engine.format_alert(res)
    _out(text)
    path = engine.save_alert(text, res)
    if not no_log:
        rows = engine.log_rows(res)
        log.append(rows, engine.WEATHER_LOG)
        _out(f"Saved {path.name}; logged {len(rows)} calls to {engine.WEATHER_LOG.name}")
    return res


def cmd_run(a):
    if a.all_liquid:
        evs = []
        for s in stations.LIQUID:
            evs += [e for e, _ in _patient(engine.open_events, s)]
    else:
        evs = a.event
    if not evs:
        sys.exit("Give an event ticker (see `markets`) or --all-liquid")
    for ev in evs:
        try:
            _run_one(ev, a.no_log)
        except Exception as e:
            _out(f"{ev}: skipped ({e})")


def cmd_settle(a):
    rows = log.read_log(engine.WEATHER_LOG)
    pending = sorted({r["event_ticker"] for r in rows if r["status"] != "settled"})
    if not pending:
        _out("Nothing pending.")
        return
    for ev in pending:
        ms = kalshi.get_markets(event_ticker=ev, status=None)
        done = [m for m in ms if m.get("result") in ("yes", "no")]
        if not done:
            _out(f"{ev}: not settled yet")
            continue
        try:
            value = float(done[0].get("expiration_value"))
        except (TypeError, ValueError):
            value = None
        n = log.settle_event(ev, value, {m["ticker"]: m["result"] == "yes" for m in done}, engine.WEATHER_LOG)
        check = ""
        try:
            cli, icao, tz, name, kind = stations.info(stations.series_of(ev))
            got = nws_cli(cli, stations.target_date(ev))
            if got:
                nws = got["max" if kind == "high" else "min"]
                check = f"; NWS CLI says {nws:.0f}F" + (" (agrees)" if nws == value else " (DIFFERENT)")
        except Exception as e:
            check = f"; NWS CLI check failed ({e.__class__.__name__})"
        _out(f"{ev}: settled {n} rows at {value}{check}")


def cmd_backtest(a):
    if a.report_only:
        _out(backtest.report(log.read_log(a.out)))
        return
    rows, skipped = backtest.run(a.series, a.since, a.until, a.out, a.hour)
    _out(f"\nWrote {len(rows)} replayed calls to {a.out}\n")
    _out(backtest.report(rows, skipped))


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m argus.weather", description="Argus weather engine (paper only).")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("markets", help="open temperature events Argus prices")
    m.add_argument("--all", action="store_true", help="every priced series, not just the liquid ones")
    r = sub.add_parser("run", help="price one event, print + save the alert, log the calls")
    r.add_argument("event", nargs="*")
    r.add_argument("--all-liquid", action="store_true")
    r.add_argument("--no-log", action="store_true")
    s = sub.add_parser("settle", help="settle weather_log.csv from Kalshi results")
    s.add_argument("--auto", action="store_true", required=True)
    b = sub.add_parser("backtest", help="replay past settled events")
    b.add_argument("--series", nargs="*", default=stations.LIQUID, choices=list(stations.SERIES))
    b.add_argument("--since", default=backtest.DEFAULT_SINCE)
    b.add_argument("--until", default=None)
    b.add_argument("--hour", type=int, default=backtest.REPLAY_HOUR_ET, help="replay hour, New York time, day before")
    b.add_argument("--out", default=str(backtest.BACKTEST_LOG))
    b.add_argument("--report-only", action="store_true")
    a = p.parse_args(argv)
    {"markets": cmd_markets, "run": cmd_run, "settle": cmd_settle, "backtest": cmd_backtest}[a.cmd](a)
