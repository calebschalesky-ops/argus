"""Fed reference pricing: Kalshi's FOMC decision markets vs the Atlanta Fed's Market Probability Tracker.

Why this source. Fed funds futures-implied odds (CME FedWatch) need a paid CME API subscription and
CME's site blocks scripts. The free, official alternative is the Atlanta Fed's Market Probability
Tracker (MPT): a daily distribution of the average 3-month SOFR rate over each quarterly CME
contract window, estimated from CME 3-month SOFR options (CME data used by the Atlanta Fed with
CME's permission). Its full daily history since 2023-03-29 is one public file, mpt_histdata.xlsx.

Mapping a window distribution to one meeting (written down before the backtest ran):
  - meeting M, decision D. A window's average rate = a * (rate before M) + (1 - a) * (rate after M),
    a = share of the window's days before M, plus the next meeting M2's move for the days after M2.
  - Candidate windows: the one containing M and the first one starting after M. Use the one with the
    smallest M2 share c (days after M2 / window days), then the largest post-M share; price the meeting
    only if c <= 0.15 (a 25 bp move at M2 then shifts the average by under 4 bp) and 1 - a >= 0.25.
    Other meetings are skipped and counted.
  - Rate before M = current target midpoint + basis; basis = FRED SOFR minus the midpoint, mean of the
    last 10 published days on or before the observation date (SOFR does not sit at the midpoint).
  - Each bin of the average is mapped back to the post-M rate: r = (avg - a * r_now) / (1 - a), and
    D = r - r_now. Boundaries halfway between outcomes (+-12.5 bp, +-37.5 bp), probability spread
    uniformly inside each bin.
  - The file lists only bins above about 1%; the missing tail mass is recovered from its "Prob: cut"
    (average below the current range) and "Prob: hike" (above) fields as one extra 25 bp bin each side.
Moments priced: 4 PM New York, 7 days and 1 day before the decision day. Kalshi = hourly candle
ending then. MPT = the latest observation dated at least one day before (the tracker posts the
previous day's data in the morning), so the reference is always older than the Kalshi quote.
Same three rules as crypto (spec / ev3 / maker3). Rows -> refprice_fed_backtest_log.csv.
"""
import csv
import io
import math
import re
import zipfile
import xml.etree.ElementTree as ET_XML
from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import log
from . import CACHE, FED_BT_LOG, crypto, http, report

NY = ZoneInfo("America/New_York")
MPT_URL = ("https://www.atlantafed.org/-/media/Project/Atlanta/FRBA/Documents/research-and-data/data/"
           "market-probability-tracker/mpt_histdata.xlsx")
MPT_FILE = CACHE / "mpt" / "mpt_histdata.xlsx"
SERIES = ("FEDDECISION", "KXFEDDECISION")
DAYS_BEFORE = (7, 1)
# Kalshi outcome codes -> decision in bp (C26 / TC25 = "cut more than 25", H26 / TH50 = "hike more than ...")
OUTCOME = {"C25": -25, "H0": 0, "H25": 25, "H50": 50}
TAIL_DOWN = {"C26": -25, "TC25": -25}      # strictly below this
TAIL_UP = {"H26": 25, "TH50": 50}          # strictly above this


# ---------------- the Atlanta Fed file ----------------

def fetch_mpt(refresh=False):
    if refresh or not MPT_FILE.exists():
        http.check_allowed(MPT_URL)
        http._pace("www.atlantafed.org")
        r = http._session.get(MPT_URL, timeout=120)
        http._log_call(MPT_URL, {}, r.status_code)
        r.raise_for_status()
        MPT_FILE.parent.mkdir(parents=True, exist_ok=True)
        MPT_FILE.write_bytes(r.content)
    return MPT_FILE


