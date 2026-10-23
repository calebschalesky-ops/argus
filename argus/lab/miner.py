"""`python -m argus.lab mine`: sweep thousands of simple rules with an honest train/holdout split.

Plain words: we try every simple rule we can think of on the OLD data only (the mining
period). Trying thousands of rules guarantees some look good by luck, so a rule must clear
a false-discovery control (Benjamini-Hochberg at 5%) and a minimum number of events. The
few that clear it are then scored ONCE on the newer data they never saw (the holdout), by
the bench, and must make money there too. Holdout survivors can be promoted to live paper
loggers (live.py); the live record is the real test.

Rule families (every rule = one scope x one decision time x one condition x side x pricing):
  grid      buy YES or NO when that side's price is in a band (1-5c ... 95-99c)
  fav       buy the favourite (whichever side is above 50c) when it is in a high band
  longshot  buy NO when YES is cheap (YES ask at or under 5/10/15/20/30c)
  move      after the mid moved k cents (5/10/20) in the last 1/6/24 hours, bet on reversion
            (against the move) or momentum (with it)
Scope: all markets, one category, or one series (series need MIN_EVENTS mining events).
Decision time: 1, 2, 3, 6, 12, 24, 48, 96 or 168 hours before the scheduled close.
Pricing: taker (pay the ask + taker fee) or maker (rest at bid + 1c; filled only if a later
trade went strictly through; maker fee only where the series charges one).

Split: mining uses markets that CLOSED before the holdout date; the holdout uses decisions
made on or after it. A market that straddles the date is in neither.
"""
import bisect
import csv
import hashlib
import json
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np

from .. import log
from . import HOLDOUT_LOG, LAKE, SURVIVORS, bench, lake, registry, stats

HOURS = [1, 2, 3, 6, 12, 24, 48, 96, 168]
LOOKBACK = [1, 6, 24]
BANDS = [(0.01, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 0.35), (0.35, 0.50),
         (0.50, 0.65), (0.65, 0.80), (0.80, 0.90), (0.90, 0.95), (0.95, 1.00)]
FAV_BANDS = [(0.80, 0.90), (0.85, 0.97), (0.90, 0.95), (0.95, 1.00)]
FAV_HOURS = [1, 2, 3, 6]
LONGSHOT_MAX = [0.05, 0.10, 0.15, 0.20, 0.30]
MOVES = [0.05, 0.10, 0.20]
EXIT_HOURS = 3           # the "movex" family sells 3 hours later (survey idea 2.7: underreaction after big moves)
ATTN_QUANTILE = 0.30     # "low attention" = volume traded before t in the bottom 30% of mining rows (idea 2.5)
SIDES = ("yes", "no")
MODES = ("taker", "maker")
MIN_EVENTS = 40          # mining events a rule needs before it is tested at all
HOLDOUT_MIN_EVENTS = 20  # holdout events a survivor needs to pass
FDR_Q = 0.05
MAX_HOLDOUT_RULES = 150  # survivors evaluated on the holdout (lowest mining p first)
DEFAULT_HOLDOUT_DAYS = 120
COHORT_MIN_DAYS = 28     # nightly mines a new cohort only when the last one is this old
SNAPSHOT = LAKE / "snapshot.npz"
COHORTS = LAKE / "cohorts"


# ---------------- vectorised fees (same rounding as argus/fees.py) ----------------

def _round_up(raw):
    return np.ceil(raw * 100 - 1e-9) / 100


def taker_fee_v(p, mult):
    return _round_up(0.07 * mult * p * (1 - p))


def maker_fee_v(p, mult, charged):
    return np.where(charged, _round_up(0.0175 * mult * p * (1 - p)), 0.0)


# ---------------- snapshot: every market's book at every decision time ----------------

def _suffix_extreme(vals, fn):
    out = list(vals)
    for i in range(len(out) - 2, -1, -1):
        a, b = out[i], out[i + 1]
        out[i] = b if a is None else (a if b is None else fn(a, b))
    return out


def _fill_index(candles, tape):
    """Fast fill lookup equal to bench.filled: (starts, suffix low, suffix high) over later trades."""
    if tape is not None:
        pts = sorted((x[0], x[2]) for x in tape)
        keys = [p[0] for p in pts]
        return "tape", keys, _suffix_extreme([p[1] for p in pts], min), _suffix_extreme([p[1] for p in pts], max)
    cs = [c for c in candles if c.get("volume")]
    keys = [c["end_ts"] - 3600 for c in cs]
    return "candles", keys, _suffix_extreme([c.get("px_lo") for c in cs], min), \
        _suffix_extreme([c.get("px_hi") for c in cs], max)


