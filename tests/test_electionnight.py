"""Election-night engine: strict timestamp alignment, the projection maths, the feed parsers, read-only rules."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from argus import log
from argus.electionnight import backtest as B
from argus.electionnight import http, live, model, races, sources
from argus.electionnight.sources import Snapshot, Unit

T0 = datetime(2024, 11, 6, 1, 0, tzinfo=timezone.utc)


def candle(t, bid, ask, low=None, high=None):
    return {"end_ts": int(t.timestamp()), "bid": bid, "ask": ask, "low": low, "high": high}


# ---------------- timestamp alignment ----------------

def test_quote_is_strictly_after_the_count_was_public():
    cs = [candle(T0 - timedelta(minutes=5), 0.40, 0.42),     # before: must never be used
          candle(T0, 0.50, 0.52),                            # same minute: too early (no time to read it)
          candle(T0 + timedelta(seconds=30), 0.55, 0.57),    # inside the 60 s reading delay: too early
          candle(T0 + timedelta(seconds=120), 0.70, 0.72)]
    bid, ask, ts = B.quote_after(cs, T0)
    assert (bid, ask) == (0.70, 0.72)
    assert ts >= T0.timestamp() + B.EXEC_DELAY_S


def test_quote_too_late_or_empty_book_is_refused():
    assert B.quote_after([candle(T0 + timedelta(minutes=20), 0.5, 0.52)], T0) is None
    assert B.quote_after([candle(T0 + timedelta(minutes=2), 0.01, 0.99)], T0) is None     # empty book
    assert B.quote_after([candle(T0 + timedelta(minutes=2), 0.40, 0.50)], T0) is None     # 10c wide
    assert B.quote_after([candle(T0 + timedelta(minutes=2), None, 0.50)], T0) is None


def test_snapshot_clock_is_the_capture_time_not_the_states_stamp():
    doc = {"name": [{"text": "President"}],
           "reportingStatus": {"asOf": "2024-11-06T00:10:00Z", "reportingUnits": 1, "totalUnits": 2},
           "breakdownResults": [{"locality": {"name": [{"text": "A County"}], "id": "x"},
                                 "precinctReportingStatus": {"reportingUnits": 1, "totalUnits": 2},
                                 "ballotOptions": [{"name": [{"text": "Ann Smith (Dem)"}], "voteCount": 10,
                                                    "party": {"abbreviation": "Dem"}, "groupResults": []}]}]}
    s = sources.ev_contest(doc, T0, by="party")
    assert s.t_known == T0 and s.as_of < T0          # as_of is informational only
    assert s.units["A County"].cand("dem") == 10


def _fake_race(monkeypatch, snaps, candles, result="yes"):
    rc = {"id": "XX-TEST", "contest": "t", "cdx": "u", "polls_close": T0 - timedelta(hours=1),
          "night_end": T0 + timedelta(hours=6), "remaining_type": "eday",
          "markets": {"TEST-R": ("beats", "rep")}}
    monkeypatch.setattr(B, "snapshots", lambda rc_, v=False: snaps)
    monkeypatch.setattr(B, "prior_for", lambda rc_: {})
    monkeypatch.setattr(B, "kalshi_markets", lambda rc_: {"TEST-R": (
        {"ticker": "TEST-R", "event_ticker": "TEST", "result": result, "open_time": "2024-01-01T00:00:00Z",
         "close_time": "2025-01-01T00:00:00Z", "yes_sub_title": "Rep"}, ("beats", "rep"))})
    monkeypatch.setattr(B, "minute_candles", lambda t, a, b: candles)
    monkeypatch.setattr(B.http, "kalshi", lambda *a, **k: {"series": {"fee_type": "quadratic"}})
    return rc


def _snap(t, rep, dem, frac=1.0):
    n = 100
    return Snapshot(t, "t", {f"C{i}": Unit({"rep": {"eday": rep, "other": 0}, "dem": {"eday": dem, "other": 0}},
                                           (int(n * frac), n)) for i in range(4)})


def test_replay_prices_each_call_after_its_count(monkeypatch):
    t1 = T0 + timedelta(minutes=30)
    snaps = [_snap(t1, 600, 400, frac=0.9)]
    # the market was cheap BEFORE the count and expensive after: only the after price may be used
    cs = [candle(t1 - timedelta(minutes=1), 0.30, 0.32), candle(t1 + timedelta(minutes=2), 0.80, 0.82)]
    rows = B.replay(_fake_race(monkeypatch, snaps, cs), verbose=False)
    take = [r for r in rows if r["engine"] == B.ENGINE]
    assert take
    for r in take:
        n = dict(x.split("=", 1) for x in r["notes"].split("; "))
        assert n["quote_ts"] > n["t_known"]
        assert float(r["market_price"]) == 0.82          # the after-count ask, never 0.32


def test_no_calls_before_real_reporting(monkeypatch):
    t1 = T0 + timedelta(minutes=5)
    tiny = Snapshot(t1, "t", {"C0": Unit({"rep": {"eday": 6, "other": 0}, "dem": {"eday": 4, "other": 0}}, (1, 100)),
                              "C1": Unit({}, (0, 100)), "C2": Unit({}, (0, 100))})
    tiny.units["C1"].votes = {"rep": {"eday": 0, "other": 0}, "dem": {"eday": 0, "other": 0}}
    tiny.units["C2"].votes = {"rep": {"eday": 0, "other": 0}, "dem": {"eday": 0, "other": 0}}
    rows = B.replay(_fake_race(monkeypatch, [tiny], [candle(t1 + timedelta(minutes=2), 0.5, 0.52)]), verbose=False)
    assert rows == []


def test_one_paper_bet_per_market(monkeypatch):
    ts = [T0 + timedelta(minutes=m) for m in (30, 45, 60)]
    snaps = [_snap(t, 600, 400, 0.9) for t in ts]
    cs = [candle(t + timedelta(minutes=2), 0.70, 0.72) for t in ts]
    rows = B.replay(_fake_race(monkeypatch, snaps, cs), verbose=False)
    assert sum(r["paper_contracts"] == "1" for r in rows if r["engine"] == B.ENGINE) == 1


def test_rest_fill_needs_a_later_trade_at_our_price():
    t = int(T0.timestamp())
    cs = [{"end_ts": t - 60, "low": 0.50, "high": 0.9}, {"end_ts": t + 120, "low": 0.66, "high": 0.7}]
    assert not B.rest_filled(cs, "YES", 0.65, t)          # the earlier 50c print does not count
    cs.append({"end_ts": t + 300, "low": 0.65, "high": 0.7})
    assert B.rest_filled(cs, "YES", 0.65, t)
    assert B.rest_filled([{"end_ts": t + 60, "low": 0.2, "high": 0.36}], "NO", 0.64, t)


# ---------------- the model ----------------

def test_fully_counted_race_is_certain():
    s = _snap(T0, 510, 490, frac=1.0)
    p = model.project(s)
    assert p.remaining == 0 and model.p_beats(p, "rep") == 1.0 and model.p_beats(p, "dem") == 0.0


def test_election_day_votes_scale_with_precincts():
    u = Unit({"a": {"eday": 30, "other": 100}, "b": {"eday": 20, "other": 100}}, (50, 100))
    p = model.project(Snapshot(T0, "t", {"C": u}))
    # early vote in, half the election-day precincts in: 50 more election-day votes, split 60/40
    assert p.final["a"] == pytest.approx(130 + 30) and p.final["b"] == pytest.approx(120 + 20)
    assert 0.5 < model.p_beats(p, "a") < 1.0


def test_unreported_county_sized_from_registered_voters():
    s = Snapshot(T0, "t", {"Big": Unit({"a": {"eday": 0, "other": 0}, "b": {"eday": 0, "other": 0}}, (0, 10)),
                           "Small": Unit({"a": {"eday": 60, "other": 0}, "b": {"eday": 40, "other": 0}}, (10, 10))})
    p = model.project(s, prior={"Big": {"registered": 1000}, "Small": {"registered": 200}})
    # Small is complete: 100 ballots / 200 registered = 50% turnout, so Big expects 500
    assert p.total() == pytest.approx(600)


def test_margin_bucket_probabilities_sum_to_one():
    s = Snapshot(T0, "t", {"C": Unit({"rep": {"eday": 300, "other": 2000}, "dem": {"eday": 200, "other": 1900}},
                                     (20, 100))})
    p = model.project(s)
    edges = [None, -2, -1, 0, 1, 2, 3, None]
    tot = sum(model.p_margin_between(p, "rep", "dem", edges[i], edges[i + 1]) for i in range(len(edges) - 1))
    assert tot == pytest.approx(1.0, abs=1e-9)


def test_majority_question():
    s = _snap(T0, 520, 480, frac=1.0)
    assert model.p_majority(model.project(s), "rep") == 1.0


def test_margin_labels():
    assert races.margin_question("Trump by 2-2.99%") == ("margin", "rep", "dem", 2.0, 3.0)
    assert races.margin_question("Trump by 6% or more") == ("margin", "rep", "dem", 6.0, None)
    assert races.margin_question("Harris by 1-1.99%") == ("margin", "rep", "dem", -2.0, -1.0)


# ---------------- feed parsers ----------------

def test_candidate_keys():
    assert sources.cand_key("Donald J. Trump (Rep)") == "trump"
    assert sources.cand_key("BIDEN, JOSEPH ROBINETTE JR") == "biden"
    assert sources.cand_key("BOB CASEY JR") == "casey"
    assert sources.cand_key("X", "REP", by="party") == "rep"
    assert sources.cand_key("X", "D", by="party") == "dem"


def test_pa_double_encoded_county_break():
    inner = {"Election": {"Statewide": [{"ADAMS": [
        {"CandidateName": "KAMALA D HARRIS", "PartyName": "DEM", "ElectionDayVotes": "5", "MailInVotes": "7",
         "ProvisionalVotes": "1"}]}]}}
    s = sources.pa_county(json.dumps(json.dumps(inner)), T0, "P", by="party")
    assert s.units["Adams"].votes["dem"] == {"eday": 5, "other": 8}


def test_nc_statewide_modes():
    rows = [{"cnm": "US SENATE (VOTE FOR 1)", "bnm": "Roy Cooper", "pty": "DEM", "evc": "10", "ovc": "20",
             "avc": "5", "pvc": "1", "prt": "3", "ptl": "10"},
            {"cnm": "US SENATE (VOTE FOR 1)", "bnm": "Write-In (Miscellaneous)", "pty": "", "evc": "1",
             "ovc": "1", "avc": "0", "pvc": "0", "prt": "3", "ptl": "10"}]
    s = sources.nc_statewide(rows, T0, r"^US SENATE", by="party")
    assert s.units["STATE"].votes == {"dem": {"eday": 10, "other": 26}} and s.units["STATE"].precincts == (3, 10)


def test_captures_drop_repeats(monkeypatch):
    monkeypatch.setattr(http, "public", lambda *a, **k: [
        ["timestamp", "original", "digest"],
        ["20241106010500", "u", "B"], ["20241106010000", "u", "A"], ["20241106011000", "u", "B"],
        ["20241106011500", "u", "A"]])
    caps = sources.captures("u", T0, T0 + timedelta(hours=1))
    assert [c[0].strftime("%H%M") for c in caps] == ["0100", "0105", "0115"]


# ---------------- read-only ----------------

def test_read_only_and_allowlist():
    with pytest.raises(PermissionError):
        http.kalshi("/portfolio/orders")
    with pytest.raises(PermissionError):
        http.public("https://example.com/results")


# ---------------- live ----------------

def test_live_ignores_counts_before_polls_close(monkeypatch, tmp_path):
    rc = dict(live.LIVE_2026[0], polls_close=datetime.now(timezone.utc) + timedelta(hours=1))
    monkeypatch.setattr(live, "snapshot", lambda rc_, st: (_snap(datetime.now(timezone.utc), 600, 400), ""))
    out = []
    rows = live.run_once([rc], path=tmp_path / "l.csv", write=True, out=out.append)
    assert rows == [] and "test data, ignored" in out[1]


def test_live_logs_one_call_and_settles_with_its_fee(monkeypatch, tmp_path):
    path = tmp_path / "electionnight_log.csv"
    rc = dict(live.LIVE_2026[0], polls_close=datetime(2020, 1, 1, tzinfo=timezone.utc),
              markets={"GOVPARTYGA-26-R": ("beats", "rep")})
    monkeypatch.setattr(live, "snapshot", lambda rc_, st: (_snap(datetime.now(timezone.utc), 600, 400, 0.9), ""))
    monkeypatch.setattr(live, "kalshi_quote", lambda t: ({"event_ticker": "GOVPARTYGA-26"}, 0.70, 0.72))
    for _ in range(2):
        live.run_once([rc], path=path, out=lambda *_: None)
    rows = log.read_log(path)
    assert sum(r["paper_contracts"] == "1" for r in rows) == 1
    monkeypatch.setattr(live.http, "kalshi", lambda *a, **k: {"market": {"result": "yes"}})
    assert live.settle(path) == 2
    bet = next(r for r in log.read_log(path) if r["paper_contracts"] == "1")
    assert bet["won"] == "1" and float(bet["pnl_after_fees"]) == pytest.approx(1 - 0.72 - float(bet["fee_per_contract"]))
