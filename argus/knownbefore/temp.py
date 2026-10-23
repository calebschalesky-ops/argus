"""observed-temp: price today's high/low ranges from what the station has ALREADY recorded.

At a moment t on the target day the station's max (or min) so far is public. The final value can
only go up from there (down, for lows). How much further it goes is read from past days at the same
station, same wall-clock time, same season ("analogs"): on days when the temperature was already
g degrees below the day's max so far and moving at the same rate, how much higher did the official
climate-report high end up than the max observed by then? That spread of outcomes, shifted to
today's max so far, is the model's distribution of the final value.

No look-ahead, by construction (tests in tests/test_knownbefore.py):
  - a report counts only 20 min after its time and only inside the climate day (obs.state_at);
  - analog days are strictly before the target day, so their reports and climate reports were public;
  - the Kalshi price is the close of the hourly candle that ended at or before t.

The bet rule was fixed before the first backtest run (2026-10-22): take the side whose model
probability beats its ask by 5c or more after the taker fee, only if that side costs 10c or more and
the quote has a real bid within 5c.
"""
import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .. import fees, log
from ..markets import Contract
from ..weather import stations
from . import LOG_PATH, obs, quotes

ENGINE = "observed-temp"
SLOTS = (10, 13, 16, 19, 22)        # station wall-clock hours replayed on the target day
SEASON_DAYS = 30
FIRST_YEAR = 2023                   # analog history starts here (Iowa State archive has more)
K = 80                              # analogs per prediction
KERNEL_SD = 0.6                     # F; smooths the analog outcomes into a distribution
MIN_EDGE = 0.05                     # bet rule: model beats the ask by 5c after the fee
MIN_PRICE = 0.10                    # longshot rule
P_FLOOR = 0.005


@dataclass(frozen=True)
class Analog:
    day: date
    gap: float      # high: max so far minus now; low: now minus min so far (>= 0)
    trend: float    # high: now minus 2 h ago; low: the negative of that (so "moving toward a new extreme" is +)
    more: float     # high: final minus max so far; low: min so far minus final (how much further it went)


def features(st, kind):
    if kind == "high":
        return st.hi - st.now, st.trend, st.hi
    return st.now - st.lo, -st.trend, st.lo


def build_analogs(metars, finals, tz, kind, slot):
    """One Analog per past day that has reports and a final climate value at this slot."""
    out = []
    for d, (hi, lo) in sorted(finals.items()):
        final = hi if kind == "high" else lo
        if final is None:
            continue
        st = obs.state_at(metars, d, tz, obs.wall_clock(d, slot, tz))
        if st is None:
            continue
        gap, trend, extreme = features(st, kind)
        more = final - extreme if kind == "high" else extreme - final
        out.append(Analog(d, gap, trend, more))
    return out


def _doy_dist(a, b):
    x = abs(a.timetuple().tm_yday - b.timetuple().tm_yday)
    return min(x, 365 - x)


def pick(analogs, day, gap, trend, k=K, season=SEASON_DAYS):
    """The k analogs nearest to (gap, trend), from days strictly before `day` in the same season."""
    pool = [a for a in analogs if a.day < day and _doy_dist(a.day, day) <= season]
    pool.sort(key=lambda a: abs(a.gap - gap) + 0.5 * abs(a.trend - trend))
    return pool[:k]


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def cdf_of(extreme, chosen, kind, sd=KERNEL_SD):
    """CDF of the final value: a smoothed mix of extreme +/- each analog's further move."""
    sign = 1 if kind == "high" else -1
    centres = [extreme + sign * a.more for a in chosen]
    return lambda x: sum(_ncdf((x - c) / sd) for c in centres) / len(centres)


def range_prob(c, cdf):
    """P(final whole-degree value lands in contract c). Values are integers, so edges sit at +/- 0.5."""
    if c.kind == "between":
        p = cdf(c.high + 0.5) - cdf(c.low - 0.5)
    elif c.kind == "greater":
        p = 1 - cdf(c.low + 0.5)
    elif c.kind == "greater_or_equal":
        p = 1 - cdf(c.low - 0.5)
    elif c.kind == "less":
        p = cdf(c.high - 0.5)
    elif c.kind == "less_or_equal":
        p = cdf(c.high + 0.5)
    else:
        raise ValueError(f"unknown strike type {c.kind}")
    return min(1 - P_FLOOR, max(P_FLOOR, p))


