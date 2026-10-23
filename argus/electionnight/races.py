"""Which races, which official feed, which Kalshi markets.

PAST: results nights the backtest can replay, because the Internet Archive captured the
state's official results feed many times during the count (measured 2026-10-22, see
research/election_night.md for every state checked).
LIVE_2026: the Nov 3 2026 races whose state publishes an official machine-readable feed
this engine can read.

Market questions:
  ("beats", cand)              candidate (or party) finishes first
  ("majority", cand)           candidate gets over 50% (Georgia: wins the primary outright)
  ("margin", a, b, lo, hi)     (a - b) / all votes, in points, is in [lo, hi); None = open end
"""
import re
from datetime import datetime, timezone


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


GA_EV = "https://results.sos.ga.gov/results/public/api/elections/Georgia"
VA_EV = "https://enr.elections.virginia.gov/results/public/api/elections/virginia"
PA_API = "https://www.electionreturns.pa.gov/api/ElectionReturn"
NC_ENR = "https://er.ncsbe.gov/enr"
VA_EXPORT = "https://enr.elections.virginia.gov/cdn/results/virginia"


def margin_question(sub_title, a="rep", b="dem"):
    """Kalshi margin-bucket label -> ("margin", a, b, lo, hi) in points of a minus b.
    'Trump by 2-2.99%' -> [2, 3);  'Trump by 6% or more' -> [6, None);  'Harris by 1-1.99%' -> (-2, -1]
    (treated as [-2, -1): the boundary has zero probability under a smooth curve)."""
    s = sub_title.replace("%", "")
    dem = bool(re.search(r"harris|biden|democrat", s, re.I))
    m = re.search(r"by ([\d.]+)\s*-\s*([\d.]+)", s)
    if m:
        lo, hi = float(m.group(1)), round(float(m.group(2)) + 0.01, 2)
    else:
        m = re.search(r"by ([\d.]+) or more", s)
        if not m:
            return None
        lo, hi = float(m.group(1)), None
    if dem:
        lo, hi = (-hi if hi is not None else None), -lo
    return ("margin", a, b, lo, hi)


