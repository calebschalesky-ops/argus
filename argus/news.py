"""News scoring with the Claude API.

Each headline is scored on:
  direction   - supply_up, supply_down, demand_up, demand_down, or none
  size        - none, small, medium, large
  persistence - 0 to 1: how likely the effect still matters when the market closes
Then: shift = sign x size% x persistence. Less supply or more demand pushes price UP.

The API key is read from the ANTHROPIC_API_KEY environment variable by the SDK.
Argus never stores it.
"""
import csv
import json
from pathlib import Path

SIZE_PCT = {"none": 0.0, "small": 0.005, "medium": 0.015, "large": 0.04}
SIGN = {"supply_down": +1, "demand_up": +1, "supply_up": -1, "demand_down": -1, "none": 0}
MAX_TOTAL_SHIFT = 0.08
MODEL = "claude-opus-5-5"

SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "headline": {"type": "string"},
                    "direction": {"type": "string", "enum": list(SIGN)},
                    "size": {"type": "string", "enum": list(SIZE_PCT)},
                    "persistence": {"type": "number"},
                    "why": {"type": "string"},
                },
                "required": ["headline", "direction", "size", "persistence", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["scores"],
    "additionalProperties": False,
}

SYSTEM = """You are an oil-market analyst scoring headlines for their effect on the
{commodity} crude price between now and {close}. For each headline decide:
- direction: does it change physical SUPPLY (up/down) or DEMAND (up/down), or none.
- size: none (<0.2% move), small (~0.5%), medium (~1.5%), large (~4%+).
- persistence: probability 0-1 the effect is still in the price at {close}.
Be sceptical: most headlines are noise or already priced in. Rumours, opinion pieces and
repeats of old news are size none or small. Score each headline on its own.
Return one score per headline, in the same order, copying the headline text exactly."""


def shift_for(score):
    """The price shift (fraction) one scored headline causes."""
    p = min(max(float(score["persistence"]), 0.0), 1.0)
    return SIGN[score["direction"]] * SIZE_PCT[score["size"]] * p


def total_shift(scores):
    s = sum(shift_for(x) for x in scores)
    return max(-MAX_TOTAL_SHIFT, min(MAX_TOTAL_SHIFT, s))


def score_headlines(headlines, commodity="WTI", close="the market close"):
    """Ask Claude to score a list of headlines. Returns a list of score dicts."""
    if not headlines:
        return []
    import anthropic                     # only needed when news is used

    client = anthropic.Anthropic()       # reads ANTHROPIC_API_KEY from the environment
    numbered = "\n".join(f"{i + 1}. {h}" for i, h in enumerate(headlines))
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",             # if a request is declined, Anthropic re-runs it on a fallback model
        output_config={"effort": "medium",
                       "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEM.format(commodity=commodity, close=close),
        messages=[{"role": "user", "content": f"Headlines:\n{numbered}"}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to score these headlines.")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)["scores"]


def read_headlines_file(path):
    """One headline per line; blank lines and lines starting with # are skipped."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]


def save_scores(scores, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["headline", "direction", "size", "persistence", "shift_pct", "why"])
        w.writeheader()
        for s in scores:
            w.writerow({**s, "shift_pct": round(shift_for(s) * 100, 2)})
