"""Word matching by Kalshi's Mentions rules.

The rules (read on every Mentions series 2026-10-22, MENTION / EARNINGSMENTION / TRUMPMENTION
contract terms, `rules_secondary`):
  - "The exact phrase/word, or a plural or possessive form of the phrase/word, must be used."
  - "Grammatical/tense inflections are otherwise not included."  (say != said, tariffs ok, tariffed no)
  - "For phrases with slashes like 'Doge/Dogecoin,' either word satisfies the criterion."
  - Plural nouns inside proper nouns and noun phrases count.
So a strike matches when any slash alternative appears as whole words, in order, with only the
LAST word allowed a plural (-s, -es, y->ies) or possessive ('s, s') ending. Case is ignored.
A word inside a longer word does not count ("tax" is not in "taxation", "AI" is not in "said").
Hyphens and dashes split words ("AI-driven" contains "AI"). Irregular plurals are not handled.
Count strikes ("Tariff 5+ times", "Cut 3+") need that many matches.
"""
import re

_APOS = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "`": "'"})
_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]*)?")
NOT_A_WORD = ("event does not qualify",)
_TIMES = re.compile(r"^(.*?)\s*\(?\s*(\d+)\s*\+\s*(?:times?)?\s*\)?\s*$", re.I)


def threshold(strike):
    """('Tariff', 5) for 'Tariff 5+ times'; (strike, 1) otherwise."""
    m = _TIMES.match((strike or "").strip())
    if m and m.group(1):
        return m.group(1), int(m.group(2))
    return (strike or ""), 1


def tokens(text):
    """Lowercase words of a text; apostrophes kept inside a word ("warsh's", "banks'")."""
    t = (text or "").translate(_APOS).lower()
    t = re.sub(r"(?<=\b[a-z])\.(?=[a-z]\.)|(?<=\b[a-z]\.[a-z])\.", "", t)   # A.I. -> ai
    return _TOKEN.findall(t)


def alternatives(strike):
    """'Slowdown / Slow Down' -> [['slowdown'], ['slow', 'down']]. Empty for non-word strikes."""
    s = threshold(strike)[0].strip()
    if not s or s.lower() in NOT_A_WORD:
        return []
    out = []
    for part in s.split("/"):
        tk = tokens(part)
        if tk and tk not in out:
            out.append(tk)
    return out


def forms(word):
    """The word itself plus its plural and possessive spellings (rules: no other inflections)."""
    w = word.split("'")[0]
    f = {w, w + "s", w + "es", w + "'s", w + "s'", w + "'"}
    if w.endswith("y") and len(w) > 1 and w[-2] not in "aeiou":
        f |= {w[:-1] + "ies", w[:-1] + "ies'"}
    if w.endswith("s"):
        f |= {w + "'", w + "es'"}
    return f


def count(strike, text_or_tokens):
    """How many times any alternative of `strike` appears in the text, by the rules above."""
    tk = tokens(text_or_tokens) if isinstance(text_or_tokens, str) else text_or_tokens
    n = 0
    for alt in alternatives(strike):
        head, last = alt[:-1], forms(alt[-1])
        k = len(alt)
        for i in range(len(tk) - k + 1):
            if tk[i + k - 1] in last and tk[i:i + k - 1] == head:
                n += 1
    return n


def said(strike, text_or_tokens):
    return count(strike, text_or_tokens) >= threshold(strike)[1]


def key(strike):
    """Canonical form for comparing the same strike across events ('Slow Down / Slowdown' ==
    'slowdown/slow down'). Plural spellings of the strike itself are folded ('Tariffs' == 'Tariff')."""
    alts = []
    for a in alternatives(strike):
        last = a[-1]
        if last.endswith("ies") and len(last) > 4:
            last = last[:-3] + "y"
        elif last.endswith("s") and not last.endswith("ss") and len(last) > 3:
            last = last[:-1]
        alts.append(" ".join(a[:-1] + [last]))
    n = threshold(strike)[1]
    return "/".join(sorted(set(alts))) + (f" {n}+" if n > 1 and alts else "")
