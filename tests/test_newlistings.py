"""New-listings study: prices at a moment, honest maker fills, walk-forward with no look-ahead, live filter."""
from datetime import datetime, timedelta, timezone

import pytest

from argus.newlistings import data as D
from argus.newlistings import live as L
from argus.newlistings import study as S

T0 = datetime(2026, 1, 5, 12, tzinfo=timezone.utc)


def candle(end, bid, ask, px=None):
    return {"end_period_ts": int(end.timestamp()), "yes_bid": {"close_dollars": str(bid)},
            "yes_ask": {"close_dollars": str(ask)}, "price": {"close_dollars": str(px)} if px else {}}


def book(bid, ask):
    return {"bid": bid, "ask": ask, "last": None, "age_h": 0}


def row(i, open_t, won, h1=(0.40, 0.42), cat="Sports", settle_after_h=72, event=None, fills=()):
    o = open_t
    return {"ticker": f"M{i}", "event": event or f"E{i}", "series": "KXTEST", "category": cat, "n_in_event": 2,
            "type": "pick-one list", "tier": "live", "open": o.isoformat(), "close": (o + timedelta(hours=settle_after_h)).isoformat(),
            "settle": (o + timedelta(hours=settle_after_h + 1)).isoformat(), "won": won, "early_close": False,
            "volume": 10, "fills": list(fills),
            "books": {"h1": book(*h1), "h6": book(*h1), "h24": book(*h1), "late": book(*h1)}}


# ---------------- read-only ----------------

@pytest.mark.parametrize("path", ["/portfolio/orders", "/markets/../portfolio/balance", "/orders", "/exchange/status"])
def test_get_refuses_anything_but_public_reads(path, monkeypatch, tmp_path):
    monkeypatch.setattr(D, "CALL_LOG", tmp_path / "calls.csv")
    monkeypatch.setattr(D, "CACHE", tmp_path)
    with pytest.raises(PermissionError):
        D.get(path)
    assert "REFUSED" in (tmp_path / "calls.csv").read_text()


# ---------------- prices at a moment ----------------

def test_book_at_ignores_candles_that_end_after_the_moment():
    cs = [candle(T0, 0.30, 0.32), candle(T0 + timedelta(hours=1), 0.80, 0.82)]
    b = D.book_at(cs, int((T0 + timedelta(minutes=59)).timestamp()), 3600)
    assert (b["bid"], b["ask"]) == (0.30, 0.32)


def test_book_at_too_old_is_none():
    assert D.book_at([candle(T0, 0.3, 0.32)], int((T0 + timedelta(hours=3)).timestamp()), 3600) is None


def test_real_book_needs_bid_within_5c():
    assert D.real_book(book(0.40, 0.45)) and D.mid(book(0.40, 0.45)) == 0.425
    assert not D.real_book(book(0.01, 0.99)) and D.mid(book(0.01, 0.99)) is None
    assert not D.real_book(book(None, 0.40))


def test_market_type_and_strikes():
    assert D.market_type({"strike_type": "greater"}, 1) == "single"
    assert D.market_type({"strike_type": "between"}, 8) == "numeric ladder"
    assert D.market_type({"strike_type": "custom"}, 8) == "pick-one list"
    assert [D.strikes_bin(n) for n in (1, 3, 10, 40)] == ["1", "2-5", "6-15", "16+"]


# ---------------- honest maker fills ----------------

def test_maker_fill_needs_a_later_taker_crossing():
    t0 = T0.timestamp()
    fills = [[t0 - 10, 0, 0.30, 5],          # before we placed: never counts
             [t0 + 10, 1, 0.30, 5],          # taker BOUGHT yes: does not fill a yes bid
             [t0 + 20, 0, 0.32, 5]]          # taker sold yes above our limit: no
    assert D.maker_fill(fills, "yes", 0.31, t0, t0 + 3600) is None
    fills.append([t0 + 30, 0, 0.31, 1])      # taker sold yes at our price
    assert D.maker_fill(fills, "yes", 0.31, t0, t0 + 3600) == t0 + 30
    assert D.maker_fill(fills, "yes", 0.31, t0, t0 + 3600, strict=True) is None
    assert D.maker_fill(fills, "yes", 0.31, t0, t0 + 25) is None     # window over


def test_maker_fill_no_side():
    t0 = T0.timestamp()
    # No bid at 0.60 = selling Yes at 0.40: filled when a taker buys Yes at 0.40 or more
    assert D.maker_fill([[t0 + 5, 1, 0.39, 1]], "no", 0.60, t0, t0 + 99) is None
    assert D.maker_fill([[t0 + 5, 1, 0.40, 1]], "no", 0.60, t0, t0 + 99) == t0 + 5


# ---------------- walk-forward, no look-ahead ----------------

def test_history_uses_only_markets_settled_before_t(monkeypatch):
    monkeypatch.setattr(S, "MIN_HIST", 3)
    monkeypatch.setattr(S, "MIN_HIST_EVENTS", 3)
    rows = [row(i, T0 + timedelta(days=i), won=False) for i in range(6)]
    h = S.History(rows, "h1", S.default_cell)
    cell = ("Sports", "30-50")
    settle2 = D.parse_time(rows[2]["settle"])
    assert h.bias_before(settle2, cell) == (None, 2)              # only 2 settled strictly before
    b, n = h.bias_before(settle2 + timedelta(seconds=1), cell)
    assert n == 3 and b == pytest.approx(-0.41)


