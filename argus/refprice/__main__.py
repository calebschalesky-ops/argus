"""python -m argus.refprice <command>   (see the package docstring)"""
import argparse
import statistics
import sys
from datetime import date

from . import CRYPTO_BT_LOG, CRYPTO_LIVE_LOG, FED_BT_LOG, http, report


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m argus.refprice")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("crypto-backtest", help="replay settled 5 PM ET BTC/ETH events vs Deribit's archive")
    b.add_argument("--since", default="2026-08-23")
    b.add_argument("--until", default=None)
    b.add_argument("--report-only", action="store_true")
    sub.add_parser("crypto-live", help="log paper calls on open 5 PM ET BTC/ETH events")
    sub.add_parser("crypto-settle", help="fill in results of live calls")
    f = sub.add_parser("fed-backtest", help="Kalshi Fed decision markets vs the official free reference")
    f.add_argument("--report-only", action="store_true")
    sub.add_parser("report", help="scorecards of every refprice log")
    a = ap.parse_args(argv)

    if a.cmd == "crypto-backtest":
        if not a.report_only:
            from . import backtest
            until = date.fromisoformat(a.until) if a.until else None
            rows, skipped, basis = backtest.run(date.fromisoformat(a.since), until)
            print(f"\n{len(rows)} rows -> {CRYPTO_BT_LOG.name}; skipped: {skipped}; "
                  f"{http.stats['calls']} calls ({http.stats['429']} x 429)")
            for cur in ("BTC", "ETH"):
                bp = [x[2] for x in basis if x[0] == cur]
                if len(bp) > 1:
                    print(f"{cur} Kalshi settle vs Deribit index at close: mean {statistics.mean(bp):+.1f} bp, "
                          f"sd {statistics.stdev(bp):.1f} bp, n={len(bp)}")
        print(report.format_table(report.by_engine(CRYPTO_BT_LOG)))
        print()
        print(report.calibration(CRYPTO_BT_LOG))
    elif a.cmd == "crypto-live":
        from . import live
        live.run()
    elif a.cmd == "crypto-settle":
        from . import live
        print(f"Settled {live.settle()} calls in {CRYPTO_LIVE_LOG.name}")
    elif a.cmd == "fed-backtest":
        from . import fed
        fed.main(report_only=a.report_only)
    elif a.cmd == "report":
        for p in (CRYPTO_BT_LOG, CRYPTO_LIVE_LOG, FED_BT_LOG):
            if p.exists():
                print(f"== {p.name}")
                print(report.format_table(report.by_engine(p)))
                print()


if __name__ == "__main__":
    sys.exit(main())