def _xlsx_rows(path, sheet="DATA"):
    """Rows of one sheet, read straight from the xlsx XML (the file's drawings trip openpyxl)."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    z = zipfile.ZipFile(path)
    shared = ["".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t"))
              for si in ET_XML.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns)]
    names = [s.attrib.get("name") for s in ET_XML.fromstring(z.read("xl/workbook.xml")).find("m:sheets", ns)]
    root = ET_XML.fromstring(z.read(f"xl/worksheets/sheet{names.index(sheet) + 1}.xml"))
    for r in root.find("m:sheetData", ns).findall("m:row", ns):
        out = []
        for c in r.findall("m:c", ns):
            v = c.find("m:v", ns)
            v = None if v is None else v.text
            if c.attrib.get("t") == "s" and v is not None:
                v = shared[int(v)]
            out.append(v)
        yield out


def _excel_date(v):
    return date(1899, 12, 30) + timedelta(days=int(float(v)))


def load_mpt(path=None):
    """{obs_date: {window_start: {"range": (lo, hi), "bins": [(lo, hi, prob)]}}} in bp and fractions."""
    path = path or fetch_mpt()
    out = defaultdict(dict)
    rows = _xlsx_rows(path)
    next(rows)
    for r in rows:
        if len(r) < 5 or not r[0]:
            continue
        obs, start = date.fromisoformat(r[0]), _excel_date(r[1])
        rng = tuple(int(x) for x in re.findall(r"-?\d+", r[2])[:2])
        w = out[obs].setdefault(start, {"range": rng, "bins": []})
        m = re.match(r"Prob: (-?\d+)bps - (-?\d+)bps", r[3] or "")
        if m:
            w["bins"].append((int(m.group(1)), int(m.group(2)), float(r[4]) / 100))
        elif r[3] in ("Prob: cut", "Prob: hike"):
            w[r[3][6:]] = float(r[4]) / 100
    for windows in out.values():
        for w in windows.values():
            w["bins"] = with_tails(w)
    return dict(out)


def with_tails(w):
    """Add the omitted tail mass back as one 25 bp bin below and one above the listed bins."""
    bins = sorted(w["bins"])
    if not bins:
        return bins
    lo, hi = w["range"]
    below = (w.get("cut") or 0) - sum(p * max(0, min(b, lo) - a) / (b - a) for a, b, p in bins)
    above = (w.get("hike") or 0) - sum(p * max(0, b - max(a, hi)) / (b - a) for a, b, p in bins)
    if below > 1e-4:
        bins.insert(0, (bins[0][0] - 25, bins[0][0], below))
    if above > 1e-4:
        bins.append((bins[-1][1], bins[-1][1] + 25, above))
    return bins


def sofr_history():
    """[(date, SOFR in bp)] from FRED (official, free, no key)."""
    t = http.get("https://fred.stlouisfed.org/graph/fredgraph.csv", {"id": "SOFR"}, text=True)
    out = []
    for row in csv.reader(io.StringIO(t)):
        try:
            out.append((date.fromisoformat(row[0]), float(row[1]) * 100))
        except (ValueError, IndexError):
            continue
    return out


# ---------------- maths ----------------

def prob_between(bins, lo, hi):
    """P(lo <= avg < hi), probability spread uniformly inside each bin; renormalised to the bins' total."""
    tot = sum(p for _, _, p in bins) or 1.0
    s = 0.0
    for a, b, p in bins:
        ov = max(0.0, min(b, hi) - max(a, lo))
        s += p * ov / (b - a)
    return s / tot


def post_meeting_bins(bins, a, r_now):
    """Map bins of the window average to bins of the post-meeting rate: r = (avg - a*r_now)/(1 - a)."""
    f = lambda x: (x - a * r_now) / (1 - a)
    return [(f(lo), f(hi), p) for lo, hi, p in bins]