def test_walk_forward_bets_against_a_cell_that_was_too_high_and_never_sees_the_future(monkeypatch):
    monkeypatch.setattr(S, "MIN_HIST", 5)
    monkeypatch.setattr(S, "MIN_HIST_EVENTS", 5)
    past = [row(i, T0 + timedelta(days=i), won=False) for i in range(5)]     # 41c early, all lost
    later = row(99, T0 + timedelta(days=30), won=True)
    bets = S.walk_forward(past + [later], "h1", {})
    assert [b["ticker"] for b in bets] == ["M99"]            # first five had no history yet
    b = bets[0]
    assert b["side"] == "no" and b["price"] == 0.60 and b["won"] is False
    assert b["pnl"] == pytest.approx(-0.60 - b["fee"])
    # a market that settles AFTER the entry must not change the decision
    future = [row(200 + i, T0 + timedelta(days=29, hours=23), won=True, settle_after_h=200) for i in range(20)]
    assert [x["ticker"] for x in S.walk_forward(past + [later] + future, "h1", {}) if x["ticker"] == "M99"] == ["M99"]


def test_decide_requires_edge_after_fee():
    assert S.decide(book(0.40, 0.42), -0.02) is None                         # 2 pts is not enough
    side, price, fee, p = S.decide(book(0.40, 0.42), -0.20)
    assert side == "no" and price == 0.60 and fee == 0.02 and p == pytest.approx(0.79)
    assert S.decide(book(0.01, 0.99), -0.5) is None                          # empty book: no price


def test_maker_variant_counts_only_filled_orders(monkeypatch):
    monkeypatch.setattr(S, "MIN_HIST", 5)
    monkeypatch.setattr(S, "MIN_HIST_EVENTS", 5)
    past = [row(i, T0 + timedelta(days=i), won=False) for i in range(5)]
    o = T0 + timedelta(days=30)
    entry = (o + timedelta(hours=1)).timestamp()
    unfilled = row(98, o, won=True)
    filled = row(99, o, won=True, fills=[[entry + 60, 1, 0.42, 1]])       # taker bought Yes at 42c -> our No at 59c fills
    bets = S.walk_forward(past + [unfilled, filled], "h1", {}, maker=True)
    assert [b["ticker"] for b in bets] == ["M99"]
    assert bets[0]["side"] == "no" and bets[0]["price"] == 0.59 and bets[0]["fee"] == 0.0


def test_maker_both_legs():
    o = T0
    entry = (o + timedelta(hours=1)).timestamp()
    r = row(1, o, won=True, fills=[[entry + 5, 0, 0.41, 1], [entry + 9, 1, 0.41, 1]])
    bets = S.maker_both([r], "h1", {})
    assert sorted((b["side"], b["price"]) for b in bets) == [("no", 0.59), ("yes", 0.41)]


def test_splits_and_verdict_need_twenty_events():
    bets = [{"t": (T0 + timedelta(days=i)).isoformat(), "event": f"E{i}", "price": 0.5, "fee": 0.02,
             "pnl": 0.48, "won": True, "p_model": 0.6} for i in range(10)]
    assert S.verdict(S.splits(bets)).startswith("TOO EARLY")


# ---------------- live ----------------

def test_live_candidates_filter(monkeypatch):
    now = T0
    smeta = {"KXA": {"category": "Sports", "frequency": "daily"}, "KXB15M": {"category": "Crypto", "frequency": "fifteen_min"}}

    def m(t, series, age_h, life_h, bid="0.40", ask="0.42"):
        o = now - timedelta(hours=age_h)
        return {"ticker": t, "event_ticker": f"{series}-X", "open_time": o.isoformat(),
                "close_time": (o + timedelta(hours=life_h)).isoformat(), "yes_bid_dollars": bid, "yes_ask_dollars": ask}
    ms = [m("ok", "KXA", 24.5, 100), m("young", "KXA", 3, 100), m("short", "KXA", 24.5, 30),
          m("fifteen", "KXB15M", 24.5, 100), m("empty", "KXA", 24.5, 100, "0.01", "0.99")]
    cands = L.candidates(ms, smeta, now, "h24", 2.0, {("Sports", "30-50"): -0.20}, pooled=False)
    assert [c["m"]["ticker"] for c in cands] == ["ok"]
    assert cands[0]["side"] == "no" and cands[0]["price"] == 0.60


def test_live_pooled_cell_and_event_cap():
    now = T0
    smeta = {"KXA": {"category": "Sports", "frequency": "daily"}}
    o = now - timedelta(hours=6.5)
    ms = [{"ticker": f"t{i}", "event_ticker": "KXA-X", "open_time": o.isoformat(), "close_time": (o + timedelta(days=5)).isoformat(),
           "yes_bid_dollars": "0.40", "yes_ask_dollars": "0.42"} for i in range(5)]
    cands = L.candidates(ms, smeta, now, "h6", 2.0, {"30-50": -0.20})
    assert len(cands) == L.CAP_PER_EVENT and cands[0]["cell"] == "30-50"
    rows = L.log_rows(cands, now, "h6")
    assert rows[0]["engine"] == "newlisting-live-wfband-6h" and rows[0]["side"] == "NO" and rows[0]["status"] == "pending"
