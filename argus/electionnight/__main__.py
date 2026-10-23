"""python -m argus.electionnight <command>

  backtest [--race ID ...]    replay past results nights (official counts vs Kalshi), write electionnight_log.csv
  report                      reprint the backtest scorecard from electionnight_log.csv
  races                       list the past nights and the Nov 3 2026 live races
  live [--once] [--interval S] [--race ID ...] [--dry]
                              poll the official feeds on election night, log paper calls
  settle                      fill results of live paper calls from Kalshi

Paper only. Read-only public data. Argus never trades.
"""
import argparse
import sys

from .. import log
from . import LOG_PATH, backtest, live, races


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m argus.electionnight", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backtest")
    b.add_argument("--race", nargs="*")
    sub.add_parser("report")
    sub.add_parser("races")
    lv = sub.add_parser("live")
    lv.add_argument("--once", action="store_true")
    lv.add_argument("--interval", type=int, default=live.DEFAULT_INTERVAL_S)
    lv.add_argument("--race", nargs="*")
    lv.add_argument("--dry", action="store_true", help="print only, log nothing")
    sub.add_parser("settle")
    a = p.parse_args(argv)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(errors="replace")
    if a.cmd == "backtest":
        rows = backtest.run(a.race)
        print(f"\nWrote {len(rows)} replayed rows to {LOG_PATH}\n")
        print(backtest.report(rows))
    elif a.cmd == "report":
        print(backtest.report([r for r in log.read_log(LOG_PATH) if r["engine"].startswith(backtest.ENGINE)]))
    elif a.cmd == "races":
        print("Past results nights (backtest):")
        for r in races.PAST:
            print(f"  {r['id']:<14} {r['contest']:<40} {', '.join(r['markets'])}")
        print("\nNov 3 2026 live races:")
        for r in live.LIVE_2026:
            print(f"  {r['id']:<14} polls close {r['polls_close_et']} ET   {', '.join(r['markets'])}")
    elif a.cmd == "live":
        live.run(a.interval, a.once, not a.dry, a.race)
    elif a.cmd == "settle":
        print(f"Settled {live.settle()} live rows.")


if __name__ == "__main__":
    main()
