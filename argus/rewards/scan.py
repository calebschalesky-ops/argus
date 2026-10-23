"""Snapshot every rewarded market's order book (public reads only) and save it for the model."""
from datetime import datetime, timezone
from pathlib import Path

from . import data

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "cache" / "rewards"


def is_fifteen(series):
    return series.endswith("15M")          # hard rule 4: 15-minute markets are paper research only


def scan(top=800, min_hours=6, verbose=True):
    """Read the reward list, then the books of the `top` biggest pools per day (0 = all).

    Skips 15-minute series and periods with under `min_hours` left. Returns the snapshot rows and
    saves them to data/cache/rewards/<UTC time>.json.
    """
    progs = [p for p in data.active_programs()
             if not is_fifteen(p["series"]) and p["hours_left"] >= min_hours]
    progs.sort(key=lambda p: -p["per_day"])
    if top:
        progs = progs[:top]
    if verbose:
        print(f"Reading {len(progs)} order books ...", flush=True)
    rows = []
    for i, p in enumerate(progs):
        try:
            p["yes"], p["no"] = data.orderbook(p["ticker"])
        except Exception as e:                # one bad market should not stop the scan
            if verbose:
                print(f"  skip {p['ticker']}: {e}")
            continue
        rows.append(p)
        if verbose and i and i % 200 == 0:
            print(f"  {i} books", flush=True)
    stamp = datetime.now(timezone.utc)
    data.save_json(CACHE / f"{stamp:%Y-%m-%d_%H%M}.json", {"taken": stamp.isoformat(), "rows": rows})   # books first
    mk = data.markets(p["ticker"] for p in rows)
    for p in rows:
        m = mk.get(p["ticker"], {})
        p["vol24"] = data.fnum(m.get("volume_24h_fp"))
        p["close"] = m.get("close_time")
        p["title"] = m.get("title") or ""
        p["label"] = m.get("yes_sub_title") or ""
        p["status"] = m.get("status")
    data.save_json(CACHE / f"{stamp:%Y-%m-%d_%H%M}.json", {"taken": stamp.isoformat(), "rows": rows})
    return stamp, rows


def latest():
    import json
    files = sorted(CACHE.glob("*.json"))
    if not files:
        return None, []
    d = json.loads(files[-1].read_text())
    return datetime.fromisoformat(d["taken"]), d["rows"]
