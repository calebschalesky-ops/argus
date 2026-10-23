"""python -m argus.rewards today | model   (see research/rewards.md)"""
import argparse
from datetime import datetime, timezone
from pathlib import Path

from . import model, scan
from .scan import ROOT

ALERTS = ROOT / "alerts"
RESEARCH = ROOT / "research" / "rewards.md"
BEGIN, END = "<!-- rewards-today:begin -->", "<!-- rewards-today:end -->"
BUDGETS = [(10, 5), (100, 10), (500, 15)]          # (dollars, max orders one person re-checks by hand)


def _money(x):
    return f"${x:,.2f}"


def table_one_contract(cands, n):
    rows = sorted(model.one_contract(cands), key=lambda c: -c["per_dollar"])[:n]
    out = ["| market | side | bid at | pool $/day | qualifying size ahead | 1 contract $/day | $/day per $1 resting | hours left |",
           "|---|---|---|---|---|---|---|---|"]
    for c in rows:
        name = c["ticker"] + (f" ({c['label']})" if c["label"] else "")
        out.append(f"| {name} | {c['side']} | {c['price'] * 100:g}c | {c['per_day']:,.0f} | {c['ahead']:,.0f} | "
                   f"{c['reward1']:.4f} | {c['per_dollar']:.2f} | {c['hours']:.0f} |")
    return out


def hand_list(cands, budget, max_orders):
    orders = model.allocate(cands, budget, max_orders, "central")
    s = model.summary(orders)
    out = [f"**${budget} account, at most {max_orders} orders** (central case): rests {_money(s['cost'])}, "
           f"reward {_money(s['reward'])}/day, expected fill P/L {_money(s['fill'])}/day, net {_money(s['net'])}/day.",
           "", "| market | buy | limit price | contracts | ties up | reward $/day | hours left in period |", "|---|---|---|---|---|---|---|"]
    for o in orders:
        out.append(f"| {o['ticker']} | {o['side'].upper()} | {o['price'] * 100:g}c | {o['contracts']} | "
                   f"{_money(o['cost'])} | {o['reward']:.2f} | {o['hours']:.0f} |")
    return out


def scenario_table(cands):
    out = ["| account | scenario | orders | resting | reward/day | fill P/L/day | net/day | paid for periods running now |",
           "|---|---|---|---|---|---|---|---|"]
    for b, mo in BUDGETS:
        for sc in ("low", "central", "high"):
            s = model.summary(o := model.allocate(cands, b, mo, sc))
            out.append(f"| ${b} | {sc} | {len(o)} | {_money(s['cost'])} | {_money(s['reward'])} | {_money(s['fill'])} | "
                       f"{_money(s['net'])} | {_money(s['this_period'])} |")
    return out


def cmd_today(a):
    if a.cached:
        taken, rows = scan.latest()
    else:
        taken, rows = scan.scan(top=a.top)
    taken = taken if taken.tzinfo else taken.replace(tzinfo=timezone.utc)
    cands = model.sides(rows, taken)
    head = (f"Liquidity rewards, snapshot {taken:%Y-%m-%d %H:%M} UTC: {len(rows)} rewarded books read "
            f"(15-minute markets skipped), {len(cands)} sides where both sides meet the Target Size, so a "
            f"1-contract bid at the reference price scores now.")
    lines = [head, "", "### Best reward per dollar resting (1 contract)", ""] + table_one_contract(cands, a.n)
    lines += ["", "### Hand list", ""] + hand_list(cands, a.budget, a.max_orders)
    lines += ["", "### Budgets and scenarios (assumptions: research/rewards.md section 4)", ""] + scenario_table(cands)
    lines += ["", "Read-only estimate from one order-book snapshot. Argus places no orders. Rewards pay only if a "
              "period's total is at least $1.00, and Kalshi can end or change the program at any time."]
    text = "\n".join(lines)
    print(text)
    ALERTS.mkdir(exist_ok=True)
    path = ALERTS / f"{taken:%Y-%m-%d_%H%M}_rewards.md"
    path.write_text(f"# Rewards today\n\n{text}\n", encoding="utf-8")
    if RESEARCH.exists():
        doc = RESEARCH.read_text(encoding="utf-8")
        if BEGIN in doc and END in doc:
            pre, rest = doc.split(BEGIN, 1)
            post = rest.split(END, 1)[1]
            RESEARCH.write_text(f"{pre}{BEGIN}\n{text}\n{END}{post}", encoding="utf-8")
    print(f"\nSaved: {path}")


def cmd_model(a):
    taken, rows = scan.latest()
    if not rows:
        raise SystemExit("No snapshot yet: run  python -m argus.rewards today")
    taken = taken if taken.tzinfo else taken.replace(tzinfo=timezone.utc)
    print("\n".join(scenario_table(model.sides(rows, taken))))


def main():
    ap = argparse.ArgumentParser(prog="python -m argus.rewards")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("today", help="rewarded markets where a 1-contract resting bid scores, with $/day")
    t.add_argument("--top", type=int, default=800, help="books to read, biggest pools first (0 = all, ~20 min)")
    t.add_argument("--n", type=int, default=30, help="rows in the 1-contract table")
    t.add_argument("--budget", type=float, default=10)
    t.add_argument("--max-orders", type=int, default=5)
    t.add_argument("--cached", action="store_true", help="reuse the latest saved snapshot")
    t.set_defaults(func=cmd_today)
    m = sub.add_parser("model", help="$10/$100/$500 scenarios from the latest snapshot")
    m.set_defaults(func=cmd_model)
    a = ap.parse_args()
    a.func(a)


main()
