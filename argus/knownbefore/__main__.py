"""python -m argus.knownbefore <command>

  scan                          step 1: find Kalshi series whose answer is visible before close
                                -> data/knownbefore/scan_<date>.csv (research/known_before_close.md is written from it)
  temp-backtest [--since D] [--until D] [--series S,S]
                                observed-temp backtest -> knownbefore_log.csv (engine=observed-temp)
  temp-report                   print the observed-temp report from the log
  temp-live [--series S,S]      price TODAY's open events from api.weather.gov reports so far (engine=observed-temp-live)
  temp-settle                   fill results of pending observed-temp-live rows from Kalshi
  depth [--series S,S]          live top-of-book size on today's open temperature markets
  rain-backtest / rain-report   KXRAIN "will it rain today" from the gauge so far (engine=observed-rain)
  gas-backtest / gas-report     AAA national gas ladders from the last AAA print (engine=observed-gas)

Paper only. Argus never trades.
"""
import argparse
import sys
from datetime import date, timedelta

from .. import log
from ..weather import stations
from . import LOG_PATH


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m argus.knownbefore")
    ap.add_argument("command")
    ap.add_argument("--since", default="2026-05-01")
    ap.add_argument("--until", default=None)
    ap.add_argument("--series", default=None)
    a = ap.parse_args(argv)
    until = date.fromisoformat(a.until) if a.until else date.today() - timedelta(days=1)
    since = date.fromisoformat(a.since)
    series = a.series.split(",") if a.series else None

    if a.command == "temp-backtest":
        from . import temp
        rows, skipped = temp.run(series or stations.LIQUID, since, until)
        print(temp.report(rows, skipped))
    elif a.command == "temp-report":
        from . import temp
        rows = [r for r in log.read_log(LOG_PATH) if r["engine"] == temp.ENGINE]
        if series:
            rows = [r for r in rows if r["series"] in series]
        print(temp.report(rows))
    elif a.command == "temp-live":
        from . import temp
        rows = temp.live(series or stations.LIQUID)
        print(f"{len(rows)} calls logged, {sum(r['paper_contracts'] == '1' for r in rows)} paper bets.")
    elif a.command == "temp-settle":
        from . import temp
        print(f"Settled {temp.live_settle()} rows.")
    elif a.command == "depth":
        from . import depth
        print(depth.report(depth.snapshot(series or stations.LIQUID)))
    elif a.command == "scan":
        from . import scan
        print(scan.report(scan.run()))
    elif a.command in ("rain-backtest", "gas-backtest"):
        from . import gas, rain
        mod = rain if a.command.startswith("rain") else gas
        print(mod.report(mod.run(since, until)))
    elif a.command in ("rain-report", "gas-report"):
        from . import gas, rain
        mod = rain if a.command.startswith("rain") else gas
        print(mod.report([r for r in log.read_log(LOG_PATH) if r["engine"] == mod.ENGINE]))
    else:
        ap.print_help()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
