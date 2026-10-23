"""Tests for the read-only Polymarket client and the Kalshi-vs-Polymarket check."""
import pytest

from argus import crossvenue as cv
from argus import polymarket


# ---------------- read-only guard ----------------

@pytest.mark.parametrize("host, path", [
    (polymarket.CLOB, "/order"),
    (polymarket.CLOB, "/orders"),
    (polymarket.CLOB, "/auth/api-key"),
    (polymarket.CLOB, "/trades"),
    (polymarket.CLOB, "/books"),             # batch endpoint is a POST: not allowed either
    (polymarket.GAMMA, "/book"),             # right path, wrong host
    ("https://data-api.polymarket.com", "/positions"),
])
def test_polymarket_refuses_non_allowlisted_paths(host, path):
    with pytest.raises(PermissionError):
        polymarket._get(host, path)


def test_polymarket_token_ids_parse_gamma_strings():
    m = {"clobTokenIds": '["111", "222"]', "outcomes": '["Yes", "No"]'}
    assert polymarket.token_ids(m) == ("111", "222")
    assert polymarket.token_ids({"clobTokenIds": '["1","2","3"]', "outcomes": '["A","B","C"]'}) == (None, None)


# ---------------- race keys ----------------

def test_kalshi_race_key_reads_state_from_rules_not_ticker():
    # SENATELA-26 is the Kentucky race on Kalshi (measured 2026-10-22)
    rules = "If a representative of the Republican party is sworn in as a Senator of Kentucky for the term beginning in 2027, then the market resolves to Yes."
    assert cv.kalshi_race_key("SENATELA", rules) == ("senate", "Kentucky")


def test_kalshi_race_key_specials_governors_control_and_other_years():
    special = "If a representative of the Republican party is sworn in as a Senator of Ohio pursuant to the Class III (special) election in 2026"
    assert cv.kalshi_race_key("SENATEOHS", special) == ("senate", "Ohio")
    gov = "If a representative of the Democratic party is inaugurated as the governor of New Mexico pursuant to the 2026 election, then"
    assert cv.kalshi_race_key("GOVPARTYNM", gov) == ("governor", "New Mexico")
    person = "If Xavier Becerra is elected to the governorship of California pursuant to the election in 2026, then"
    assert cv.kalshi_race_key("KXGOVCA", person) == ("governor", "California")
    assert cv.kalshi_race_key("CONTROLS", "If the Democratic Party has won control of the U.S. Senate in 2026") == ("control-senate", "US")
    assert cv.kalshi_race_key("CONTROLS", "If the Democratic Party has won control of the U.S. Senate in 2028") is None
    assert cv.kalshi_race_key("SENATEWI", "sworn in as a Senator of Wisconsin for the term beginning in 2029") is None


def test_pm_race_key_takes_only_plain_winner_events():
    assert cv.pm_race_key("maine-senate-election-winner") == ("senate", "Maine")
    assert cv.pm_race_key("alabama-senate-election-winner-154") == ("senate", "Alabama")
    assert cv.pm_race_key("new-hampshire-governor-winner-2026") == ("governor", "New Hampshire")
    assert cv.pm_race_key("california-governor-election-2026") == ("governor", "California")
    assert cv.pm_race_key("which-party-will-win-the-house-in-2026") == ("control-house", "US")
    assert cv.pm_race_key("texas-senate-margin-of-victory-2026") is None
    assert cv.pm_race_key("pennsylvania-state-senate-winner-2026") is None
    assert cv.pm_race_key("which-party-will-win-the-minnesota-state-senate-in-2026") is None


# ---------------- outcome matching ----------------

def K(ticker, label):
    return cv.kalshi_outcome({"ticker": ticker, "yes_sub_title": label})


def P(title, question):
    return cv.pm_outcome({"groupItemTitle": title, "question": question})


def test_party_outcomes_match_by_party():
    ks = [K("SENATEME-26-R", "Susan Collins"), K("SENATEME-26-D", "Troy Jackson")]
    ps = [P("Troy Jackson (D)", "Will the Democrats win the Maine Senate race in 2026?"),
          P("Susan Collins (R)", "Will the Republicans win the Maine Senate race in 2026?")]
    pairs, lk, lp = cv.match_outcomes(ks, ps)
    assert [(k["label"], p["label"], w) for k, p, w in pairs] == [
        ("Troy Jackson", "Troy Jackson (D)", []), ("Susan Collins", "Susan Collins (R)", [])]
    assert lk == [] and lp == []


def test_control_outcomes_match():
    ks = [K("CONTROLS-2026-R", "Republican Party"), K("CONTROLS-2026-D", "Democratic Party")]
    ps = [P("Democratic Party", "Will the Democratic Party control the Senate after the 2026 Midterm elections?"),
          P("Republican Party", "Will the Republican Party control the Senate after the 2026 Midterm elections?")]
    pairs, lk, lp = cv.match_outcomes(ks, ps)
    assert {(k["party"], p["party"]) for k, p, _ in pairs} == {("D", "D"), ("R", "R")}
    assert all(w == [] for _, _, w in pairs)


def test_different_names_same_party_is_paired_with_a_warning():
    ks = [K("SENATENE-26-D", "Cindy Burbank")]
    ps = [P("Democrat", "Will the Democrats win the Nebraska Senate race in 2026?")]
    pairs, _, _ = cv.match_outcomes(ks, ps)
    assert len(pairs) == 1 and pairs[0][2] == []      # Polymarket shows no name: nothing to compare
    ps = [P("Julian Smith (D)", "Will the Democrats win the X Senate race in 2026?")]
    pairs, _, _ = cv.match_outcomes([K("SENATESD-26-D", "Julian Beaudion")], ps)
    assert "names differ" in pairs[0][2][0]


