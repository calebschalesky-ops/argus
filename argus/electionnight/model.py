"""P(candidate wins | votes counted so far, what is still out, pre-election expectation).

Plain words, one county at a time:
  1. How many votes will this county cast in the end?  ("expected final")
       - If the state publishes precincts reporting (Georgia): the early/mail vote is in once the
         county reports at all, and the election-day vote grows with precincts:
         election-day final = election-day counted / share of precincts in.
         A county with no precincts in yet gets the election-day-to-early ratio of the counties
         that are fully in (or RATIO_EDAY_PRIOR before any are).
       - A county with nothing counted yet: its registered voters x the turnout rate of the counties
         that are fully in (TURNOUT_PRIOR before any are), when the feed lists registered voters (Georgia).
       - Else, if we have the county's total in the last comparable election (Pennsylvania 2020):
         expected final = that total x a turnout factor (pre-election expectation: 1.0).
       - Statewide totals only (North Carolina's results file): registered voters x TURNOUT_PRIOR,
         or the race's pre-election expected total (Pennsylvania Senate: the presidential
         expectation x the Senate's share of presidential votes so far).
  2. Votes still out = expected final - counted (never below zero).
  3. How will those votes split?  Like the votes of the SAME TYPE already counted in that county
     (election-day votes look like election-day votes). A county with no election-day votes yet
     takes its early-vote split plus the election-day shift seen in counties that have both.
     A county with nothing counted takes its last-election split plus the statewide swing
     (when we have the last election), else the statewide split so far.
  4. Add counted + projected per candidate. The uncertainty is a normal curve with three parts:
     each county's leftover votes can split differently (SD_UNIT), all leftover votes can lean the
     same way together (SD_COMMON, the "everyone got the late vote wrong" error), and turnout can
     be off (SD_TURNOUT). P(win) = chance the projected margin stays above zero.

The constants below were fixed BEFORE the backtest was run (see research/election_night.md) and are
not tuned to it. They are guesses about vote-counting, written down first so the test is honest.
"""
import math
from dataclasses import dataclass

SD_UNIT = 0.08          # one county's leftover votes: SD of their margin (share A - share B), own data
SD_UNIT_BORROWED = 0.15 # same, when the split is borrowed (statewide / last election)
SD_COMMON = 0.04        # all leftover votes shifting together, SD of margin
SD_TURNOUT = 0.08       # expected-final error, as a share of the county's expected final
RATIO_EDAY_PRIOR = 0.30 # election-day votes per early/mail vote before any county is fully in
MIN_PRECINCT_SHARE = 0.10   # below this, a county's precinct count is too thin to scale up
TURNOUT_PRIOR = 0.65    # ballots per registered voter before any county is fully in (added 2026-10-22, see research note)


def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


@dataclass
class Projection:
    final: dict          # candidate -> projected final votes
    remaining: float     # projected votes still out
    counted: float       # votes counted so far
    sd_margin: float     # SD (in votes) of any two-candidate margin
    share_in: float      # counted / projected final
    units_in: int        # units with any votes counted
    units: int

    def total(self):
        return sum(self.final.values())


def _split(votes, mode=None):
    """Share of each candidate among this unit's votes of one type (None = all types)."""
    tot = sum((v.get(mode, 0) if mode else sum(v.values())) for v in votes.values())
    if tot <= 0:
        return None
    return {c: (v.get(mode, 0) if mode else sum(v.values())) / tot for c, v in votes.items()}


def _from_prior(p, turnout, turnout_factor):
    """Expected final for a unit from pre-election data: last election's total, or registered x turnout."""
    if not p:
        return None
    if p.get("total"):
        return p["total"] * turnout_factor
    if p.get("registered"):
        return p["registered"] * turnout
    return None


def _vote_weighted(pairs):
    num = sum(w * x for w, x in pairs)
    den = sum(w for w, _ in pairs)
    return num / den if den else 0.0


