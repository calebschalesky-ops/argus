"""Tests for fees, the prediction log, settling and the scorecard."""
import pytest

from argus import fees, log


def test_taker_fee_matches_kalshi_example():
    # Kalshi's published example: 100 contracts at 55c costs $1.74
    assert fees.taker_fee(0.55, 100) == pytest.approx(1.74)


def test_taker_fee_rounds_up_to_a_cent():
    assert fees.taker_fee(0.19) == pytest.approx(0.02)   # 0.0108 -> 0.02
    assert fees.taker_fee(0.50) == pytest.approx(0.02)   # 0.0175 -> 0.02
    assert fees.taker_fee(0.99) == pytest.approx(0.01)


def test_pnl_after_fees():
    assert fees.pnl(True, 0.19) == pytest.approx(0.81 - 0.02)
    assert fees.pnl(False, 0.19) == pytest.approx(-0.19 - 0.02)


def test_expected_value():
    assert fees.expected_value(0.30, 0.19) == pytest.approx(0.30 - 0.19 - 0.02)


def row(side, prob, price, kind="between", low=98.0, high=98.99, bet=1, event="E1", ticker="M1"):
    return {"event_ticker": event, "market_ticker": ticker, "side": side,
            "model_prob": str(prob), "market_price": str(price), "paper_contracts": str(bet),
            "worth_a_look": str(bet), "notes": f"shape={kind}|{low}|{high}; flag=-"}


def test_append_settle_and_scorecard(tmp_path):
    path = tmp_path / "log.csv"
    log.append([
        row("YES", 0.30, 0.19, ticker="A"),                          # bet: 98.00-98.99 YES
        row("NO", 0.85, 0.80, low=99.0, high=99.99, ticker="B"),    # bet: 99.00-99.99 NO
        row("NO", 0.95, 0.97, low=105.0, high=105.99, bet=0, ticker="C"),  # logged, not bet
        row("YES", 0.60, 0.50, event="E2", ticker="D"),              # different event, stays pending
    ], path)
    rows = log.read_log(path)
    assert len(rows) == 4 and all(r["status"] == "pending" for r in rows)

    n = log.settle_event("E1", value=98.40, path=path)
    assert n == 3
    rows = {r["market_ticker"]: r for r in log.read_log(path)}
    assert rows["A"]["won"] == "1"          # 98.40 is inside 98.00-98.99
    assert rows["B"]["won"] == "1"          # NO on 99.00-99.99 wins
    assert rows["C"]["won"] == "1"
    assert rows["D"]["status"] == "pending"
    assert float(rows["A"]["pnl_after_fees"]) == pytest.approx(0.79)
    assert float(rows["B"]["pnl_after_fees"]) == pytest.approx(0.20 - 0.02)
    assert float(rows["C"]["pnl_after_fees"]) == 0.0   # not a paper bet

    card = log.scorecard(log.read_log(path))
    assert card["total_calls"] == 4
    assert card["settled_calls"] == 3
    assert card["paper_bets"] == 2
    assert card["win_rate"] == 1.0
    assert card["pnl_after_fees"] == pytest.approx(0.79 + 0.18)
    assert card["staked"] == pytest.approx(0.99)


def test_settle_never_resettles(tmp_path):
    path = tmp_path / "log.csv"
    log.append([row("YES", 0.3, 0.19)], path)
    assert log.settle_event("E1", value=98.5, path=path) == 1
    assert log.settle_event("E1", value=50.0, path=path) == 0
    assert log.read_log(path)[0]["won"] == "1"


def test_settle_from_kalshi_results(tmp_path):
    path = tmp_path / "log.csv"
    log.append([row("NO", 0.8, 0.75, ticker="X")], path)
    log.settle_event("E1", results={"X": True}, path=path)   # YES won, so our NO lost
    r = log.read_log(path)[0]
    assert r["won"] == "0"
    assert float(r["pnl_after_fees"]) == pytest.approx(-0.75 - 0.02)


def test_calibration_and_brier():
    rows = []
    for i in range(10):    # ten 70% calls, seven win
        rows.append({"status": "settled", "model_prob": "0.70", "market_price": "0.60",
                     "won": "1" if i < 7 else "0", "paper_contracts": "0", "pnl_after_fees": "0"})
    card = log.scorecard(rows)
    b = card["calibration"][0]
    assert b["bin"] == "70-80%" and b["n"] == 10
    assert b["actual_win_rate"] == pytest.approx(0.7)
    assert card["brier_model"] == pytest.approx(0.7 * 0.09 + 0.3 * 0.49)
    assert card["brier_market"] == pytest.approx(0.7 * 0.16 + 0.3 * 0.36)
    assert card["brier_model"] < card["brier_market"]


def test_kalshi_client_refuses_non_market_paths():
    from argus import kalshi
    with pytest.raises(PermissionError):
        kalshi._get("/portfolio/orders")
