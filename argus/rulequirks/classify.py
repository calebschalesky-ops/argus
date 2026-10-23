"""Classify the quirks in one Kalshi market's rules: where the rules differ from a casual reading.

A casual reader looks at the title ("Will CPI rise above 0.3%?") and assumes the obvious:
the number itself counts, the obvious source decides, a tie or a delay is someone else's
problem. The rules (rules_primary + rules_secondary) often say otherwise. Each quirk type
below is a plain rule over the rules text and Kalshi's strike fields, so it can be argued with.

    from argus.rulequirks.classify import classify
    for q in classify(market):
        q.kind, q.detail          # "strict_threshold", "above 0.3 (0.3 itself is No)"

Pure text work: nothing here talks to Kalshi.
"""
import re
from dataclasses import dataclass

# kind -> (short label, what the casual reader assumes, what the rules say)
CATALOGUE = {
    "strict_threshold": ("Strict threshold", "landing exactly on the number counts",
                         "'above/more than X' needs strictly more than X; X itself resolves No"),
    "inclusive_threshold": ("Inclusive threshold", "'X' is a cut-off it must beat",
                            "'at least X' / 'X or more': X itself resolves Yes"),
    "title_rules_mismatch": ("Title vs rules mismatch", "the title says what pays",
                             "the title's wording or number differs from the rules"),
    "rounding": ("Rounding / precision", "the raw number decides",
                 "the value is rounded or read at a stated precision first"),
    "averaged_value": ("Averaged / windowed value", "the price at the moment decides",
                       "an average, a 1-minute candle close or a trimmed mean decides"),
    "named_station": ("Specific station / report", "'the temperature in the city'",
                      "one named station or report (e.g. NWS CLI for one airport) decides"),
    "contract_month": ("Specific contract / index", "'the oil price'",
                       "a specific futures contract month or a named index decides"),
    "clock_time": ("Specific clock time / zone", "the calendar day in my time zone",
                   "a stated clock time and zone (often ET, sometimes UTC) decides"),
    "first_release": ("First release only", "the true (revised) number",
                      "the first published value counts; later revisions are ignored"),
    "tie_rule": ("Tie rule", "someone wins",
                 "a tie has its own payout (both No, 50/50, proportional, or a tie market)"),
    "postponement_rule": ("Postponement / delay rule", "a delayed event still settles on the result",
                          "a delay or reschedule changes the deadline or the payout"),
    "cancellation_rule": ("Cancellation rule", "a cancelled event is void and refunded",
                          "a cancelled event resolves No, 50/50 or to a stated market"),
    "no_data_rule": ("No-data rule", "it settles when the data comes",
                     "missing or late data falls back to an earlier value or a fair price"),
    "fair_value_fallback": ("Fair-value settlement", "every contract pays $1 or $0",
                            "some cases settle at a fair/last price between 0 and $1"),
    "regulation_only": ("Regulation time only", "the final result counts",
                        "only regulation (e.g. 90 minutes plus stoppage) counts; extra time excluded"),
    "counting_exclusions": ("Counting exclusions", "any mention / any instance counts",
                            "only exact forms or qualifying instances count (plural/possessive rules, replays excluded)"),
    "path_dependent": ("Touch / any-time condition", "it is judged at the end",
                       "touching the level at any point resolves it, even if it falls back"),
    "early_close": ("Can close early", "trading runs to the stated close",
                    "the market can close (and stop trading) as soon as the outcome is known"),
    "none_outcome": ("'None' / 'does not qualify' outcome", "one of the listed outcomes wins",
                     "a 'none' / 'other' / 'does not qualify' outcome takes the payout"),
}

STRICT_WORDS = r"(?:strictly )?(?:above|more than|greater than|higher than|exceeds?|over|below|less than|lower than|fewer than|under)"
INCLUSIVE_WORDS = r"(?:at least|or more|or above|or higher|or greater|or over|greater than or equal to|no less than|at or above|or below|or less|or lower|or fewer|at most|less than or equal to|no more than|at or below)"

