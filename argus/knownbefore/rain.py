"""observed-rain: price "will it rain today in <city>" (KXRAIN) from what the gauge has ALREADY recorded.

KXRAIN pays Yes when the NWS climate report (CLI) shows more than 0 inches for the climate day
(trace counts as 0). Once the station's hourly report has measured 0.01 inch, the answer is very
nearly settled; before that, the chance of rain in the hours left depends on whether it is wet or
overcast right now. Both are read from the station's own METAR reports (obs.py).

Model: the share of past days (same station, same wall-clock slot, within 30 days of the date in
earlier years and earlier this year, strictly before the target day) in the same observed state that
ended with CLI precipitation above 0. States: measured (>= 0.01 in so far), wet now (precipitation in
the latest report but under 0.01 measured), overcast, clear. Laplace-smoothed. Same bet rule and no
look-ahead guarantees as observed-temp (temp.py).
"""
import hashlib
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from .. import fees, log
from ..markets import Contract
from ..weather import stations
from . import LOG_PATH, obs, quotes
from .temp import decide, notes, replace_engine, report as _report

ENGINE = "observed-rain"
SERIES = "KXRAIN"
SLOTS = (10, 13, 16, 19, 22)
SEASON_DAYS = 30
FIRST_YEAR = 2023
P_FLOOR = 0.005
EXTRA = {"ORD": ("KORD", "America/Chicago", "Chicago O'Hare")}   # rain cities not in weather/stations.py
_CLI = re.compile(r"precipitation at CLI([A-Z]{3})")


def station(cli):
    if cli in stations.STATIONS:
        return stations.STATIONS[cli]
    return EXTRA.get(cli)


def cli_of(market):
    m = _CLI.search(market.get("rules_primary") or "")
    return m.group(1) if m else None


def _window(o):
    """Start of the period a report's precipitation covers. The hourly P group covers the hour since
    the last routine report; the 6 group covers 6 hours at 00/06/12/18Z and 3 hours at 03/09/15/21Z.
    A report early in the day can carry rain that fell before midnight LST, so it only counts when
    its whole window lies inside the climate day."""
    h = (o.t + timedelta(minutes=15)).hour
    if h % 3 == 0:
        return o.t - timedelta(hours=6 if h % 6 == 0 else 3)
    return o.t - timedelta(hours=1)


def state_class(metars, day, tz, t, lag=obs.LAG):
    """'measured' | 'wet' | 'overcast' | 'clear' for one climate day at time t, or None if too few reports."""
    start, end = obs.climate_day(day, tz)
    cut = min(t - lag, end)
    use = [o for o in metars if start < o.t <= cut]
    if len(use) < 3 or cut - use[-1].t > timedelta(hours=2):
        return None
    if any((o.precip or 0) >= 0.01 and _window(o) >= start - timedelta(minutes=10) for o in use):
        return "measured"
    if use[-1].wet:
        return "wet"
    return "overcast" if use[-1].cloudy else "clear"


def build_history(metars, finals, tz, slot):
    """[(day, state, rained)] for every past day with reports and a CLI precipitation value."""
    out = []
    for d, v in sorted(finals.items()):
        if v.get("precip") is None:
            continue
        c = state_class(metars, d, tz, obs.wall_clock(d, slot, tz))
        if c:
            out.append((d, c, v["precip"] > 0))
    return out


def _doy(a, b):
    x = abs(a.timetuple().tm_yday - b.timetuple().tm_yday)
    return min(x, 365 - x)


def prob_rain(history, day, cls, season=SEASON_DAYS):
    """(P(CLI precip > 0), n) from past days in the same state and season, strictly before `day`."""
    for s in (season, 2 * season, 3 * season):
        hits = [r for d, c, r in history if d < day and c == cls and _doy(d, day) <= s]
        if len(hits) >= 15:
            break
    p = (sum(hits) + 1) / (len(hits) + 2)
    return min(1 - P_FLOOR, max(P_FLOOR, p)), len(hits)


