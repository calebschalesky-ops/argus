"""python -m argus.commodities <command>

  scan                       today's commodity board (every non-15-minute series), saved to data/commodities/
  backtest [--only ...]      replay the structural strategies on settled events -> commodities_*_log.csv
  report                     reprint the strategy results from the logs
  latefav-live [--at 60|30]  late favourites, forward paper test: log calls 60/30 min before a settle
                             and save a hand list to alerts/<date>_latefav.md   (alias: live)
  latefav-settle             fill in results of the live paper calls from Kalshi   (alias: settle)
"""
import argparse
import sys


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m argus.commodities")
    sub = p.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("scan", help="today's commodity board")
    sc.add_argument("--top", type=int, default=None)
    sc.add_argument("--series", nargs="*")
    bt = sub.add_parser("backtest", help="replay the structural strategies")
    bt.add_argument("--only", nargs="*", help="consistency latefav maker buckets")
    sub.add_parser("report", help="reprint strategy results")
    for name in ("latefav-live", "live"):
        lv = sub.add_parser(name, help="log late-favourite paper calls (run 60 and 30 min before a settle)")
        lv.add_argument("--at", type=int, choices=(60, 30), default=None, help="horizon; default from the clock")
    for name in ("latefav-settle", "settle"):
        sub.add_parser(name, help="settle the live paper calls")
    a = p.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    if a.cmd == "scan":
        from . import scan
        rows = scan.scan(a.series)
        path = scan.save(rows)
        print(scan.format_board(rows, a.top))
        print(f"\nSaved {path}")
    elif a.cmd == "backtest":
        from . import strategies
        strategies.main(a.only)
    elif a.cmd in ("latefav-live", "live", "latefav-settle", "settle"):
        from . import live
        live.main(a.cmd, getattr(a, "at", None))
    elif a.cmd == "report":
        from . import strategies
        print(strategies.report())


if __name__ == "__main__":
    main()
