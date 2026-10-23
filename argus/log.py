"""The prediction log and the scorecard.

Every call goes into prediction_log.csv, win or lose. Rows are never deleted;
`settle` only fills in the result columns.
"""
import csv
import uuid
from pathlib import Path

from . import fees
from .markets import contains

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "prediction_log.csv"

COLUMNS = ["call_id", "logged_at_utc", "engine", "series", "event_ticker", "market_ticker",
           "range_label", "side", "model_prob", "market_price", "gap_pts", "worth_a_look",
           "paper_contracts", "fee_per_contract", "anchor_price", "anchor_kind", "vol_daily",
           "horizon_days", "event_days", "news_shift_pct", "close_time_utc", "status",
           "settle_value", "won", "pnl_after_fees", "notes"]


def read_log(path=LOG_PATH):
    if not Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_log(rows, path=LOG_PATH):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


def append(rows, path=LOG_PATH):
    """Add new calls to the end of the log (creates the file the first time)."""
    new_file = not Path(path).exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if new_file:
            w.writeheader()
        for r in rows:
            r.setdefault("call_id", uuid.uuid4().hex[:10])
            r.setdefault("status", "pending")
            w.writerow({k: r.get(k, "") for k in COLUMNS})


# ---------------- settling ----------------

class _Range:
    """Rebuild just enough of a contract from a log row to check a settle value."""

    def __init__(self, kind, low, high):
        self.kind, self.low, self.high, self.ticker = kind, low, high, ""


def range_from_row(row):
    """Log rows store the label; the contract shape is in `notes` as kind|low|high."""
    kind, low, high = (row["notes"].split("shape=")[1].split(";")[0].split("|")
                       if "shape=" in row["notes"] else ("", "", ""))
    f = lambda x: float(x) if x not in ("", "None") else None
    return _Range(kind, f(low), f(high))


def settle_row(row, value=None, yes_result=None):
    """Fill in a row's result from a settlement value OR Kalshi's yes/no result."""
    if yes_result is None:
        yes_result = contains(range_from_row(row), value)
    won = yes_result if row["side"] == "YES" else not yes_result
    row["status"] = "settled"
    row["settle_value"] = "" if value is None else f"{value:.2f}"
    row["won"] = "1" if won else "0"
    contracts = int(row["paper_contracts"] or 0)
    row["pnl_after_fees"] = (f"{fees.pnl(won, float(row['market_price']), contracts):.2f}"
                             if contracts else "0.00")
    return row


def settle_event(event_ticker, value=None, results=None, path=LOG_PATH):
    """Settle every pending row of one event. `results` maps market_ticker -> True/False (Yes won)."""
    rows = read_log(path)
    n = 0
    for r in rows:
        if r["event_ticker"] != event_ticker or r["status"] == "settled":
            continue
        if results is not None:
            if r["market_ticker"] not in results:
                continue
            settle_row(r, value, results[r["market_ticker"]])
        else:
            settle_row(r, value)
        n += 1
    write_log(rows, path)
    return n


# ---------------- scorecard ----------------

def brier(pairs):
    """Average squared error of probabilities vs outcomes (0 = perfect, 0.25 = coin flip)."""
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs) if pairs else None


def scorecard(rows):
    settled = [r for r in rows if r["status"] == "settled"]
    bets = [r for r in settled if int(r["paper_contracts"] or 0) > 0]
    card = {
        "total_calls": len(rows),
        "settled_calls": len(settled),
        "pending_calls": len(rows) - len(settled),
        "paper_bets": len(bets),
        "paper_wins": sum(r["won"] == "1" for r in bets),
        "pnl_after_fees": round(sum(float(r["pnl_after_fees"] or 0) for r in bets), 2),
        "staked": round(sum(float(r["market_price"]) * int(r["paper_contracts"]) for r in bets), 2),
    }
    card["win_rate"] = card["paper_wins"] / len(bets) if bets else None
    card["roi"] = card["pnl_after_fees"] / card["staked"] if card["staked"] else None

    # Calibration: group calls by predicted probability, compare to how often they won.
    bins = []
    for lo in range(0, 100, 10):
        group = [r for r in settled if lo <= float(r["model_prob"]) * 100 < lo + 10
                 or (lo == 90 and float(r["model_prob"]) == 1.0)]
        if group:
            bins.append({"bin": f"{lo}-{lo + 10}%", "n": len(group),
                         "avg_predicted": sum(float(r["model_prob"]) for r in group) / len(group),
                         "actual_win_rate": sum(r["won"] == "1" for r in group) / len(group)})
    card["calibration"] = bins

    # Did the model beat the market? Lower Brier score = better probabilities.
    card["brier_model"] = brier([(float(r["model_prob"]), int(r["won"])) for r in settled])
    card["brier_market"] = brier([(float(r["market_price"]), int(r["won"])) for r in settled
                                  if r["market_price"]])
    return card
