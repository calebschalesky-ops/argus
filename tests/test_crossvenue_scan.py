"""Tests for the general cross-venue scanner (`python -m argus.crossvenue scan`)."""
import json

import pytest

from argus import crossvenue as cvs
from argus import polymarket


def kev(title, markets, ticker="KXTEST-26", sources=()):
    return {"title": title, "sub_title": "", "event_ticker": ticker, "category": "Test",
            "_sources": [{"name": n, "url": ""} for n in sources], "markets": markets}


def kmk(ticker, title, sub="", yes_ask=0.40, no_ask=0.61, rules="", exp="2026-10-23T21:00:00Z"):
    return {"ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0], "title": title, "yes_sub_title": sub,
            "status": "active", "yes_ask_dollars": str(yes_ask), "no_ask_dollars": str(no_ask),
            "yes_bid_dollars": "0.39", "yes_ask_size_fp": "100", "yes_bid_size_fp": "50",
            "rules_primary": rules, "rules_secondary": "", "close_time": exp, "expected_expiration_time": exp}


def pev(title, markets, eid="1", end="2026-10-23T21:00:00Z", desc=""):
    return {"id": eid, "slug": "test", "title": title, "endDate": end, "description": desc, "markets": markets}


def pmk(mid, question, group="", bid=0.55, ask=0.57, outcomes=("Yes", "No"), desc="", end="2026-10-23T21:00:00Z"):
    return {"id": mid, "question": question, "groupItemTitle": group, "outcomes": json.dumps(list(outcomes)),
            "clobTokenIds": json.dumps([f"{mid}a", f"{mid}b"]), "bestBid": bid, "bestAsk": ask,
            "active": True, "closed": False, "enableOrderBook": True, "outcomePrices": '["0.5","0.5"]',
            "endDate": end, "description": desc, "liquidityNum": 1000}


def pair(k_event, p_event):
    """Run the matcher on one Kalshi event and one Polymarket event."""
    return cvs.match_all([k_event], [p_event], min_conf=0)


# ---------------- read-only guard ----------------

def test_polymarket_allows_only_listed_paths_including_keyset():
    assert "/events/keyset" in polymarket.ALLOWED[polymarket.GAMMA]
    for path in ("/events/keyset/../order", "/order", "/rewards"):
        with pytest.raises(PermissionError):
            polymarket._get(polymarket.GAMMA, path)


# ---------------- matching ----------------

def test_tokens_normalise_money_ordinals_and_months():
    assert cvs.tokens("Bitcoin above $100k on October 3rd") == ["bitcoin", "above", "100000", "oct", "3"]
    assert cvs.tokens("Third Round Leader") == ["3", "round", "leader"]
    assert "80000" in cvs.tokens("$80,000 or above")


def test_same_crypto_threshold_matches_with_high_confidence():
    k = kev("BTC price on Oct 22, 2026 at 5pm EDT?",
            [kmk("KXBTCD-26OCT2217-T80000", "Bitcoin price on Oct 22, 2026?", "$80,000 or above"),
             kmk("KXBTCD-26OCT2217-T81000", "Bitcoin price on Oct 22, 2026?", "$81,000 or above")])
    p = pev("Bitcoin above ___ on October 22, 5PM ET?",
            [pmk("1", "Bitcoin above 80,000 on October 22, 5PM ET?", "80,000"),
             pmk("2", "Bitcoin above 81,000 on October 22, 5PM ET?", "81,000")])
    rows = pair(k, p)
    assert {(r["k"]["m"]["ticker"][-6:], r["p"]["m"]["id"]) for r in rows} == {("T80000", "1"), ("T81000", "2")}
    assert all(r["conf"] >= cvs.MIN_CONF_FLAG for r in rows)


@pytest.mark.parametrize("k_title, k_sub, p_title, p_q, p_group", [
    # a different threshold
    ("BTC price on Oct 22?", "$80,000 or above", "Bitcoin above ___ on October 22?",
     "Bitcoin above 82,000 on October 22?", "82,000"),
    # above at one moment vs touched at any time
    ("Bitcoin above $90k on Dec 31?", "", "Will Bitcoin reach $90k by December 31?",
     "Will Bitcoin reach $90k by December 31?", ""),
    # exactly 5 vs fewer than 5
    ("How many Starship launches in 2026?", "5", "How many Starship launches in 2026?",
     "Will less than 5 launches reach space?", "<5"),
    # Round 3 leader vs tournament winner
    ("Genesis Championship End of Round 3 Leader", "Elvis Smylie", "Genesis Championship Winner",
     "Will Elvis Smylie win?", "Elvis Smylie"),
    # global chart vs US chart
    ("Top artist on Spotify in 2026?", "Drake", "Top Spotify Artist 2026",
     "Will Drake be the top artist in the US for 2026?", "Drake"),
    # mention bundles that pay on different word lists
    ("What will Warsh say at his October press conference?", "Gas / Gasoline",
     "What will Warsh say during October Press Conference?", "Will Warsh say Oil or Gas or Gasoline?",
     "Oil / Gas / Gasoline"),
    # same surname, different person
    ("Genesis Championship Winner", "Hyungjoon Lee", "Genesis Championship Winner", "Will Dai-Han Lee win?",
     "Dai-Han Lee"),
    # a different award in the same show
    ("Game Awards: Best Independent Game", "Witchbrook", "The Game Awards: Best Game Direction",
     "Will Witchbrook win Best Game Direction?", "Witchbrook"),
])
def test_conservative_matcher_refuses_lookalikes(k_title, k_sub, p_title, p_q, p_group):
    k = kev(k_title, [kmk("KXT-26-A", k_title, k_sub), kmk("KXT-26-B", k_title, "Someone Else Entirely")])
    p = pev(p_title, [pmk("1", p_q, p_group), pmk("2", "Will Nobody Atall win?", "Nobody Atall")])
    rows = [r for r in pair(k, p) if r["k"]["m"]["ticker"] == "KXT-26-A" and r["p"]["m"]["id"] == "1"]
    assert rows == []