def decide(p_yes, bid, ask):
    """(side, price, prob of that side, edge after fee, bet?) for one range at one quote."""
    no_ask = round(1 - bid, 4)
    e_yes = p_yes - ask - fees.taker_fee(ask)
    e_no = (1 - p_yes) - no_ask - fees.taker_fee(no_ask)
    if e_yes >= e_no:
        side, price, p, e = "YES", ask, p_yes, e_yes
    else:
        side, price, p, e = "NO", no_ask, 1 - p_yes, e_no
    return side, price, p, e, (e >= MIN_EDGE and price >= MIN_PRICE)


# ---------------- backtest ----------------

def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def replay_event(series, event_ticker, markets, metars, analogs_by_slot, tz, kind, candles):
    """Rows (one per range with a usable quote, per slot) for one settled event."""
    day = stations.target_date(event_ticker)
    rows = []
    for slot in SLOTS:
        t = obs.wall_clock(day, slot, tz)
        alive = [m for m in markets if _t(m["open_time"]) <= t < _t(m["close_time"]) and m.get("strike_type")]
        if not alive:
            continue
        st = obs.state_at(metars, day, tz, t)
        if st is None:
            continue
        gap, trend, extreme = features(st, kind)
        chosen = pick(analogs_by_slot[slot], day, gap, trend)
        if len(chosen) < K // 2:
            continue
        cdf = cdf_of(extreme, chosen, kind)
        stamp = t.strftime("%Y-%m-%dT%H:%M:%SZ")
        for m in alive:
            q = quotes.quote(candles.get(m["ticker"]), t)
            if q is None:
                continue
            bid, ask, vol = q
            c = Contract.from_api(m)
            p_yes = range_prob(c, cdf)
            side, price, p, edge, bet = decide(p_yes, bid, ask)
            row = {
                "call_id": hashlib.sha1(f"{m['ticker']}@{stamp}".encode()).hexdigest()[:10],
                "logged_at_utc": stamp, "engine": ENGINE, "series": series, "event_ticker": event_ticker,
                "market_ticker": m["ticker"], "range_label": c.label, "side": side,
                "model_prob": f"{p:.4f}", "market_price": f"{price:.2f}", "gap_pts": f"{(p - price) * 100:.1f}",
                "worth_a_look": "1" if bet else "0", "paper_contracts": "1" if bet else "0",
                "fee_per_contract": f"{fees.taker_fee(price):.2f}", "anchor_price": f"{extreme:.1f}",
                "anchor_kind": f"observed {kind} so far at {slot}:00 local", "vol_daily": f"{KERNEL_SD:.2f}",
                "horizon_days": f"{(_t(m['close_time']) - t).total_seconds() / 86400:.3f}",
                "event_days": "0.00", "news_shift_pct": "0.00", "close_time_utc": m["close_time"],
                "notes": (f"shape={c.kind}|{c.low}|{c.high}; slot={slot}; p_yes={p_yes:.4f}; edge={edge:.4f}; "
                          f"yes_bid={bid:.2f}; yes_ask={ask:.2f}; hour_vol={vol:.0f}; now={st.now:.1f}; "
                          f"gap={gap:.1f}; trend={trend:.1f}; n_obs={st.n}; last_ob={st.last_t:%H:%MZ}; "
                          f"n_analogs={len(chosen)}"),
            }
            try:
                value = float(m.get("expiration_value"))
            except (TypeError, ValueError):
                value = None
            log.settle_row(row, value, m.get("result") == "yes")
            rows.append(row)
    return rows