def test_generic_independent_pairs_only_with_a_single_kalshi_independent():
    ks = [K("SENATENE-26-R", "Pete Ricketts"), K("SENATENE-26-DOSB", "Dan Osborn")]
    ps = [P("Independent", "Will an independent win the Nebraska Senate race in 2026?")]
    pairs, lk, _ = cv.match_outcomes(ks, ps)
    assert pairs[0][0]["label"] == "Dan Osborn"
    assert "ANY independent" in pairs[0][2][0]
    ks.append(K("SENATENE-26-XYZ", "Someone Else"))
    pairs, _, lp = cv.match_outcomes(ks, ps)
    assert pairs == [] and len(lp) == 1               # ambiguous: refuse to guess


def test_named_independent_matches_by_last_name():
    ks = [K("SENATEID-26-R", "Jim Risch"), K("SENATEID-26-TACH", "Todd Achilles")]
    ps = [P("Todd Achilles (I)", "Will Todd Achilles win the Idaho Senate race in 2026?")]
    pairs, _, _ = cv.match_outcomes(ks, ps)
    assert pairs[0][0]["label"] == "Todd Achilles" and pairs[0][2] == []


def test_person_vs_party_contracts_do_not_match_without_a_name():
    # Alaska: Kalshi lists parties, Polymarket lists people. Nothing should pair.
    ks = [K("SENATEAK-26-R", "Republican party"), K("SENATEAK-26-D", "Democratic party")]
    ps = [P("Sen. Dan Sullivan", "Will Dan Sullivan win the Alaska Senate race?"),
          P("Mary Peltola", "Will Mary Peltola win the Alaska Senate race?")]
    pairs, lk, lp = cv.match_outcomes(ks, ps)
    assert pairs == [] and len(lk) == 2 and len(lp) == 2


def test_person_market_matched_to_party_market_is_warned():
    ks = [K("GOVPARTYCA-26-D", "Xavier Becerra")]
    ps = [P("Xavier Becerra", "Will Xavier Becerra win the California Governor Election in 2026?")]
    pairs, _, _ = cv.match_outcomes(ks, ps)
    assert "person" in pairs[0][2][0]


def test_each_kalshi_outcome_used_once():
    ks = [K("SENATEXX-26-D", "A Person")]
    ps = [P("Democrat", "Will the Democrats win?"), P("Democratic Party", "Will the Democratic Party win?")]
    pairs, _, lp = cv.match_outcomes(ks, ps)
    assert len(pairs) == 1 and len(lp) == 1


# ---------------- gap maths ----------------

def test_side_gap_is_net_of_kalshi_fee():
    # PM 70%, Kalshi Yes ask 55c: fee = ceil(0.07*0.55*0.45 c) = 2c -> 70 - 55 - 2 = 13 pts
    assert cv.side_gap(0.70, 0.55) == pytest.approx(13.0)
    assert cv.side_gap(0.70, None) is None
    assert cv.side_gap(0.70, 1.0) is None


def test_best_side_picks_the_bigger_gap_and_uses_the_no_complement():
    side, prob, ask, gap = cv.best_side(0.685, yes_ask=0.68, no_ask=0.33)
    # YES: 68.5 - 68 - 2 = -1.5;  NO: 31.5 - 33 - 2 = -3.5
    assert side == "YES" and gap == pytest.approx(-1.5)
    side, prob, ask, gap = cv.best_side(0.20, yes_ask=0.40, no_ask=0.62)
    # NO: 80 - 62 - 2 = 16
    assert side == "NO" and prob == pytest.approx(0.80) and gap == pytest.approx(16.0)
    assert cv.best_side(0.5, None, None) is None


def test_flag_needs_ten_points_and_a_tight_polymarket_book():
    assert cv.flag_for(10.0, 0.01) == "WORTH A LOOK"
    assert cv.flag_for(9.9, 0.01) == "-"
    assert cv.flag_for(15.0, 0.30) == "WIDE PM SPREAD"
    assert cv.flag_for(None, 0.01) == "-"


def test_log_rows_use_spec_columns():
    from datetime import datetime, timezone
    from argus import log
    k = dict(K("SENATEME-26-D", "Troy Jackson"),
             market={"_series": "SENATEME", "event_ticker": "SENATEME-26", "ticker": "SENATEME-26-D",
                     "close_time": "2027-11-03T15:00:00Z"})
    p = dict(P("Troy Jackson (D)", "Will the Democrats win the Maine Senate race in 2026?"), market={"id": "630772"})
    bk = {"bid": 0.66, "ask": 0.67, "mid": 0.665, "spread": 0.01}
    res = {"at": datetime(2026, 10, 22, tzinfo=timezone.utc), "rows": [{
        "key": ("senate", "Maine"), "k": k, "p": p, "warn": [], "book": bk,
        "event": {"slug": "maine-senate-election-winner"}, "yes_ask": 0.54, "no_ask": 0.47,
        "best": cv.best_side(0.665, 0.54, 0.47), "flag": "WORTH A LOOK"}]}
    rows = cv.log_rows(res)
    assert set(rows[0]) <= set(log.COLUMNS)
    r = rows[0]
    assert r["engine"] == "crossvenue" and r["side"] == "YES" and r["model_prob"] == "0.6650"
    assert r["market_price"] == "0.54" and r["gap_pts"] == "10.5" and r["paper_contracts"] == "1"
    assert "pm_market=630772" in r["notes"] and "SWORN IN" in r["notes"]
