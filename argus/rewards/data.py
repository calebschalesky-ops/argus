"""Public reads for the rewards study: the reward list, order books, and market rows.

`/incentive_programs` is not on argus.kalshi's allowlist (that file belongs to another thread),
so this module has its own GET with the same rule: a fixed list of public paths, no credentials.
Order books and markets go through argus.kalshi._get.
"""
import json
import time
from datetime import datetime, timezone

import requests

from .. import kalshi

BASE = kalshi.BASE
_ALLOWED = ("/incentive_programs",)
_session = requests.Session()
_session.headers["User-Agent"] = "argus-research/0.1 (read-only)"
MIN_GAP = 0.2            # seconds between calls: about 5 a second, shared API with sibling sessions
_last = [0.0]


def _pace():
    wait = MIN_GAP - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()


def _get_programs(params):
    path = "/incentive_programs"
    if not path.startswith(_ALLOWED):
        raise PermissionError(path)
    for attempt in range(5):
        _pace()
        r = _session.get(BASE + path, params=params, timeout=60)
        if r.status_code == 429:
            time.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def active_programs():
    """Every liquidity reward running right now, with dollar amounts filled in.

    `period_reward` is in centi-cents (1/10,000 of a dollar): 1,000,000 = $100. Not stated on the API
    page; inferred 2026-10-22 because it is the only unit that keeps every live program inside the
    filed $1-$1,000 per market per day cap (cents would put the median at $1,429/day).
    """
    out, cursor = [], None
    while True:
        params = {"status": "active", "type": "liquidity", "limit": 10000}
        if cursor:
            params["cursor"] = cursor
        d = _get_programs(params)
        out += d.get("incentive_programs", [])
        cursor = d.get("next_cursor")
        if not cursor or not d.get("incentive_programs"):
            break
    now = datetime.now(timezone.utc)
    rows = []
    for p in out:
        start, end = parse_ts(p["start_date"]), parse_ts(p["end_date"])
        if not (start <= now < end):
            continue
        days = (end - start).total_seconds() / 86400
        reward = p["period_reward"] / 10000
        rows.append({"ticker": p["market_ticker"], "series": p["market_ticker"].split("-")[0],
                     "reward": reward, "days": days, "per_day": reward / days if days else 0.0,
                     "target": float(p.get("target_size_fp") or 0), "discount": p["discount_factor_bps"] / 10000,
                     "start": start, "end": end, "hours_left": (end - now).total_seconds() / 3600,
                     "kind": p.get("incentive_description", ""),
                     "cap": (p["max_reward_per_account"] / 10000) if p.get("max_reward_per_account") else None})
    return rows


def orderbook(ticker):
    """(yes bids, no bids) as [(price $, size)], from the public order book."""
    _pace()
    ob = kalshi._get(f"/markets/{ticker}/orderbook").get("orderbook_fp", {})
    conv = lambda xs: [(float(p), float(s)) for p, s in (xs or [])]
    return conv(ob.get("yes_dollars")), conv(ob.get("no_dollars"))


def markets(tickers):
    """{ticker: market row} read 100 at a time."""
    out = {}
    tickers = list(tickers)
    for i in range(0, len(tickers), 50):
        for attempt in range(6):
            _pace()
            try:
                ms = kalshi._get("/markets", {"tickers": ",".join(tickers[i:i + 50]), "limit": 50}).get("markets", [])
                break
            except requests.HTTPError:     # 429s are shared with sibling sessions: back off, then give up on this batch
                time.sleep(5 * (attempt + 1))
        else:
            continue
        for m in ms:
            out[m["ticker"]] = m
    return out


def fnum(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, default=str))
