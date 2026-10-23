"""The strategy board groups every *_log.csv by engine and applies the same gate."""
from argus import board, log


def rows(engine, n_events, won, prob, price, bet):
    return [{"event_ticker": f"E{i}", "market_ticker": f"M{i}", "engine": engine, "side": "YES",
             "model_prob": str(prob), "market_price": str(price), "paper_contracts": str(bet),
             "worth_a_look": str(bet), "status": "settled", "won": "1" if won(i) else "0",
             "pnl_after_fees": str((1 - price - 0.02) if won(i) else (-price - 0.02)) if bet else "0"}
            for i in range(n_events)]


def test_board_ranks_and_gates(tmp_path):
    log.write_log(rows("good", 25, lambda i: i % 10 < 8, 0.8, 0.6, 1), tmp_path / "a_log.csv")
    log.write_log(rows("early", 5, lambda i: True, 0.8, 0.6, 1), tmp_path / "b_log.csv")
    log.write_log(rows("bad", 25, lambda i: i % 10 < 5, 0.8, 0.6, 1), tmp_path / "c_log.csv")
    (tmp_path / "notes_log.csv").write_text("x,y\n1,2\n")          # not a prediction log: ignored
    b = board.board(tmp_path)
    by = {x["engine"]: x for x in b}
    assert set(by) == {"good", "early", "bad"}
    assert by["good"]["verdict"].startswith("PASS")
    assert by["early"]["verdict"].startswith("TOO EARLY")
    assert by["bad"]["verdict"] == "NO EDGE"
    assert b[0]["engine"] == "good"
    assert "ARGUS STRATEGY BOARD" in board.format_board(b)
