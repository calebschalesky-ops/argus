"""python -m argus.rulequirks <command>

  scan       classify the rule quirks of every open non-15-minute market (downloads them once a day)
  mattered   find settled markets where a quirk decided the outcome; check the price beforehand
  backtest   replay the rule-quirk strategies on settled markets -> rulequirks_log.csv
  report     reprint the saved results
"""
import argparse
import json
import sys


def _series_meta():
    from . import fetch_open, http
    p = fetch_open.path_for()
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))["series"]
    return {s["ticker"]: s for s in http.get("/series", {}).get("series", [])}


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m argus.rulequirks")
    sub = p.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("scan", help="classify today's open markets")
    sc.add_argument("--day", default=None, help="use a saved download (YYYY-MM-DD)")
    sub.add_parser("mattered", help="where quirks decided settled markets, and the prices before")
    sub.add_parser("backtest", help="replay the rule-quirk strategies")
    sub.add_parser("report", help="reprint saved results")
    a = p.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    if a.cmd == "scan":
        from . import scan
        summary, rows = scan.scan(a.day)
        path = scan.save(summary, rows, a.day)
        print(scan.format_summary(summary, rows))
        print(f"\nSaved {path}")
    elif a.cmd == "mattered":
        from . import corpus, mattered
        summary, _ = mattered.run(corpus.cached_listings(), _series_meta())
        print(json.dumps(summary, indent=1))
    elif a.cmd == "backtest":
        from . import corpus, strategies
        print(strategies.main(corpus.cached_listings(), _series_meta()))
    elif a.cmd == "report":
        from . import strategies
        print(strategies.report())


if __name__ == "__main__":
    main()
