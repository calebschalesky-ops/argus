"""`python -m argus crossvenue-scan ...` forwards its options to crossvenue.main(["scan", ...])."""
from argus import cli, crossvenue


def test_crossvenue_scan_forwards_options(monkeypatch):
    seen = []
    monkeypatch.setattr(crossvenue, "main", lambda argv=None: seen.append(argv))
    cli.main(["crossvenue-scan", "--top", "5", "--no-log"])
    assert seen == [["scan", "--top", "5", "--no-log"]]