def _later_extremes(idx, t, close_ts):
    import bisect
    kind, keys, lo, hi = idx
    i = bisect.bisect_right(keys, t) if kind == "tape" else bisect.bisect_left(keys, t)
    if i >= len(keys):
        return None, None
    return lo[i], hi[i]


def build_snapshot(con, verbose=True, chunk=4000):
    """One row per (market, decision time) with a valid book. Uses only bench functions for
    the scheduled close, the quote and the maker limit, so the miner and the bench agree."""
    all_ms = lake.markets(con, "m.ticker IN (SELECT ticker FROM candle_done WHERE period=60 AND n > 0)")
    gaps = bench.usual_gaps(all_ms)
    complete = {r[0] for r in con.execute("SELECT ticker FROM tape_done WHERE complete=1")}
    cats = sorted({m.get("category") or "Other" for m in all_ms})
    sers = sorted({m["series"] for m in all_ms})
    evs = {}
    cat_i, ser_i = {c: i for i, c in enumerate(cats)}, {s: i for i, s in enumerate(sers)}
    cols = defaultdict(list)
    t0 = time.time()
    for k in range(0, len(all_ms), chunk):
        part = all_ms[k:k + chunk]
        cs_by = lake.candles_for(con, [m["ticker"] for m in part])
        tp_by = lake.trades_for(con, [m["ticker"] for m in part if m["ticker"] in complete])
        for m in part:
            if m.get("frequency") == "fifteen_min" or m["result"] not in ("yes", "no"):
                continue
            sched = bench.scheduled_close(m, gaps.get(m["series"], 0))
            if sched is None:
                continue
            candles = cs_by.get(m["ticker"], [])
            if not candles:
                continue
            tape = tp_by.get(m["ticker"], []) if m["ticker"] in complete else None
            fidx = _fill_index(candles, tape)
            ends = [c["end_ts"] for c in candles]
            cumvol = list(np.cumsum([c.get("volume") or 0.0 for c in candles]))
            e = evs.setdefault(m["event_ticker"], len(evs))
            for hi, h in enumerate(HOURS):
                t = int(sched - h * 3600)
                if not (m["open_ts"] <= t < m["close_ts"]):
                    continue
                age = bench.max_age(h)
                bid, ask = bench.quote(bench.history_until(candles, t, ends), t, age)
                if bid is None:
                    continue
                past = []
                for L in LOOKBACK:
                    tl = t - L * 3600
                    b2, a2 = bench.quote(bench.history_until(candles, tl, ends), tl, age)
                    past.append(np.nan if b2 is None else (b2 + a2) / 2)
                lo, hi_px = _later_extremes(fidx, t, m["close_ts"])
                k_t = bisect.bisect_right(ends, t)
                cols["vol_before"].append(cumvol[k_t - 1] if k_t else 0.0)
                te = t + EXIT_HOURS * 3600
                for side in SIDES:
                    xb = bench.exit_bid(candles, te, side) if te < min(sched, m["close_ts"]) else None
                    cols[f"xbid_{side}"].append(np.nan if xb is None else xb)
                lims, fills = [], []
                for side in SIDES:
                    s_ask, s_bid = bench.side_prices(side, bid, ask)
                    lim = bench.default_maker_limit(s_bid, s_ask)
                    lims.append(np.nan if lim is None else lim)
                    if lim is None:
                        fills.append(False)
                    elif side == "yes":
                        fills.append(lo is not None and lo < lim - 1e-9)
                    else:
                        fills.append(hi_px is not None and hi_px > 1 - lim + 1e-9)
                cols["event"].append(e)
                cols["cat"].append(cat_i[m.get("category") or "Other"])
                cols["ser"].append(ser_i[m["series"]])
                cols["h"].append(hi)
                cols["t"].append(t)
                cols["close"].append(m["close_ts"])
                cols["bid"].append(bid)
                cols["ask"].append(ask)
                for L, v in zip(LOOKBACK, past):
                    cols[f"mid{L}"].append(v)
                cols["lim_yes"].append(lims[0])
                cols["lim_no"].append(lims[1])
                cols["fill_yes"].append(fills[0])
                cols["fill_no"].append(fills[1])
                cols["won_yes"].append(m["result"] == "yes")
                cols["mult"].append(m.get("fee_multiplier") or 1.0)
                cols["maker_charged"].append("maker" in (m.get("fee_type") or ""))
        if verbose:
            print(f"  snapshot: {min(k + chunk, len(all_ms))}/{len(all_ms)} markets, {len(cols['t'])} rows, "
                  f"{time.time() - t0:.0f}s", flush=True)
    snap = {k: np.array(v) for k, v in cols.items()}
    snap["cats"] = np.array(cats)
    snap["sers"] = np.array(sers)
    snap["events"] = np.array(sorted(evs, key=evs.get))
    return snap


