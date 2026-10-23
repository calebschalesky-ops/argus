"""Cross-venue checks: Kalshi vs Polymarket.

Two tools live here:
  * the general scanner, `python -m argus.crossvenue scan` (see the "General scanner" section
    below): every market listed on both venues, matched conservatively with a confidence
    score, both venues' rules compared side by side, ranked by gap after Kalshi's fee;
  * the midterm check, `python -m argus crossvenue` / `python -m argus.crossvenue midterms`,
    described next.

Midterms: for every race listed on both venues (Senate control, House control, each Senate
race, each governor race) it pairs up the outcomes, reads Kalshi's ask and
Polymarket's order-book midpoint, and works out the gap after Kalshi's taker fee.

Matching is deliberately strict. Two outcomes are paired only when they pay on the
same thing: the same party, or the same named person. Anything looser (Polymarket's
"any independent" vs Kalshi's one named independent, or a person vs a party) is
still paired but carries a written warning, because that difference alone can explain
a price gap. Races where nothing pairs cleanly are listed as unmatched, never forced.

Paper only. Nothing here can trade: both clients are read-only.
"""
import gzip
import json
import math
import re
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import fees, kalshi, polymarket

FLAG_PTS = 10.0          # gap (points, after fee) that earns WORTH A LOOK
MAX_PM_SPREAD = 0.10     # wider than this, Polymarket's mid is not a trustworthy price

STATES = ["Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut",
          "Delaware", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa",
          "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan",
          "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire",
          "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio",
          "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
          "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington", "West Virginia",
          "Wisconsin", "Wyoming"]
_STATE_BY_SLUG = {s.lower().replace(" ", "-"): s for s in STATES}

# Settlement-rule differences, read from both venues' rules on 2026-10-22.
RULES = {
    "control-senate": ("Kalshi: early on a media consensus, else the party of the Senate President pro tempore "
                       "on 2027-02-01. Polymarket: party with a majority of seats (independents count with the "
                       "party they caucus with; the Vice President breaks a 50-50 tie), may stay open to "
                       "2027-01-04 waiting on caucus announcements"),
    "control-house": ("Kalshi: early on a media consensus, else the party of the Speaker on 2027-02-01. "
                      "Polymarket: party with a majority of seats (independents count with their caucus), "
                      "else the party of the first elected Speaker"),
    "senate": ("Kalshi: party of the person SWORN IN for the 2027 term (fast-tracked on a media consensus). "
               "Polymarket: winner called by AP+Fox+NBC, else certification, runoffs included; "
               "independents never count as D or R"),
    "governor": ("Kalshi: party (or person) INAUGURATED. Polymarket: winner called by AP+Fox+NBC, "
                 "else certification"),
}

PARTY_WORDS = {"democrat": "D", "democrats": "D", "democratic": "D", "democratic party": "D",
               "republican": "R", "republicans": "R", "republican party": "R"}


# ---------------- race keys ----------------

def kalshi_race_key(series, rules):
    """(office, state) for a Kalshi market, read from its rules text (tickers can be wrong:
    SENATELA-26 is Kentucky). None if it is not a 2026 midterm race we compare."""
    if series == "CONTROLS" and "2026" in rules:
        return ("control-senate", "US")
    if series == "CONTROLH" and "2026" in rules:
        return ("control-house", "US")
    if series.startswith("SENATE"):
        m = re.search(r"Senator of ([A-Z][A-Za-z ]+?) (?:for the term beginning in 2027|pursuant to)", rules)
        if m and m.group(1) in STATES and ("beginning in 2027" in rules or "2026" in rules):
            return ("senate", m.group(1))
    if series.startswith("GOVPARTY") or series == "KXGOVCA":
        m = re.search(r"governor(?:ship)? of ([A-Z][A-Za-z ]+?) pursuant to the (?:election in )?2026", rules)
        if m and m.group(1) in STATES:
            return ("governor", m.group(1))
    return None


def pm_race_key(slug):
    """(office, state) for a Polymarket event slug, or None.

    Only the plain race-winner events count: "<state>-senate-election-winner",
    "<state>-governor-winner-2026", "<state>-governor-election-winner",
    "<state>-governor-election-2026" and the two chamber-control events.
    Margins, state legislatures and seat-count markets are left out on purpose."""
    if slug == "which-party-will-win-the-senate-in-2026":
        return ("control-senate", "US")
    if slug == "which-party-will-win-the-house-in-2026":
        return ("control-house", "US")
    m = re.fullmatch(r"([a-z-]+?)-(senate|governor)-(?:election-winner(?:-\d+)?|winner-2026|election-2026)", slug)
    if m and m.group(1) in _STATE_BY_SLUG:
        return (m.group(2), _STATE_BY_SLUG[m.group(1)])
    return None


# ---------------- outcomes ----------------

def _last_name(name):
    words = [w for w in re.sub(r"[^\w\s-]", " ", name.lower()).split()
             if w not in ("jr", "sr", "ii", "iii", "sen", "gov", "rep", "dr")]
    return words[-1] if words else ""


def kalshi_outcome(m):
    """Party and name of one Kalshi market. Tickers ending -D / -R are party contracts."""
    suffix = m["ticker"].rsplit("-", 1)[-1]
    label = (m.get("yes_sub_title") or "").strip()
    party = suffix if suffix in ("D", "R") and not m["ticker"].startswith("KXGOVCA") else None
    name = "" if label.lower() in PARTY_WORDS or label.lower().endswith("party") else label
    return {"party": party, "name": name, "label": label, "basis": "party" if party else "person"}


def pm_outcome(market):
    """Party, name and basis of one Polymarket market (one outcome of a race)."""
    title = (market.get("groupItemTitle") or "").strip()
    question = market.get("question") or ""
    party, name, generic_ind = None, title, False
    m = re.match(r"(.*?)\s*\((D|R|I)\)\s*$", title)
    if m:
        name, tag = m.group(1).strip(), m.group(2)
        party = tag if tag in ("D", "R") else None
    elif title.lower() in PARTY_WORDS:
        party, name = PARTY_WORDS[title.lower()], ""
    elif title.lower() in ("independent", "an independent"):
        generic_ind, name = True, ""
    # The question says what actually pays: "Will the Democrats win ..." is a party contract
    # even when the label shows a candidate's name.
    q = re.match(r"Will the (Democrats|Republicans|Democratic Party|Republican Party) ", question)
    if q:
        party = "D" if q.group(1).startswith("Democrat") else "R"
    basis = "party" if party or generic_ind else "person"
    return {"party": party, "name": name, "label": title, "generic_ind": generic_ind, "basis": basis}


def match_outcomes(k_outs, pm_outs):
    """Pair Kalshi and Polymarket outcomes of one race.

    k_outs / pm_outs: lists of dicts from kalshi_outcome / pm_outcome (any extra keys are kept).
    Returns (pairs, unmatched_kalshi, unmatched_pm); each pair is (k, p, warnings).
    """
    used, pairs, left_pm = set(), [], []
    for p in pm_outs:
        hit, warn = None, []
        if p["party"]:
            for i, k in enumerate(k_outs):
                if i not in used and k["party"] == p["party"]:
                    hit = i
                    break
            if hit is not None:
                k = k_outs[hit]
                if k["name"] and p["name"] and _last_name(k["name"]) != _last_name(p["name"]):
                    warn.append(f"names differ (Kalshi {k['name']}, Polymarket {p['name']}); both pay on the party")
        elif p.get("generic_ind"):
            others = [i for i, k in enumerate(k_outs) if i not in used and not k["party"]]
            if len(others) == 1:
                hit = others[0]
                warn.append(f"Polymarket 'Independent' pays on ANY independent; Kalshi only on {k_outs[hit]['label']}")
        elif p["name"]:
            for i, k in enumerate(k_outs):
                if i not in used and k["name"] and _last_name(k["name"]) == _last_name(p["name"]):
                    hit = i
                    break
            if hit is not None and k_outs[hit]["basis"] == "party":
                warn.append("Polymarket pays on the person, Kalshi on the party")
        if hit is None:
            left_pm.append(p)
        else:
            used.add(hit)
            pairs.append((k_outs[hit], p, warn))
    return pairs, [k for i, k in enumerate(k_outs) if i not in used], left_pm


