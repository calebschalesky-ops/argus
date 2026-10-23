"""python -m argus.decay <command>

  collect      list settled by-date markets and fetch their daily prices (cached; resumable)
  backtest     replay every variant from the cached data, print the report, write decay_log.csv
  live         list today's open by-date markets that qualify (paper only; --log to log them)
  settle       fill in results for logged live picks whose market has settled
"""
import argparse
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m argus.decay")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect")
    b = sub.add_parser("backtest")
    b.add_argument("--no-log", action="store_true", help="print the report without rewriting the backtest rows")
    lv = sub.add_parser("live")
    lv.add_argument("--log", action="store_true", help="append today's qualifying markets to decay_log.csv")
    sub.add_parser("settle")
    a = ap.parse_args(argv)
    if a.cmd == "collect":
        from . import collect
        collect.collect_markets()
    elif a.cmd == "backtest":
        from . import backtest
        backtest.main(log=not a.no_log)
    elif a.cmd == "live":
        from . import live
        live.main(log=a.log)
    elif a.cmd == "settle":
        from . import live
        live.settle()
    return 0


if __name__ == "__main__":
    sys.exit(main())
