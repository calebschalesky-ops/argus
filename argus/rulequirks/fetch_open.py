"""Download every open, non-combo Kalshi market (rules included) to data/cache/rulequirks/open_markets_<date>.json (gitignored: ~400 MB).

Kept apart from scan.py so the slow download (a few hundred pages) can run once and the
classifier can be re-run on the saved file for free.
"""
import json
from datetime import datetime, timezone

from . import CACHE, http
from ..model import ET


def path_for(day=None):
    day = day or datetime.now(ET).strftime("%Y-%m-%d")
    return CACHE / f"open_markets_{day}.json"


def download(day=None, verbose=True):
    out, params = [], {"status": "open", "limit": 1000, "mve_filter": "exclude"}
    for page in range(2000):
        d = http.get("/markets", params) or {}
        out.extend(d.get("markets", []))
        if verbose and page % 20 == 0:
            print(f"  page {page}: {len(out)} markets", flush=True)
        if not d.get("cursor"):
            break
        params["cursor"] = d["cursor"]
    series = http.get("/series", {}).get("series", [])
    CACHE.mkdir(parents=True, exist_ok=True)
    p = path_for(day)
    p.write_text(json.dumps({"fetched_utc": datetime.now(timezone.utc).isoformat(), "markets": out,
                             "series": {s["ticker"]: {k: s.get(k) for k in ("title", "category", "frequency",
                                                                            "settlement_sources", "contract_url")}
                                        for s in series}}), encoding="utf-8")
    return p


if __name__ == "__main__":
    print(download())