# ---------------- gap maths ----------------

def side_gap(prob, ask):
    """Points by which Polymarket's probability beats Kalshi's ask, after Kalshi's taker fee.

    prob: Polymarket probability of this side (0-1).  ask: Kalshi ask for this side in dollars.
    None when Kalshi has no real ask on this side."""
    if ask is None or not 0 < ask < 1:
        return None
    return (prob - ask - fees.taker_fee(ask)) * 100


def best_side(pm_yes, yes_ask, no_ask):
    """(side, pm_prob_of_side, ask, gap_pts) for whichever Kalshi side has the bigger gap."""
    options = []
    for side, prob, ask in (("YES", pm_yes, yes_ask), ("NO", 1 - pm_yes, no_ask)):
        g = side_gap(prob, ask)
        if g is not None:
            options.append((side, prob, ask, g))
    return max(options, key=lambda o: o[3]) if options else None


def flag_for(gap, pm_spread):
    if gap is None or gap < FLAG_PTS:
        return "-"
    if pm_spread is None or pm_spread > MAX_PM_SPREAD:
        return "WIDE PM SPREAD"
    return "WORTH A LOOK"


# ---------------- live fetch ----------------

def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _patient(fn, **kw):
    """Call a Kalshi read, pacing it and waiting out a 429 (other Argus commands share the limit)."""
    for attempt in range(4):
        time.sleep(0.2)
        try:
            return fn(**kw)
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code != 429 or attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))


def kalshi_races():
    """{race_key: [market, ...]} for every open 2026 midterm race on Kalshi."""
    races = {}
    for s in _patient(kalshi.all_series):
        t = s.get("ticker", "")
        if kalshi.is_fifteen_minute(s):
            continue
        if not (t.startswith(("SENATE", "GOVPARTY")) or t in ("CONTROLS", "CONTROLH", "KXGOVCA")):
            continue
        for m in _patient(kalshi.get_markets, series_ticker=t):
            key = kalshi_race_key(t, m.get("rules_primary") or "")
            if key:
                m["_series"] = t
                races.setdefault(key, []).append(m)
    # California has a party market and a person market. Polymarket's California event is
    # per person, so compare it with Kalshi's person market (same basis) and drop the party one.
    ca = races.get(("governor", "California"), [])
    if any(m["_series"] == "KXGOVCA" for m in ca):
        races[("governor", "California")] = [m for m in ca if m["_series"] == "KXGOVCA"]
    return races


def pm_races():
    """{race_key: (event, [live market, ...])} for Polymarket's midterm events."""
    out = {}
    for e in polymarket.events_by_tag("midterms"):
        key = pm_race_key(e.get("slug", ""))
        if key:
            out[key] = (e, [m for m in e.get("markets", []) if polymarket.is_live(m)])
    return out


def pm_lookup(key):
    """Find one race on Polymarket by its usual slugs, for events the "midterms" tag misses
    (California's governor race is tagged "governor-midterms" only, seen 2026-10-22)."""
    office, state = key
    if office not in ("senate", "governor"):
        return None
    base = state.lower().replace(" ", "-")
    slugs = ([f"{base}-senate-election-winner"] if office == "senate" else
             [f"{base}-governor-winner-2026", f"{base}-governor-election-2026", f"{base}-governor-election-winner"])
    for slug in slugs:
        e = polymarket.get_event(slug)
        if e and not e.get("closed"):
            return e, [m for m in e.get("markets", []) if polymarket.is_live(m)]
    return None


def compare():
    """Run the whole check live. Returns a dict with rows, unmatched races and notes."""
    kr, pr = kalshi_races(), pm_races()
    for key in set(kr) - set(pr):
        found = pm_lookup(key)
        if found:
            pr[key] = found
    results, unmatched = [], []
    for key in sorted(set(kr) | set(pr)):
        if key not in kr or key not in pr:
            unmatched.append((key, "only on " + ("Kalshi" if key in kr else "Polymarket"), []))
            continue
        k_markets, (event, pm_markets) = kr[key], pr[key]
        k_outs = [dict(kalshi_outcome(m), market=m) for m in k_markets]
        p_outs = [dict(pm_outcome(m), market=m) for m in pm_markets]
        pairs, left_k, left_p = match_outcomes(k_outs, p_outs)
        if left_k or left_p:
            unmatched.append((key, "outcomes left unpaired",
                              [f"Kalshi {k['label']}" for k in left_k] + [f"Polymarket {p['label']}" for p in left_p]))
        for k, p, warn in pairs:
            km, pm = k["market"], p["market"]
            yes_tok, _ = polymarket.token_ids(pm)
            if not yes_tok:
                unmatched.append((key, "Polymarket market has no Yes/No tokens", [p["label"]]))
                continue
            bk = polymarket.book(yes_tok)
            if bk["mid"] is None:
                unmatched.append((key, "Polymarket book empty on one side", [p["label"]]))
                continue
            yes_ask, no_ask = _f(km.get("yes_ask_dollars")), _f(km.get("no_ask_dollars"))
            best = best_side(bk["mid"], yes_ask, no_ask)
            results.append({"key": key, "k": k, "p": p, "warn": warn, "book": bk, "event": event,
                            "yes_ask": yes_ask, "no_ask": no_ask, "best": best,
                            "flag": flag_for(best[3] if best else None, bk["spread"])})
    return {"rows": results, "unmatched": unmatched,
            "kalshi_races": len(kr), "pm_races": len(pr),
            "at": datetime.now(timezone.utc)}


# ---------------- output ----------------

def race_label(key):
    office, state = key
    return {"control-senate": "Senate control", "control-house": "House control"}.get(
        office, f"{state} {'Sen' if office == 'senate' else 'Gov'}")


def _ascii(text):
    """Plain ASCII for the Windows console: Lujan, not Luj?n."""
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _c(x):
    return f"{round(x * 100):>3d}c" if x else "  -"


def format_report(res):
    from .model import ET
    L = ["=" * 22 + " ARGUS CROSS-VENUE: 2026 MIDTERMS " + "=" * 22,
         f"Run:       {res['at'].astimezone(ET):%a %b %d %I:%M %p ET}",
         f"Races:     Kalshi {res['kalshi_races']}, Polymarket {res['pm_races']}, "
         f"on both {len({r['key'] for r in res['rows']})}; outcomes matched {len(res['rows'])}",
         "Gap = Polymarket's chance for that side - Kalshi's ask - Kalshi taker fee, in points.",
         "-" * 78,
         f"{'Race':<20}{'Outcome':<22}{'K yes':>6}{'K no':>6}{'PM mid':>8}{'spr':>5}  {'Side':<4}{'Gap':>7}  Flag",
         ]
    rows = sorted(res["rows"], key=lambda r: -(r["best"][3] if r["best"] else -999))
    for r in rows:
        b, bk = r["best"], r["book"]
        side, gap = (b[0], f"{b[3]:+.1f}") if b else ("-", "  n/a")
        warn = " *" if r["warn"] else ""
        L.append(f"{race_label(r['key'])[:19]:<20}{(r['k']['label'] + warn)[:21]:<22}{_c(r['yes_ask']):>6}{_c(r['no_ask']):>6}"
                 f"{bk['mid'] * 100:>7.1f}%{round(bk['spread'] * 100):>4d}c  {side:<4}{gap:>7}  {r['flag']}")
    L.append("-" * 78)
    flagged = [r for r in rows if r["flag"] == "WORTH A LOOK"]
    if rows and rows[0]["best"]:
        t = rows[0]
        L.append(f"Biggest:   {t['best'][0]} {race_label(t['key'])} '{t['k']['label']}' on Kalshi at "
                 f"{_c(t['best'][2]).strip()} - Polymarket {t['best'][1] * 100:.1f}% - gap {t['best'][3]:+.1f} pts")
    L.append(f"Verdict:   {len(flagged)} WORTH A LOOK" if flagged else "Verdict:   NO GAP of 10+ points")
    warned = [r for r in rows if r["warn"]]
    if warned:
        L.append("\n* Outcome-level rule differences:")
        for r in warned:
            L.append(f"  {race_label(r['key'])} / {r['k']['label']}: " + "; ".join(r["warn"]))
    L.append("\nRace-level rule differences (apply to every row of that kind):")
    for k in ("control-senate", "control-house", "senate", "governor"):
        L.append(f"  {k}: {RULES[k]}")
    if res["unmatched"]:
        L.append("\nNot compared:")
        for key, why, items in res["unmatched"]:
            if len(items) > 6:
                items = items[:4] + [f"and {len(items) - 4} more"]
            L.append(f"  {race_label(key):<20} {why}" + (f": {', '.join(items)}" if items else ""))
    L.append("\nPaper only. Argus never trades.")
    L.append("=" * 78)
    return _ascii("\n".join(L))


