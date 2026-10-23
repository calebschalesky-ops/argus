"""Replay past results nights: official counts as they were captured vs Kalshi's prices just after.

Strict timestamp alignment (tests in tests/test_electionnight.py prove each rule):
  1. A count is usable only from its capture time (`Snapshot.t_known`), never from the state's own
     "as of" stamp, which could be earlier than the moment the public could actually see it.
  2. The Kalshi price used is the close of the first one-minute candle that ends at least
     EXEC_DELAY_S after t_known (and within MAX_QUOTE_WAIT_S). So the price is always from AFTER
     the count was public; a minute of delay is allowed for reading it and acting.
  3. The quote must have a real bid within MAX_SPREAD of the ask (empty books show 1c/99c).
  4. Only markets that were open at that moment, and whose result is known, are scored.
  5. Prior-election data (Pennsylvania 2020) was certified years before the night.

Strategy (paper only): once real reporting exists (GATE_SHARE_IN of the projected vote counted),
take the side the model favours when model - ask - taker fee >= 10 points and the side's price
is inside the shared band (filters.py: 50-99c, never under 10c). One paper contract per market,
the first time it qualifies. A second strategy ("rest") puts a resting order one cent over the
bid instead, counted only if a later trade in the next REST_WINDOW_S printed at that price or better.

Output: electionnight_log.csv rows with engine=electionnight-backtest (take) and
electionnight-backtest-rest (rest). One row per market per scored snapshot, at least
THIN_S apart, so a night with 40 snapshots does not drown a night with 4.
"""
import math
import re
from datetime import datetime, timedelta, timezone

from .. import fees, filters, log
from . import LOG_PATH, http, model, races, sources

ENGINE = "electionnight-backtest"
ENGINE_REST = "electionnight-backtest-rest"
EXEC_DELAY_S = 60
MAX_QUOTE_WAIT_S = 600
MAX_SPREAD = 0.05
GATE_SHARE_IN = 0.10          # "real reporting exists": 10% of the projected final vote counted
GATE_UNITS_IN = 0.50          # ... and at least half the counties have reported something
MIN_GAP_PTS = 10              # after the taker fee
REST_WINDOW_S = 900
THIN_S = 600


# ---------------- official counts ----------------

def parse(rc, doc, t):
    f, by = rc["feed"], rc.get("by", "name")
    if f == "ev":
        return sources.ev_contest(doc, t, rc["cdx"], by=by)
    if f == "ev_statewide":
        return sources.ev_statewide(doc, t, rc["contest_match"], rc["cdx"], by=by)
    if f == "ev_export":
        return sources.ev_export(doc, t, rc["contest_name"], rc["cdx"], by=by)
    if f == "nc_statewide":
        return sources.nc_statewide(doc, t, rc["contest_match"], rc["cdx"], by=by)
    if f == "pa_county":
        return sources.pa_county(doc, t, rc["contest"], rc["cdx"], by=by)
    if f == "pa_statewide":
        return sources.pa_statewide(doc, t, rc["office"], rc["cdx"], by=by)
    raise ValueError(f"unknown feed {f}")


def snapshots(rc, verbose=False):
    """Every distinct capture of the race's official feed(s) during the night, parsed, oldest first.
    A race may list several "feeds" (e.g. Virginia: the export file and the contest page)."""
    out = []
    for feed in rc.get("feeds") or [{}]:
        out += _snapshots(dict(rc, **feed), verbose)
    return sorted(out, key=lambda s: s.t_known)