def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def replay_market(m, day, metars, history_by_slot, tz, candles, cli):
    rows = []
    for slot in SLOTS:
        t = obs.wall_clock(day, slot, tz)
        if not (_t(m["open_time"]) <= t < _t(m["close_time"])):
            continue
        cls = state_class(metars, day, tz, t)
        if cls is None:
            continue
        p_yes, n = prob_rain(history_by_slot[slot], day, cls)
        if n < 10:
            continue
        q = quotes.quote(candles.get(m["ticker"]), t)
        if q is None:
            continue
        bid, ask, vol = q
        side, price, p, edge, bet = decide(p_yes, bid, ask)
        stamp = t.strftime("%Y-%m-%dT%H:%M:%SZ")
        c = Contract.from_api(m)
        row = {
            "call_id": hashlib.sha1(f"{m['ticker']}@{stamp}".encode()).hexdigest()[:10],
            "logged_at_utc": stamp, "engine": ENGINE, "series": SERIES, "event_ticker": m["event_ticker"],
            "market_ticker": m["ticker"], "range_label": f"rain {cli}", "side": side,
            "model_prob": f"{p:.4f}", "market_price": f"{price:.2f}", "gap_pts": f"{(p - price) * 100:.1f}",
            "worth_a_look": "1" if bet else "0", "paper_contracts": "1" if bet else "0",
            "fee_per_contract": f"{fees.taker_fee(price):.2f}", "anchor_price": "",
            "anchor_kind": f"observed state '{cls}' at {slot}:00 local", "vol_daily": "",
            "horizon_days": f"{(_t(m['close_time']) - t).total_seconds() / 86400:.3f}",
            "event_days": "0.00", "news_shift_pct": "0.00", "close_time_utc": m["close_time"],
            "notes": (f"shape={c.kind}|{c.low}|{c.high}; slot={slot}; city={cli}; state={cls}; p_yes={p_yes:.4f}; "
                      f"edge={edge:.4f}; yes_bid={bid:.2f}; yes_ask={ask:.2f}; hour_vol={vol:.0f}; n_hist={n}"),
        }
        log.settle_row(row, None, m.get("result") == "yes")
        row["settle_value"] = m.get("expiration_value") or ""
        rows.append(row)
    return rows


def run(since, until, out_path=LOG_PATH, verbose=True):
    ms = [m for m in quotes.settled_markets(SERIES) if m.get("result") in ("yes", "no")]
    by_cli = defaultdict(list)
    for m in ms:
        d = stations.target_date(m["event_ticker"])
        cli = cli_of(m)
        if d and since <= d <= until and cli and station(cli):
            by_cli[cli].append(m)
    events = defaultdict(list)
    for mk in by_cli.values():
        for m in mk:
            events[m["event_ticker"]].append(m)
    if verbose:
        print(f"{SERIES}: {len(events)} events, {sum(len(v) for v in by_cli.values())} city-days, "
              f"{len(by_cli)} cities", flush=True)
    # candles: one request per event (live tier) or per market (historical tier)
    candles = {}
    for i, (e, mk) in enumerate(sorted(events.items())):
        day = stations.target_date(e)
        start = datetime.combine(day, time(12), timezone.utc)      # 8 AM ET: before the first slot anywhere
        end = max(_t(m["close_time"]) for m in mk)
        try:
            candles.update(quotes.event_candles(SERIES, e, mk, start, end))
        except Exception as ex:
            print(f"  candles failed for {e}: {ex}", flush=True)
        if verbose and i % 20 == 19:
            print(f"  candles {i + 1}/{len(events)} events", flush=True)
    rows = []
    for cli, mk in sorted(by_cli.items()):
        icao, tz, _ = station(cli)
        finals, metars = {}, []
        for y in range(FIRST_YEAR, until.year + 1):
            finals.update(obs.iem_cli_full(icao, y))
            metars += obs.iem_metars(icao, date(y, 1, 1) - timedelta(days=1),
                                     min(date(y + 1, 1, 1), until + timedelta(days=2)))
        metars = sorted({o.t: o for o in metars}.values(), key=lambda o: o.t)
        finals = {d: v for d, v in finals.items() if d <= until}
        hist = {s: build_history(metars, finals, tz, s) for s in SLOTS}
        n0 = len(rows)
        for m in mk:
            rows += replay_market(m, stations.target_date(m["event_ticker"]), metars, hist, tz, candles, cli)
        if verbose:
            print(f"  {cli} ({icao}): {len(mk)} days, {len(rows) - n0} calls", flush=True)
    replace_engine(ENGINE, rows, out_path)
    return rows


def report(rows):
    out = _report(rows, title="observed-rain backtest (KXRAIN)", slots=SLOTS, by_kind=False)
    L = ["", "By observed state at the call:"]
    for cls in ("measured", "wet", "overcast", "clear"):
        rs = [r for r in rows if notes(r).get("state") == cls]
        if not rs:
            continue
        bets = [r for r in rs if r["paper_contracts"] == "1"]
        yes = sum((r["side"] == "YES") == (r["won"] == "1") for r in rs) / len(rs)
        mdl = sum(float(notes(r)["p_yes"]) for r in rs) / len(rs)
        mid = sum((float(notes(r)["yes_bid"]) + float(notes(r)["yes_ask"])) / 2 for r in rs) / len(rs)
        pnl = sum(float(r["pnl_after_fees"]) for r in bets)
        L.append(f"  {cls:<9} calls {len(rs):>5}  model {mdl * 100:5.1f}%  market mid {mid * 100:5.1f}%  rained {yes * 100:5.1f}%"
                 f"   bets {len(bets):>4}  P/L {pnl:+7.2f}")
    return out + "\n".join(L)