def load_snapshot(con, rebuild=False, verbose=True):
    sig = json.dumps(lake.counts(con), sort_keys=True)
    if SNAPSHOT.exists() and not rebuild:
        z = np.load(SNAPSHOT, allow_pickle=False)
        if str(z["sig"]) == sig:
            return {k: z[k] for k in z.files if k != "sig"}
    snap = build_snapshot(con, verbose=verbose)
    np.savez_compressed(SNAPSHOT, sig=np.array(sig), **snap)
    return snap


# ---------------- rule families as vectorised bets ----------------

def _side_arrays(snap, side, mode):
    """(price, fee, filled) arrays for buying `side` at every snapshot row."""
    bid, ask, mult = snap["bid"], snap["ask"], snap["mult"]
    if mode == "taker":
        price = ask if side == "yes" else np.round(1 - bid, 4)
        return price, taker_fee_v(price, mult), np.ones(len(price), dtype=bool)
    price = snap["lim_yes"] if side == "yes" else snap["lim_no"]
    filled = (snap["fill_yes"] if side == "yes" else snap["fill_no"]) & ~np.isnan(price)
    p = np.nan_to_num(price, nan=0.5)
    return price, maker_fee_v(p, mult, snap["maker_charged"]), filled


def _pnl(price, fee, won):
    return np.where(won, 1 - price, -price) - fee


def family_bets(snap):
    """Yield (family, spec_list, local_rule_id_per_row (-1 = no bet), price, pnl, won, filled) per side/mode block."""
    won_yes = snap["won_yes"]
    h = snap["h"]
    mid = (snap["bid"] + snap["ask"]) / 2
    for mode in MODES:
        sp = {s: _side_arrays(snap, s, mode) for s in SIDES}
        # grid: side x band x hour
        for side in SIDES:
            price, fee, filled = sp[side]
            won = won_yes if side == "yes" else ~won_yes
            band = np.full(len(price), -1)
            for bi, (lo, hi) in enumerate(BANDS):
                band[(price >= lo - 1e-9) & (price < hi - 1e-9)] = bi
            specs = [{"family": "grid", "side": side, "mode": mode, "hours": hh, "band": list(BANDS[bi])}
                     for hh in HOURS for bi in range(len(BANDS))]
            local = np.where(band >= 0, h * len(BANDS) + band, -1)
            yield specs, local, price, _pnl(price, fee, won), won, filled
        # fav: whichever side has the higher mid, in a high band, last hours only
        fav_yes = mid >= 0.5
        price = np.where(fav_yes, sp["yes"][0], sp["no"][0])
        fee = np.where(fav_yes, sp["yes"][1], sp["no"][1])
        filled = np.where(fav_yes, sp["yes"][2], sp["no"][2])
        won = np.where(fav_yes, won_yes, ~won_yes)
        pnl = _pnl(price, fee, won)
        hmap = np.full(len(HOURS), -1)
        for i, hh in enumerate(FAV_HOURS):
            hmap[HOURS.index(hh)] = i
        for bi, (lo, hi) in enumerate(FAV_BANDS):        # bands overlap, so one block each
            ok = (price >= lo - 1e-9) & (price < hi - 1e-9) & (hmap[h] >= 0)
            specs = [{"family": "fav", "side": "fav", "mode": mode, "hours": hh, "band": [lo, hi]} for hh in FAV_HOURS]
            yield specs, np.where(ok, hmap[h], -1), price, pnl, won, filled
        # longshot: buy NO when the YES ask is at or under a threshold (thresholds nest: one block each)
        price, fee, filled = sp["no"]
        won = ~won_yes
        pnl = _pnl(price, fee, won)
        for thr in LONGSHOT_MAX:
            specs = [{"family": "longshot", "side": "no", "mode": mode, "hours": hh, "yes_max": thr} for hh in HOURS]
            ok = snap["ask"] <= thr + 1e-9
            yield specs, np.where(ok, h, -1), price, pnl, won, filled
        # move: reversion / momentum after a k-cent move over L hours
        for L in LOOKBACK:
            d = mid - snap[f"mid{L}"]
            for k in MOVES:
                up, down = d >= k - 1e-9, d <= -k + 1e-9
                for style in ("rev", "mom"):
                    buy_yes = down if style == "rev" else up
                    ok = up | down
                    price = np.where(buy_yes, sp["yes"][0], sp["no"][0])
                    fee = np.where(buy_yes, sp["yes"][1], sp["no"][1])
                    filled = np.where(buy_yes, sp["yes"][2], sp["no"][2])
                    won = np.where(buy_yes, won_yes, ~won_yes)
                    specs = [{"family": "move", "side": style, "mode": mode, "hours": hh, "lookback": L, "move": k}
                             for hh in HOURS]
                    yield specs, np.where(ok, h, -1), price, _pnl(price, fee, won), won, filled
                    if mode != "taker":
                        continue
                    # same signal, but sold at the side's bid 3 hours later (held to settlement if no quote then)
                    xb = np.where(buy_yes, snap["xbid_yes"], snap["xbid_no"])
                    held = _pnl(price, fee, won)
                    xb0 = np.nan_to_num(xb, nan=0.5)
                    exited = xb0 - price - fee - taker_fee_v(xb0, snap["mult"])
                    pnl_x = np.where(np.isnan(xb), held, exited)
                    specs = [{"family": "movex", "side": style, "mode": mode, "hours": hh, "lookback": L, "move": k}
                             for hh in HOURS]
                    yield specs, np.where(ok, h, -1), price, pnl_x, won, filled, price + fee