def test_exact_number_and_plain_number_are_the_same_bet():
    k = kev("Unemployment rate in Oct 2026?", [kmk("KXU-26-4.4", "Unemployment rate in Oct 2026?", "Exactly 4.4%"),
                                               kmk("KXU-26-4.5", "Unemployment rate in Oct 2026?", "Exactly 4.5%")])
    p = pev("October Unemployment Rate", [pmk("1", "Will the October 2026 unemployment rate be 4.4%?", "4.4%"),
                                          pmk("2", "Will the October 2026 unemployment rate be 4.5%?", "4.5%")])
    got = {(r["k"]["m"]["ticker"], r["p"]["m"]["id"]) for r in pair(k, p)}
    assert got == {("KXU-26-4.4", "1"), ("KXU-26-4.5", "2")}


def test_correct_scores_must_be_written_the_same_way():
    k = kev("Columbus Crew vs Toronto FC: Correct Score",
            [kmk("KXS-1", "x", "Columbus Crew wins 3-0"), kmk("KXS-2", "x", "Toronto FC wins 2-1"),
             kmk("KXS-3", "x", "Draw 1-1")])
    p = pev("Columbus Crew vs. Toronto FC - Exact Score",
            [pmk("1", "Exact score?", "Columbus Crew 0 - 3 Toronto FC"),
             pmk("2", "Exact score?", "Columbus Crew 3 - 0 Toronto FC"),
             pmk("3", "Exact score?", "Columbus Crew 1 - 1 Toronto FC")])
    got = {(r["k"]["m"]["ticker"], r["p"]["m"]["id"]) for r in pair(k, p)}
    assert ("KXS-1", "1") not in got          # 3-0 to Columbus is not 0-3
    assert ("KXS-1", "2") in got


def test_team_name_markets_become_one_record_per_team():
    m = pmk("9", "Lehigh vs. Villanova", "", bid=0.30, ask=0.34, outcomes=("Lehigh", "Villanova"))
    recs = cvs.pm_records(pev("Lehigh vs. Villanova", [m]), m, 1)
    assert [r["label"] for r in recs] == ["Lehigh", "Villanova"]
    assert recs[1]["bid"] == pytest.approx(0.66) and recs[1]["ask"] == pytest.approx(0.70)
    assert recs[1]["token"] == "9b"
    k = kev("Lehigh vs Villanova", [kmk("KXG-LEH", "Lehigh vs Villanova", "Lehigh"),
                                    kmk("KXG-VIL", "Lehigh vs Villanova", "Villanova")])
    got = {(r["k"]["m"]["ticker"], r["p"]["label"]) for r in pair(k, pev("Lehigh vs. Villanova", [m]))}
    assert got == {("KXG-LEH", "Lehigh"), ("KXG-VIL", "Villanova")}


# ---------------- rules side by side ----------------

def _rec(k_rules="", p_rules="", k_sources=(), k_full="Who wins X?", p_full="Who wins X?",
         k_exp="2026-10-23T21:00:00Z", p_end="2026-10-23T21:00:00Z"):
    k = cvs.k_record(kev("X", [], sources=k_sources), kmk("KXX-1", k_full, rules=k_rules, exp=k_exp), 1)
    p = cvs.pm_records(pev("X", []), pmk("1", p_full, desc=p_rules, end=p_end), 1)[0]
    return k, p


def test_rules_tie_split_vs_tiebreak_differs():
    k, p = _rec("If multiple participants are tied for the lead, Yes positions will pay $1/N.",
                "In the event of a tie, this market will resolve in favor of the player with the lower score in Round 3.")
    status, why = cvs.rules_check(k, p, ["a"], ["a"])["ties/cancel"]
    assert status == "DIFFERS" and "split payout" in why and "tiebreak" in why