_P = lambda p: re.compile(p, re.I)
RX = {
    "rounding": _P(r"\bround(?:ed|ing)?\b|single[- ]decimal|\b(?:one|two|three|four|1|2|3|4) decimal|decimal places?|nearest (?:tenth|hundredth|whole|cent|integer|\d)|truncat"),
    "averaged_value": _P(r"\baverage\b|\bmean of\b|1-minute candle|one-minute|60[- ]second|sixty seconds|trimmed|\bvwap\b|candlestick"),
    "named_station": _P(r"climatological report|\bCLI[A-Z]{3}\b|weather station|\bstation\b|\bairport\b|central park|the weather company"),
    "contract_month": _P(r"front[- ]month|contract month|nearest (?:listed|expiring)|(?:january|february|march|april|may|june|july|august|september|october|november|december) 20\d\d (?:futures? )?contract|futures contract|real[- ]time index|\brti\b|pyth|cf benchmarks|truflation"),
    "clock_time": _P(r"\bUTC\b|\bGMT\b|\d{1,2}:\d{2} ?(?:am|pm) ?(?:e[sd]?t|c[sd]?t|p[sd]?t|m[sd]?t)\b|11:59"),
    "first_release": _P(r"revision|revised|initial (?:release|estimate|print|publication|report)|first (?:release|print|publication|value reported|reported)|advance estimate|subsequent (?:changes|corrections)"),
    "tie_rule": _P(r"\bties?\b|\btied\b|dead heat|\bdraws?\b(?! up)"),
    "postponement_rule": _P(r"postpone|delay(?:ed|s)?\b|reschedul|suspended|abandon"),
    "cancellation_rule": _P(r"cancel+(?:ed|ation|s)?\b|\bvoid(?:ed)?\b|(?:does|did) not (?:take place|occur)|not played"),
    "no_data_rule": _P(r"no data|not (?:be )?(?:released|published|available|reported)|fails? to (?:publish|release|report)|shutdown|unavailable|most recently available"),
    "fair_value_fallback": _P(r"fair (?:market )?(?:value|price)|last (?:traded|fair) price|resolve(?:s)? proportionally|50/50|50-50|\$0?\.50 |settle(?:s)? (?:to|at) (?:the )?(?:fair|last|a price)"),
    "regulation_only": _P(r"regulation(?: time)?|90 minutes|does not include (?:extra time|overtime|penalt)|excluding (?:overtime|extra time)|not including (?:overtime|extra time)|before (?:extra time|overtime|penalt)"),
    "counting_exclusions": _P(r"plural|possessive|inflection|does(?:n't| not) count|will not count|not (?:be )?counted|do not qualify|does not qualify for payout|exact (?:phrase|word)|previously aired|replays?\b"),
    "path_dependent": _P(r"at any (?:point|time)(?! (?:prior to|before) the)|\bever (?:reach|trade|hit)|\btouch(?:es)?\b|intraday high|intraday low"),
    "early_close": _P(r"clos(?:e|es|ing) early|early (?:close|expiration)|immediately resolve|resolves immediately|close (?:the market )?(?:as soon as|once)"),
    "none_outcome": _P(r"does not qualify\" resolves|\"(?:none|other|no one|nobody)[^\"]{0,30}\" (?:market )?(?:will )?resolves? to yes|none of the (?:above|listed)|no (?:listed )?(?:candidate|team|player|one) (?:wins|qualifies)"),
}
# "draw" is a real tie in sports but also a verb (draw down, draw a card); "tie" also means "tie-break" etc.
# We keep both: a false "tie_rule" flag costs little, the scan prints the matching phrase.


@dataclass(frozen=True)
class Quirk:
    kind: str
    detail: str


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def rules_text(m):
    return " ".join(str(m.get(k) or "") for k in ("rules_primary", "rules_secondary"))


def title_text(m):
    return " ".join(str(m.get(k) or "") for k in ("title", "yes_sub_title", "subtitle"))


def _fmt(x):
    return f"{x:g}"


NUM = r"\$?(-?\d[\d,]*(?:\.\d+)?)"
HALF_POINT_COUNTS = re.compile(r"\b(?:games|goals|points|runs|sets|wins|votes|seats|strikeouts|hits|yards|touchdowns|times|cases|posts)\b", re.I)
_RULE_INCL = [re.compile(r"\b" + INCLUSIVE_WORDS + r" " + NUM, re.I),
              re.compile(r"\b(?:within|between) " + NUM + r"%? (?:to|and) [\d.,]+%?,? inclusive of (?:its|the) lower bound", re.I),
              re.compile(NUM + r"%?\+", re.I)]
_RULE_STRICT = re.compile(r"(?<!no )(?<!or )(?<!at )\b" + STRICT_WORDS + r" " + NUM, re.I)


def _f(x):
    return float(x.replace(",", ""))


def same(a, b):
    """Equal to 15 significant digits (0.0000055 vs 0.000005499 and 1e10 vs 9999999999.99 differ)."""
    return f"{a:.15g}" == f"{b:.15g}"


def _field_side(m):
    st = m.get("strike_type")
    return {"greater": ("strict", "floor_strike"), "less": ("strict", "cap_strike"),
            "greater_or_equal": ("inclusive", "floor_strike"),
            "less_or_equal": ("inclusive", "cap_strike")}.get(st, (None, None))


