"""Official results, as they stood at a known moment.

Live: the state's own election-night results feed, read directly.
History: the Internet Archive's timestamped captures of that same official feed. A capture
is a copy of what the state's server returned at the capture time, so the counts in it were
public AT OR BEFORE that time. That capture time is the only clock the backtest trusts
(`Snapshot.t_known`); the state's own "as of" stamp is kept for reference only.

Every result becomes a Snapshot: per contest, per unit (a county, or the whole state when
only statewide totals exist), votes per candidate split by vote type ("eday" = cast on
election day, "other" = early, mail, absentee, provisional), plus precincts reporting
when the state publishes it.

Feeds handled (format names used in races.py):
  ev        Enhanced Voting platform: Georgia (results.sos.ga.gov), Virginia (enr.elections.virginia.gov).
            /ballot-items/{id} has every county; /ballot-items and /data have statewide totals.
  pa        Pennsylvania Department of State (www.electionreturns.pa.gov): GetCountyBreak (per county)
            and GetSummaryData (statewide), both split Election Day / Mail-in / Provisional.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import http

CDX = "https://web.archive.org/cdx/search/cdx"


# ---------------- the snapshot ----------------

@dataclass
class Unit:
    votes: dict = field(default_factory=dict)       # candidate key -> {"eday": n, "other": n}
    precincts: tuple = None                         # (reporting, total) or None

    def total(self, mode=None):
        return sum(v.get(mode, 0) if mode else sum(v.values()) for v in self.votes.values())

    def cand(self, c, mode=None):
        v = self.votes.get(c, {})
        return v.get(mode, 0) if mode else sum(v.values())


@dataclass
class Snapshot:
    t_known: datetime            # the earliest moment we can PROVE these counts were public
    contest: str
    units: dict                  # unit name -> Unit
    as_of: datetime = None       # the state's own timestamp (informational)
    source: str = ""
    units_reporting: tuple = None   # (reporting, total) at contest level, when given

    def total(self, mode=None):
        return sum(u.total(mode) for u in self.units.values())

    def cand(self, c, mode=None):
        return sum(u.cand(c, mode) for u in self.units.values())

    def candidates(self):
        return sorted({c for u in self.units.values() for c in u.votes})


PARTY = {"dem": "dem", "democratic": "dem", "d": "dem", "rep": "rep", "republican": "rep", "r": "rep"}


def cand_key(name, party=None, by="name"):
    """How a candidate is keyed. by="party": 'dem' / 'rep' (other parties keep their own name),
    so a race can be compared with the last election's different candidates.
    by="name": last name, 'Donald J. Trump (Rep)' -> 'trump'; 'BIDEN, JOSEPH R JR' -> 'biden'."""
    if by == "party" and party:
        p = party.strip().lower()
        return PARTY.get(p, p)
    s = (name or "").split(",")[0] if "," in (name or "") else (name or "")
    s = re.sub(r"\(.*?\)", "", s).strip().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\.?$", "", s).strip()
    parts = [p for p in re.split(r"\s+", s) if p]
    return parts[-1].strip(".") if parts else ""


def mode_of(group_name):
    g = (group_name or "").lower()
    return "eday" if ("election day" in g or g == "eday") else "other"


def parse_ts(s):
    if not s:
        return None
    s = s.rstrip("Z")
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def wayback_time(ts14):
    return datetime.strptime(ts14, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


# ---------------- Internet Archive captures ----------------

def captures(url, start, end, prefix=False):
    """[(capture time UTC, original url)] of successful captures of an official URL, oldest first.
    `start`/`end` are datetimes (UTC). Identical consecutive captures are dropped."""
    params = {"url": url, "from": start.strftime("%Y%m%d%H%M%S"), "to": end.strftime("%Y%m%d%H%M%S"),
              "output": "json", "fl": "timestamp,original,digest", "filter": "statuscode:200"}
    if prefix:
        params["matchType"] = "prefix"
    rows = http.public(CDX, params, cache=end < datetime.now(timezone.utc)) or []
    out, last = [], {}
    for ts, orig, digest in sorted(rows[1:], key=lambda r: r[0]):
        if last.get(orig) == digest:
            continue
        last[orig] = digest
        out.append((wayback_time(ts), orig))
    return out


def archived(t, url, as_text=False):
    """The exact bytes the official server returned at capture time t ('id_' = no archive rewriting)."""
    return http.public(f"https://web.archive.org/web/{t.strftime('%Y%m%d%H%M%S')}id_/{url}",
                       cache=True, as_text=as_text)


# ---------------- Enhanced Voting (Georgia, Virginia) ----------------

def _txt(x):
    return (x or [{}])[0].get("text", "") if isinstance(x, list) else (x or "")


def _ev_votes(ballot_options, keep=None, by="name"):
    votes = {}
    for o in ballot_options or []:
        if o.get("isWriteIn"):
            continue
        k = cand_key(_txt(o.get("name")), (o.get("party") or {}).get("abbreviation"), by)
        if keep and k not in keep:
            k = "_other"
        v = votes.setdefault(k, {"eday": 0, "other": 0})
        groups = o.get("groupResults") or []
        if groups:
            for g in groups:
                v[mode_of(_txt(g.get("groupName")))] += int(g.get("voteCount") or 0)
        else:
            v["other"] += int(o.get("voteCount") or 0)
    return votes


def ev_contest(doc, t_known, source="", keep=None, by="name"):
    """One ballot-item document -> Snapshot with every county as a unit.
    Two layouts: 2024 (/ballot-items/{id}: the contest itself) and 2025+ (/data/ballot-item/{id}:
    the contest under "ballotItemWithBreakdown", plus registered voters and precinct shares per locality)."""
    registered, frac = {}, {}
    if "ballotItemWithBreakdown" in doc:
        for v in doc.get("voterRegistration") or []:
            registered[(v.get("id") or "").lower()] = v.get("total")
        for c in (doc.get("jurisdiction") or {}).get("childLocalities") or []:
            if c.get("precinctsReporting") is not None:
                frac[(c.get("id") or "").lower()] = float(c["precinctsReporting"])
        doc = doc["ballotItemWithBreakdown"] or {}
    rs = doc.get("reportingStatus") or {}
    units, regd = {}, {}
    for b in doc.get("breakdownResults") or []:
        loc = b.get("locality") or {}
        name, lid = _txt(loc.get("name")), (loc.get("id") or "").lower()
        p = b.get("precinctReportingStatus") or {}
        if p.get("totalUnits"):
            pr = (int(p.get("reportingUnits") or 0), int(p["totalUnits"]))
        elif lid in frac:
            pr = (round(frac[lid] * 1000), 1000)
        else:
            pr = None
        units[name] = Unit(_ev_votes(b.get("ballotOptions"), keep, by), pr)
        if registered.get(lid):
            regd[name] = float(registered[lid])
    if not units:     # statewide-only document
        units["STATE"] = Unit(_ev_votes((doc.get("summaryResults") or {}).get("ballotOptions"), keep, by))
    snap = Snapshot(t_known, _txt(doc.get("name")), units, parse_ts(rs.get("asOf")), source,
                    (rs.get("reportingUnits"), rs.get("totalUnits")) if rs.get("totalUnits") else None)
    snap.registered = regd
    return snap


def ev_statewide(doc, t_known, contest_match, source="", keep=None, by="name"):
    """The /ballot-items list (or /data) document -> Snapshot of one contest, statewide totals only."""
    items = doc.get("data") if isinstance(doc, dict) else doc
    if isinstance(items, dict):
        items = items.get("ballotItems") or []
    for it in items or []:
        if re.search(contest_match, _txt(it.get("name")), re.I):
            it = dict(it)
            it.pop("breakdownResults", None)
            return ev_contest(it, t_known, source, keep, by)
    return None


def ev_registered(jurisdictions_doc, localities_doc):
    """{county name: registered voters}, from the feed's county list and its per-county election list.
    Both are published before polls close, so this is a pre-election number."""
    names = {c["id"].lower(): _txt(c.get("name")) for c in jurisdictions_doc.get("childLocalities", [])}
    out = {}
    for e in localities_doc or []:
        n = names.get((e.get("jurisdictionId") or "").lower())
        if n and e.get("totalVoters"):
            out[n] = float(e["totalVoters"])
    return out


def ev_export(doc, t_known, contest_name, source="", keep=None, by="name"):
    """The feed's full export file (export-<election>.json) -> Snapshot of one contest by locality."""
    units = {}
    for loc in doc.get("localResults") or []:
        for it in loc.get("ballotItems") or []:
            if it.get("name", "").strip().lower() != contest_name.lower():
                continue
            votes = {}
            for o in it.get("ballotOptions") or []:
                if "write" in (o.get("name") or "").lower():
                    continue
                k = cand_key(o.get("name"), o.get("politicalParty"), by)
                if keep and k not in keep:
                    k = "_other"
                v = votes.setdefault(k, {"eday": 0, "other": 0})
                for g in o.get("groupResults") or []:
                    v[mode_of(g.get("groupName"))] += int(g.get("voteCount") or 0)
            tot = it.get("precinctsParticipating")
            units[loc.get("name", "").title()] = Unit(
                votes, (int(it.get("precinctsReporting") or 0), int(tot)) if tot else None)
    if not units:
        return None
    return Snapshot(t_known, contest_name, units, parse_ts(doc.get("createdAt")), source)


