"""Collect settled by-date markets and their daily prices.

A by-date market is one Kalshi says will "close and expire early if the event occurs"
(the market's early_close_condition), with a deadline written into its title or rules
("before Jul 1, 2026", "by March 31, 2026", "before 2027"). Those are the "Will X happen
by <date>?" questions.

Steps (every answer that cannot change is cached, so a re-run is free):
  1. Every series in the kept categories (not sports, crypto, weather, mentions, commodities,
     elections or financials; not daily/weekly/hourly/15-minute), listed from both the
     historical and the live tier.
  2. Keep settled yes/no markets that are by-date and whose deadline could be read.
  3. For those that traded, one daily-candle request over the market's whole life.
Saved to data/cache/decay/markets.json and data/cache/decay/candles/<ticker>.json.
"""
import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import CACHE, ROOT
from . import http

ET = ZoneInfo("America/New_York")
SKIP_CATS = {"Sports", "Crypto", "Climate and Weather", "Mentions", "Commodities", "Exotics",
             "Elections", "Financials"}
SKIP_FREQ = {"daily", "hourly", "fifteen_min", "weekly"}
SINCE = datetime(2025, 1, 1, tzinfo=timezone.utc)
MARKETS_FILE = CACHE / "markets.json"
CANDLE_DIR = CACHE / "candles"

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_LEAD = r"\b(before|by)\s+(?:the\s+end\s+of\s+)?"
# "before Jul 1, 2026" / "by March 31, 2026" / "before 1 July 2026" / "before July 2026" / "before 2027"
_PATTERNS = [
    ("mdy", re.compile(_LEAD + _MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", re.I)),
    ("dmy", re.compile(_LEAD + r"(\d{1,2})(?:st|nd|rd|th)?\s+" + _MON + r",?\s+(\d{4})", re.I)),
    ("my", re.compile(_LEAD + _MON + r",?\s+(\d{4})", re.I)),
    ("y", re.compile(_LEAD + r"(\d{4})\b", re.I)),
]


def is_by_date(m):
    """True for a market that closes early when its event happens (a 'by <date>' question)."""
    cond = (m.get("early_close_condition") or "").lower()
    return bool(m.get("can_close_early")) and "early" in cond and any(
        w in cond for w in ("occurs", "happens", "is announced", "takes place"))


def parse_deadline(text):
    """The deadline written in the text, as an ET datetime, or None.

    'before <day>' = the START of that day (midnight ET); 'by <day>' = the END of that day.
    'before July 2026' = Jul 1; 'by July 2026' / 'by the end of July 2026' = Aug 1.
    'before 2027' = Jan 1, 2027; 'by 2026' / 'by the end of 2026' = Jan 1, 2027."""
    if not text:
        return None
    for kind, pat in _PATTERNS:
        g = pat.search(text)
        if not g:
            continue
        word = g.group(1).lower()
        end_of = "end of" in g.group(0).lower()
        try:
            if kind == "mdy":
                day = datetime(int(g.group(4)), MONTHS[g.group(2).lower()[:3]], int(g.group(3)), tzinfo=ET)
            elif kind == "dmy":
                day = datetime(int(g.group(4)), MONTHS[g.group(3).lower()[:3]], int(g.group(2)), tzinfo=ET)
            elif kind == "my":
                y, mo = int(g.group(3)), MONTHS[g.group(2).lower()[:3]]
                if word == "before" and not end_of:
                    return datetime(y, mo, 1, tzinfo=ET)
                return datetime(y + (mo == 12), mo % 12 + 1, 1, tzinfo=ET)
            else:
                y = int(g.group(2))
                if not 2020 <= y <= 2040:
                    continue
                if word == "before" and not end_of:
                    return datetime(y, 1, 1, tzinfo=ET)
                return datetime(y + 1, 1, 1, tzinfo=ET)
        except ValueError:
            continue
        return day if word == "before" else day + timedelta(days=1)
    return None


def deadline_of(m):
    """Scheduled deadline (UTC) read from the rules first, then the title. None if unreadable."""
    for field in ("rules_primary", "title", "yes_sub_title"):
        d = parse_deadline(m.get(field))
        if d is not None:
            return d.astimezone(timezone.utc)
    return None


def kept_series(series):
    return [s for s in series if s.get("category") not in SKIP_CATS
            and s.get("frequency") not in SKIP_FREQ]


# Tags whose series are numbers, rankings or award winners, almost never "by <date>" questions.
# They are listed LAST (not dropped), so a rate-limited run reaches the likely series first.
UNLIKELY_TAGS = {"Awards", "Grammys", "Oscars", "Music charts", "Rotten Tomatoes", "Music Streams",
                 "Inflation", "Growth", "Jobs & Economy", "Housing", "Fed", "KPIs", "Critics Choice Awards",
                 "Music", "Movies"}
_KW = re.compile(r"\b(before|by|until|when will|deadline)\b", re.I)


def priority(s, hinted=()):
    """0 = title reads like a by-date question or the lake saw a dated market in it,
    1 = ordinary, 2 = unlikely tags. Order only: every kept series is still listed."""
    if _KW.search(s.get("title") or "") or s["ticker"] in hinted:
        return 0
    return 2 if set(s.get("tags") or []) & UNLIKELY_TAGS else 1


def lake_hints():
    """Series where the edge-factory lake (data/lake/lake.db, read-only, if present) already holds
    a settled market with a readable deadline. Used only to list those series first."""
    db = ROOT / "data" / "lake" / "lake.db"
    if not db.exists():
        return set()
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)
        rows = con.execute("SELECT series, rules_primary, title FROM markets").fetchall()
        con.close()
    except sqlite3.Error:
        return set()
    return {s for s, r, t in rows if parse_deadline(r) or parse_deadline(t)}


def _list_series(s):
    out = []
    for path, extra, tier in (("/historical/markets", {}, "historical"),
                              ("/markets", {"status": "settled"}, "live")):
        ms = http.paged(path, "markets", {"series_ticker": s["ticker"], "limit": 1000, **extra},
                        cache=True, max_pages=20)
        for m in ms:
            m["_tier"], m["_category"] = tier, s.get("category") or "Other"
        out += ms
    return out


def _vol(m):
    try:
        return float(m.get("volume_fp", m.get("volume")) or 0)
    except (TypeError, ValueError):
        return 0.0


def slim(m):
    keep = ["ticker", "event_ticker", "title", "yes_sub_title", "open_time", "close_time",
            "expected_expiration_time", "latest_expiration_time", "settlement_ts", "result",
            "early_close_condition", "rules_primary", "_tier", "_category"]
    out = {k: m.get(k) for k in keep}
    out["volume"] = _vol(m)
    d = deadline_of(m)
    out["deadline"] = d.isoformat() if d else None
    return out


def _keep(m):
    if m.get("result") not in ("yes", "no") or not is_by_date(m):
        return False
    close = m.get("close_time")
    return bool(close) and datetime.fromisoformat(close.replace("Z", "+00:00")) >= SINCE


def _series_job(s, with_candles):
    """List one series; keep its by-date markets; fetch their candles straight away (so a
    stopped, rate-limited run still leaves complete markets behind)."""
    found = [slim(m) for m in _list_series(s) if _keep(m)]
    if with_candles:
        for m in found:
            if m["deadline"] and m["volume"] >= 1:
                try:
                    candles_for(m)
                except Exception as e:      # one bad market must not stop the run
                    print(f"  candles failed {m['ticker']}: {e}", flush=True)
    return found


def _save(found):
    rows = sorted(found.values(), key=lambda m: m["close_time"])
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = MARKETS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows), encoding="utf-8")
    tmp.replace(MARKETS_FILE)
    return rows


