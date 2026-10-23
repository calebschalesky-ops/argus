"""The oil engine: fetch one Kalshi event, price every range, build the alert and log rows."""
from dataclasses import dataclass, field

from . import fees, filters, kalshi, prices
from .events import event_days, events_between
from .markets import (Contract, ladder_implied, ladder_median, ladder_points, model_prob)
from .model import ET, PriceCurve, now_utc, parse_time, trading_days
from .news import shift_for, total_shift

WORTH_A_LOOK_PTS = filters.WORTH_A_LOOK_PTS

# Which series Argus prices, what commodity they track, and where the "current price" ladder lives.
SERIES = {
    "KXWTIW":   {"commodity": "WTI",   "ladder": "KXWTI",    "settles": "ICE WTI daily settle, nearest contract, 2:30 PM ET"},
    "KXWTI":    {"commodity": "WTI",   "ladder": "KXWTI",    "settles": "ICE WTI daily settle, nearest contract, 2:30 PM ET"},
    "KXBRENTW": {"commodity": "BRENT", "ladder": "KXBRENTW", "settles": "Pyth Brent index, 1-min close at 5:00 PM ET"},
    "KXBRENTD": {"commodity": "BRENT", "ladder": "KXBRENTD", "settles": "Pyth Brent index, 1-min close at 5:00 PM ET"},
}


@dataclass
class Row:
    c: Contract
    p_yes: float
    side: str = "-"
    price: float | None = None
    gap: float | None = None
    ev: float | None = None
    ladder_p: float | None = None
    flag: str = "-"
    quote: filters.Quote | None = None    # the chosen side priced both ways (taker / maker bid+1c)


@dataclass
class Result:
    series: str
    event_ticker: str
    title: str
    close: object
    anchor: float
    anchor_kind: str
    last_settle: tuple | None
    vol: float
    days: float
    events: list
    ev_days: float
    news: list = field(default_factory=list)
    news_shift: float = 0.0
    rows: list = field(default_factory=list)
    vol_notes: dict = field(default_factory=dict)
    band: filters.Band | None = filters.DEFAULT_BAND   # None = no price band
    pricing: str = "taker"                             # "taker" (pay the ask) or "maker" (rest at bid + 1c)
    maker_fees: bool = False                           # does this series charge makers?


def series_of(event_ticker):
    return event_ticker.split("-")[0]


def open_events(series):
    """Open events of a series, soonest close first."""
    ms = kalshi.get_markets(series_ticker=series)
    evs = {}
    for m in ms:
        evs.setdefault(m["event_ticker"], m["close_time"])
    return sorted(evs.items(), key=lambda kv: kv[1])


def side_quotes(p_yes, c, band=None, maker_fees=False, multiplier=1):
    """Both sides priced two ways: {"YES": Quote, "NO": Quote} (filters.check)."""
    out = {}
    for side, p in (("YES", p_yes), ("NO", 1 - p_yes)):
        ask, bid = ((c.yes_ask, c.yes_bid) if side == "YES" else (c.no_ask, c.no_bid))
        out[side] = filters.check(p, ask, bid, band, maker_fees, multiplier)
    return out


def _pick_side(p_yes, c, pricing="taker", maker_fees=False, band=None):
    """Return the side with the bigger gap: (side, price, gap_pts, ev_per_contract).

    pricing="taker" prices at the ask (the original rule); "maker" at a resting bid + 1c."""
    options = []
    for side, q in side_quotes(p_yes, c, band, maker_fees).items():
        if q.price(pricing) is not None and (pricing == "maker" or q.ask is not None):
            options.append((side, q.price(pricing), q.gap(pricing), q.ev(pricing)))
    if not options:
        return "-", None, None, None
    return max(options, key=lambda o: o[2])


def _same_close_ladder(ladder_series, close_iso):
    """The daily ladder that settles at the same moment (for the anchor and consistency check)."""
    try:
        ms = kalshi.get_markets(series_ticker=ladder_series)
    except Exception:
        return []
    return [Contract.from_api(m) for m in ms if m["close_time"] == close_iso]