PAST = [
    {
        "id": "GA-2024-PRES", "state": "GA", "contest": "President (Georgia)",
        "polls_close": utc(2024, 11, 6, 0, 0), "night_end": utc(2024, 11, 6, 9, 0),
        "feed": "ev", "by": "party", "remaining_type": "eday",
        "cdx": f"{GA_EV}/2024NovGen/ballot-items/01000000-d884-2e72-6367-08dcda4b86b5", "match": r"",
        "prior": {"feed": "ev_registered", "what": "registered voters per county, as published before polls closed",
                  "jurisdictions": "https://results.sos.ga.gov/results/public/api/jurisdictions/Georgia/2024NovGen",
                  "localities": f"{GA_EV}/2024NovGen/localities"},
        "markets": {"PRESPARTYGA-24-R": ("beats", "rep"), "PRESPARTYGA-24-D": ("beats", "dem")},
        "margin_series": "POPVOTEMOVGA",
    },
    {
        "id": "PA-2024-PRES", "state": "PA", "contest": "President (Pennsylvania)",
        "polls_close": utc(2024, 11, 6, 1, 0), "night_end": utc(2024, 11, 6, 9, 0),
        "feed": "pa_county", "by": "party", "remaining_type": "mixed",
        "cdx": f"{PA_API}/GetCountyBreak",
        "match": r"(?=.*officeId=1(&|$))(?=.*electionid=105)(?=.*isactive=1)",
        "prior": {"feed": "pa_county", "url": f"{PA_API}/GetCountyBreak",
                  "params": {"officeId": 1, "districtId": 1, "methodName": "GetCountyBreak",
                             "electionid": 83, "electiontype": "G", "isactive": 0},
                  "what": "2020 presidential result by county (PA Department of State)"},
        "markets": {"PRESPARTYPA-24-R": ("beats", "rep"), "PRESPARTYPA-24-D": ("beats", "dem")},
        "margin_series": "POPVOTEMOVPA",
    },
    {
        "id": "PA-2024-SEN", "state": "PA", "contest": "United States Senator",
        "polls_close": utc(2024, 11, 6, 1, 0), "night_end": utc(2024, 11, 6, 9, 0),
        "feed": "pa_statewide", "office": "United States Senator", "by": "party", "remaining_type": "mixed",
        "cdx": f"{PA_API}/GET",
        # the page asked for this both with electionid=105 and electionid=undefined; same live data
        "match": r"(?=.*GetSummaryData)(?=.*isactive=1)(?!.*electionid=83)",
        # statewide totals only: expected final = 2020 presidential total x (Senate votes / President votes so far)
        "expected": {"ratio_to_office": "President of the United States", "prior_of": "PA-2024-PRES"},
        "markets": {"SENATEPA-24-R": ("beats", "rep"), "SENATEPA-24-D": ("beats", "dem")},
    },
    {
        "id": "NC-2024-PRES", "state": "NC", "contest": "US President (North Carolina)",
        "polls_close": utc(2024, 11, 6, 0, 30), "night_end": utc(2024, 11, 6, 9, 0),
        "feed": "nc_statewide", "by": "party", "remaining_type": "eday",
        "cdx": f"{NC_ENR}/20241105/data/results_0.txt", "match": r"results_0\.txt",
        "contest_match": r"^US PRESIDENT",
        "prior": {"feed": "nc_registered", "url": f"{NC_ENR}/20241105/data/county.txt",
                  "what": "registered voters statewide, from the board's own county file"},
        "markets": {"PRESPARTYNC-24-R": ("beats", "rep"), "PRESPARTYNC-24-D": ("beats", "dem")},
    },
    {
        "id": "NC-2024-GOV", "state": "NC", "contest": "NC Governor",
        "polls_close": utc(2024, 11, 6, 0, 30), "night_end": utc(2024, 11, 6, 9, 0),
        "feed": "nc_statewide", "by": "party", "remaining_type": "eday",
        "cdx": f"{NC_ENR}/20241105/data/results_0.txt", "match": r"results_0\.txt",
        "contest_match": r"^NC GOVERNOR",
        "prior": {"feed": "nc_registered", "url": f"{NC_ENR}/20241105/data/county.txt",
                  "what": "registered voters statewide, from the board's own county file"},
        "markets": {"GOVPARTYNC-24-R": ("beats", "rep"), "GOVPARTYNC-24-D": ("beats", "dem")},
    },
    {
        "id": "VA-2025-GOV", "state": "VA", "contest": "Governor",
        "polls_close": utc(2025, 11, 5, 0, 0), "night_end": utc(2025, 11, 5, 9, 0),
        "by": "party", "remaining_type": "eday",
        "feeds": [{"feed": "ev_export", "cdx": f"{VA_EXPORT}/export-2025-November-General.json", "match": "",
                   "contest_name": "Governor"},
                  {"feed": "ev", "cdx": f"{VA_EV}/2025-November-General/data/ballot-item/01000000-1238-16ac-ab58-08ddfb9ff9ea",
                   "match": ""}],
        "markets": {"GOVPARTYVA-25-R": ("beats", "rep"), "GOVPARTYVA-25-D": ("beats", "dem")},
    },
    {
        "id": "VA-2025-LG", "state": "VA", "contest": "Lieutenant Governor",
        "polls_close": utc(2025, 11, 5, 0, 0), "night_end": utc(2025, 11, 5, 9, 0),
        "by": "party", "remaining_type": "eday",
        "feed": "ev_export", "cdx": f"{VA_EXPORT}/export-2025-November-General.json", "match": "",
        "contest_name": "Lieutenant Governor",
        "markets": {"KXLTGOVVA-25-R": ("beats", "rep"), "KXLTGOVVA-25-D": ("beats", "dem")},
    },
    {
        "id": "VA-2025-AG", "state": "VA", "contest": "Attorney General",
        "polls_close": utc(2025, 11, 5, 0, 0), "night_end": utc(2025, 11, 5, 9, 0),
        "by": "party", "remaining_type": "eday",
        "feed": "ev_export", "cdx": f"{VA_EXPORT}/export-2025-November-General.json", "match": "",
        "contest_name": "Attorney General",
        "markets": {"KXATTYGENVA-25-R": ("beats", "rep"), "KXATTYGENVA-25-D": ("beats", "dem")},
    },
    {
        "id": "GA-2025-PSC3", "state": "GA", "contest": "PSC - District 3 (statewide)",
        "polls_close": utc(2025, 11, 5, 0, 0), "night_end": utc(2025, 11, 5, 9, 0),
        "feed": "ev", "by": "name", "remaining_type": "eday",
        "cdx": f"{GA_EV}/MunicipalGeneralSpecialElectionPSC11042025/data/ballot-item/01000000-b5ce-a69b-c49e-08de06bd5e59",
        "match": "",
        "markets": {"KXGAPUBLICSERVICE3-25NOV04-PHUB": ("beats", "hubbard"),
                    "KXGAPUBLICSERVICE3-25NOV04-FJON": ("beats", "johnson")},
    },
    {
        "id": "VA-2026-REF", "state": "VA", "contest": "Redistricting amendment (April 21, 2026)",
        "polls_close": utc(2026, 4, 21, 23, 0), "night_end": utc(2026, 4, 22, 9, 0),
        "by": "name", "remaining_type": "eday",
        "feeds": [{"feed": "ev", "cdx": f"{VA_EV}/2026-April-21-Special/data/ballot-item/01000000-b74a-1167-ae8c-08de7ea0aabf",
                   "match": ""},
                  {"feed": "ev_export", "cdx": f"{VA_EXPORT}/export-2026-April-21-Special.json", "match": "",
                   "contest_name": "Proposed Constitutional Amendment"}],
        "markets": {"KXVIRGINIAREDISTRICTING-26": ("beats", "yes")},
    },
]


def race(rid):
    return next(r for r in PAST if r["id"] == rid)