def collect_markets(verbose=True, workers=4, with_candles=True):
    hinted = lake_hints()
    series = sorted(kept_series(http.get("/series", {}, cache=True)["series"]),
                    key=lambda s: (priority(s, hinted), s["ticker"]))
    if verbose:
        print(f"listing settled markets in {len(series)} series (likely by-date series first) ...", flush=True)
    found = {m["ticker"]: m for m in load_markets()}
    done = failed = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(_series_job, s, with_candles) for s in series]
        for f in as_completed(futs):
            done += 1
            try:
                got = f.result()
            except Exception as e:          # rate-limited past all retries: re-run later picks it up
                failed += 1
                print(f"  series failed: {e}", flush=True)
                continue
            for m in got:
                found[m["ticker"]] = m
            if done % 100 == 0:
                _save(found)
                if verbose:
                    print(f"  {done}/{len(series)} series, {len(found)} by-date markets", flush=True)
    rows = _save(found)
    if verbose:
        n_dl = sum(1 for r in rows if r["deadline"])
        print(f"done: {len(rows)} settled by-date markets ({n_dl} with a readable deadline); "
              f"{failed} series failed (re-run to retry)", flush=True)
    return rows


def _ts(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def candles_for(m):
    """Daily candles over the whole life of one market (cached per ticker)."""
    f = CANDLE_DIR / f"{m['ticker']}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    params = {"start_ts": _ts(m["open_time"]) - 86400, "end_ts": _ts(m["close_time"]) + 86400,
              "period_interval": 1440}
    if m["_tier"] == "historical":
        data = http.get(f"/historical/markets/{m['ticker']}/candlesticks", params)
    else:
        s = m["event_ticker"].split("-", 1)[0]
        data = http.get(f"/series/{s}/markets/{m['ticker']}/candlesticks", params)
    cs = (data or {}).get("candlesticks") or []
    CANDLE_DIR.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(cs), encoding="utf-8")
    return cs


def collect_candles(markets, verbose=True, workers=4, min_volume=1):
    todo = [m for m in markets if m["deadline"] and m["volume"] >= min_volume]
    if verbose:
        print(f"daily candles for {len(todo)} traded by-date markets ...", flush=True)
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(candles_for, m): m for m in todo}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                f.result()
            except Exception as e:          # one bad market must not stop the run
                print(f"  candles failed {futs[f]['ticker']}: {e}", flush=True)
            if verbose and i % 500 == 0:
                print(f"  {i}/{len(todo)}", flush=True)


def load_markets():
    return json.loads(MARKETS_FILE.read_text(encoding="utf-8")) if MARKETS_FILE.exists() else []


def load_candles(ticker):
    f = CANDLE_DIR / f"{ticker}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