def _snapshots(rc, verbose=False):
    start = rc["polls_close"] - timedelta(minutes=30)
    caps = sources.captures(rc["cdx"], start, rc["night_end"], prefix=bool(rc.get("match")))
    caps = [(t, u) for t, u in caps if re.search(rc.get("match") or "", u)]
    out = []
    for t, u in caps:
        try:
            raw = sources.archived(t, u, as_text=rc["feed"].startswith(("pa", "nc")))
        except Exception as e:      # a missing or broken capture is skipped, not guessed
            if verbose:
                print(f"  skip capture {t:%H:%M:%S}: {e}")
            continue
        try:
            snap = parse(rc, raw, t) if raw else None
        except (ValueError, KeyError, TypeError, AttributeError) as e:   # an error page, not results
            if verbose:
                print(f"  skip capture {t:%H:%M:%S}: not a results document ({type(e).__name__})")
            continue
        if snap and snap.total() > 0:
            snap.raw = raw
            out.append(snap)
    if verbose:
        print(f"  {rc['id']}: {len(caps)} captures, {len(out)} with votes")
    return out


def prior_for(rc):
    """{unit: {"total": n, "split": {cand: share}}} from the last comparable election, or {}."""
    p = rc.get("prior")
    if not p:
        return {}
    if p["feed"] == "ev_registered":
        # the last captures of the county list and registered voters BEFORE polls closed
        docs = []
        for url in (p["jurisdictions"], p["localities"]):
            caps = sources.captures(url, rc["polls_close"] - timedelta(days=2), rc["polls_close"])
            if not caps:
                return {}
            docs.append(sources.archived(*caps[-1]))
        return {n: {"registered": v} for n, v in sources.ev_registered(*docs).items()}
    if p["feed"] == "nc_registered":
        caps = sources.captures(p["url"], rc["polls_close"] - timedelta(hours=1), rc["polls_close"] + timedelta(minutes=30),
                                prefix=True)
        caps = [c for c in caps if re.search(r"county\.txt", c[1])]
        if not caps:
            return {}
        reg = sources.nc_registered(sources.archived(*caps[0], as_text=True))
        return {n: {"registered": v} for n, v in reg.items()}
    raw = http.public(p["url"], p["params"], cache=True, as_text=True)
    snap = parse({"feed": p["feed"], "contest": "prior", "by": rc.get("by", "name"), "cdx": p["url"]},
                 raw, datetime(1970, 1, 1, tzinfo=timezone.utc))
    out = {}
    for name, u in snap.units.items():
        tot = u.total()
        if tot:
            out[name] = {"total": tot, "split": {c: u.cand(c) / tot for c in u.votes}}
    return out


def expected_total(rc, snap, priors):
    """Pre-election expected final votes for a statewide-only contest."""
    e = rc.get("expected")
    if not e:
        return None
    if "total" in e:
        return e["total"]
    base = sum(x["total"] for x in priors.get(e["prior_of"], {}).values())
    other = sources.pa_statewide(snap.raw, snap.t_known, e["ratio_to_office"], by=rc.get("by", "name"))
    if not base or not other or not other.total():
        return None
    return base * snap.total() / other.total()


# ---------------- Kalshi ----------------

def kalshi_markets(rc):
    """Settled Kalshi markets for this race: the named ones plus every margin bucket."""
    tickers = dict(rc["markets"])
    ms = {}
    for t in tickers:
        d = http.kalshi(f"/historical/markets/{t}", cache=True) or {}
        if d.get("market"):
            ms[t] = d["market"]
    if rc.get("margin_series"):
        for m in http.kalshi_paged("/historical/markets", "markets",
                                   {"series_ticker": rc["margin_series"], "limit": 1000}, cache=True):
            q = races.margin_question(m.get("yes_sub_title") or "")
            if q:
                ms[m["ticker"]] = m
                tickers[m["ticker"]] = q
    return {t: (ms[t], tickers[t]) for t in ms if ms[t].get("result") in ("yes", "no")}