def decision_probs(bins, centre):
    """P(decision) from a distribution of the post-meeting rate; `centre` = rate if nothing changes (bp)."""
    edge = lambda d: centre + d
    p = {}
    for code, d in OUTCOME.items():
        p[code] = prob_between(bins, edge(d - 12.5), edge(d + 12.5))
    for code, d in TAIL_DOWN.items():
        p[code] = prob_between(bins, -1e9, edge(d - 12.5))
    for code, d in TAIL_UP.items():
        p[code] = prob_between(bins, edge(d + 12.5), 1e9)
    return p


def window_shares(window_start, M, next_meeting, days=91):
    """(a, c): share of the window's days before meeting M, and after the next meeting M2.
    A CME 3-month SOFR window runs from one IMM date to the next (about 91 days)."""
    end = window_start + timedelta(days=days)
    a = min(max((M - window_start).days, 0), days) / days
    c = 0.0 if next_meeting is None or next_meeting >= end else (end - max(next_meeting, window_start)).days / days
    return a, c


def pick_window(starts, M, next_meeting, max_c=0.15, min_post=0.25):
    """The window that isolates meeting M best, or None. Returns (start, a, c)."""
    cands = []
    containing = [s for s in starts if s <= M < s + timedelta(days=91)]
    after = [s for s in starts if s > M]
    for s in containing[-1:] + after[:1]:
        a, c = window_shares(s, M, next_meeting)
        if c <= max_c and 1 - a >= min_post:
            cands.append((c, -(1 - a), s, a))
    if not cands:
        return None
    c, _, s, a = min(cands)
    return s, a, c


def basis_at(sofr, obs, rng):
    """SOFR minus the target midpoint, mean of the last 10 published days on or before obs."""
    ds = [d for d, _ in sofr]
    i = bisect_right(ds, obs)
    last = [v for _, v in sofr[max(0, i - 10):i]]
    return (sum(last) / len(last) - (rng[0] + rng[1]) / 2) if last else 0.0


# ---------------- Kalshi ----------------

def meetings():
    """[(event_ticker, decision date, markets)] for every settled and open decision event."""
    out = []
    for s in SERIES:
        for st in ("settled", "open"):
            evs = (http.kalshi("/events", {"series_ticker": s, "limit": 200, "status": st}) or {}).get("events", [])
            for e in evs:
                ms = []
                for path in ("/markets", "/historical/markets"):
                    ms = (http.kalshi(path, {"event_ticker": e["event_ticker"], "limit": 100}, cache=(st == "settled")) or {}).get("markets", [])
                    if ms:
                        for m in ms:
                            m["_tier"] = "historical" if path.startswith("/historical") else "live"
                        break
                if ms:
                    d = datetime.fromisoformat(ms[0]["close_time"].replace("Z", "+00:00")).astimezone(NY).date()
                    out.append((e["event_ticker"], d, ms))
    return sorted(out, key=lambda x: x[1])


def candles(m, start_ts, end_ts):
    if m["_tier"] == "historical":
        d = http.kalshi(f"/historical/markets/{m['ticker']}/candlesticks",
                        {"start_ts": start_ts, "end_ts": end_ts, "period_interval": 60}, cache=True) or {}
        return sorted((crypto.parse_candle(c) for c in d.get("candlesticks", [])), key=lambda c: c["end_ts"])
    return crypto.hourly_candles([m["ticker"]], start_ts, end_ts).get(m["ticker"], [])


def code_of(ticker):
    return ticker.rsplit("-", 1)[-1]


# ---------------- backtest ----------------

