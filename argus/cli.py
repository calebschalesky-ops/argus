"""Argus command line.  Run `python -m argus --help` to see every command."""
import argparse
import sys

from . import alert, engine, kalshi, log, scanner
from .markets import Contract
from .model import ET, now_utc, parse_time

OIL_SERIES = ["KXWTIW", "KXWTI", "KXBRENTW", "KXBRENTD"]


def cmd_markets(a):
    """List open oil events and every range with prices, close time and rules."""
    for series in a.series or OIL_SERIES:
        for ev, close in engine.open_events(series):
            print(f"\n=== {ev}   closes {parse_time(close).astimezone(ET):%a %b %d %I:%M %p ET}")
            cs = sorted((Contract.from_api(m) for m in kalshi.get_markets(event_ticker=ev)), key=Contract.sort_key)
            for c in cs:
                f = lambda x: f"{round(x * 100):>3d}c" if x else "  -"
                print(f"  {c.label:<22} Yes {f(c.yes_bid)}/{f(c.yes_ask)}   No {f(c.no_bid)}/{f(c.no_ask)}   {c.ticker}")
            if a.rules and cs:
                print("  RULES:", cs[0].rules.strip().replace("\n", "\n         "))


def _resolve_event(a):
    if a.event:
        return a.event
    evs = engine.open_events(a.series)
    if not evs:
        sys.exit(f"No open events in {a.series}")
    return evs[0][0]                      # soonest-closing event


def cmd_run(a):
    """Price one event, print the alert, save it, and log every call."""
    from . import news
    event = _resolve_event(a)
    headlines = list(a.headline or [])
    if a.headlines_file:
        headlines += news.read_headlines_file(a.headlines_file)
    if a.feeds:
        from . import feeds
        posts, _, _ = feeds.gather("oil", hours=a.feeds_hours)
        headlines += [f"{p['source']}: {p['title']}" for p in posts[:25]]
    scores = []
    if headlines:
        commodity = engine.SERIES[engine.series_of(event)]["commodity"]
        print(f"Scoring {len(headlines)} headline(s) with Claude ...")
        scores = news.score_headlines(headlines, commodity)
    from . import filters
    band = None if a.no_band else filters.Band(min_price=a.min_price)
    res = engine.run_event(event, anchor_mode=a.anchor, news_scores=scores, band=band, pricing=a.pricing)
    text = alert.format_alert(res)
    print(text)
    path = alert.save_alert(text, res)
    print(f"\nSaved: {path}")
    if not a.no_log:
        rows = engine.log_rows(res)
        log.append(rows)
        print(f"Logged {len(rows)} calls to {log.LOG_PATH.name} "
              f"({sum(r['worth_a_look'] == '1' for r in rows)} paper bets).")


def cmd_events(a):
    """Scheduled events in the next N days, with how much each widens the odds."""
    from datetime import timedelta
    from .events import events_between
    now = now_utc().astimezone(ET)
    for e in events_between(now, now + timedelta(days=a.days)):
        print(f"{e.when:%a %b %d %I:%M %p} ET   +{e.weight:.1f} day   {e.name}"
              + ("" if e.confirmed else "   (unconfirmed)") + (f"   [{e.source}]" if e.source else ""))


def cmd_news(a):
    """Score headlines without running a market (to see what Claude thinks)."""
    from . import news
    heads = list(a.headline or []) + (news.read_headlines_file(a.headlines_file) if a.headlines_file else [])
    for s in news.score_headlines(heads, a.commodity):
        print(f"{news.shift_for(s) * 100:+.2f}%  {s['direction']:<11} {s['size']:<6} "
              f"persist {s['persistence']:.2f}  {s['headline'][:70]}\n          why: {s['why']}")


def cmd_settle(a):
    """Enter results. Manual: settle EVENT --value 98.73.  Automatic: settle --auto."""
    if a.auto:
        pending = sorted({r["event_ticker"] for r in log.read_log() if r["status"] != "settled"})
        for ev in pending:
            ms = kalshi.get_markets(event_ticker=ev, status=None)
            done = {m["ticker"]: m.get("result") == "yes" for m in ms if m.get("result") in ("yes", "no")}
            if not done:
                print(f"{ev}: not settled yet")
                continue
            value = next((float(m["expiration_value"]) for m in ms if m.get("expiration_value")), None)
            n = log.settle_event(ev, value=value, results=done)
            print(f"{ev}: settled {n} calls (value {value})")
        return
    if not (a.event and a.value is not None):
        sys.exit("Use: settle EVENT_TICKER --value 98.73   or   settle --auto")
    n = log.settle_event(a.event, value=a.value)
    print(f"Settled {n} calls in {a.event} at ${a.value:.2f}")


