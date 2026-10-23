"""The data lake: one local SQLite copy of Kalshi's settled history (data/lake/lake.db).

Plain words: every research session used to download its own markets and prices. The lake
keeps one copy on disk, filled once and topped up every night, so a new idea costs a few
seconds of local maths instead of an hour of downloads.

What is stored (all public, read-only):
  series   every series: category, frequency, fee type, fee multiplier
  markets  every settled market since 2025-04-01 that is not a 15-minute market and not a
           multi-leg combo: times, result, settle value, volume, strike, rules text
  candles  hourly Yes bid/ask/trade prices (close, low, high) and volume, for the last
           CANDLE_DAYS of each market's life, for markets that traded at all (volume > 0)
  trades   the trade tape (each fill: time, which side the taker bought, price, size) where
           a sibling session already downloaded it, plus markets the miner asks for
Bookkeeping tables (candle_done, tape_done, state) make every step resumable: a run that is
stopped picks up where it left off, and nothing that already landed is fetched again.
"""
import gzip
import json
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import DB_PATH, LAKE, ROOT

SINCE = datetime(2025, 4, 1, tzinfo=timezone.utc)
HIST_CUTOFF = datetime(2026, 8, 23, tzinfo=timezone.utc)   # /historical/cutoff, read 2026-10-22
CANDLE_DAYS = 9          # hourly candles kept for the last 9 days of a market (7-day rules + a 2-day lookback)
CANDLE_CAP = 9000        # Kalshi answers at most ~10,000 candles per request: tickers x hours under this