def minute_candles(ticker, start, end):
    """Kalshi one-minute candles for a settled market, as [{end_ts, bid, ask, low, high}]."""
    out = []
    a = int(start.timestamp())
    while a < int(end.timestamp()):
        b = min(a + 6 * 3600, int(end.timestamp()))
        d = http.kalshi(f"/historical/markets/{ticker}/candlesticks",
                        {"start_ts": a, "end_ts": b, "period_interval": 1}, cache=True) or {}
        for c in d.get("candlesticks", []):
            f = lambda side, k: _f((c.get(side) or {}).get(k, (c.get(side) or {}).get(k + "_dollars")))
            out.append({"end_ts": int(c["end_period_ts"]), "bid": f("yes_bid", "close"),
                        "ask": f("yes_ask", "close"), "low": f("price", "low"), "high": f("price", "high")})
        a = b
    return sorted({c["end_ts"]: c for c in out}.values(), key=lambda c: c["end_ts"])


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def quote_after(candles, t_known):
    """(yes_bid, yes_ask, quote_ts) from the first candle ending >= t_known + EXEC_DELAY_S.
    None if that candle is more than MAX_QUOTE_WAIT_S away or the book is empty / too wide."""
    t0 = t_known.timestamp() + EXEC_DELAY_S
    for c in candles:
        if c["end_ts"] < t0:
            continue
        if c["end_ts"] > t0 + MAX_QUOTE_WAIT_S:
            return None
        bid, ask = c["bid"], c["ask"]
        if not bid or not ask or not (0 < bid < ask < 1) or ask - bid > MAX_SPREAD + 1e-9:
            return None
        return bid, ask, c["end_ts"]
    return None


def rest_filled(candles, side, price, from_ts):
    """Did a trade print at our resting price or better within REST_WINDOW_S after we posted?"""
    for c in candles:
        if c["end_ts"] <= from_ts or c["end_ts"] > from_ts + REST_WINDOW_S:
            continue
        if side == "YES" and c["low"] is not None and c["low"] <= price + 1e-9:
            return True
        if side == "NO" and c["high"] is not None and c["high"] >= 1 - price - 1e-9:
            return True
    return False


# ---------------- the model's answer to one market ----------------

def p_yes(proj, q):
    kind = q[0]
    if kind == "beats":
        return model.p_beats(proj, q[1])
    if kind == "majority":
        return model.p_majority(proj, q[1])
    if kind == "margin":
        return model.p_margin_between(proj, q[1], q[2], q[3], q[4])
    raise ValueError(kind)


def gate(proj):
    return proj.share_in >= GATE_SHARE_IN and proj.units_in >= GATE_UNITS_IN * proj.units


def decide(p_yes_, bid, ask, maker_fees=False):
    """Pick the side with the bigger edge; return (side, p_side, quote, gap_after_fee_pts)."""
    best = None
    for side in ("YES", "NO"):
        p = p_yes_ if side == "YES" else 1 - p_yes_
        s_ask, s_bid = filters.side_book(side, bid, ask)
        q = filters.check(p, s_ask, s_bid, maker_fees=maker_fees)
        if q.ask is None:
            continue
        g = (p - q.ask - q.taker_fee) * 100
        if best is None or g > best[3]:
            best = (side, p, q, g)
    return best