def cmd_scorecard(a):
    card = log.scorecard(log.read_log())
    pct = lambda x: "n/a" if x is None else f"{x * 100:.1f}%"
    print("ARGUS SCORECARD (paper only)")
    print(f"  Total calls logged:   {card['total_calls']}  (settled {card['settled_calls']}, pending {card['pending_calls']})")
    print(f"  Paper bets (WORTH A LOOK, 1 contract each): {card['paper_bets']}")
    print(f"  Win rate:             {pct(card['win_rate'])}")
    print(f"  Profit/loss after fees: ${card['pnl_after_fees']:+.2f}  on ${card['staked']:.2f} staked  (ROI {pct(card['roi'])})")
    b = lambda x: "n/a" if x is None else f"{x:.4f}"
    print(f"  Brier score  model {b(card['brier_model'])}  vs market {b(card['brier_market'])}   (lower is better)")
    print("\n  Calibration (do 70% calls win about 70% of the time?)")
    print("  Predicted    Calls   Avg predicted   Actually won")
    for x in card["calibration"]:
        print(f"  {x['bin']:<10} {x['n']:>6}   {x['avg_predicted'] * 100:>10.1f}%   {x['actual_win_rate'] * 100:>10.1f}%")
    if card["settled_calls"] < 50:
        print("\n  Fewer than 50 settled calls: too few to tell skill from luck yet.")


def cmd_scan(a):
    from pathlib import Path
    by_cat, checks, n = scanner.scan(a.category, max_pages=a.pages)
    text = scanner.format_scan(by_cat, checks, n)
    print(text)
    out = Path(log.ROOT) / "scans"
    out.mkdir(exist_ok=True)
    p = out / f"{now_utc().astimezone(ET):%Y-%m-%d_%H%M}_scan.md"
    p.write_text("```\n" + text + "\n```\n", encoding="utf-8")
    print(f"\nSaved: {p}")


def cmd_feeds(a):
    from . import feeds
    posts, manual, errors = feeds.gather(a.topic, hours=a.hours, min_rating=a.min_rating)
    print(f"Latest posts ({a.topic}, last {a.hours} h):")
    for p in posts:
        print(f"  {p['when'].astimezone(ET):%m-%d %H:%M} ET  [{p['rating']}] {p['source'][:24]:<24} {p['title'][:80]}\n                {p['link']}")
    if manual:
        print("\nCheck by hand (no free feed):")
        for s in manual:
            print(f"  [{s.get('rating', '')}] {s.get('name', '')[:30]:<30} {s.get('url', '')}")
    for name, err in errors:
        print(f"  feed error: {name}: {err}")


def cmd_crossvenue(a):
    """Kalshi vs Polymarket on the 2026 midterms: print, save, log every matched outcome."""
    from pathlib import Path
    from . import crossvenue
    print("Reading Kalshi and Polymarket (public, read-only) ...")
    res = crossvenue.compare()
    text = crossvenue.format_report(res)
    print(text)
    out = Path(log.ROOT) / "alerts"
    out.mkdir(exist_ok=True)
    p = out / f"{res['at'].astimezone(ET):%Y-%m-%d_%H%M}_crossvenue-midterms.md"
    p.write_text("```\n" + text + "\n```\n", encoding="utf-8")
    print(f"\nSaved: {p}")
    if not a.no_log:
        rows = crossvenue.log_rows(res)
        log.append(rows)
        print(f"Logged {len(rows)} calls to {log.LOG_PATH.name} "
              f"({sum(r['worth_a_look'] == '1' for r in rows)} paper bets).")


def cmd_crossvenue_scan(a):
    """Every market listed on both Kalshi and Polymarket (argus-crossvenue's scan)."""
    from . import crossvenue
    crossvenue.main(["scan"] + a.rest)


