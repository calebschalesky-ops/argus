"""Edge factory commands.

  python -m argus.lab sync [--minutes N] [--steps series,import,recent,backfill,candles]
  python -m argus.lab status                     lake row counts and disk size
  python -m argus.lab mine [--holdout YYYY-MM-DD] [--force] [--dry]
  python -m argus.lab reference                  late-day commodity favourites through the bench
  python -m argus.lab ordersize                  near-misses re-scored at 1/5/10/25 contracts per order
  python -m argus.lab nested                     nested-strike price violations (idea 2.2)
  python -m argus.lab bench IDEA.py              score your own idea file (defines HYPOTHESIS, optional SQL)
  python -m argus.lab promote [--watch RULE_ID]   holdout survivors (or one labelled near-miss) become paper loggers
  python -m argus.lab live                       promoted rules make forward calls now
  python -m argus.lab settle                     settle pending live calls from Kalshi results
  python -m argus.lab nightly [--minutes N]      sync + mine (if due) + live + settle + board
Read-only, public data, no key. It never trades.
"""
import argparse
import json
import sys


def _print(x):
    print(json.dumps(x, indent=2, default=str))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m argus.lab", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sync")
    p.add_argument("--minutes", type=float, default=None, help="stop cleanly after this long (resumes next run)")
    p.add_argument("--steps", default="series,import,recent,candles,backfill,candles")
    sub.add_parser("status")
    p = sub.add_parser("mine")
    p.add_argument("--holdout", default=None, help="first day of the holdout (default: 120 days ago)")
    p.add_argument("--force", action="store_true", help="mine a new cohort even if the last one is recent")
    p.add_argument("--dry", action="store_true", help="mining only; the holdout is not looked at")
    sub.add_parser("reference")
    sub.add_parser("ordersize")
    sub.add_parser("nested")
    p = sub.add_parser("bench")
    p.add_argument("idea", help="a .py file defining HYPOTHESIS (a bench.Hypothesis) and optionally SQL (a market filter)")
    p = sub.add_parser("promote")
    p.add_argument("--watch", default=None, help="paper-log this rule id although it did not pass (labelled lab-watch)")
    p.add_argument("--prob", type=float, default=None, help="with --watch: the rule's mining win rate")
    sub.add_parser("live")
    sub.add_parser("settle")
    p = sub.add_parser("nightly")
    p.add_argument("--minutes", type=float, default=90, help="time budget for the sync step")
    a = ap.parse_args(argv)

    if a.cmd == "sync":
        from . import sync
        _print(sync.run(minutes=a.minutes, steps=tuple(a.steps.split(","))))
    elif a.cmd == "status":
        from . import lake
        con = lake.connect()
        _print({"counts": lake.counts(con), "disk_mb": round(lake.disk_bytes() / 1e6, 1),
                "last_sync": lake.get_state(con, "last_sync")})
    elif a.cmd == "mine":
        from . import miner
        print(miner.run(holdout=a.holdout, force=a.force, dry=a.dry))
    elif a.cmd == "reference":
        from . import reference
        print(reference.run())
    elif a.cmd == "ordersize":
        from . import ordersize
        print(ordersize.run())
    elif a.cmd == "nested":
        from . import nested
        print(nested.run())
    elif a.cmd == "bench":
        import runpy
        from . import bench, lake, stats
        ns = runpy.run_path(a.idea)
        hyps = ns.get("HYPOTHESES") or [ns["HYPOTHESIS"]]
        got = bench.run_lake(hyps, lake.connect(), ns.get("SQL", "1=1"))
        from . import registry
        for h in hyps:
            sm = stats.summary(got[h.name])
            print(stats.format_summary(h.name, sm))
            registry.append([{"kind": "bench", "name": h.name, "batch": f"bench:{a.idea}", "data_window": "whole lake",
                              "n": sm.get("n", 0), "events": sm.get("events", 0), "roi": sm.get("roi"), "p": sm.get("p", 1.0)}])
    elif a.cmd == "promote":
        from . import live
        print(live.watch(a.watch, a.prob) if a.watch else live.promote())
    elif a.cmd == "live":
        from . import live
        print(live.run_live())
    elif a.cmd == "settle":
        from . import live
        print(live.settle())
    elif a.cmd == "nightly":
        from . import live
        print(live.nightly(minutes=a.minutes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