def threshold_side(m):
    """('strict'|'inclusive', X) for a one-sided threshold market, else (None, None).

    The rules sentence decides, because it is what settles the market: "at least 40 goals" is
    inclusive at 40 even when Kalshi's strike field says "greater than 39.5" (same contract), and
    "above 7894.9999" is strict at 7894.9999. When the rules state several numbers, the one nearest
    the strike field is used. Without a threshold phrase in the rules, the strike field decides."""
    if m.get("strike_type") in ("between", "structured"):
        return None, None
    fside, fkey = _field_side(m)
    fx = _num(m.get(fkey)) if fkey else None
    r = m.get("rules_primary") or ""
    cands = []
    for rx in _RULE_INCL:
        cands += [("inclusive", _f(mt.group(1)), mt.start()) for mt in rx.finditer(r)]
    for mt in _RULE_STRICT.finditer(r):
        if not re.search(r"^\S* ?or (?:equal|more|above|higher|greater|below|less|lower|fewer)\b", r[mt.end():mt.end() + 25], re.I):
            cands.append(("strict", _f(mt.group(1)), mt.start()))
    if cands:
        if fx is not None:
            near = [c for c in cands if abs(c[1] - fx) <= 0.02 * max(abs(fx), 1e-12) + 1.0]
            if near:
                side, x, _ = min(near, key=lambda c: (abs(c[1] - fx), c[2]))
                return side, x
            return fside, fx
        if m.get("strike_type") in (None, "", "custom"):
            side, x, _ = min(cands, key=lambda c: c[2])
            return side, x
    return (fside, fx) if fside else (None, None)


def boundary_matters(m, x):
    """False when the value cannot land on x: a count (goals, games, votes...) against a half-point line."""
    return not (abs(abs(x) % 1 - 0.5) < 1e-9 and HALF_POINT_COUNTS.search(rules_text(m) + " " + title_text(m)))


def _title_numbers(title, rx):
    for mt in rx.finditer(title):
        before = title[max(0, mt.start() - 12):mt.start()].lower()
        if "category" in before:                 # "category 3 or above" is a hurricane class, not the line
            continue
        yield _f(mt.group(1))


_T_INCL = [re.compile(r"\b(?:at least|no less than|at or above|at or below|no more than|at most|minimum of) " + NUM, re.I),
           re.compile(NUM + r"[^\d\s]{0,6} ?(?:\+|or (?:more|above|higher|greater|over|below|less|lower|fewer))", re.I),
           re.compile("[\u2265\u2264] ?" + NUM)]
_T_STRICT = [re.compile(r"(?<!no )(?<!or )(?<!at )\b(?:above|more than|greater than|higher than|exceeds?|over|below|less than|under|fewer than) " + NUM + r"(?![\d,.]*\s?(?:or|\+))", re.I),
             re.compile(r"[><] ?" + NUM)]


def _title_says(title, x, rxs):
    return any(same(y, x) for rx in rxs for y in _title_numbers(title, rx))


def threshold_mismatch(m):
    """Detail string if the title and the rules disagree about whether the threshold itself counts."""
    side, x = threshold_side(m)
    if side is None or x is None or not boundary_matters(m, x):
        return None
    title = title_text(m)
    if side == "strict" and _title_says(title, x, _T_INCL):
        return f"title counts {_fmt(x)} itself, rules need strictly past {_fmt(x)}"
    if side == "inclusive" and _title_says(title, x, _T_STRICT):
        return f"title says past {_fmt(x)}, rules count {_fmt(x)} itself"
    return None


def classify(m):
    """Every quirk in one market's rules, as a list of Quirk(kind, detail)."""
    text, out = rules_text(m), []
    side, x = threshold_side(m)
    if x is not None and not boundary_matters(m, x):
        side = None
    if side == "strict" and x is not None:
        out.append(Quirk("strict_threshold", f"needs strictly past {_fmt(x)}; exactly {_fmt(x)} resolves No"))
    elif side == "inclusive" and x is not None:
        out.append(Quirk("inclusive_threshold", f"exactly {_fmt(x)} resolves Yes"))
    mm = threshold_mismatch(m)
    if mm:
        out.append(Quirk("title_rules_mismatch", mm))
    for kind, rx in RX.items():
        hit = rx.search(text)
        if hit:
            a, b = max(0, hit.start() - 50), min(len(text), hit.end() + 50)
            out.append(Quirk(kind, " ".join(text[a:b].split())))
    return out


def kinds(m):
    return sorted({q.kind for q in classify(m)})