def scope_ids(snap, mask, min_events=MIN_EVENTS):
    """Scope columns: 0 = all; 1.. = categories; then series with enough mining events."""
    n_cat = len(snap["cats"])
    ev_by_ser = defaultdict(set)
    for s, e in zip(snap["ser"][mask], snap["event"][mask]):
        ev_by_ser[int(s)].add(int(e))
    eligible = sorted(s for s, es in ev_by_ser.items() if len(es) >= min_events)
    ser_scope = np.full(len(snap["sers"]), -1)
    for i, s in enumerate(eligible):
        ser_scope[s] = 1 + n_cat + i
    names = ["all"] + [f"category:{c}" for c in snap["cats"]] + [f"series:{snap['sers'][s]}" for s in eligible]
    cols = [np.zeros(len(snap["t"]), dtype=np.int64), 1 + snap["cat"].astype(np.int64), ser_scope[snap["ser"]]]
    if "vol_before" in snap and mask.any():
        # low attention: little traded before t (threshold from mining rows only)
        v = float(np.quantile(snap["vol_before"][mask], ATTN_QUANTILE))
        low = snap["vol_before"] <= v + 1e-9
        base = len(names)
        names += [f"lowattn<={v:g}:all"] + [f"lowattn<={v:g}:category:{c}" for c in snap["cats"]]
        cols += [np.where(low, base, -1), np.where(low, base + 1 + snap["cat"].astype(np.int64), -1)]
    return names, cols


def aggregate(local, scope_cols, n_scopes, event, pnl, price, won, use, q=None):
    """Per rule (local x scope): bets, events, sums, and per-event moments, all vectorised."""
    out = {}
    for sc in scope_cols:
        ok = use & (local >= 0) & (sc >= 0)
        if not ok.any():
            continue
        rule = local[ok].astype(np.int64) * n_scopes + sc[ok]
        ev = event[ok].astype(np.int64)
        key = rule * (int(event.max()) + 1) + ev
        uk, inv = np.unique(key, return_inverse=True)
        pe = np.bincount(inv, weights=pnl[ok])
        r_of = uk // (int(event.max()) + 1)
        ur, rinv = np.unique(r_of, return_inverse=True)
        k = np.bincount(rinv)
        s1 = np.bincount(rinv, weights=pe)
        s2 = np.bincount(rinv, weights=pe ** 2)
        ur2, binv = np.unique(rule, return_inverse=True)
        n = np.bincount(binv)
        stake = np.bincount(binv, weights=price[ok])
        wins = np.bincount(binv, weights=won[ok].astype(float))
        qq = np.clip(q[ok] if q is not None else price[ok], 1e-6, 1 - 1e-6)
        var = np.bincount(binv, weights=qq * (1 - qq))
        logq = np.bincount(binv, weights=np.log(qq))
        for j, r in enumerate(ur):
            assert ur2[j] == r
            out[int(r)] = (int(k[j]), float(s1[j]), float(s2[j]), int(n[j]), float(stake[j]), float(wins[j]),
                           float(var[j]), float(logq[j]))
    return out