def log_rows(res):
    """One prediction-log row per matched outcome (SPEC section 9 columns).

    model_prob is Polymarket's mid for the side shown (the No side is 1 - Yes mid);
    market_price is Kalshi's ask for that side."""
    stamp = res["at"].strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for r in res["rows"]:
        if not r["best"]:
            continue
        side, prob, ask, gap = r["best"]
        km, bk = r["k"]["market"], r["book"]
        bet = 1 if r["flag"] == "WORTH A LOOK" else 0
        notes = [f"pm_market={r['p']['market']['id']}", f"pm_event={r['event'].get('slug')}",
                 f"pm_label={r['p']['label']}", f"pm_bid={bk['bid']}", f"pm_ask={bk['ask']}",
                 f"flag={r['flag']}", f"rule={RULES[r['key'][0]]}"]
        notes += [f"warn={w}" for w in r["warn"]]
        rows.append({
            "logged_at_utc": stamp, "engine": "crossvenue", "series": km["_series"],
            "event_ticker": km["event_ticker"], "market_ticker": km["ticker"],
            "range_label": f"{race_label(r['key'])}: {r['k']['label']}", "side": side,
            "model_prob": f"{prob:.4f}", "market_price": f"{ask:.2f}", "gap_pts": f"{gap:.1f}",
            "worth_a_look": str(bet), "paper_contracts": str(bet),
            "fee_per_contract": f"{fees.taker_fee(ask):.2f}",
            "anchor_price": f"{bk['mid']:.4f}", "anchor_kind": "Polymarket CLOB Yes mid",
            "close_time_utc": km.get("close_time", ""), "notes": "; ".join(notes),
        })
    return rows


# =====================================================================================
# General scanner: every market listed on both venues  (python -m argus.crossvenue scan)
# =====================================================================================
#
# Caleb, 2026-10-22: gaps on the big markets are 1-2 points and Kalshi's fee eats them;
# bigger gaps survive on thin, obscure markets, which is exactly where the two venues'
# rules wording differs and can cost both legs. So every match carries a confidence
# score and a side-by-side rules check, and a 10+ point gap is WORTH A LOOK only when
# the match is confident AND no rule dimension differs or is unclear.

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "crossvenue"   # git-ignored
SCAN_DIR = Path(__file__).resolve().parent.parent / "scans"
MIN_CONF_SHOW = 60       # matches below this are not reported at all
MIN_CONF_FLAG = 75       # WORTH A LOOK needs at least this much confidence
PM_SKIP_TAGS = {"5M", "15M"}   # Polymarket's 5- and 15-minute recurring markets (hard rule 4)

_STOP = set("""a an the of in on at by to for from and or with will be is are was were who what which
when whose how many much does do did than vs versus before after during this that next end win wins won
winner market price yes no outcome its it as if not any get gets their there into out up per new""".split())
_PHRASES = [("u.s.", "us"), ("united states", "us"), ("federal reserve", "fed"), ("s&p 500", "sp500"),
            ("s&p", "sp500"), ("new york city", "nyc"), ("prime minister", "pm"), ("&", " and ")]
_MONTHS = {"january": "jan", "february": "feb", "march": "mar", "april": "apr", "june": "jun",
           "july": "jul", "august": "aug", "september": "sep", "sept": "sep", "october": "oct",
           "november": "nov", "december": "dec"}
_ALIASES = {"btc": "bitcoin", "eth": "ethereum", "sol": "solana", "doge": "dogecoin", "democratic": "democrat",
            "democrats": "democrat", "dems": "democrat", "dem": "democrat", "republicans": "republican",
            "gop": "republican", "presidential": "president", "elections": "election", "governorship": "governor",
            "gubernatorial": "governor", "senator": "senate", "mayoral": "mayor", "championship": "champion",
            "champions": "champion", "fc": "", "cf": "", "correct": "exact", "tie": "draw", "tied": "draw", "v": "", "w": ""}
_ORDINAL = {"first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5",
            "one": "1", "two": "2", "three": "3", "four": "4", "five": "5"}
# Venue boilerplate that one side adds and the other leaves out without changing the bet.
_NOISE = set("""pga tour lpga dp nfl nba mlb nhl mls ncaa ncaaf ncaab ufc atp wta fifa uefa epl pro season
regular official game match result today tonight day et edt est pt pdt pst ct cdt utc gmt pm am""".split())
# Words that change WHAT pays. If one side has one of these and the other side does not, no match.
_CRITICAL = set("""round half quarter period inning set map leg stage leader lead finalist nominee nomination
top exact margin point assist rebound rushing receiving passing scrimmage yard touchdown goal scorer btts
total spread first next last primary runoff mvp rookie award cut podium pole fastest most least announce
confirm resign leave fired impeach mention say tweet post close high low daily weekly monthly annual yoy mom
core headline seasonally adjusted popular vote seat margin turnout nominated elected sworn inaugurated
us global worldwide international
qualify qualifies relegated promoted advance eliminated draft pick place draw week
jan feb mar apr may jun jul aug sep oct nov dec""".split())
_STATE_CODES = set("""al ak az ar ca co ct de fl ga hi id il ia ks ky la md ma mi mn ms mo mt ne nv nh nj nm ny
nc nd oh ok pa ri sc sd tn tx ut vt va wa wv wi wy dc""".split())
_LABEL_FILLER = set("or above below exactly least more less over under higher lower plus greater fewer".split())
_SCORE = re.compile(r"\b(\d+)\s*-\s*(\d+)\b")
_OTHER_LABEL = re.compile(r"^(other|others|another|someone else|anyone else|any other|none of the above|field|"
                          r"a candidate not listed|not listed|.*not listed above)\b", re.I)


def _num(s):
    s = f"{float(s):.6f}".rstrip("0").rstrip(".")
    return s or "0"


def _money(m):
    mult = {"k": 1e3, "m": 1e6, "b": 1e9}[m.group(2)]
    if m.group(2) != "k" and not m.group(0).startswith("$"):
        return m.group(0)             # "3m" alone may be minutes; only "$3m" is money
    return _num(float(m.group(1)) * mult)


def tokens(text):
    """Normalised words and numbers of a title or label, for matching."""
    t = _ascii(text or "").lower()
    for a, b in _PHRASES:
        t = t.replace(a, b)
    t = re.sub(r"(?<=\d),(?=\d{3})", "", t)
    t = re.sub(r"\b(\d+)(st|nd|rd|th)\b", r"\1", t)                      # 3rd -> 3
    t = re.sub(r"\b(first|second|third|fourth|fifth)\s+(round|half|quarter|period|set|leg|stage|map|inning)",
               lambda m: f"{_ORDINAL[m.group(1)]} {m.group(2)}", t)        # third round -> 3 round
    t = re.sub(r"\b(round|half|quarter|period|set|leg|stage|map|inning)\s+(one|two|three|four|five)\b",
               lambda m: f"{m.group(1)} {_ORDINAL[m.group(2)]}", t)
    t = re.sub(r"\$?(\d+(?:\.\d+)?)\s*([kmb])\b", _money, t)
    out = []
    for w in re.findall(r"[a-z]+|\d+(?:\.\d+)?", t):
        if w[0].isdigit():
            out.append(_num(w))
            continue
        w = _ALIASES.get(_MONTHS.get(w, w), _MONTHS.get(w, w))
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        if w and w not in _STOP and len(w) > 1:
            out.append(w)
    return out


