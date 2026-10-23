"""News scoring: the shift maths, and the Claude request with a fake client (no API key needed)."""
import json
import sys
import types

import pytest

from argus import news


def test_shift_signs_and_sizes():
    assert news.shift_for({"direction": "supply_down", "size": "large", "persistence": 1}) == pytest.approx(0.04)
    assert news.shift_for({"direction": "supply_up", "size": "medium", "persistence": 0.5}) == pytest.approx(-0.0075)
    assert news.shift_for({"direction": "none", "size": "large", "persistence": 1}) == 0


def test_total_shift_is_capped():
    big = [{"direction": "supply_down", "size": "large", "persistence": 1}] * 5   # 20% raw
    assert news.total_shift(big) == pytest.approx(news.MAX_TOTAL_SHIFT)


def test_score_headlines_with_fake_client(monkeypatch):
    sent = {}
    reply = {"scores": [{"headline": "Saudi cuts output", "direction": "supply_down",
                         "size": "medium", "persistence": 0.8, "why": "real cut"}]}

    class FakeMessages:
        def create(self, **kw):
            sent.update(kw)
            block = types.SimpleNamespace(type="text", text=json.dumps(reply))
            return types.SimpleNamespace(stop_reason="end_turn", content=[block])

    class FakeClient:
        def __init__(self):
            self.beta = types.SimpleNamespace(messages=FakeMessages())

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=FakeClient))
    scores = news.score_headlines(["Saudi cuts output"], "WTI", "Fri 2:30 PM ET")
    assert scores == reply["scores"]
    assert sent["model"] == "claude-opus-5-5"
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert "Saudi cuts output" in sent["messages"][0]["content"]
    assert news.total_shift(scores) == pytest.approx(0.012)