def mine_rules(snap, holdout_ts, verbose=True):
    """Every rule's mining-period numbers. Returns (all rule rows, number enumerated)."""
    mining = snap["close"] < holdout_ts
    names, scols = scope_ids(snap, mining)
    rows, enumerated = [], 0
    for block in family_bets(snap):
        specs, local, price, pnl, won, filled = block[:6]
        enumerated += len(specs) * len(names)
        use = mining & filled & ~np.isnan(price)
        price0 = np.nan_to_num(price)
        pnl0 = np.nan_to_num(pnl)
        # break-even probability = price + fee (given explicitly where the bet can be sold before settlement)
        q = np.nan_to_num(block[6], nan=0.5) if len(block) > 6 else np.where(won, 1 - pnl0, -pnl0)
        agg = aggregate(local, scols, len(names), snap["event"], pnl0, price0, won, use, q)
        for r, (k, s1, s2, n, stake, wins, var, logq) in agg.items():
            spec = dict(specs[r // len(names)], scope=names[r % len(names)])
            rows.append({**spec, "events": k, "n": n, "pnl": s1, "stake": stake, "wins": wins, "s1": s1, "s2": s2,
                         "var": var, "logq": logq})
    if verbose:
        print(f"  mined {enumerated} rules; {len(rows)} had at least one mining bet", flush=True)
    return rows, enumerated


def rule_id(spec):
    parts = [spec["family"], spec["scope"], f"{spec['hours']}h", spec["side"], spec["mode"]]
    if "band" in spec:
        parts.append(f"{int(round(spec['band'][0] * 100))}-{int(round(spec['band'][1] * 100))}c")
    if "yes_max" in spec:
        parts.append(f"yes<={int(round(spec['yes_max'] * 100))}c")
    if "move" in spec:
        parts.append(f"{int(round(spec['move'] * 100))}c/{spec['lookback']}h")
    return "|".join(parts)


def spec_from_id(rid):
    """Inverse of rule_id."""
    parts = rid.split("|")
    fam, scope, hours, side, mode = parts[:5]
    spec = {"family": fam, "scope": scope, "hours": int(hours[:-1]), "side": side, "mode": mode}
    for x in parts[5:]:
        if x.startswith("yes<="):
            spec["yes_max"] = int(x[5:-1]) / 100
        elif "/" in x:
            k, L = x.split("/")
            spec["move"], spec["lookback"] = int(k[:-1]) / 100, int(L[:-1])
        else:
            lo, hi = x[:-1].split("-")
            spec["band"] = [int(lo) / 100, int(hi) / 100]
    assert rule_id(spec) == rid, rid
    return spec


# ---------------- a rule as a bench hypothesis ----------------

def hypothesis(spec, prob=None):
    """The same rule, written as a bench hypothesis (so the holdout is scored by the bench)."""
    h = spec["hours"]
    scope = spec["scope"]

    attn = None
    base_scope = scope
    if scope.startswith("lowattn<="):
        head, base_scope = scope.split(":", 1)
        attn = float(head[len("lowattn<="):])

    def where(m):
        if base_scope == "all":
            return True
        kind, val = base_scope.split(":", 1)
        return (m.get("category") or "Other") == val if kind == "category" else m["series"] == val

    def fn(m, hist, t):
        bid, ask = bench.quote(hist, t, bench.max_age(h))
        if bid is None:
            return None
        if attn is not None and sum(c.get("volume") or 0.0 for c in hist) > attn + 1e-9:
            return None
        mid = (bid + ask) / 2
        fam = spec["family"]
        if fam == "grid":
            side = spec["side"]
        elif fam == "fav":
            side = "yes" if mid >= 0.5 else "no"
        elif fam == "longshot":
            if ask > spec["yes_max"] + 1e-9:
                return None
            side = "no"
        else:
            tl = t - spec["lookback"] * 3600
            b2, a2 = bench.quote(bench.history_until(hist, tl), tl, bench.max_age(h))
            if b2 is None:
                return None
            d = mid - (b2 + a2) / 2
            up, down = d >= spec["move"] - 1e-9, d <= -spec["move"] + 1e-9
            if not (up or down):
                return None
            buy_yes = down if spec["side"] == "rev" else up
            side = "yes" if buy_yes else "no"
        s_ask, s_bid = bench.side_prices(side, bid, ask)
        limit = None
        if spec["mode"] == "maker":
            limit = bench.default_maker_limit(s_bid, s_ask)
            if limit is None:
                return None
        price = s_ask if limit is None else limit
        if "band" in spec and not (spec["band"][0] - 1e-9 <= price < spec["band"][1] - 1e-9):
            return None
        return bench.Call(side=side, limit=limit, prob=prob,
                          exit_hours=EXIT_HOURS if spec["family"] == "movex" else None)

    return bench.Hypothesis(name=rule_id(spec), hours=h, fn=fn, where=where)


# ---------------- the run ----------------

SURVIVOR_COLS = ["cohort", "rule_id", "family", "scope", "hours", "side", "mode", "band", "extra",
                 "mine_events", "mine_n", "mine_roi", "mine_win_rate", "mine_p", "mine_bh",
                 "hold_events", "hold_n", "hold_roi", "hold_ci_lo", "hold_ci_hi", "hold_p", "hold_bh",
                 "hold_win_rate", "hold_avg_price", "hold_fill_rate", "hold_brier_model", "hold_brier_market",
                 "hold_first_half_roi", "hold_second_half_roi", "hold_recent_3m_roi", "status", "spec_json"]


def holdout_rows(rule, recs, cohort, prob):
    """Prediction-log rows (the board's format) for one rule's holdout calls."""
    out = []
    for r in recs:
        out.append({"call_id": f"{cohort}-" + hashlib.sha1(f"{rule}|{r['ticker']}".encode()).hexdigest()[:10],
                    "logged_at_utc": datetime.fromtimestamp(r["t"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "engine": f"lab-holdout:{rule}", "series": r["series"], "event_ticker": r["event"],
                    "market_ticker": r["ticker"], "range_label": r["ticker"].split("-")[-1], "side": r["side"].upper(),
                    "model_prob": f"{prob:.4f}", "market_price": f"{r['price']:.4f}", "gap_pts": "",
                    "worth_a_look": "", "paper_contracts": "1" if r["filled"] else "0",
                    "fee_per_contract": f"{r['fee']:.2f}", "anchor_price": f"{r['mid_side']:.4f}",
                    "anchor_kind": "side mid at decision", "vol_daily": "", "horizon_days": "", "event_days": "",
                    "news_shift_pct": "",
                    "close_time_utc": datetime.fromtimestamp(r["close_ts"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "status": "settled", "settle_value": "", "won": str(r["won"]),
                    "pnl_after_fees": f"{r['pnl']:.2f}" if r["filled"] else "0.00",
                    "notes": f"cohort={cohort}; mode={r['mode']}" + (f"; maker_fill={'yes' if r['filled'] else 'no'} "
                                                                      f"({r.get('fill_proof')})" if r["mode"] == "maker" else "")})
    return out


def _latest_cohort():
    if not COHORTS.exists():
        return None
    cs = [json.loads(f.read_text()) for f in COHORTS.glob("*.json") if not f.name.endswith("_survivors.json")]
    return max(cs, key=lambda c: c["mined_at"]) if cs else None


def run(holdout=None, force=False, nightly=False, con=None, verbose=True, dry=False):
    con = con or lake.connect()
    now = datetime.now(timezone.utc)
    last = _latest_cohort()
    if nightly and last and not force:
        age = (now - datetime.fromisoformat(last["mined_at"])).days
        if age < COHORT_MIN_DAYS:
            return f"Mining skipped: cohort {last['cohort']} is {age} days old (a new one every {COHORT_MIN_DAYS} days)."
    hdate = datetime.fromisoformat(holdout).replace(tzinfo=timezone.utc) if holdout else \
        (now - timedelta(days=DEFAULT_HOLDOUT_DAYS)).replace(hour=0, minute=0, second=0, microsecond=0)
    cohort = f"{now:%Y%m%d%H%M}-h{hdate:%Y%m%d}"     # unique: two cohorts on one day must not share an id
    looks = lake.get_state(con, f"holdout_looks:{hdate:%Y-%m-%d}", 0)
    if looks and not force and not dry:
        return (f"The holdout starting {hdate:%Y-%m-%d} was already used {looks} time(s). Pick a new --holdout date, "
                f"or pass --force (the extra look is counted in the cohort file).")
    t0 = time.time()
    snap = load_snapshot(con, verbose=verbose)
    hts = int(hdate.timestamp())
    rows, enumerated = mine_rules(snap, hts, verbose=verbose)
    tested = [r for r in rows if r["events"] >= MIN_EVENTS]
    k = np.array([r["events"] for r in tested])
    p_t = stats.p_from_moments(k, np.array([r["s1"] for r in tested]), np.array([r["s2"] for r in tested]))
    p_b = stats.p_breakeven(np.array([r["pnl"] for r in tested]), np.array([r["var"] for r in tested]),
                            np.array([r["logq"] for r in tested]), np.array([r["wins"] == r["n"] for r in tested]))
    p = np.maximum(p_t, p_b)
    if dry:
        bh = stats.benjamini_hochberg(p, FDR_Q)
        m_total = len(p)
    else:
        bh, m_total = registry.bh_with_registry(p, FDR_Q, exclude_batch=cohort)
        registry.append([{"kind": "miner", "name": rule_id(r), "batch": cohort, "data_window": f"closed before {hdate:%Y-%m-%d}",
                          "n": r["n"], "events": r["events"], "roi": r["pnl"] / r["stake"], "p": float(pv)}
                         for r, pv in zip(tested, p)])
    for r, pv, b in zip(tested, p, bh):
        r["p"], r["bh"] = float(pv), bool(b)
    disc = sorted((r for r in tested if r["bh"] and r["pnl"] > 0), key=lambda r: (r["p"], -r["events"]))
    if verbose:
        print(f"  {len(tested)} rules had {MIN_EVENTS}+ mining events; {len(disc)} pass BH at q={FDR_Q} "
              f"(corrected over {m_total:,} trials incl. the registry)", flush=True)
    if dry:      # mining only: the holdout is not touched and no look is counted
        top = "\n".join(f"  {rule_id(r)}: {r['events']} ev, n={r['n']}, ROI {r['pnl'] / r['stake'] * 100:+.1f}%, p={r['p']:.1e}"
                        for r in disc[:25])
        return (f"DRY RUN (holdout untouched): {enumerated:,} rules enumerated, {len(rows):,} with a mining bet, "
                f"{len(tested):,} tested, {len(disc)} BH survivors\n{top}")

    # the holdout: once, through the bench
    lake.set_state(con, f"holdout_looks:{hdate:%Y-%m-%d}", looks + 1)
    con.commit()
    to_eval = disc[:MAX_HOLDOUT_RULES]
    specs = [{k2: r[k2] for k2 in ("family", "scope", "hours", "side", "mode", "band", "yes_max", "lookback", "move")
              if k2 in r} for r in to_eval]
    hyps = [hypothesis(sp, r["wins"] / r["n"]) for sp, r in zip(specs, to_eval)]
    got = bench.run_lake(hyps, con, "m.close_ts >= ?", (hts,)) if hyps else {}
    results = []
    for r, spec, h in zip(to_eval, specs, hyps):
        recs = [x for x in got[h.name] if x["t"] >= hts]
        s = stats.summary(recs, now_ts=int(now.timestamp()))
        results.append((r, spec, r["wins"] / r["n"], recs, s))
    hp = np.array([s.get("p", 1.0) if s.get("events", 0) >= HOLDOUT_MIN_EVENTS else 1.0 for *_, s in results])
    hbh = stats.benjamini_hochberg(hp, FDR_Q) if len(hp) else np.zeros(0, dtype=bool)

    out_rows, log_rows = [], []
    for (r, spec, prob, recs, s), hb in zip(results, hbh):
        passed = bool(hb) and (s.get("roi") or 0) > 0 and s.get("events", 0) >= HOLDOUT_MIN_EVENTS
        rid = rule_id(spec)
        out_rows.append({"cohort": cohort, "rule_id": rid, "family": spec["family"], "scope": spec["scope"],
                         "hours": spec["hours"], "side": spec["side"], "mode": spec["mode"],
                         "band": "-".join(f"{x:.2f}" for x in spec.get("band", [])),
                         "extra": json.dumps({k2: spec[k2] for k2 in ("yes_max", "lookback", "move") if k2 in spec}),
                         "mine_events": r["events"], "mine_n": r["n"], "mine_roi": f"{r['pnl'] / r['stake']:.4f}",
                         "mine_win_rate": f"{prob:.4f}", "mine_p": f"{r['p']:.2e}", "mine_bh": int(r["bh"]),
                         "hold_events": s.get("events", 0), "hold_n": s.get("n", 0),
                         "hold_roi": _f4(s.get("roi")), "hold_ci_lo": _f4(s.get("ci_lo")), "hold_ci_hi": _f4(s.get("ci_hi")),
                         "hold_p": f"{s.get('p', 1.0):.2e}", "hold_bh": int(bool(hb)),
                         "hold_win_rate": _f4(s.get("win_rate")), "hold_avg_price": _f4(s.get("avg_price")),
                         "hold_fill_rate": _f4(s.get("fill_rate")), "hold_brier_model": _f4(s.get("brier_model")),
                         "hold_brier_market": _f4(s.get("brier_market")),
                         "hold_first_half_roi": _f4((s.get("first_half") or {}).get("roi")),
                         "hold_second_half_roi": _f4((s.get("second_half") or {}).get("roi")),
                         "hold_recent_3m_roi": _f4((s.get("recent_3m") or {}).get("roi")),
                         "status": "HOLDOUT PASS" if passed else "HOLDOUT FAIL", "spec_json": json.dumps(spec)})
        log_rows += holdout_rows(rid, recs, cohort, prob)
    registry.append([{"kind": "holdout", "name": x["rule_id"], "batch": cohort + "-holdout",
                      "data_window": f"decisions from {hdate:%Y-%m-%d}", "n": x["hold_n"], "events": x["hold_events"],
                      "roi": x["hold_roi"], "p": x["hold_p"], "note": x["status"]} for x in out_rows])
    _append_csv(SURVIVORS, SURVIVOR_COLS, out_rows)
    log.append(log_rows, HOLDOUT_LOG)
    COHORTS.mkdir(parents=True, exist_ok=True)
    summary = {"cohort": cohort, "mined_at": now.isoformat(), "holdout_from": hdate.date().isoformat(),
               "holdout_looks": looks + 1, "snapshot_rows": int(len(snap["t"])),
               "mining_rows": int((snap["close"] < hts).sum()), "rules_enumerated": enumerated,
               "rules_with_bets": len(rows), "rules_tested": len(tested), "trials_in_correction": m_total, "bh_survivors": len(disc),
               "holdout_evaluated": len(to_eval),
               "holdout_pass": sum(1 for x in out_rows if x["status"] == "HOLDOUT PASS"),
               "minutes": round((time.time() - t0) / 60, 1)}
    (COHORTS / f"{cohort}.json").write_text(json.dumps(summary, indent=2))
    (COHORTS / f"{cohort}_survivors.json").write_text(json.dumps(
        [{**{k2: v for k2, v in r.items() if k2 not in ("s1", "s2", "var", "logq")}, "rule_id": rule_id(sp)}
         for r, sp, *_ in results], indent=1))
    return format_run(summary, out_rows)


def _f4(x):
    return "" if x is None else f"{x:.4f}"


def _append_csv(path, cols, rows):
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new:
            w.writeheader()
        w.writerows(rows)


def format_run(s, rows):
    L = [f"Cohort {s['cohort']}: mining before {s['holdout_from']}, holdout from {s['holdout_from']}",
         f"  rules enumerated {s['rules_enumerated']:,}; with a mining bet {s['rules_with_bets']:,}; "
         f"tested ({MIN_EVENTS}+ events) {s['rules_tested']:,}; BH survivors {s['bh_survivors']}; "
         f"evaluated on holdout {s['holdout_evaluated']}; HOLDOUT PASS {s['holdout_pass']}"]
    good = sorted((r for r in rows if r["status"] == "HOLDOUT PASS"), key=lambda r: float(r["hold_p"]))
    for r in good[:20]:
        L.append(f"  PASS {r['rule_id']}: holdout n={r['hold_n']} ev={r['hold_events']} ROI {float(r['hold_roi']) * 100:+.1f}% "
                 f"CI [{float(r['hold_ci_lo']) * 100:+.1f}%, {float(r['hold_ci_hi']) * 100:+.1f}%] (mining ROI "
                 f"{float(r['mine_roi']) * 100:+.1f}% on {r['mine_events']} ev)")
    return "\n".join(L)