def run(out=FED_BT_LOG, verbose=True):
    mpt = load_mpt()
    obs_dates = sorted(mpt)
    sofr = sofr_history()
    mtgs = meetings()
    dates = [d for _, d, _ in mtgs]
    rows, skipped = [], defaultdict(int)
    shares = isolation_table(mpt, mtgs)
    for i, (ev, M, ms) in enumerate(mtgs):
        if not all(m.get("result") in ("yes", "no") for m in ms):
            continue                                     # not decided yet
        nxt = dates[i + 1] if i + 1 < len(dates) else None
        close_ts = crypto.ts(ms[0]["close_time"])
        cs = None                                       # Kalshi candles: only fetched if a window isolates M
        for k in DAYS_BEFORE:
            X = M - timedelta(days=k)
            t = int(datetime.combine(X, dtime(16, 0), NY).timestamp())
            j = bisect_right(obs_dates, X - timedelta(days=1))
            if j == 0:
                skipped["before the tracker's history"] += 1
                continue
            obs = obs_dates[j - 1]
            pick = pick_window(sorted(mpt[obs]), M, nxt)
            if pick is None:
                skipped[f"no window isolates the meeting ({k}d)"] += 1
                continue
            ws, a, frac = pick
            w = mpt[obs][ws]
            if cs is None:
                start = int(datetime.combine(M - timedelta(days=max(DAYS_BEFORE) + 1), dtime(0), NY).timestamp())
                cs = {m["ticker"]: candles(m, start, close_ts) for m in ms
                      if code_of(m["ticker"]) in {**OUTCOME, **TAIL_DOWN, **TAIL_UP}}
            b = basis_at(sofr, obs, w["range"])
            r_now = (w["range"][0] + w["range"][1]) / 2 + b
            probs = decision_probs(post_meeting_bins(w["bins"], a, r_now), r_now)
            priced = []
            for m in ms:
                code = code_of(m["ticker"])
                if code not in probs or crypto.ts(m["open_time"]) > t:
                    continue
                bid, ask = crypto.quote_at(cs.get(m["ticker"]), t)
                if not crypto.real_book(bid, ask):
                    continue
                p = min(max(probs[code], 1e-4), 1 - 1e-4)
                later = [c for c in cs[m["ticker"]] if t < c["end_ts"] <= close_ts]
                priced.append({"market": m, "p_yes": p, "bid": bid, "ask": ask,
                               "quotes": crypto.side_quotes(p, bid, ask), "later": later,
                               "tau": (close_ts - t) / (365 * 86400)})
            ref = {"spot": r_now, "atm_iv": 0.0, "n_trades": len(w["bins"]),
                   "smile": type("S", (), {"coef": (b, a, frac)})()}
            rs = crypto.rows_for(ev.split("-")[0], priced, t, close_ts, ref, "refprice-fed-bt", f"{k * 24}")
            for r in rs:
                r["anchor_kind"] = f"atlantafed-mpt obs={obs} window={ws}"
                r["notes"] = r["notes"].replace("smile=", "basis_bp,pre_share,m2_share=")
            rows += rs
            if not priced:
                skipped[f"no real Kalshi book ({k}d)"] += 1
        if verbose:
            print(f"  {ev} {M}: {len(rows)} rows so far", flush=True)
    log.write_log(rows, out)
    return rows, dict(skipped), shares


def isolation_table(mpt, mtgs):
    """For each decided meeting: the best window's M2 share (how much the next meeting leaks in)."""
    obs_dates = sorted(mpt)
    out = []
    dates = [d for _, d, _ in mtgs]
    for i, (ev, M, ms) in enumerate(mtgs):
        j = bisect_right(obs_dates, M - timedelta(days=2))
        if j == 0 or i + 1 >= len(dates):
            continue
        starts = sorted(mpt[obs_dates[j - 1]])
        best = min((window_shares(s, M, dates[i + 1])[::-1] + (s,) for s in starts if s > M), default=None)
        out.append((ev, M, best))
    return out


def main(report_only=False):
    if not report_only:
        rows, skipped, shares = run(verbose=False)
        print("Meeting                decision    first window after   next-meeting share (must be <= 0.15)")
        for ev, M, best in shares:
            print(f"{ev:<22} {M}  {best[2] if best else '-'}           {best[0]:.2f}" if best else f"{ev:<22} {M}  none")
        print(f"\n{len(rows)} rows -> {FED_BT_LOG.name}; skipped: {skipped}")
    print(report.format_table(report.by_engine(FED_BT_LOG)))