# ---------------- North Carolina ----------------

def nc_statewide(rows, t_known, contest_match, source="", keep=None, by="name"):
    """results_0.txt (statewide, every contest) -> Snapshot of one contest, unit STATE.
    Vote types: evc = election day; ovc = one-stop early, avc = absentee by mail, pvc = provisional.
    prt/ptl = precincts reporting / total for the contest."""
    if isinstance(rows, str):
        rows = json.loads(rows)
    mine = [r for r in rows if re.search(contest_match, r.get("cnm", ""), re.I)]
    if not mine:
        return None
    votes = {}
    for r in mine:
        if "write-in" in (r.get("bnm") or "").lower():
            continue
        k = cand_key(r.get("bnm"), r.get("pty"), by)
        if keep and k not in keep:
            k = "_other"
        v = votes.setdefault(k, {"eday": 0, "other": 0})
        v["eday"] += int(r.get("evc") or 0)
        v["other"] += int(r.get("ovc") or 0) + int(r.get("avc") or 0) + int(r.get("pvc") or 0)
    prt, ptl = int(mine[0].get("prt") or 0), int(mine[0].get("ptl") or 0)
    return Snapshot(t_known, mine[0]["cnm"], {"STATE": Unit(votes, (prt, ptl) if ptl else None)}, None, source,
                    (prt, ptl) if ptl else None)