def _is_year(n):
    return len(n) == 4 and n.startswith("20") and n.isdigit()


def strike_numbers(toks):
    """Numbers that define WHAT pays (thresholds, prices, counts): must be identical on both sides."""
    return {n for n in toks if n[0].isdigit() and not _is_year(n) and ("." in n or float(n) > 31)}


def small_numbers(toks):
    """Small whole numbers (days of the month, seeds, round numbers): one side may omit them."""
    return {n for n in toks if n[0].isdigit() and not _is_year(n) and "." not in n and float(n) <= 31}


def direction(text):
    """above / below / range / touch-up / touch-down, or None. Touch ("reach", "hit", "dip to")
    pays if the price is touched at ANY time; above/below pays on one reading. They never match."""
    t = (text or "").lower()
    if re.search(r"\bbetween\b|\d\s*(?:-|–|to)\s*\$?\d", t):
        return "range"
    if re.search(r"\bexactly\b", t):
        return "exact"
    if re.search(r"<\s*\$?\d", t):
        return "below"
    if re.search(r">\s*\$?\d|\d\+", t):
        return "above"
    touch_up =re.search(r"\b(reach|reaches|hit|hits|touch|touches)\b", t)
    touch_dn = re.search(r"\b(dip|dips|fall to|drop to|drops to)\b", t)
    up = re.search(r"\b(above|over|more than|greater than|at least|or more|or higher|or above|higher than|exceeds?)\b|↑", t)
    down = re.search(r"\b(below|under|less than|fewer than|or less|or fewer|or lower|lower than)\b|↓", t)
    if touch_up and not touch_dn:
        return "touch-up"
    if touch_dn and not touch_up:
        return "touch-down"
    if up and not down:
        return "above"
    if down and not up:
        return "below"
    return None


def boundary(text):
    """Whether the threshold itself counts: 'inclusive' (at least / or more) or 'exclusive' (above / more than)."""
    t = (text or "").lower()
    inc = re.search(r"\b(at least|or more|or higher|or above|or greater|or less|or fewer|or lower|or below|"
                    r"greater than or equal|less than or equal|no less than|no more than)\b|≥|≤", t)
    exc = re.search(r"\b(above|below|more than|less than|greater than|fewer than|over|under|exceeds?)\b", t)
    if inc:
        return "inclusive"
    if exc:
        return "exclusive"
    return None


class _Idf:
    def __init__(self, docs):
        df = defaultdict(int)
        for d in docs:
            for w in set(d):
                df[w] += 1
        self.n, self.df = len(docs), df
        self.wt = {w: math.log((self.n + 1) / (c + 1)) + 1.0 for w, c in df.items()}
        self.unseen = math.log(self.n + 1) + 1.0

    def w(self, word):
        return self.wt.get(word, self.unseen)

    def total(self, words):
        return sum(self.wt.get(x, self.unseen) for x in words)

    def jaccard(self, a, b):
        a, b = set(a), set(b)
        if not a or not b:
            return 0.0
        shared = self.total(a & b)
        return shared / (self.total(a) + self.total(b) - shared)

    def coverage(self, part, whole):
        """Share of `part`'s weight that also appears in `whole`."""
        part, whole = set(part), set(whole)
        if not part:
            return 0.0
        return sum(self.w(x) for x in part & whole) / sum(self.w(x) for x in part)