def cmd_board(a):
    """Every strategy side by side, ranked by measured results."""
    from . import board
    print(board.format_board(board.board()))


def main(argv=None):
    p = argparse.ArgumentParser(prog="argus", description="Argus: read-only Kalshi research. Paper only.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("board", help="every strategy side by side, ranked by measured results")
    s.set_defaults(fn=cmd_board)

    s = sub.add_parser("markets", help="list open oil markets with prices and rules")
    s.add_argument("--series", nargs="*", choices=OIL_SERIES)
    s.add_argument("--rules", action="store_true", help="also print the rules text")
    s.set_defaults(fn=cmd_markets)

    s = sub.add_parser("run", help="price one event, print + save the alert, log the calls")
    s.add_argument("event", nargs="?", help="event ticker, e.g. KXWTIW-26OCT2314 (default: soonest in --series)")
    s.add_argument("--series", default="KXWTIW", choices=OIL_SERIES)
    s.add_argument("--anchor", default="auto", choices=["auto", "settle"])
    s.add_argument("--headline", action="append", help="a news headline (repeat for more)")
    s.add_argument("--headlines-file", help="text file, one headline per line")
    s.add_argument("--feeds", action="store_true", help="also score recent posts from research/sources.csv feeds")
    s.add_argument("--feeds-hours", type=int, default=24)
    s.add_argument("--no-log", action="store_true", help="do not write to the prediction log")
    s.add_argument("--min-price", type=float, default=0.50,
                   help="only flag sides priced at least this (dollars; default 0.50; never below 0.10)")
    s.add_argument("--no-band", action="store_true", help="flag at any price from 10c up (the 10c floor stays)")
    s.add_argument("--pricing", default="taker", choices=["taker", "maker"],
                   help="taker = pay the ask; maker = assume a resting buy at bid + 1c fills (maker fee)")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("events", help="scheduled events that widen the odds")
    s.add_argument("--days", type=int, default=14)
    s.set_defaults(fn=cmd_events)

    s = sub.add_parser("news", help="score headlines with Claude (no market)")
    s.add_argument("--headline", action="append")
    s.add_argument("--headlines-file")
    s.add_argument("--commodity", default="WTI", choices=["WTI", "BRENT"])
    s.set_defaults(fn=cmd_news)

    s = sub.add_parser("settle", help="enter results for logged calls")
    s.add_argument("event", nargs="?")
    s.add_argument("--value", type=float, help="the official settlement price")
    s.add_argument("--auto", action="store_true", help="read results from Kalshi's public API")
    s.set_defaults(fn=cmd_settle)

    s = sub.add_parser("scorecard", help="win rate, P/L after fees, calibration")
    s.set_defaults(fn=cmd_scorecard)

    s = sub.add_parser("scan", help="every open Kalshi market by category (skips 15-minute)")
    s.add_argument("--category", help="e.g. Politics, Economics, Commodities")
    s.add_argument("--pages", type=int, default=150, help="pages of 200 events to read (150 = everything)")
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("feeds", help="latest posts from the creator/source lists")
    s.add_argument("--topic", default="oil", choices=["oil", "politics"])
    s.add_argument("--hours", type=int, default=48)
    s.add_argument("--min-rating", default="B", choices=["A", "B", "C"])
    s.set_defaults(fn=cmd_feeds)

    s = sub.add_parser("crossvenue-scan", help="every market on both Kalshi and Polymarket (options: --top, --books, --min-conf, --no-log)")
    s.add_argument("rest", nargs=argparse.REMAINDER)
    s.set_defaults(fn=cmd_crossvenue_scan)

    s = sub.add_parser("crossvenue", help="Kalshi vs Polymarket prices on the 2026 midterms")
    s.add_argument("--no-log", action="store_true", help="do not write to the prediction log")
    s.set_defaults(fn=cmd_crossvenue)

    args = sys.argv[1:] if argv is None else list(argv)
    if args[:1] == ["crossvenue-scan"]:        # pass every option through untouched
        sys.stdout.reconfigure(errors="replace")
        return cmd_crossvenue_scan(argparse.Namespace(rest=args[1:]))
    a = p.parse_args(args)
    sys.stdout.reconfigure(errors="replace")   # never crash on an odd character in a headline
    a.fn(a)


if __name__ == "__main__":
    main()
