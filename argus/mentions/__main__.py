"""python -m argus.mentions <command>

  scan        map every Mentions series (volume, repeats, Yes rate, live spreads)
  fedcheck    match Fed transcripts against Kalshi's results (word-matcher check)
  backtest    replay settled events -> mentions_log.csv  [--series S ...] [--top N] [--max-events N]
  report      strategy and price-bucket tables from mentions_log.csv
Read-only and paper only: Argus never trades.
"""
import argparse

from . import backtest, report, scan, validate


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m argus.mentions")
    ap.add_argument("command", choices=["scan", "fedcheck", "backtest", "report"])
    ap.add_argument("--refresh", action="store_true", help="re-read the market lists from Kalshi")
    ap.add_argument("--series", nargs="*")
    ap.add_argument("--max-events", type=int, help="most recent N settled events per series")
    ap.add_argument("--top", type=int, help="only the N most traded series (8+ settled events)")
    a = ap.parse_args(argv)
    if a.command == "scan":
        scan.run(refresh=a.refresh)
    elif a.command == "fedcheck":
        validate.main()
    elif a.command == "backtest":
        backtest.run(series=a.series, max_events=a.max_events, top=a.top)
        report.main()
    else:
        report.main()


if __name__ == "__main__":
    main()