def run_series(series, since, until, verbose=True):
    cli, icao, tz, name, kind = stations.info(series)
    ms = [m for m in quotes.settled_markets(series) if m.get("result") in ("yes", "no")]
    events = defaultdict(list)
    for m in ms:
        d = stations.target_date(m["event_ticker"])
        if d and since <= d <= until:
            events[m["event_ticker"]].append(m)
    if verbose:
        print(f"{series} ({icao} {kind}): {len(events)} settled events {since}..{until}", flush=True)
    finals, metars = {}, []
    for y in range(FIRST_YEAR, until.year + 1):
        finals.update(obs.iem_cli(icao, y))
        metars += obs.iem_metars(icao, date(y, 1, 1) - timedelta(days=1), min(date(y + 1, 1, 1), until + timedelta(days=2)))
    metars = sorted({o.t: o for o in metars}.values(), key=lambda o: o.t)
    finals = {d: v for d, v in finals.items() if d <= until}
    analogs = {s: build_analogs(metars, finals, tz, kind, s) for s in SLOTS}
    rows, skipped = [], []
    for i, (e, mk) in enumerate(sorted(events.items())):
        day = stations.target_date(e)
        try:
            start = obs.wall_clock(day, min(SLOTS), tz) - timedelta(hours=2)
            end = min(obs.wall_clock(day, max(SLOTS), tz), max(_t(m["close_time"]) for m in mk))
            candles = quotes.event_candles(series, e, mk, start, end)
            rs = replay_event(series, e, mk, metars, analogs, tz, kind, candles)
            rows += rs
            if not rs:
                skipped.append((series, e, "no usable quote or observations"))
        except Exception as ex:                 # one bad event never stops the backtest
            skipped.append((series, e, f"error: {ex.__class__.__name__}: {ex}"))
        if verbose and i % 40 == 39:
            print(f"  {series}: {i + 1}/{len(events)} events, {len(rows)} calls", flush=True)
    return rows, skipped


def run(series_list, since, until, out_path=LOG_PATH):
    """Backtest every series; rows go to knownbefore_log.csv (other engines' rows there are kept)."""
    all_rows, skipped = [], []
    for s in series_list:
        rows, sk = run_series(s, since, until)
        all_rows += rows
        skipped += sk
        replace_engine(ENGINE, all_rows, out_path)   # saved after every series
    return all_rows, skipped


def replace_engine(engine, rows, path=LOG_PATH):
    """Write `rows` as the whole of `engine`'s part of the log; other engines' rows are re-read and kept."""
    keep = [r for r in log.read_log(path) if r["engine"] != engine]
    log.write_log(keep + rows, path)


# ---------------- report ----------------

def notes(r):
    return dict(x.split("=", 1) for x in r["notes"].split("; ") if "=" in x)


def summarize(rows):
    """Paper-bet stats for a set of rows: n bets, events, won, P/L, staked, ROI, Brier model/market."""
    card = log.scorecard(rows)
    bets = [r for r in rows if r["paper_contracts"] == "1"]
    ev = {r["event_ticker"] for r in bets}
    edge = (sum(float(notes(r)["edge"]) for r in bets) / len(bets)) if bets else None
    return {"calls": len(rows), "bets": len(bets), "events": len(ev), "won": card["paper_wins"],
            "pnl": card["pnl_after_fees"], "staked": card["staked"], "roi": card["roi"],
            "brier_model": card["brier_model"], "brier_market": card["brier_market"], "model_edge": edge,
            "realized_per_ct": (card["pnl_after_fees"] / len(bets)) if bets else None}


def weeks_spanned(rows):
    days = sorted({stations.target_date(r["event_ticker"]) for r in rows})
    return max(1.0, ((days[-1] - days[0]).days + 1) / 7) if days else 1.0