def project(snap, prior=None, expected_total=None, turnout_factor=1.0, remaining_type="eday"):
    """Project the final count of one contest.

    prior: {unit: {"total": n, "split": {cand: share}}} from the last comparable election (optional).
    expected_total: the race's pre-election expected total (used for statewide-only snapshots).
    remaining_type: "eday" when the votes still out are mostly election-day votes (Georgia reports
                    early votes first by law); "mixed" when either kind can still be out (Pennsylvania).
    """
    prior = prior or {}
    cands = snap.candidates()
    units = snap.units

    # election-day vs early ratio and the election-day shift, from counties that have both
    full_names = [n for n, u in units.items()
                  if u.precincts and u.precincts[1] and u.precincts[0] >= u.precincts[1] * 0.95]
    full = [units[n] for n in full_names]
    ratios = [(u.total("other"), u.total("eday") / u.total("other")) for u in full if u.total("other") > 0]
    ratio_eday = _vote_weighted(ratios) if ratios else RATIO_EDAY_PRIOR
    regd = [(prior[n]["registered"], units[n].total() / prior[n]["registered"]) for n in full_names
            if prior.get(n, {}).get("registered")]
    turnout = _vote_weighted(regd) if regd else TURNOUT_PRIOR
    shift = {}
    both = [u for u in units.values() if u.total("eday") > 0 and u.total("other") > 0]
    for c in cands:
        shift[c] = _vote_weighted([(u.total("eday"), _split(u.votes, "eday")[c] - _split(u.votes, "other")[c])
                                   for u in both]) if both else 0.0

    state_split = _split({c: {"x": snap.cand(c)} for c in cands}) or {c: 1 / len(cands) for c in cands}
    # swing vs the last election, from units that have votes and a prior split
    swing = {}
    sw_units = [(n, u) for n, u in units.items() if u.total() > 0 and n in prior and prior[n].get("split")]
    for c in cands:
        swing[c] = _vote_weighted([(u.total(), _split(u.votes)[c] - prior[n]["split"].get(c, 0.0))
                                   for n, u in sw_units]) if sw_units else 0.0

    final = {c: float(snap.cand(c)) for c in cands}
    var_unit, remaining_total, units_in = 0.0, 0.0, 0
    for name, u in units.items():
        counted = u.total()
        units_in += counted > 0
        # 1. expected final for this unit
        if name == "STATE" and prior.get(name, {}).get("registered"):
            # statewide totals only: counties post their early votes at different times, so the
            # precinct share says little. Use registered voters x expected turnout.
            f = u.precincts[0] / u.precincts[1] if u.precincts and u.precincts[1] else 0.0
            exp = counted if f >= 0.999 else max(counted, prior[name]["registered"] * turnout)
        elif u.precincts and u.precincts[1]:
            f = u.precincts[0] / u.precincts[1]
            if counted == 0:
                exp = _from_prior(prior.get(name), turnout, turnout_factor)
            elif f >= 0.999:
                exp = counted
            elif f >= MIN_PRECINCT_SHARE and u.total("eday") > 0:
                exp = u.total("other") + u.total("eday") / f
            else:
                exp = u.total("other") * (1 + ratio_eday)
        elif name in prior:
            exp = _from_prior(prior[name], turnout, turnout_factor)
        elif name == "STATE" and expected_total:
            exp = expected_total * turnout_factor
        else:
            exp = None
        if exp is None:     # no way to size it: assume the median reporting unit (flagged by units_in)
            sizes = sorted(x.total() for x in units.values() if x.total() > 0)
            exp = sizes[len(sizes) // 2] if sizes else 0.0
        left = max(exp - counted, 0.0)
        remaining_total += left
        if left <= 0:
            continue
        # 3. how the leftover splits
        borrowed = False
        if remaining_type == "eday" and u.total("eday") > 0:
            sp = _split(u.votes, "eday")
        elif remaining_type == "eday" and u.total("other") > 0:
            sp = {c: _split(u.votes, "other")[c] + shift[c] for c in cands}
        elif counted > 0:
            sp = _split(u.votes)
        elif name in prior and prior[name].get("split"):
            sp = {c: prior[name]["split"].get(c, 0.0) + swing[c] for c in cands}
            borrowed = True
        else:
            sp, borrowed = dict(state_split), True
        sp = {c: min(max(sp.get(c, 0.0), 0.0), 1.0) for c in cands}
        s = sum(sp.values()) or 1.0
        for c in cands:
            final[c] += left * sp[c] / s
        sd = SD_UNIT_BORROWED if borrowed else SD_UNIT
        # margin SD from this unit: split noise + its own turnout noise (scaled by its lean)
        lean = abs(max(sp.values()) - sorted(sp.values())[-2]) if len(sp) > 1 else 0.0
        var_unit += (left * sd) ** 2 + (exp * SD_TURNOUT * lean) ** 2
    var = var_unit + (remaining_total * SD_COMMON) ** 2
    counted_all = float(snap.total())
    tot = counted_all + remaining_total
    return Projection(final, remaining_total, counted_all, math.sqrt(var),
                      counted_all / tot if tot else 0.0, units_in, len(units))


# ---------------- market questions ----------------

def p_beats(proj, a, rivals=None):
    """P(candidate a finishes ahead of every rival). Uses the strongest rival (two-way margin)."""
    rivals = [c for c in (rivals or proj.final) if c != a and not c.startswith("_")]
    if a not in proj.final or not rivals:
        return None
    b = max(rivals, key=lambda c: proj.final[c])
    m = proj.final[a] - proj.final[b]
    if proj.sd_margin <= 0:
        return 1.0 if m > 0 else 0.0
    return _phi(m / proj.sd_margin)


def p_majority(proj, a):
    """P(candidate a gets more than half of all votes) - Georgia's 'wins the primary outright'."""
    if a not in proj.final:
        return None
    t = proj.total()
    m = 2 * proj.final[a] - t           # a minus everyone else
    if proj.sd_margin <= 0:
        return 1.0 if m > 0 else 0.0
    return _phi(m / proj.sd_margin)


def p_margin_between(proj, a, b, lo, hi):
    """P(lo <= (a - b) / all votes x 100 < hi), margins in percentage points; None = open end."""
    t = proj.total()
    if not t or a not in proj.final or b not in proj.final:
        return None
    mu = (proj.final[a] - proj.final[b]) / t * 100
    sd = max(proj.sd_margin / t * 100, 1e-6)
    up = 1.0 if hi is None else _phi((hi - mu) / sd)
    dn = 0.0 if lo is None else _phi((lo - mu) / sd)
    return max(up - dn, 0.0)