def nc_registered(rows):
    """county.txt -> {"STATE": registered voters} (the cid 0 row; registration is fixed by election day)."""
    if isinstance(rows, str):
        rows = json.loads(rows)
    for r in rows:
        if str(r.get("cid")) == "0" and r.get("rtl"):
            return {"STATE": float(r["rtl"])}
    return {}


# ---------------- Pennsylvania ----------------

def _pa_votes(rows, keep=None, by="name"):
    votes = {}
    for r in rows:
        k = cand_key(r.get("CandidateName"), r.get("PartyName"), by)
        if keep and k not in keep:
            k = "_other"
        v = votes.setdefault(k, {"eday": 0, "other": 0})
        v["eday"] += int(r.get("ElectionDayVotes") or 0)
        v["other"] += int(r.get("MailInVotes") or 0) + int(r.get("ProvisionalVotes") or 0)
    return votes


def _pa_doc_any(doc):
    if isinstance(doc, str):
        doc = json.loads(doc)
        if isinstance(doc, str):        # the API double-encodes its JSON
            doc = json.loads(doc)
    return doc or {}


def _pa_doc(doc):
    return _pa_doc_any(doc).get("Election") or {}


def pa_county(doc, t_known, contest, source="", keep=None, by="name"):
    """GetCountyBreak document -> Snapshot with each county as a unit."""
    units = {}
    for block in _pa_doc(doc).get("Statewide", []):
        for county, rows in block.items():
            units[county.title()] = Unit(_pa_votes(rows, keep, by))
    return Snapshot(t_known, contest, units, None, source)


def pa_statewide(doc, t_known, office, source="", keep=None, by="name"):
    """GetSummaryData document -> Snapshot of one office, statewide totals (unit STATE)."""
    for name, blocks in _pa_doc(doc).items():
        if name.lower() == office.lower():
            rows = [r for b in blocks for r in b.get("Statewide", [])]
            return Snapshot(t_known, name, {"STATE": Unit(_pa_votes(rows, keep, by))}, None, source)
    return None