def report(rows, skipped=(), title="observed-temp backtest", slots=SLOTS, by_kind=True, slot_unit=":00 local"):
    from ..board import verdict
    pct = lambda x: "  n/a" if x is None else f"{x * 100:+5.1f}%"
    b = lambda x: " n/a " if x is None else f"{x:.4f}"
    L = [f"ARGUS KNOWN-BEFORE: {title} (paper only; taker at the candle ask, fee included)",
         f"Bet rule: side beats its ask by {MIN_EDGE * 100:.0f}c+ after fee, price >= {MIN_PRICE * 100:.0f}c, bid within 5c.",
         "", f"{'group':<22}{'calls':>6}{'bets':>6}{'events':>7}{'won':>6}{'P/L':>9}{'ROI':>8}"
             f"{'$/ct':>7}{'Brier mdl/mkt':>16}{'bets/wk':>8}  verdict"]
    wk = weeks_spanned(rows) if rows else 1

    def line(label, rs, weeks):
        s = summarize(rs)
        n_ev = len({r["event_ticker"] for r in rs if r["paper_contracts"] == "1"})
        v = verdict(log.scorecard(rs), n_ev) if rs else "-"
        per = "  n/a" if s["realized_per_ct"] is None else f"{s['realized_per_ct'] * 100:+5.1f}c"
        L.append(f"{label:<22}{s['calls']:>6}{s['bets']:>6}{s['events']:>7}{s['won']:>6}{s['pnl']:>+9.2f}"
                 f"{pct(s['roi']):>8}{per:>7}  {b(s['brier_model'])}/{b(s['brier_market'])}"
                 f"{s['bets'] / weeks:>8.1f}  {v}")

    for slot in slots:
        line(f"slot {slot}{slot_unit}", [r for r in rows if notes(r).get("slot") == str(slot)], wk)
    if by_kind:
        for kind in ("high", "low"):
            line(f"all {kind}s", [r for r in rows if ("LOW" in r["series"]) == (kind == "low")], wk)
    groups = sorted({r["series"] if by_kind else notes(r).get("city", r["series"]) for r in rows})
    for g in groups:
        line(g, [r for r in rows if (r["series"] if by_kind else notes(r).get("city", r["series"])) == g], wk)
    line("ALL", rows, wk)

    bets = [r for r in rows if r["paper_contracts"] == "1"]
    L += ["", "Bets by price band (side price paid):"]
    for lo, hi in ((0.10, 0.50), (0.50, 0.80), (0.80, 0.90), (0.90, 0.95), (0.95, 1.0)):
        rs = [r for r in bets if lo <= float(r["market_price"]) < hi]
        if rs:
            pnl = sum(float(r["pnl_after_fees"]) for r in rs)
            st = sum(float(r["market_price"]) for r in rs)
            L.append(f"  {lo * 100:>3.0f}-{hi * 100:<3.0f}c  n={len(rs):>5}  won {sum(r['won'] == '1' for r in rs) / len(rs) * 100:5.1f}%"
                     f"  avg price {st / len(rs) * 100:5.1f}c  P/L {pnl:+8.2f}  ROI {pnl / st * 100:+5.1f}%")
    L += ["", "Calibration of the model's Yes probability (all calls with a quote):"]
    for lo in (0, 0.05, 0.2, 0.5, 0.8, 0.95):
        hi = {0: 0.05, 0.05: 0.2, 0.2: 0.5, 0.5: 0.8, 0.8: 0.95, 0.95: 1.01}[lo]
        rs = [r for r in rows if lo <= float(notes(r)["p_yes"]) < hi]
        if rs:
            yes = [((r["side"] == "YES") == (r["won"] == "1")) for r in rs]
            mid = [(float(notes(r)["yes_bid"]) + float(notes(r)["yes_ask"])) / 2 for r in rs]
            L.append(f"  model {lo * 100:>3.0f}-{min(hi, 1) * 100:<3.0f}%  n={len(rs):>6}  model avg {sum(float(notes(r)['p_yes']) for r in rs) / len(rs) * 100:5.1f}%"
                     f"  market mid avg {sum(mid) / len(mid) * 100:5.1f}%  Yes won {sum(yes) / len(yes) * 100:5.1f}%")
    if skipped:
        L.append(f"\nSkipped events: {len(skipped)} (first 10)")
        for s, e, why in list(skipped)[:10]:
            L.append(f"  {s} {e}: {why}")
    return "\n".join(L)


# ---------------- live (forward paper test) ----------------

LIVE_ENGINE = "observed-temp-live"


