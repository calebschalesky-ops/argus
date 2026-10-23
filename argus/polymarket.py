"""Read-only Polymarket client.

Like kalshi.py, this file can only READ public data. It sends no wallet, no key and
no signature, it only makes GET requests, and `_get` refuses any host or path that
is not on the allowlist below. Polymarket's order, trade and auth endpoints are not
reachable from here on purpose: Argus never trades.

Endpoints used (Polymarket docs, checked 2026-10-22, all public, no login):
  Gamma  https://gamma-api.polymarket.com  /events, /events/keyset, /markets, /public-search
  CLOB   https://clob.polymarket.com       /book, /midpoint, /price  (GET only)
"""
import json
import time

import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

# The only (host, path) pairs Argus may touch. Exact paths, not prefixes, so
# nothing like /order, /orders, /auth or /trades can slip through.
ALLOWED = {
    GAMMA: {"/events", "/events/keyset", "/markets", "/public-search"},
    CLOB: {"/book", "/midpoint", "/price"},
}

_session = requests.Session()
_session.headers["User-Agent"] = "argus-research/0.1 (read-only)"


def _get(host, path, params=None):
    """Make one GET request to a public Polymarket API and return the JSON."""
    if path not in ALLOWED.get(host, set()):
        raise PermissionError(f"Argus is read-only; refusing {host}{path}")
    for attempt in range(4):
        r = _session.get(host + path, params=params, timeout=30)
        if r.status_code == 429:          # rate limited: wait a little, then retry
            time.sleep(1.5 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


def events_by_tag(tag_slug, max_pages=30):
    """Every open event carrying one Gamma tag (e.g. "midterms"), markets nested."""
    out = []
    for page in range(max_pages):
        batch = _get(GAMMA, "/events", {"tag_slug": tag_slug, "closed": "false",
                                        "limit": 100, "offset": page * 100})
        out.extend(batch)
        if len(batch) < 100:
            break
    return out


def all_open_events(max_pages=1000, progress=None):
    """Every open, active event with its markets nested (about 23,000 on 2026-10-22).

    Gamma refuses offsets past ~2,000, so this follows the /events/keyset cursor.
    The full read is large (about 1.9 GB of JSON, ~5 minutes on 2026-10-22)."""
    out, cursor = [], None
    for page in range(max_pages):
        params = {"closed": "false", "active": "true", "limit": 100}
        if cursor:
            params["after_cursor"] = cursor
        data = _get(GAMMA, "/events/keyset", params)
        out.extend(data.get("events", []))
        cursor = data.get("next_cursor")
        if progress and page % 25 == 0:
            progress(len(out))
        if not cursor or not data.get("events"):
            break
    return out


def markets_by_id(ids, batch=20):
    """Full Gamma records (including the rules text in `description`) for market ids."""
    ids, out = list(ids), []
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        out.extend(_get(GAMMA, "/markets", [("id", x) for x in chunk] + [("limit", len(chunk))]))
    return out


def get_event(slug):
    found = _get(GAMMA, "/events", {"slug": slug})
    return found[0] if found else None


def token_ids(market):
    """(yes_token, no_token) for a Gamma market. Gamma stores the list as a JSON string."""
    ids = market.get("clobTokenIds") or "[]"
    ids = json.loads(ids) if isinstance(ids, str) else ids
    outcomes = market.get("outcomes") or '["Yes", "No"]'
    outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
    if len(ids) != 2 or [o.lower() for o in outcomes] != ["yes", "no"]:
        return None, None
    return ids[0], ids[1]


def is_live(market):
    """Open, active, and actually trading (placeholder slots like "Person A" are not)."""
    return bool(market.get("active") and not market.get("closed")
                and market.get("outcomePrices") and market.get("enableOrderBook", True))


def book(token_id):
    """Top of the order book for one outcome token.

    Returns {"bid", "ask", "bid_size", "ask_size", "mid", "spread", "last"} in dollars,
    any of which may be None when that side of the book is empty.
    """
    b = _get(CLOB, "/book", {"token_id": token_id})
    bids = [(float(x["price"]), float(x["size"])) for x in b.get("bids", [])]
    asks = [(float(x["price"]), float(x["size"])) for x in b.get("asks", [])]
    best_bid = max(bids) if bids else (None, None)
    best_ask = min(asks) if asks else (None, None)
    out = {"bid": best_bid[0], "bid_size": best_bid[1],
           "ask": best_ask[0], "ask_size": best_ask[1],
           "last": float(b["last_trade_price"]) if b.get("last_trade_price") else None}
    if out["bid"] is not None and out["ask"] is not None:
        out["mid"] = (out["bid"] + out["ask"]) / 2
        out["spread"] = out["ask"] - out["bid"]
    else:
        out["mid"], out["spread"] = None, None
    return out