def _row(rc, m, q, snap, proj, py, side, p_side, quote, gap, price, fee, bet, engine, extra=""):
    won = (m["result"] == "yes") == (side == "YES")
    bid, ask, qts = quote
    return {
        "logged_at_utc": snap.t_known.strftime("%Y-%m-%dT%H:%M:%SZ"), "engine": engine,
        "series": m["ticker"].split("-")[0], "event_ticker": m["event_ticker"], "market_ticker": m["ticker"],
        "range_label": (m.get("yes_sub_title") or "")[:40], "side": side,
        "model_prob": f"{p_side:.4f}", "market_price": f"{price:.4f}", "gap_pts": f"{gap:+.1f}",
        "worth_a_look": "1" if bet else "0", "paper_contracts": "1" if bet else "0",
        "fee_per_contract": f"{fee:.2f}", "anchor_price": f"{proj.share_in:.3f}", "anchor_kind": "share_counted",
        "vol_daily": "", "horizon_days": "", "event_days": "", "news_shift_pct": "",
        "close_time_utc": m.get("close_time", ""), "status": "settled", "settle_value": m["result"],
        "won": "1" if won else "0",
        "pnl_after_fees": f"{fees.pnl(won, price, 1, fee):.2f}" if bet else "0.00",
        "notes": "; ".join([
            f"race={rc['id']}", f"tier={'county' if proj.units > 1 else 'statewide'}", f"p_yes={py:.4f}", f"yes_mid={(bid + ask) / 2:.4f}",
            f"yes_bid={bid:.2f}", f"yes_ask={ask:.2f}",
            f"t_known={snap.t_known:%Y-%m-%dT%H:%M:%SZ}",
            f"quote_ts={datetime.fromtimestamp(qts, timezone.utc):%Y-%m-%dT%H:%M:%SZ}",
            f"as_of={snap.as_of:%Y-%m-%dT%H:%M:%SZ}" if snap.as_of else "as_of=",
            f"counted={int(proj.counted)}", f"projected={int(proj.total())}",
            f"units_in={proj.units_in}/{proj.units}", f"source=web.archive.org capture of {snap.source}",
        ] + ([extra] if extra else [])),
    }


def replay(rc, verbose=True):
    """All log rows for one race night."""
    priors = {rc["id"]: prior_for(rc)}
    if rc.get("expected", {}).get("prior_of"):
        po = rc["expected"]["prior_of"]
        priors[po] = prior_for(races.race(po))
    snaps = snapshots(rc, verbose)
    mkts = kalshi_markets(rc)
    start = rc["polls_close"] - timedelta(hours=1)
    candles = {t: minute_candles(t, start, rc["night_end"] + timedelta(hours=1)) for t in mkts}
    series_meta = {}
    rows, bet_done, rest_done, last_logged = [], set(), set(), {}
    for snap in snaps:
        exp = expected_total(rc, snap, priors)
        prior = priors.get(rc["id"]) or {n: {"registered": v} for n, v in getattr(snap, "registered", {}).items()}
        proj = model.project(snap, prior, exp, remaining_type=rc.get("remaining_type", "eday"))
        if not gate(proj):
            continue
        for t, (m, q) in mkts.items():
            if not (sources.parse_ts(m.get("open_time")) or start) <= snap.t_known:
                continue
            quote = quote_after(candles[t], snap.t_known)
            if not quote:
                continue
            py = p_yes(proj, q)
            if py is None:
                continue
            s = m["ticker"].split("-")[0]
            if s not in series_meta:
                series_meta[s] = (http.kalshi(f"/series/{s}", cache=True) or {}).get("series")
            mfee, mult = fees.series_maker_fees(series_meta[s])
            side, p_side, qt, gap = decide(py, quote[0], quote[1], mfee)
            take = (t not in bet_done and gap >= MIN_GAP_PTS - 1e-9 and qt.allowed)
            due = t not in last_logged or (snap.t_known - last_logged[t]).total_seconds() >= THIN_S
            if take or due:
                rows.append(_row(rc, m, q, snap, proj, py, side, p_side, quote, gap, qt.ask, qt.taker_fee,
                                 take, ENGINE))
                last_logged[t] = snap.t_known
            if take:
                bet_done.add(t)
            # resting order one cent over the bid, same side, same trigger
            if t not in rest_done and qt.maker_price and qt.band.allows(qt.maker_price):
                mgap = (p_side - qt.maker_price - (qt.maker_fee or 0)) * 100
                if mgap >= MIN_GAP_PTS - 1e-9:
                    filled = rest_filled(candles[t], side, qt.maker_price, quote[2])
                    rows.append(_row(rc, m, q, snap, proj, py, side, p_side, quote, mgap, qt.maker_price,
                                     qt.maker_fee or 0.0, filled, ENGINE_REST,
                                     f"pricing=maker; maker_fill={'yes' if filled else 'no'}"))
                    rest_done.add(t)
    if verbose:
        print(f"  {rc['id']}: {len(mkts)} markets, {len(rows)} rows, "
              f"{sum(r['paper_contracts'] == '1' and r['engine'] == ENGINE for r in rows)} take bets")
    return rows