def live(series_list, now=None, out_path=LOG_PATH, verbose=True):
    """Price today's open temperature events from api.weather.gov reports so far; log paper calls.

    Same model and bet rule as the backtest. Prices are the live market list's best bid/ask.
    Rows go to knownbefore_log.csv as engine=observed-temp-live; settle them with `live-settle`.
    """
    from . import http
    now = now or datetime.now(timezone.utc)
    out = []
    for series in series_list:
        cli, icao, tz, name, kind = stations.info(series)
        day = now.astimezone(obs.ZoneInfo(tz)).date()
        start, _ = obs.climate_day(day, tz)
        ms = [m for m in http.kalshi_paged("/markets", "markets", {"series_ticker": series, "status": "open", "limit": 1000})
              if stations.target_date(m["event_ticker"]) == day]
        if not ms:
            continue
        metars = obs.nws_live_obs(icao, start - timedelta(hours=2))
        st = obs.state_at(metars, day, tz, now)
        if st is None:
            continue
        slot = min(SLOTS, key=lambda s: abs(obs.wall_clock(day, s, tz) - now))
        finals, hist = {}, []
        for y in range(FIRST_YEAR, day.year + 1):
            finals.update(obs.iem_cli(icao, y))
            hist += obs.iem_metars(icao, date(y, 1, 1) - timedelta(days=1), min(date(y + 1, 1, 1), day))
        hist = sorted({o.t: o for o in hist}.values(), key=lambda o: o.t)
        analogs = build_analogs(hist, {d: v for d, v in finals.items() if d < day}, tz, kind, slot)
        gap, trend, extreme = features(st, kind)
        chosen = pick(analogs, day, gap, trend)
        if len(chosen) < K // 2:
            continue
        cdf = cdf_of(extreme, chosen, kind)
        stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        for m in ms:
            b, a = (float(m.get("yes_bid_dollars") or 0), float(m.get("yes_ask_dollars") or 0))
            if not (0 < b < a < 1) or a - b > quotes.MAX_SPREAD + 1e-9:
                continue
            c = Contract.from_api(m)
            p_yes = range_prob(c, cdf)
            side, price, p, edge, bet = decide(p_yes, b, a)
            size = m.get("yes_ask_size_fp") if side == "YES" else m.get("yes_bid_size_fp")
            out.append({"logged_at_utc": stamp, "engine": LIVE_ENGINE, "series": series, "event_ticker": m["event_ticker"],
                        "market_ticker": m["ticker"], "range_label": c.label, "side": side, "model_prob": f"{p:.4f}",
                        "market_price": f"{price:.2f}", "gap_pts": f"{(p - price) * 100:.1f}",
                        "worth_a_look": "1" if bet else "0", "paper_contracts": "1" if bet else "0",
                        "fee_per_contract": f"{fees.taker_fee(price):.2f}", "anchor_price": f"{extreme:.1f}",
                        "anchor_kind": f"observed {kind} so far (api.weather.gov {icao})", "vol_daily": f"{KERNEL_SD:.2f}",
                        "horizon_days": f"{(_t(m['close_time']) - now).total_seconds() / 86400:.3f}", "event_days": "0.00",
                        "news_shift_pct": "0.00", "close_time_utc": m["close_time"],
                        "notes": (f"shape={c.kind}|{c.low}|{c.high}; slot={slot}; p_yes={p_yes:.4f}; edge={edge:.4f}; "
                                  f"yes_bid={b:.2f}; yes_ask={a:.2f}; size_at_price={size}; now={st.now:.1f}; "
                                  f"last_ob={st.last_t:%H:%MZ}; n_analogs={len(chosen)}")})
            if verbose and bet:
                print(f"PAPER {side} {m['ticker']} ({c.label}) at {price * 100:.0f}c: model {p * 100:.1f}%, "
                      f"edge {edge * 100:+.1f}c after fee, {size} contracts at that price. "
                      f"{kind} so far {extreme:.1f}F, now {st.now:.1f}F", flush=True)
    log.append(out, out_path)
    return out


def live_settle(out_path=LOG_PATH):
    """Fill results of pending live rows from Kalshi's settled markets."""
    from . import http
    rows = log.read_log(out_path)
    n = 0
    for r in rows:
        if r["engine"] != LIVE_ENGINE or r["status"] == "settled":
            continue
        m = (http.kalshi(f"/markets/{r['market_ticker']}") or {}).get("market", {})
        if m.get("result") in ("yes", "no"):
            log.settle_row(r, None, m["result"] == "yes")
            r["settle_value"] = m.get("expiration_value") or ""
            n += 1
    log.write_log(rows, out_path)
    return n