SCHEMA = """
CREATE TABLE IF NOT EXISTS series (
  ticker TEXT PRIMARY KEY, category TEXT, frequency TEXT, title TEXT,
  fee_type TEXT, fee_multiplier REAL, settlement_sources TEXT);
CREATE TABLE IF NOT EXISTS markets (
  ticker TEXT PRIMARY KEY, event_ticker TEXT, series TEXT, tier TEXT,
  open_ts INTEGER, close_ts INTEGER, exp_ts INTEGER, latest_exp_ts INTEGER, settle_ts INTEGER,
  can_close_early INTEGER, result TEXT, settle_value TEXT, volume REAL, open_interest REAL,
  strike_type TEXT, floor_strike REAL, cap_strike REAL, title TEXT, yes_sub_title TEXT,
  rules_primary TEXT, rules_secondary TEXT, last_price REAL, source TEXT);
CREATE INDEX IF NOT EXISTS markets_close ON markets(close_ts);
CREATE INDEX IF NOT EXISTS markets_series ON markets(series);
CREATE INDEX IF NOT EXISTS markets_event ON markets(event_ticker);
CREATE TABLE IF NOT EXISTS candles (
  ticker TEXT, period INTEGER, end_ts INTEGER,
  bid REAL, ask REAL, bid_lo REAL, bid_hi REAL, ask_lo REAL, ask_hi REAL,
  px REAL, px_lo REAL, px_hi REAL, volume REAL, open_interest REAL,
  PRIMARY KEY (ticker, period, end_ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS candle_done (
  ticker TEXT, period INTEGER, start_ts INTEGER, end_ts INTEGER, n INTEGER, fetched_at TEXT,
  PRIMARY KEY (ticker, period)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS trades (
  trade_id TEXT PRIMARY KEY, ticker TEXT, ts REAL, taker_yes INTEGER, yes_px REAL, count REAL);
CREATE INDEX IF NOT EXISTS trades_ticker ON trades(ticker, ts);
CREATE TABLE IF NOT EXISTS tape_done (
  ticker TEXT PRIMARY KEY, complete INTEGER, n INTEGER, fetched_at TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path=DB_PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    return con


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_state(con, key, default=None):
    r = con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return json.loads(r[0]) if r else default


def set_state(con, key, value):
    con.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, json.dumps(value)))


# ---------------- parsing Kalshi objects into rows ----------------

def _ts(s):
    if not s:
        return None
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def series_of(event_ticker):
    return event_ticker.split("-", 1)[0]


def series_row(s):
    return (s["ticker"], s.get("category") or "Other", s.get("frequency") or "", s.get("title") or "",
            s.get("fee_type") or "", _f(s.get("fee_multiplier")) or 1.0,
            json.dumps([x.get("name") or x.get("url") for x in (s.get("settlement_sources") or [])]))


def market_row(m, source):
    close = _ts(m.get("close_time"))
    return (m["ticker"], m["event_ticker"], series_of(m["event_ticker"]),
            "historical" if close and close < HIST_CUTOFF.timestamp() else "live",
            _ts(m.get("open_time")), close, _ts(m.get("expected_expiration_time")),
            _ts(m.get("latest_expiration_time")), _ts(m.get("settlement_ts")),
            1 if m.get("can_close_early") else 0, m.get("result"), m.get("expiration_value"),
            _f(m.get("volume_fp", m.get("volume"))), _f(m.get("open_interest_fp", m.get("open_interest"))),
            m.get("strike_type"), _f(m.get("floor_strike")), _f(m.get("cap_strike")),
            m.get("title"), m.get("yes_sub_title"), m.get("rules_primary"), m.get("rules_secondary"),
            _f(m.get("last_price_dollars", m.get("last_price"))), source)


def usable_market(m):
    """Settled yes/no binary, not a multi-leg combo. (15-minute series are dropped by series frequency.)"""
    if m.get("result") not in ("yes", "no"):
        return False
    if m.get("market_type", "binary") != "binary":
        return False
    if m.get("mve_collection_ticker") or m["event_ticker"].startswith("KXMVE"):
        return False
    close = _ts(m.get("close_time"))
    return close is not None and close >= SINCE.timestamp()


def _side(c, name):
    s = c.get(name) or {}
    g = lambda k: _f(s.get(k + "_dollars", s.get(k)))
    # very old cached candles store cents as integers
    out = [g("close"), g("low"), g("high")]
    return [None if v is None else (v / 100 if v > 1.5 else v) for v in out]


def candle_row(ticker, period, c):
    bid, bid_lo, bid_hi = _side(c, "yes_bid")
    ask, ask_lo, ask_hi = _side(c, "yes_ask")
    px, px_lo, px_hi = _side(c, "price")
    return (ticker, period, int(c["end_period_ts"]), bid, ask, bid_lo, bid_hi, ask_lo, ask_hi,
            px, px_lo, px_hi, _f(c.get("volume_fp", c.get("volume"))) or 0.0,
            _f(c.get("open_interest_fp", c.get("open_interest"))))


def infer_period(candles):
    """Candle length in minutes (1, 60 or 1440) from the end times, or None if it cannot be told."""
    ends = sorted({int(c["end_period_ts"]) for c in candles})
    if not ends:
        return None
    if any(e % 3600 for e in ends):
        return 1
    diffs = [b - a for a, b in zip(ends, ends[1:])]
    if all(e % 86400 == 0 for e in ends) and (not diffs or min(diffs) >= 86400):
        return 1440 if diffs else None       # one midnight candle alone is ambiguous
    return 60


def hourly_from_minutes(rows):
    """Roll minute candle rows up into hourly rows: last close, lowest low, highest high, summed volume."""
    by = defaultdict(list)
    for r in rows:
        by[(r[0], -(-r[2] // 3600) * 3600)].append(r)
    out = []
    for (t, end), rs in by.items():
        rs.sort(key=lambda r: r[2])
        last = rs[-1]
        lo = lambda i: min((r[i] for r in rs if r[i] is not None), default=None)
        hi = lambda i: max((r[i] for r in rs if r[i] is not None), default=None)
        px = next((r[9] for r in reversed(rs) if r[9] is not None), None)
        out.append((t, 60, end, last[3], last[4], lo(5), hi(6), lo(7), hi(8), px, lo(10), hi(11),
                    sum(r[12] or 0 for r in rs), last[13]))
    return out


def insert_series(con, items):
    con.executemany("INSERT OR REPLACE INTO series VALUES (?,?,?,?,?,?,?)", [series_row(s) for s in items])


def insert_markets(con, items, source):
    rows = [market_row(m, source) for m in items if usable_market(m)]
    con.executemany(f"INSERT OR REPLACE INTO markets VALUES ({','.join('?' * 23)})", rows)
    return len(rows)


def insert_candles(con, rows):
    con.executemany(f"INSERT OR REPLACE INTO candles VALUES ({','.join('?' * 14)})", rows)


def insert_trades(con, ticker, fills):
    """fills: [ts, taker_yes, yes_px, count] or [..., trade_id]. The housemap cache drops the
    trade id, so one is made from the ticker, time, price, size and position."""
    con.executemany("INSERT OR IGNORE INTO trades VALUES (?,?,?,?,?,?)",
                    [(f[4] if len(f) >= 5 else f"{ticker}|{f[0]}|{f[2]}|{f[3]}|{i}", ticker, f[0], f[1], f[2], f[3])
                     for i, f in enumerate(fills) if len(f) >= 4])


# ---------------- importing sibling sessions' caches ----------------

CACHE_DIRS = ["favorites", "commodities", "sports", "longshot", "knownbefore", "mentions",
              "rulequirks", "crossvenue", "housemap"]


def _classify_and_insert(con, d, fifteen, stats):
    """Recognise a cached Kalshi answer by its shape and load it."""
    if not isinstance(d, dict):
        return
    if isinstance(d.get("series"), list) and d["series"] and "ticker" in d["series"][0]:
        insert_series(con, d["series"])
        stats["series_lists"] += 1
    if isinstance(d.get("markets"), list) and d["markets"]:
        first = d["markets"][0]
        if "result" in first and "event_ticker" in first:
            ms = [m for m in d["markets"] if series_of(m["event_ticker"]) not in fifteen]
            stats["markets"] += insert_markets(con, ms, "cache")
        elif "market_ticker" in first and "candlesticks" in first:       # batch candles answer
            for x in d["markets"]:
                _load_candles(con, x["market_ticker"], x.get("candlesticks") or [], fifteen, stats)
    if "candlesticks" in d and "ticker" in d:
        _load_candles(con, d["ticker"], d.get("candlesticks") or [], fifteen, stats)
    if "market_tickers" in d and "market_candlesticks" in d:
        for t, cs in zip(d["market_tickers"], d["market_candlesticks"]):
            _load_candles(con, t, cs or [], fifteen, stats)


def _load_candles(con, ticker, cs, fifteen, stats):
    if not cs or series_of(ticker) in fifteen:
        return
    period = infer_period(cs)
    if period is None:
        return
    rows = [candle_row(ticker, period, c) for c in cs if "end_period_ts" in c]
    if period == 1:
        rows = hourly_from_minutes(rows)
    insert_candles(con, rows)
    stats["candles"] += len(rows)


def import_caches(con, root=ROOT, verbose=True):
    """Load every cached Kalshi answer that sibling sessions saved under data/cache/.

    Market lists, candles (per market, per event, batch) and trade tapes are recognised by
    their shape. Minute candles are rolled up to hourly. Files already imported are skipped
    (by name and size), so this is cheap to re-run."""
    fifteen = {r[0] for r in con.execute("SELECT ticker FROM series WHERE frequency='fifteen_min'")}
    seen = set(get_state(con, "imported_files", []))
    stats = defaultdict(int)
    base = Path(root) / "data" / "cache"
    files = []
    for name in CACHE_DIRS:
        files += [p for p in (base / name).rglob("*.json") if p.is_file()]
        files += [p for p in (base / name).rglob("*.json.gz") if p.is_file()]
    t0, done = time.time(), 0
    for p in files:
        key = f"{p.relative_to(base)}|{p.stat().st_size}"
        if key in seen:
            continue
        try:
            raw = p.read_bytes()
            d = json.loads(gzip.decompress(raw) if p.suffix == ".gz" else raw)
        except (ValueError, OSError):
            stats["unreadable"] += 1
            seen.add(key)
            continue
        if p.suffix == ".gz" and "trades" in p.parts and isinstance(d, dict) and "fills" in d:
            ticker = p.name[:-len(".json.gz")]
            if series_of(ticker) not in fifteen:
                insert_trades(con, ticker, d["fills"])
                con.execute("INSERT OR REPLACE INTO tape_done VALUES (?,?,?,?,?)",
                            (ticker, 1 if d.get("complete") else 0, len(d["fills"]), now_iso(), "housemap"))
                stats["tapes"] += 1
                stats["fills"] += len(d["fills"])
        else:
            _classify_and_insert(con, d, fifteen, stats)
        seen.add(key)
        done += 1
        if done % 2000 == 0:
            con.commit()
            set_state(con, "imported_files", sorted(seen))
            if verbose:
                print(f"  imported {done} cache files ({time.time() - t0:.0f}s)", flush=True)
    set_state(con, "imported_files", sorted(seen))
    con.commit()
    return dict(stats)


def mark_imported_coverage(con):
    """Credit cached candles as complete where the cache's window is known to cover the market.

    argus/commodities fetched hourly candles per EVENT for the last 7 days of every live-tier
    commodity event (and minute candles for the last 3 hours). For an event whose markets all
    lived 7 days or less, and whose cached candles reach the last 2 hours before close, those
    candles are the whole life of every market in it, so the lake does not fetch them again."""
    con.execute("CREATE INDEX IF NOT EXISTS markets_event ON markets(event_ticker)")
    cut = int(HIST_CUTOFF.timestamp())
    last = dict(con.execute("""SELECT m.event_ticker, MAX(c.end_ts) FROM candles c JOIN markets m ON m.ticker = c.ticker
                               JOIN series s ON s.ticker = m.series
                               WHERE c.period = 60 AND s.category = 'Commodities' AND m.close_ts >= ?
                               GROUP BY m.event_ticker""", (cut,)).fetchall())
    ncand = dict(con.execute("""SELECT c.ticker, COUNT(*) FROM candles c JOIN markets m ON m.ticker = c.ticker
                                JOIN series s ON s.ticker = m.series
                                WHERE c.period = 60 AND s.category = 'Commodities' AND m.close_ts >= ?
                                GROUP BY c.ticker""", (cut,)).fetchall())
    evs = defaultdict(list)
    for t, ev, o, c, v in con.execute("""SELECT m.ticker, m.event_ticker, m.open_ts, m.close_ts, m.volume FROM markets m
                                         JOIN series s ON s.ticker = m.series
                                         WHERE s.category = 'Commodities' AND m.close_ts >= ?""", (cut,)):
        evs[ev].append((t, o, c, v))
    n = 0
    for ev, ms in evs.items():
        close = max(m[2] for m in ms)
        if last.get(ev) is None or max(m[2] - m[1] for m in ms) > 7 * 86400 or last[ev] < close - 2 * 3600:
            continue
        for t, o, c, v in ms:
            if v and v > 0:
                cur = con.execute("INSERT OR IGNORE INTO candle_done VALUES (?,?,?,?,?,?)",
                                  (t, 60, o, c, ncand.get(t, 0), "import:commodities"))
                n += cur.rowcount
    con.commit()
    return n


def credit_historical_imports(con):
    """Historical-tier markets whose hourly candles came from a sibling cache count as fetched.

    Those caches were cut at fixed windows (argus/favorites: the 2 days up to 1 hour before close;
    others similar), so decisions further out than the cached window simply find no quote and
    make no bet, and maker fills in an uncached last hour are missed (conservative)."""
    rows = con.execute("""SELECT c.ticker, m.open_ts, m.close_ts, COUNT(*) FROM candles c
                          JOIN markets m ON m.ticker = c.ticker
                          LEFT JOIN candle_done d ON d.ticker = c.ticker AND d.period = 60
                          WHERE c.period = 60 AND m.tier = 'historical' AND (d.ticker IS NULL OR d.n = 0)
                          GROUP BY c.ticker""").fetchall()
    con.executemany("INSERT OR REPLACE INTO candle_done VALUES (?,?,?,?,?,?)",
                    [(t, 60, o, c, n, "import:cache") for t, o, c, n in rows])
    con.commit()
    return len(rows)


# ---------------- reading back ----------------

def counts(con):
    q = lambda s: con.execute(s).fetchone()[0]
    return {"series": q("SELECT COUNT(*) FROM series"), "markets": q("SELECT COUNT(*) FROM markets"),
            "markets_traded": q("SELECT COUNT(*) FROM markets WHERE volume > 0"),
            "markets_with_candles": q("SELECT COUNT(DISTINCT ticker) FROM candle_done WHERE period=60"),
            "candles": q("SELECT COUNT(*) FROM candles"), "trades": q("SELECT COUNT(*) FROM trades"),
            "tapes": q("SELECT COUNT(*) FROM tape_done")}


def disk_bytes(path=DB_PATH):
    return sum(p.stat().st_size for p in Path(path).parent.glob(Path(path).name + "*") if p.is_file())


def candles_for(con, tickers, period=60):
    """{ticker: [candle dicts sorted by end_ts]} for many markets at once."""
    out = defaultdict(list)
    tickers = list(tickers)
    cols = ["end_ts", "bid", "ask", "bid_lo", "bid_hi", "ask_lo", "ask_hi", "px", "px_lo", "px_hi", "volume"]
    for i in range(0, len(tickers), 500):
        chunk = tickers[i:i + 500]
        for r in con.execute(f"SELECT ticker, {','.join(cols)} FROM candles WHERE period=? AND ticker IN "
                             f"({','.join('?' * len(chunk))}) ORDER BY ticker, end_ts", [period] + chunk):
            out[r[0]].append(dict(zip(cols, r[1:])))
    return out


def trades_for(con, tickers):
    """{ticker: [(ts, taker_yes, yes_px, count)]} for markets whose tape is in the lake."""
    out = defaultdict(list)
    tickers = list(tickers)
    for i in range(0, len(tickers), 500):
        chunk = tickers[i:i + 500]
        for r in con.execute(f"SELECT ticker, ts, taker_yes, yes_px, count FROM trades WHERE ticker IN "
                             f"({','.join('?' * len(chunk))}) ORDER BY ticker, ts", chunk):
            out[r[0]].append(r[1:])
    return out


MARKET_COLS = ["ticker", "event_ticker", "series", "tier", "open_ts", "close_ts", "exp_ts", "latest_exp_ts",
               "settle_ts", "can_close_early", "result", "settle_value", "volume", "open_interest",
               "strike_type", "floor_strike", "cap_strike", "title", "yes_sub_title"]


def markets(con, where="1=1", params=()):
    """Market dicts joined with their series' category, frequency and fees."""
    cols = ", ".join("m." + c for c in MARKET_COLS)
    sql = (f"SELECT {cols}, s.category, s.frequency, s.fee_type, s.fee_multiplier FROM markets m "
           f"LEFT JOIN series s ON s.ticker = m.series WHERE {where}")
    names = MARKET_COLS + ["category", "frequency", "fee_type", "fee_multiplier"]
    return [dict(zip(names, r)) for r in con.execute(sql, params)]