def _parse_t(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def date_factor(k_times, pm_time):
    """1.0 when the two settle within ~2 days of each other, falling as they drift apart."""
    pm = _parse_t(pm_time)
    ks = [t for t in (_parse_t(x) for x in k_times) if t]
    if not pm or not ks:
        return 0.7, None
    days = min(abs((k - pm).total_seconds()) for k in ks) / 86400
    for limit, f in ((2, 1.0), (10, 0.9), (45, 0.75), (120, 0.6)):
        if days <= limit:
            return f, days
    return 0.4, days


# ---------------- per-venue records ----------------

def _shape(full):
    """Tokens and the number/direction facts of one outcome, worked out once."""
    tok = tokens(full)
    return {"tok": tok, "strike": strike_numbers(tok), "small": small_numbers(tok), "dir": direction(full)}


def k_record(event, m, n_live):
    label = (m.get("yes_sub_title") or "").strip() if n_live > 1 else ""
    full = f"{event.get('title', '')} {event.get('sub_title', '')} {m.get('title', '')} {m.get('yes_sub_title', '')}"
    return {"venue": "K", "event": event, "m": m, "label": label, "full": full, **_shape(full),
            "etok": set(tokens(f"{event.get('title', '')} {event.get('sub_title', '')}")),
            "lab": tokens(label), "times": [m.get("expected_expiration_time"), m.get("close_time")],
            "yes_ask": _f(m.get("yes_ask_dollars")), "no_ask": _f(m.get("no_ask_dollars")),
            "yes_bid": _f(m.get("yes_bid_dollars")),
            "yes_ask_size": _f(m.get("yes_ask_size_fp")), "no_ask_size": _f(m.get("yes_bid_size_fp")),
            "rules": f"{m.get('rules_primary') or ''}\n{m.get('rules_secondary') or ''}"}


def pm_records(event, m, n_live):
    """One record per thing you can buy. A Yes/No market gives one (its Yes side). A market whose
    two outcomes are names ("Lehigh" / "Villanova") gives one per name, priced from its own side."""
    outcomes = m.get("outcomes") or '["Yes", "No"]'
    outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
    ids = m.get("clobTokenIds") or "[]"
    ids = json.loads(ids) if isinstance(ids, str) else ids
    bid, ask = _f(m.get("bestBid")), _f(m.get("bestAsk"))      # Gamma prices the FIRST outcome
    base = {"venue": "P", "event": event, "m": m, "time": m.get("endDate") or event.get("endDate"),
            "etok": set(tokens(event.get("title", ""))),
            "rules": m.get("description") or event.get("description") or ""}
    if [o.lower() for o in outcomes] == ["yes", "no"] or len(outcomes) != 2 or len(ids) != 2:
        label = (m.get("groupItemTitle") or "").strip() if n_live > 1 else ""
        sides = [(label, bid, ask, ids[0] if ids else None, 0)]
    else:
        sides = [(outcomes[0], bid, ask, ids[0], 0),
                 (outcomes[1], (1 - ask) if ask else None, (1 - bid) if bid else None, ids[1], 1)]
    out = []
    for label, b, a, tok, idx in sides:
        full = f"{event.get('title', '')} {m.get('question', '')} {m.get('groupItemTitle') or ''} {label}"
        out.append(dict(base, label=label, full=full, **_shape(full), lab=tokens(label), token=tok,
                        outcome_index=idx, key=f"{m['id']}:{idx}", bid=b, ask=a,
                        mid=(b + a) / 2 if b and a else None, spread=(a - b) if b and a else None))
    return out


def _common_label_tokens(records):
    """Words shared by most outcome labels of one event ("D/ST", "wins", a team in every score)."""
    if len(records) < 3:
        return set()
    counts = defaultdict(int)
    for r in records:
        for w in set(r["lab"]):
            counts[w] += 1
    return {w for w, n in counts.items() if n > len(records) / 2}


# ---------------- outcome matching ----------------

def outcome_match(k, p, idf):
    """Confidence 0-100 that Kalshi outcome k and Polymarket outcome p pay on the same thing,
    or (0, reason) when a hard check fails. Hard checks: same strike numbers, compatible
    small numbers, same direction (above/below/range/touch), labels that agree."""
    if k["strike"] != p["strike"]:
        return 0, "numbers differ"
    ks, ps = k["small"], p["small"]
    if not (ks <= ps or ps <= ks):
        return 0, "dates/small numbers differ"
    dk, dp = k["dir"], p["dir"]
    if {dk, dp} == {"exact", None}:      # "exactly 4.4%" and "be 4.4%" are the same bet
        dk = dp
    if (dk or dp) and dk != dp:
        return 0, f"direction differs ({dk} vs {dp})"
    sk, sp = _SCORE.findall(k["label"]), _SCORE.findall(p["label"])
    if (sk or sp) and sk != sp:
        return 0, "scores differ (or are written in a different order)"
    crit = (set(k["tok"]) ^ set(p["tok"])) & _CRITICAL
    if crit:
        return 0, f"only one side says: {', '.join(sorted(crit))}"
    # Compare only the words that tell one outcome from another inside its own event.
    # (direction words are checked above, so they are not part of what names the outcome)
    kl = [w for w in k["lab"] if w not in k.get("common", ()) and w not in _LABEL_FILLER] or k["lab"]
    pl = [w for w in p["lab"] if w not in p.get("common", ()) and w not in _LABEL_FILLER] or p["lab"]
    kl = [w for w in kl if w not in _STATE_CODES] or kl
    pl = [w for w in pl if w not in _STATE_CODES] or pl
    if ("/" in k["label"] or "/" in p["label"]) and set(kl) != set(pl):
        # "Gas / Gasoline" vs "Oil / Gas / Gasoline": a mention bundle pays on every word listed
        return 0, "word lists differ"
    if kl and pl:
        label = max(idf.jaccard(kl, pl),
                    1.0 if len(k["label"].split()) > 1 and len(_last_name(k["label"])) >= 3
                    and _last_name(k["label"]) == _last_name(p["label"])
                    and k["label"][:1].lower() == p["label"][:1].lower() else 0.0)
        if label < 0.6:
            return 0, "labels differ"
    elif kl or pl:
        lab, other = (kl, p["tok"]) if kl else (pl, k["tok"])
        label = idf.coverage(lab, other)
        if label < 0.75:
            return 0, "label not in the other question"
    else:
        label = 1.0
    # Words in one side's EVENT title that the other side never mentions ("Best Independent Game"
    # vs "Best Game Direction", "Streamer Awards" vs "Esports Awards") mean different questions.
    kt, pt = k["etok"] - _NOISE, p["etok"] - _NOISE
    missing = {w for w in (kt - set(p["tok"])) | (pt - set(k["tok"])) if not w[0].isdigit()}
    if missing and idf.total(missing) > 0.25 * idf.total(kt | pt):
        return 0, f"event titles differ: {', '.join(sorted(missing))}"
    extra = [w for w in set(k["tok"]) ^ set(p["tok"]) if w not in _NOISE and not w[0].isdigit()]
    full = idf.jaccard([w for w in k["tok"] if w not in _NOISE], [w for w in p["tok"] if w not in _NOISE])
    if extra and full < 0.5:
        return 0, "titles too different"
    df, _ = date_factor(k["times"], p["time"])
    return round(100 * (0.6 * full + 0.4 * label) ** 0.5 * df), "ok"


def match_events(k_events, pm_events, min_ev=0.35, top=3):
    """Candidate event pairs by IDF-weighted title overlap. Returns [(k_event, pm_event, sim)]."""
    k_tok = [set(tokens(f"{e.get('title', '')} {e.get('sub_title', '')}")) for e in k_events]
    p_tok = [set(tokens(e.get("title", ""))) for e in pm_events]
    idf = _Idf(k_tok + p_tok)
    p_w = [idf.total(t) for t in p_tok]
    index = defaultdict(list)
    for j, toks in enumerate(p_tok):
        for w in set(toks):
            if idf.df[w] < 1500:           # very common words do not generate candidates
                index[w].append(j)
    pairs = []
    for i, toks in enumerate(k_tok):
        hits = defaultdict(float)
        for w in toks:
            for j in index.get(w, ()):
                hits[j] += idf.w(w)
        k_w = idf.total(toks)
        need = min(4.0, 0.4 * k_w)        # 4.0 on the real corpus; scales down for tiny test corpora
        scored = sorted(((h / (k_w + p_w[j] - h), j) for j, h in hits.items() if h >= need), reverse=True)
        for sim, j in scored[:top]:
            if sim >= min_ev:
                pairs.append((k_events[i], pm_events[j], sim))
    return pairs, idf


def match_all(k_events, pm_events, min_conf=MIN_CONF_SHOW):
    """Every outcome pair across both venues with confidence >= min_conf, one-to-one."""
    pairs, idf = match_events(k_events, pm_events)
    found = []
    for ke, pe, ev_sim in pairs:
        k_live = [m for m in ke.get("markets", []) if m.get("status") in (None, "active", "open")
                  and (_f(m.get("yes_ask_dollars")) or _f(m.get("no_ask_dollars")))]
        p_live = [m for m in pe.get("markets", []) if polymarket.is_live(m)]
        ks = [k_record(ke, m, len(k_live)) for m in k_live]
        ps = [r for m in p_live for r in pm_records(pe, m, len(p_live))]
        for group in (ks, ps):
            common = _common_label_tokens(group)
            for r in group:
                r["common"] = common
        # Only compare outcomes whose distinguishing label words overlap (or that have no label).
        by_word, unlabeled = defaultdict(list), []
        for p in ps:
            words = set(p["lab"]) - p["common"] - _STATE_CODES
            if not words:
                unlabeled.append(p)
            for w in words:
                by_word[w].append(p)
        for k in ks:
            words = set(k["lab"]) - k["common"] - _STATE_CODES
            cands = ps if not words else {id(p): p for w in words for p in by_word.get(w, ())}.values()
            for p in list(cands) + (unlabeled if words else []):
                conf, why = outcome_match(k, p, idf)
                if conf and conf >= min_conf:
                    found.append((conf, k, p))
    found.sort(key=lambda x: -x[0])
    used_k, used_p, out = set(), set(), []
    for conf, k, p in found:
        if k["m"]["ticker"] in used_k or p["key"] in used_p:
            continue
        used_k.add(k["m"]["ticker"])
        used_p.add(p["key"])
        out.append({"conf": conf, "k": k, "p": p})
    return out


# ---------------- rules side by side ----------------

SOURCES = {
    # Two broad classes, then named data feeds. Classes overlap on purpose: "official NHL results"
    # and "a consensus of credible reporting" both point at the same public result.
    "official body": r"official|governing|\bclerk\b|secretary of state|board of elections|election commission|"
                     r"certif|\bfec\b|congress\.gov|library of congress|\b(nhl|nba|nfl|mlb|mls|wnba|concacaf|fifa|"
                     r"uefa|nascar|pga tour|lpga|atp|wta|ufc|f1|serie a|bundesliga|la ?liga|premier league)\b",
    "media/reporting": r"associated press|\bap\b|\bfox\b|\bnbc\b|\bcnn\b|\babc\b|\bespn\b|consensus|"
                       r"credible report|\bmedia\b",
    "BLS": r"bureau of labor statistics|\bbls\b", "BEA": r"bureau of economic analysis|\bbea\b",
    "CME": r"\bcme\b|fedwatch", "Binance": r"binance", "Coinbase": r"coinbase", "CoinGecko": r"coingecko",
    "CoinMarketCap": r"coinmarketcap", "Chainlink": r"chainlink", "Pyth": r"\bpyth\b", "Kraken": r"kraken",
    "CF Benchmarks": r"cf benchmarks|\bbrti\b", "NWS/NOAA": r"national weather service|\bnws\b|\bnoaa\b",
    "Wikipedia": r"wikipedia", "X/Twitter": r"\btwitter\b|\bx\.com\b", "Truth Social": r"truth social",
    "Rotten Tomatoes": r"rotten tomatoes", "Box Office Mojo": r"box office mojo|the-numbers",
    "Billboard": r"billboard", "Spotify": r"spotify", "Netflix": r"netflix|top10", "YouTube": r"youtube",
    "video/transcript": r"\bvideo\b|transcript",
}
_SENT = re.compile(r"(?<=[.!?])\s+|\n+")


def _sentences(text, rx):
    return " ".join(s for s in _SENT.split(text or "") if re.search(rx, s, re.I))


def sources_in(text):
    """Source classes named in the sentences that talk about resolution sources."""
    t = _sentences(text, r"source|according to|resolve[sd]? (based on|using|per)|determined by|as reported") or ""
    t = t.lower()
    return {name for name, rx in SOURCES.items() if re.search(rx, t)}


def kalshi_sources_text(k):
    """Kalshi names its sources on the series (`settlement_sources`), not in the rules text."""
    srcs = k["event"].get("_sources") or []
    return " ".join(f"Source: {s.get('name', '')} {s.get('url', '')}." for s in srcs)


_DEADLINE = re.compile(r"\b(before|by (the )?end of|by (jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*|"
                       r"by \d|in 20\d\d|this year|deadline|through)\b", re.I)
_TIE = r"\b(tie|ties|tied|draw|dead heat|multiple (winners|participants|players|drivers|artists))\b"
_CANCEL = (r"\b(cancel\w*|postpon\w*|delay\w*|reschedul\w*|not held|abandon\w*|suspend\w*|withdraw\w*|"
           r"forfeit\w*|(doesn't|does not|did not) (participate|occur|take place|start|play))\b")


def _handling(sent):
    t = sent.lower()
    how = set()
    if re.search(r"50[-/ ]50|fifty[- ]fifty|resolve[sd]? (to )?50%|\$0?\.50", t):
        how.add("50-50")
    if re.search(r"resolves? (to )?[\"“']?no\b|settles? (to )?[\"“']?no\b", t):
        how.add("No")
    if re.search(r"resolves? (to )?[\"“']?other\b", t):
        how.add("Other")
    if re.search(r"fair (market )?(value|price)|last traded price", t):
        how.add("fair value")
    if re.search(r"\brefund|\bvoid\b", t):
        how.add("void/refund")
    if re.search(r"\$1/n|1/n\b|divided|split", t):
        how.add("split payout")
    if re.search(r"in favou?r of|tie-?break|comes first alphabetically|name comes first", t):
        how.add("tiebreak")
    if re.search(r"official(ly)? (winner|recogni|determin|result|tiebreak|rules)|as determined by|"
                 r"according to the (official|rules)|per the (official )?rules", t):
        how.add("official ruling")
    if re.search(r"remain open|until (it|the event) (is|has been) (completed|played)|following (scheduled|rescheduled)", t):
        how.add("waits for reschedule")
    return how


def tie_cancel(text):
    """How a venue handles ties and cancellations: ({tie handlings}, {cancel handlings}).
    An empty set means the rules never mention it; {"unclear"} means mentioned without a payout rule."""
    out = []
    for rx in (_TIE, _CANCEL):
        sents = [s for s in _SENT.split(text or "") if re.search(rx, s, re.I)]
        if not sents:
            out.append(set())
            continue
        how = set()
        for s in sents:
            how |= _handling(s)
        out.append(how or {"unclear"})
    return tuple(out)


def _cmp_tie_cancel(k_text, p_text):
    (kt, kc), (pt, pc) = tie_cancel(k_text), tie_cancel(p_text)
    parts, statuses = [], []
    for what, a, b in (("ties", kt, pt), ("cancellation", kc, pc)):
        s, why = _cmp_sets(a, b, what)
        statuses.append("PARTIAL" if s == "OVERLAP" else s)   # for a payout rule, half-agreeing is not agreeing
        parts.append(f"{what}: {why}")
    status = next(x for x in ("DIFFERS", "PARTIAL", "UNCLEAR", "SAME", "SILENT") if x in statuses)
    return status, "; ".join(parts)


def _cmp_sets(a, b, what):
    if not a and not b:
        return "SILENT", f"neither names {what}"
    if not a or not b:
        return "UNCLEAR", f"only {'Polymarket' if b else 'Kalshi'} names {what}: {', '.join(sorted(a or b))}"
    if "unclear" in a or "unclear" in b:
        return "UNCLEAR", f"{what} mentioned without a payout rule (Kalshi {', '.join(sorted(a)) or '-'} | Polymarket {', '.join(sorted(b)) or '-'})"
    if a == b:
        return "SAME", ", ".join(sorted(a))
    return ("OVERLAP" if a & b else "DIFFERS"), f"Kalshi {', '.join(sorted(a))} | Polymarket {', '.join(sorted(b))}"


def _has_other(labels):
    return any(_OTHER_LABEL.match(x or "") for x in labels)


def rules_check(k, p, k_labels, p_labels):
    """Both venues' rules side by side, one verdict per dimension:
    SAME / SILENT (neither says) / n/a  are fine;  UNCLEAR / PARTIAL / DIFFERS block WORTH A LOOK."""
    out = {"source": _cmp_sets(sources_in(k["rules"] + " " + kalshi_sources_text(k)),
                               sources_in(p["rules"]), "a source")}
    _, days = date_factor(k["times"], p["time"])
    kt = (k["m"].get("expected_expiration_time") or k["m"].get("close_time") or "")[:10]
    apart = f"Kalshi {kt} vs Polymarket {(p['time'] or '')[:10]}"
    if days is None:
        out["time"] = ("UNCLEAR", "a close date is missing")
    elif days <= 1.5:
        out["time"] = ("SAME", f"within {days:.1f} days")
    elif _DEADLINE.search(f"{k['full']} {p['full']}"):
        # "before 2027", "by Dec 31": the deadline IS the bet, so different dates are different bets
        out["time"] = ("DIFFERS", f"deadline question; {apart} ({days:.0f} days apart)")
    else:
        # A one-off event (an award, a game): the dates only say when each venue pays out
        out["time"] = ("LATER", f"one-off event, payout dates differ: {apart} ({days:.0f} days)")
    out["ties/cancel"] = _cmp_tie_cancel(k["rules"], p["rules"])
    if len(k_labels) <= 1 and len(p_labels) <= 1:
        out["other"] = ("n/a", "yes/no question on both")
    else:
        ko, po = _has_other(k_labels), _has_other(p_labels)
        if ko == po:
            out["other"] = ("SAME", ("both have" if ko else "neither has") + " an 'other' outcome"
                            + (f"; Kalshi lists {len(k_labels)}, Polymarket {len(p_labels)}"
                               if len(k_labels) != len(p_labels) else ""))
        else:
            out["other"] = ("DIFFERS", f"only {'Kalshi' if ko else 'Polymarket'} has an 'other' outcome")
    if strike_numbers(k["tok"]):
        bk = boundary(f"{k['m'].get('title', '')} {k['m'].get('yes_sub_title', '')} {k['rules']}")
        bp = boundary(f"{p['m'].get('question', '')} {p['label']} {p['rules']}")
        out["threshold"] = (("SILENT", "neither says") if not (bk or bp) else
                            ("UNCLEAR", f"only one says ({bk or bp})") if not (bk and bp) else
                            ("SAME", bk) if bk == bp else ("DIFFERS", f"Kalshi {bk} vs Polymarket {bp}"))
    else:
        out["threshold"] = ("n/a", "no number threshold")
    return out


RULES_FINE = ("SAME", "SILENT", "n/a", "LATER", "OVERLAP")


def rules_ok(check):
    return all(s in RULES_FINE for s, _ in check.values())


def scan_flag(gap, conf, check, pm_spread):
    if gap is None or gap < FLAG_PTS:
        return "-"
    if conf < MIN_CONF_FLAG:
        return "CHECK MATCH"
    if not rules_ok(check):
        return "RULES DIFFER"
    if pm_spread is None or pm_spread > MAX_PM_SPREAD:
        return "WIDE PM SPREAD"
    return "WORTH A LOOK"


def lock_pts(side, k_ask, pm_bid, pm_ask):
    """Both legs: buy `side` on Kalshi and the opposite on Polymarket. Points of locked profit per
    $1 after Kalshi's fee (Polymarket's own fee, where a market charges one, is NOT included)."""
    if k_ask is None:
        return None
    opp = (1 - pm_bid) if side == "YES" and pm_bid else pm_ask if side == "NO" else None
    if not opp or not 0 < opp < 1:
        return None
    return (1 - k_ask - fees.taker_fee(k_ask) - opp) * 100


def price_row(r, book=None):
    """Fill gap, side, lock and depth for one match. With `book` (CLOB top of book) it replaces Gamma's prices."""
    k, p = r["k"], r["p"]
    bid, ask = (book["bid"], book["ask"]) if book else (p["bid"], p["ask"])
    mid = (bid + ask) / 2 if bid is not None and ask is not None else None
    r.update(pm_bid=bid, pm_ask=ask, pm_mid=mid, pm_spread=(ask - bid) if mid is not None else None,
             priced_by="CLOB book" if book else "Gamma")
    # A Polymarket book wider than MAX_PM_SPREAD (often 0.1c bid / 99.9c ask on a dead market)
    # has no price worth comparing: its "mid" is just 50%. Those rows are kept but never ranked.
    priced = mid is not None and r["pm_spread"] <= MAX_PM_SPREAD
    best = best_side(mid, k["yes_ask"], k["no_ask"]) if priced else None
    r["best"], r["unpriced"] = best, mid is not None and not priced
    if not best:
        r.update(gap=None, lock=None, k_depth=None, pm_depth=None)
        return r
    side, _, k_ask, gap = best
    r["gap"], r["lock"] = gap, lock_pts(side, k_ask, bid, ask)
    size = k["yes_ask_size"] if side == "YES" else k["no_ask_size"]
    r["k_depth"] = size * k_ask if size else None
    if book:     # dollars on Polymarket's opposite leg at its best price
        r["pm_depth"] = ((book["bid_size"] or 0) * (1 - bid)) if side == "YES" else ((book["ask_size"] or 0) * ask)
    else:
        r["pm_depth"] = _f(p["m"].get("liquidityNum"))
    return r


# ---------------- snapshot of Polymarket ----------------

def _slim_event(e):
    keep_m = ("id", "question", "groupItemTitle", "outcomes", "clobTokenIds", "bestBid", "bestAsk",
              "liquidityNum", "volume24hr", "endDate", "active", "closed", "enableOrderBook", "outcomePrices")
    return {"id": e.get("id"), "slug": e.get("slug"), "title": e.get("title"), "endDate": e.get("endDate"),
            "description": (e.get("description") or "")[:6000],
            "tags": [t.get("slug") for t in (e.get("tags") or [])],
            "markets": [{k: m.get(k) for k in keep_m} for m in e.get("markets", []) if polymarket.is_live(m)]}


def pm_snapshot(max_age_min=60, log_fn=print):
    """All open Polymarket events (slimmed). Reuses the saved copy when it is younger than max_age_min,
    because the full read is ~1.9 GB and ~5 minutes."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / "pm_events.json.gz"
    if path.exists() and max_age_min > 0 and time.time() - path.stat().st_mtime < max_age_min * 60:
        log_fn(f"Polymarket: reusing snapshot from {int((time.time() - path.stat().st_mtime) / 60)} min ago")
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    log_fn("Polymarket: reading every open event (about 5 minutes) ...")
    events = [_slim_event(e) for e in polymarket.all_open_events(
        progress=lambda n: log_fn(f"  ... {n} events"))]
    events = [e for e in events if e["markets"] and not (set(e["tags"]) & PM_SKIP_TAGS)]
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(events, f)
    return events


def kalshi_snapshot():
    """Every open Kalshi event with its markets, minus 15-minute series (hard rule 4)."""
    series = {s["ticker"]: s for s in _patient(kalshi.all_series)}
    out = []
    for e in _patient(kalshi.get_open_events):
        s = series.get(e.get("series_ticker"), {})
        if kalshi.is_fifteen_minute(s) or e.get("series_ticker", "").endswith("15M"):
            continue
        e["_sources"] = s.get("settlement_sources") or []
        out.append(e)
    return out


# ---------------- the scan ----------------

def scan(pm_cache_min=60, books=60, min_conf=MIN_CONF_SHOW, log_fn=print, k_events=None, pm_events=None):
    """Match every outcome listed on both venues, check the rules, price the gaps."""
    if k_events is None:
        log_fn("Kalshi: reading every open event ...")
        k_events = kalshi_snapshot()
    if pm_events is None:
        pm_events = pm_snapshot(pm_cache_min, log_fn)
    log_fn(f"Matching {len(k_events)} Kalshi events against {len(pm_events)} Polymarket events ...")
    rows = match_all(k_events, pm_events, min_conf)

    # Full rules text: Polymarket keeps per-market rules apart from the event's, so fetch them.
    ids = list({r["p"]["m"]["id"] for r in rows})
    log_fn(f"{len(rows)} outcome matches; reading Polymarket rules for {len(ids)} markets ...")
    full = {str(m["id"]): m for m in polymarket.markets_by_id(ids)}
    labels = {}
    for r in rows:
        fm = full.get(str(r["p"]["m"]["id"]))
        if fm and fm.get("description"):
            r["p"]["rules"] = fm["description"]
        ke, pe = r["k"]["event"], r["p"]["event"]
        if ke["event_ticker"] not in labels:
            labels[ke["event_ticker"]] = [m.get("yes_sub_title") for m in ke.get("markets", [])]
        if pe["id"] not in labels:
            labels[pe["id"]] = [m.get("groupItemTitle") for m in pe.get("markets", [])]
        r["rules"] = rules_check(r["k"], r["p"], labels[ke["event_ticker"]], labels[pe["id"]])
        price_row(r)

    # Order-book prices and depth for the rows that matter: every 10+ gap, then the next best.
    ranked = sorted(rows, key=lambda r: -(r["gap"] if r["gap"] is not None else -999))
    for r in ranked[:max(books, sum(1 for x in rows if (x["gap"] or -99) >= FLAG_PTS))]:
        yes_tok = r["p"]["token"]          # the token of the outcome we matched (its own book)
        if yes_tok:
            try:
                bk = polymarket.book(yes_tok)
            except requests.HTTPError:
                continue
            if bk["bid"] is not None and bk["ask"] is not None:
                price_row(r, bk)
    for r in rows:
        r["flag"] = scan_flag(r["gap"], r["conf"], r["rules"], r["pm_spread"])
    rows.sort(key=lambda r: -(r["gap"] if r["gap"] is not None else -999))
    return {"rows": rows, "at": datetime.now(timezone.utc),
            "k_events": len(k_events), "pm_events": len(pm_events)}


def _short(text, n):
    text = _ascii(text or "")
    return text if len(text) <= n else text[:n - 1] + "~"


def _usd(x):
    if x is None:
        return "    -"
    return f"{x / 1000:>4.0f}k" if x >= 10000 else f"{x:>5.0f}"


def format_scan(res, top=40):
    from .model import ET
    rows = res["rows"]
    differ = [r for r in rows if not rules_ok(r["rules"])]
    flags = defaultdict(int)
    for r in rows:
        flags[r["flag"]] += 1
    L = ["=" * 30 + " ARGUS CROSS-VENUE SCAN " + "=" * 30,
         f"Run:      {res['at'].astimezone(ET):%a %b %d %I:%M %p ET}",
         f"Read:     Kalshi {res['k_events']} open events, Polymarket {res['pm_events']} open events",
         f"Matched:  {len(rows)} outcomes (confidence >= {MIN_CONF_SHOW}); rules differ or unclear on {len(differ)}",
         f"Priced:   {sum(1 for r in rows if r['best'])}; Polymarket book too thin to price "
         f"(spread > {MAX_PM_SPREAD * 100:.0f}c): {sum(1 for r in rows if r.get('unpriced'))}",
         f"Flags:    " + ", ".join(f"{k} {v}" for k, v in sorted(flags.items()) if k != "-") if any(
             k != "-" for k in flags) else "Flags:    none (no gap of 10+ points)",
         "Gap  = Polymarket mid for that side - Kalshi ask - Kalshi taker fee (points).",
         "Lock = buy that side on Kalshi AND the other side on Polymarket: points locked per $1, after Kalshi's",
         "       fee only (not Polymarket's fee where it charges one). K$/PM$ = dollars at the best price.",
         "Rules: s=source t=time c=ties/cancel o=other outcome h=threshold.  . fine  ? unclear  X differs",
         "-" * 84,
         f"{'Kalshi market':<34}{'Conf':>5} {'Side':<4}{'Kask':>5}{'PMmid':>6}{'Gap':>6}{'Lock':>6}{'K$':>6}{'PM$':>6} Rules  Flag"]
    sym = {"SAME": ".", "SILENT": ".", "n/a": ".", "LATER": ".", "OVERLAP": ".", "UNCLEAR": "?", "PARTIAL": "X", "DIFFERS": "X"}
    for r in rows[:top]:
        b = r["best"]
        rules = "".join(sym[s] for s, _ in (r["rules"][d] for d in ("source", "time", "ties/cancel", "other", "threshold")))
        name = f"{r['k']['event'].get('title', '')} {('- ' + r['k']['label']) if r['k']['label'] else ''}"
        mid = "-" if r["pm_mid"] is None else f"{r['pm_mid'] * 100:.0f}%"
        gap = "-" if r["gap"] is None else f"{r['gap']:+.1f}"
        lock = "-" if r["lock"] is None else f"{r['lock']:+.1f}"
        L.append(f"{_short(name, 33):<34}{r['conf']:>5} {(b[0] if b else '-'):<4}"
                 f"{(_c(b[2]) if b else '  -'):>5}{mid:>6}{gap:>6}{lock:>6}"
                 f"{_usd(r['k_depth']):>6}{_usd(r['pm_depth']):>6} {rules:<6} {r['flag']}")
    L.append("-" * 84)
    shown = [r for r in rows[:top] if r["flag"] != "-"]
    if shown:
        L.append("\nRules side by side for every flagged row above:")
        for r in shown:
            L.append(f"\n  {_short(r['k']['m']['ticker'], 40)}  <->  Polymarket {r['p']['m']['id']} "
                     f"({_short(r['p']['event'].get('slug'), 50)})")
            L.append(f"    Kalshi:     {_short(r['k']['m'].get('title') or '', 76)}")
            L.append(f"    Polymarket: {_short(r['p']['m'].get('question') or '', 76)}")
            for d in ("source", "time", "ties/cancel", "other", "threshold"):
                s, why = r["rules"][d]
                L.append(f"    {d:<12} {s:<8} {_short(why, 60)}")
    L.append("\nPaper only. Argus never trades. A match is a machine guess: read both rule pages before any bet.")
    L.append("=" * 84)
    return _ascii("\n".join(L))


def scan_rows_for_log(res):
    """Prediction-log rows for every flagged match (gap 10+). Same columns as SPEC section 9;
    paper_contracts = 1 only for WORTH A LOOK. The full table goes to the scans/ CSV instead."""
    stamp = res["at"].strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    for r in res["rows"]:
        if r["flag"] == "-" or not r["best"]:
            continue
        side, prob, ask, gap = r["best"]
        km = r["k"]["m"]
        bet = 1 if r["flag"] == "WORTH A LOOK" else 0
        notes = [f"pm_market={r['p']['m']['id']}", f"pm_event={r['p']['event'].get('slug')}",
                 f"conf={r['conf']}", f"flag={r['flag']}", f"pm_bid={r['pm_bid']}", f"pm_ask={r['pm_ask']}",
                 f"lock_pts={'' if r['lock'] is None else round(r['lock'], 1)}"]
        notes += [f"rule_{d}={s}: {why}" for d, (s, why) in r["rules"].items() if s not in ("SAME", "n/a")]
        out.append({
            "logged_at_utc": stamp, "engine": "crossvenue", "series": km.get("event_ticker", "").split("-")[0],
            "event_ticker": km.get("event_ticker", ""), "market_ticker": km["ticker"],
            "range_label": _ascii(f"{r['k']['event'].get('title', '')} {r['k']['label']}".strip())[:120],
            "side": side, "model_prob": f"{prob:.4f}", "market_price": f"{ask:.2f}", "gap_pts": f"{gap:.1f}",
            "worth_a_look": str(bet), "paper_contracts": str(bet), "fee_per_contract": f"{fees.taker_fee(ask):.2f}",
            "anchor_price": f"{r['pm_mid']:.4f}", "anchor_kind": f"Polymarket mid ({r['priced_by']})",
            "close_time_utc": km.get("close_time", ""), "notes": _ascii("; ".join(notes)),
        })
    return out


def write_scan_csv(res, path):
    import csv
    cols = ["gap_pts", "lock_pts", "flag", "conf", "side", "kalshi_ask", "pm_mid", "pm_spread", "kalshi_depth_usd",
            "pm_depth_usd", "priced_by", "kalshi_ticker", "kalshi_title", "kalshi_label", "pm_market_id",
            "pm_event_slug", "pm_question", "rule_source", "rule_time", "rule_ties_cancel", "rule_other",
            "rule_threshold"]
    rnd = lambda x, n=1: "" if x is None else round(x, n)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in res["rows"]:
            b = r["best"]
            w.writerow([rnd(r["gap"]), rnd(r["lock"]), r["flag"], r["conf"], b[0] if b else "",
                        b[2] if b else "", rnd(r["pm_mid"], 4), rnd(r["pm_spread"], 3), rnd(r["k_depth"], 0),
                        rnd(r["pm_depth"], 0), r["priced_by"], r["k"]["m"]["ticker"], r["k"]["event"].get("title"),
                        r["k"]["label"], r["p"]["m"]["id"], r["p"]["event"].get("slug"), r["p"]["m"].get("question")]
                       + [f"{s}: {why}" for s, why in (r["rules"][d] for d in
                                                       ("source", "time", "ties/cancel", "other", "threshold"))])


# ---------------- command line ----------------

def main(argv=None):
    import argparse
    import sys
    from . import log
    from .model import ET
    ap = argparse.ArgumentParser(prog="python -m argus.crossvenue",
                                 description="Kalshi vs Polymarket, read-only. Paper only.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="every market listed on both venues")
    s.add_argument("--top", type=int, default=40, help="rows to print (all go to the CSV)")
    s.add_argument("--books", type=int, default=60, help="rows to re-price from Polymarket's order book")
    s.add_argument("--min-conf", type=int, default=MIN_CONF_SHOW)
    s.add_argument("--pm-cache-min", type=int, default=60,
                   help="reuse the saved Polymarket snapshot if younger than this (0 = always re-read)")
    s.add_argument("--no-log", action="store_true", help="do not write flagged rows to the prediction log")
    s = sub.add_parser("midterms", help="the 2026 midterm check (same as `python -m argus crossvenue`)")
    s.add_argument("--no-log", action="store_true")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(errors="replace")
    if a.cmd == "midterms":
        res = compare()
        print(format_report(res))
        if not a.no_log:
            rows = log_rows(res)
            log.append(rows)
            print(f"Logged {len(rows)} calls.")
        return
    res = scan(pm_cache_min=a.pm_cache_min, books=a.books, min_conf=a.min_conf)
    text = format_scan(res, a.top)
    print(text)
    SCAN_DIR.mkdir(exist_ok=True)
    stem = SCAN_DIR / f"{res['at'].astimezone(ET):%Y-%m-%d_%H%M}_crossvenue"
    (stem.parent / (stem.name + ".md")).write_text("```\n" + text + "\n```\n", encoding="utf-8")
    write_scan_csv(res, stem.parent / (stem.name + ".csv"))
    print(f"\nSaved: {stem}.md and .csv (all {len(res['rows'])} matches)")
    if not a.no_log:
        rows = scan_rows_for_log(res)
        log.append(rows)
        print(f"Logged {len(rows)} flagged calls to {log.LOG_PATH.name} "
              f"({sum(r['worth_a_look'] == '1' for r in rows)} paper bets).")


if __name__ == "__main__":
    main()