def test_rules_cancellation_50_50_vs_no_differs_and_silence_is_unclear():
    k, p = _rec("If the game is cancelled, the market resolves to No.",
                "If the game is cancelled or postponed, this market will resolve 50-50.")
    assert cvs.rules_check(k, p, ["a"], ["a"])["ties/cancel"][0] == "DIFFERS"
    k, p = _rec("", "If the game is cancelled, this market will resolve to No.")
    assert cvs.rules_check(k, p, ["a"], ["a"])["ties/cancel"][0] == "UNCLEAR"


def test_rules_sources_named_feeds_must_agree():
    k, p = _rec(p_rules="The resolution source for this market is the Binance BTC/USDT 1 minute candle.",
                k_sources=["CF Benchmarks"])
    assert cvs.rules_check(k, p, ["a"], ["a"])["source"][0] == "DIFFERS"
    k, p = _rec(p_rules="The resolution source will be official information from the NHL; a consensus of "
                        "credible reporting may also be used.", k_sources=["NHL", "ESPN"])
    assert cvs.rules_check(k, p, ["a"], ["a"])["source"][0] in ("SAME", "OVERLAP")


def test_rules_time_deadline_differs_but_one_off_event_does_not():
    k, p = _rec(k_full="Will X happen before 2027?", p_full="Will X happen before 2027?",
                k_exp="2027-01-31T00:00:00Z", p_end="2026-12-31T00:00:00Z")
    assert cvs.rules_check(k, p, ["a"], ["a"])["time"][0] == "DIFFERS"
    k, p = _rec(k_full="Art Ross Trophy: Sidney Crosby wins", p_full="Will Sidney Crosby win the Art Ross Trophy?",
                k_exp="2027-06-30T00:00:00Z", p_end="2027-04-20T00:00:00Z")
    assert cvs.rules_check(k, p, ["a"], ["a"])["time"][0] == "LATER"


def test_rules_other_outcome_on_one_side_only_differs():
    k, p = _rec()
    assert cvs.rules_check(k, p, ["Alice", "Bob"], ["Alice", "Bob", "Other"])["other"][0] == "DIFFERS"
    assert cvs.rules_check(k, p, ["Alice", "Bob"], ["Alice", "Bob"])["other"][0] == "SAME"


def test_threshold_inclusive_vs_exclusive_differs():
    k, p = _rec(k_full="Bitcoin $80,000 or above on Oct 22?", p_full="Bitcoin above 80,000 on October 22?")
    assert cvs.rules_check(k, p, ["a"], ["a"])["threshold"] == ("DIFFERS", "Kalshi inclusive vs Polymarket exclusive")


# ---------------- prices, flags, lock ----------------

def test_scan_flag_order_match_then_rules_then_spread():
    fine = {"source": ("SAME", ""), "time": ("LATER", ""), "ties/cancel": ("SILENT", ""),
            "other": ("n/a", ""), "threshold": ("n/a", "")}
    bad = dict(fine, source=("DIFFERS", ""))
    assert cvs.scan_flag(12, 90, fine, 0.02) == "WORTH A LOOK"
    assert cvs.scan_flag(12, 70, fine, 0.02) == "CHECK MATCH"
    assert cvs.scan_flag(12, 90, bad, 0.02) == "RULES DIFFER"
    assert cvs.scan_flag(12, 90, fine, 0.20) == "WIDE PM SPREAD"
    assert cvs.scan_flag(9.9, 90, fine, 0.02) == "-"


def test_lock_points_buy_both_legs():
    # YES on Kalshi at 40c (+2c fee) and NO on Polymarket at 1 - 0.55 = 45c: 1 - 0.42 - 0.45 = 13 points
    assert cvs.lock_pts("YES", 0.40, 0.55, 0.57) == pytest.approx(13.0)
    # NO on Kalshi at 40c (+2c fee) and YES on Polymarket at its 57c ask: 1 - 0.42 - 0.57 = 1 point
    assert cvs.lock_pts("NO", 0.40, 0.55, 0.57) == pytest.approx(1.0)


def test_price_row_skips_a_dead_polymarket_book():
    k = cvs.k_record(kev("X", []), kmk("KXX-1", "X", yes_ask=0.01, no_ask=0.99), 1)
    p = cvs.pm_records(pev("X", []), pmk("1", "X", bid=0.001, ask=0.999), 1)[0]
    r = cvs.price_row({"k": k, "p": p, "conf": 90})
    assert r["best"] is None and r["unpriced"] and r["gap"] is None


def test_price_row_gap_and_depth():
    k = cvs.k_record(kev("X", []), kmk("KXX-1", "X", yes_ask=0.40, no_ask=0.61), 1)
    p = cvs.pm_records(pev("X", []), pmk("1", "X", bid=0.55, ask=0.57), 1)[0]
    r = cvs.price_row({"k": k, "p": p, "conf": 90})
    # YES: 56 - 40 - 2 = 14 points; Kalshi has 100 contracts at 40c = $40
    assert r["best"][0] == "YES" and r["gap"] == pytest.approx(14.0) and r["k_depth"] == pytest.approx(40.0)