def run_event(event_ticker, anchor_mode="auto", news_scores=None, news_shift=0.0,
              band=filters.DEFAULT_BAND, pricing="taker"):
    series = series_of(event_ticker)
    if series not in SERIES:
        raise ValueError(f"{series} is not an oil series Argus prices: {', '.join(SERIES)}")
    if kalshi.is_fifteen_minute(kalshi.get_series(series)):
        raise ValueError("Argus skips 15-minute markets.")
    info = SERIES[series]
    s_meta = kalshi.get_series(series)
    contracts = sorted((Contract.from_api(m) for m in kalshi.get_markets(event_ticker=event_ticker)),
                       key=Contract.sort_key)
    if not contracts:
        raise ValueError(f"No open markets in {event_ticker}")
    close_iso = contracts[0].close_time
    close = parse_time(close_iso)

    vol, notes = prices.estimate_vol(info["commodity"])
    last = notes.get("last_settle")

    # Anchor: the 50% point of the live ladder that settles at the same moment.
    ladder = _same_close_ladder(info["ladder"], close_iso)
    pts = ladder_points(ladder)
    lad_mid = ladder_median(pts)
    if anchor_mode == "settle" or lad_mid is None:
        anchor, kind = (last[1], f"last official settle {last[0][:10]}") if last else (None, "none")
    else:
        anchor, kind = lad_mid, f"Kalshi {info['ladder']} ladder 50% point (crowd price)"
    if anchor is None:
        raise RuntimeError("No anchor price available (no ladder midpoint and no settlement history).")

    now = now_utc()
    days = trading_days(now, close)
    evs = events_between(now.astimezone(ET), close.astimezone(ET))
    ev_days = event_days(evs)
    curve = PriceCurve(anchor, vol, days, ev_days, news_shift)

    news_scores = news_scores or []
    if news_scores:
        news_shift = total_shift(news_scores)
        curve = PriceCurve(anchor, vol, days, ev_days, news_shift)
    maker_fees, _ = fees.series_maker_fees(s_meta)
    res = Result(series, event_ticker, s_meta.get("title", series), close, anchor, kind,
                 last, vol, days, evs, ev_days, news_scores, news_shift, vol_notes=notes,
                 band=band, pricing=pricing, maker_fees=maker_fees)
    use_ladder_check = info["ladder"] != series and bool(pts)
    for c in contracts:
        p = model_prob(c, curve.cdf)
        res.rows.append(price_row(c, p, res, pts if use_ladder_check else None))

    # Show how much each headline moved things: the change in the most likely range.
    if news_scores:
        top = max(res.rows, key=lambda r: r.p_yes)
        base = model_prob(top.c, PriceCurve(anchor, vol, days, ev_days, 0.0).cdf)
        for s in news_scores:
            s["shift"] = shift_for(s)
            moved = model_prob(top.c, PriceCurve(anchor, vol, days, ev_days, s["shift"]).cdf)
            s["move_pts"] = (moved - base) * 100
    return res


def price_row(c, p_yes, res, ladder_pts=None):
    """One range: pick the side, price it both ways, and flag it through the shared filter.

    WORTH A LOOK needs a 10+ point gap at the chosen pricing AND a side price inside the band;
    a big gap on a cheap side shows as e.g. "UNDER 50c" instead (the longshot rule)."""
    side, price, gap, ev = _pick_side(p_yes, c, res.pricing, res.maker_fees, res.band)
    row = Row(c, p_yes, side, price, gap, ev)
    if side != "-":
        row.quote = side_quotes(p_yes, c, res.band, res.maker_fees)[side]
        row.flag = row.quote.flag(res.pricing)
    if ladder_pts:
        row.ladder_p = ladder_implied(c, ladder_pts)
        if row.flag == "-" and c.yes_ask and abs(row.ladder_p - c.yes_ask) * 100 >= WORTH_A_LOOK_PTS:
            row.flag = "CONSISTENCY"
    return row


def log_rows(res):
    """Turn a result into prediction-log rows (one per range)."""
    from datetime import timezone
    out = []
    stamp = now_utc().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for r in res.rows:
        if r.side == "-":
            continue                      # no buyable side: nothing to call
        p_side = r.p_yes if r.side == "YES" else 1 - r.p_yes
        worth = r.flag == "WORTH A LOOK"
        q = r.quote
        fee = q.fee(res.pricing) if q and q.fee(res.pricing) is not None else fees.taker_fee(r.price)
        extra = "" if not q else (f"; pricing={res.pricing}; ask={q.ask if q.ask is not None else ''}"
                                  f"; maker_price={q.maker_price if q.maker_price is not None else ''}"
                                  f"; band={res.band.describe() if res.band else 'off'}"
                                  + ("; maker_fill=assumed" if res.pricing == "maker" else ""))
        out.append({
            "logged_at_utc": stamp, "engine": "oil", "series": res.series,
            "event_ticker": res.event_ticker, "market_ticker": r.c.ticker,
            "range_label": r.c.label, "side": r.side,
            "model_prob": f"{p_side:.4f}", "market_price": f"{r.price:.2f}",
            "gap_pts": f"{r.gap:.1f}", "worth_a_look": "1" if worth else "0",
            "paper_contracts": "1" if worth else "0",
            "fee_per_contract": f"{fee:.2f}",
            "anchor_price": f"{res.anchor:.2f}", "anchor_kind": res.anchor_kind,
            "vol_daily": f"{res.vol:.4f}", "horizon_days": f"{res.days:.2f}",
            "event_days": f"{res.ev_days:.2f}", "news_shift_pct": f"{res.news_shift * 100:.2f}",
            "close_time_utc": r.c.close_time,
            "notes": f"shape={r.c.kind}|{r.c.low}|{r.c.high}; flag={r.flag}"
                     + (f"; ladder_p={r.ladder_p:.3f}" if r.ladder_p is not None else "") + extra,
        })
    return out