def run(race_ids=None, path=LOG_PATH, verbose=True):
    todo = [r for r in races.PAST if not race_ids or r["id"] in race_ids]
    rows = []
    for rc in todo:
        if verbose:
            print(f"== {rc['id']} ({rc['contest']})")
        rows += replay(rc, verbose)
    keep = [r for r in log.read_log(path) if not r.get("engine", "").startswith(ENGINE)]
    log.write_log(keep, path)
    log.append(rows, path)
    return rows


def _tier(r):
    return "county" if "tier=county" in r["notes"] else "statewide"


def report(rows):
    lines = ["ARGUS ELECTION-NIGHT BACKTEST (replayed official counts, paper only)"]
    cuts = [(eng, eng, None) for eng in (ENGINE, ENGINE_REST)]
    cuts += [(f"{ENGINE}, county feeds only", ENGINE, "county"), (f"{ENGINE}, statewide-only feeds", ENGINE, "statewide")]
    for title, eng, tier in cuts:
        rs = [r for r in rows if r["engine"] == eng and (tier is None or _tier(r) == tier)]
        if not rs:
            continue
        card = log.scorecard(rs)
        ev = sorted({r["event_ticker"] for r in rs})
        bets = [r for r in rs if r["paper_contracts"] == "1"]
        lines += [f"\n== {title} ==",
                  f"  Races (Kalshi events): {len(ev)}   rows: {len(rs)}   paper bets: {len(bets)}",
                  f"  Brier (chosen side vs its price): model {_b(card['brier_model'])}  market {_b(card['brier_market'])}",
                  f"  Won {card['paper_wins']}/{card['paper_bets']}   P/L after fees ${card['pnl_after_fees']:+.2f} "
                  f"on ${card['staked']:.2f} staked (ROI {_pct(card['roi'])})"]
        if eng == ENGINE_REST:
            tried = len(rs)
            lines.append(f"  Resting orders tried {tried}, filled {len(bets)}")
        for r in (bets if tier is None else []):
            n = dict(x.split("=", 1) for x in r["notes"].split("; ") if "=" in x)
            lines.append(f"    {n['t_known'][11:16]}Z {r['market_ticker']:<28} {r['side']:<3} model {float(r['model_prob']):.2f} "
                         f"price {float(r['market_price']):.2f} gap {r['gap_pts']:>6} counted {float(r['anchor_price']) * 100:.0f}% "
                         f"-> {'WON' if r['won'] == '1' else 'lost'} {r['pnl_after_fees']}")
    by = {}
    for r in rows:
        if r["engine"] == ENGINE:
            by.setdefault(r["notes"].split(";")[0][5:], []).append(r)
    if by:
        lines.append("\nPer race (take rows):  race  rows  Brier model / market (Yes vs mid)")
        for rid, rs in by.items():
            pm = [(float(dict(x.split('=', 1) for x in r['notes'].split('; '))['p_yes']),
                   1 if (r['settle_value'] == 'yes') else 0) for r in rs]
            pk = [(float(dict(x.split('=', 1) for x in r['notes'].split('; '))['yes_mid']),
                   1 if (r['settle_value'] == 'yes') else 0) for r in rs]
            lines.append(f"  {rid:<14} {len(rs):>4}  {_b(log.brier(pm))} / {_b(log.brier(pk))}")
    return "\n".join(lines)


def _b(x):
    return "n/a" if x is None else f"{x:.4f}"


def _pct(x):
    return "n/a" if x is None else f"{x * 100:+.1f}%"
