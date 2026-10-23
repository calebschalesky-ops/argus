"""Election night, live: read each state's official results feed, project, compare with Kalshi, log paper calls.

`python -m argus.electionnight live`            poll every 120 s until stopped (Ctrl+C)
`python -m argus.electionnight live --once`     one pass (use it to check the feeds before the night)

Each pass, per race: one GET of the state's feed (two or three for Georgia/Virginia: the contest
list and the contest's county breakdown), one GET per Kalshi market. That is about 15 requests
every 2 minutes in total, less than one browser tab of each results page refreshing itself.
None of the four sites publishes a rate limit or a robots.txt rule (all four robots.txt were empty,
2026-10-22), so 120 s is our own choice; never go below 60 s.

The count's timestamp is the moment our request came back (t_known), never the state's "as of".
Paper only: a call is a row in electionnight_log.csv (engine=electionnight-live). Argus never trades.
"""
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .. import fees, log
from . import LOG_PATH, backtest, http, model, sources

ENGINE = "electionnight-live"
DEFAULT_INTERVAL_S = 120
MIN_INTERVAL_S = 60
ELECTION_DATE = "2026-11-03"
PT = ZoneInfo("America/Los_Angeles")

GA_EV = "https://results.sos.ga.gov/results/public/api"
VA_EV = "https://enr.elections.virginia.gov/results/public/api"
PA_API = "https://www.electionreturns.pa.gov/api"
NC_ENR = "https://er.ncsbe.gov/enr/20261103/data"

# The Nov 3 2026 races with an official machine-readable feed this engine reads (checked 2026-10-22).
LIVE_2026 = [
    {"id": "GA-2026-GOV", "polls_close": datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc), "state": "GA", "feed": "ev", "base": GA_EV, "jur": "Georgia",
     "contest_match": r"^governor\b", "by": "party", "remaining_type": "eday",
     "markets": {"GOVPARTYGA-26-R": ("beats", "rep"), "GOVPARTYGA-26-D": ("beats", "dem")},
     "polls_close_et": "7:00 PM"},
    {"id": "GA-2026-SEN", "polls_close": datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc), "state": "GA", "feed": "ev", "base": GA_EV, "jur": "Georgia",
     "contest_match": r"(us|united states) senate", "by": "party", "remaining_type": "eday",
     "markets": {"SENATEGA-26-D": ("beats", "dem"), "SENATEGA-26-R": ("beats", "rep")},
     "polls_close_et": "7:00 PM"},
    {"id": "VA-2026-SEN", "polls_close": datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc), "state": "VA", "feed": "ev", "base": VA_EV, "jur": "virginia",
     "contest_match": r"united states senate|^senate$", "by": "party", "remaining_type": "eday",
     "markets": {"SENATEVA-26-D": ("beats", "dem"), "SENATEVA-26-R": ("beats", "rep")},
     "polls_close_et": "7:00 PM"},
    {"id": "NC-2026-SEN", "polls_close": datetime(2026, 11, 4, 0, 30, tzinfo=timezone.utc), "state": "NC", "feed": "nc", "contest_match": r"^US SENATE", "by": "party",
     "remaining_type": "eday",
     "markets": {"SENATENC-26-D": ("beats", "dem"), "SENATENC-26-R": ("beats", "rep")},
     "polls_close_et": "7:30 PM"},
    {"id": "PA-2026-GOV", "polls_close": datetime(2026, 11, 4, 1, 0, tzinfo=timezone.utc), "state": "PA", "feed": "pa", "office": "Governor", "by": "party",
     "remaining_type": "mixed", "prior_office": "Governor", "prior_year": 2022,
     "markets": {"GOVPARTYPA-26-D": ("beats", "dem"), "GOVPARTYPA-26-R": ("beats", "rep")},
     "polls_close_et": "8:00 PM"},
]


def now():
    return datetime.now(timezone.utc)


# ---------------- official feeds, live ----------------

def ev_election_id(rc):
    d = http.public(f"{rc['base']}/jurisdictions/{rc['jur']}") or {}
    for e in d.get("elections") or []:
        if e.get("electionDate") == ELECTION_DATE:
            return e["publicElectionId"]
    return None


def ev_snapshot(rc, state):
    """Find the contest once (cached in `state`), then read its county breakdown."""
    if "eid" not in state:
        eid = ev_election_id(rc)
        if not eid:
            return None, "election not posted on the state's results site yet"
        state["eid"] = eid
    base = f"{rc['base']}/elections/{rc['jur']}/{state['eid']}/data"
    if "item" not in state:
        d = http.public(base) or {}
        items = d.get("ballotItems") or []
        hit = [i for i in items if re.search(rc["contest_match"], sources._txt(i.get("name")), re.I)
               and not re.search(r"state senate|special|unexpired", sources._txt(i.get("name")), re.I)]
        if not hit:
            return None, f"no contest matching /{rc['contest_match']}/ among {len(items)}"
        state["item"] = hit[0]["id"]
    doc = http.public(f"{base}/ballot-item/{state['item']}")
    t = now()
    return sources.ev_contest(doc, t, f"{base}/ballot-item/{state['item']}", by=rc["by"]), ""


def nc_snapshot(rc, state):
    if "registered" not in state:
        state["registered"] = sources.nc_registered(http.public(f"{NC_ENR}/county.txt", as_text=True) or "[]")
    raw = http.public(f"{NC_ENR}/results_0.txt", as_text=True)
    t = now()
    snap = sources.nc_statewide(raw or "[]", t, rc["contest_match"], f"{NC_ENR}/results_0.txt", by=rc["by"])
    if snap:
        snap.registered = state["registered"]
    return snap, "" if snap else "contest not in the NC results file yet"


def _pa(method, **params):
    return http.public(f"{PA_API}/ElectionReturn/{'GET' if method == 'GetSummaryData' else method}",
                       dict(methodName=method, **params), as_text=True)


def pa_snapshot(rc, state):
    if "eid" not in state:
        lst = sources._pa_doc_any(http.public(f"{PA_API}/Reports/GetElectionList", as_text=True))
        rows = lst.get("Table", [])
        cur = [r for r in rows if r.get("ElectionDate") == "11/03/2026" and r.get("ElectionType") == "G"]
        old = [r for r in rows if str(r.get("ElectionYear")) == str(rc["prior_year"]) and r.get("ElectionType") == "G"]
        if not cur:
            return None, "2026 general not in PA's election list"
        state["eid"] = cur[0]["Electionid"]
        state["prior_eid"] = old[0]["Electionid"] if old else None
    summ = _pa("GetSummaryData", electionid=state["eid"], electiontype="G", isactive=1)
    office = next((v for k, v in sources._pa_doc(summ).items() if k.lower() == rc["office"].lower()), None)
    if not office:
        return None, f"office {rc['office']} not in PA's summary yet"
    oid = office[0].get("OfficeId") or office[0]["Statewide"][0].get("OfficeId", "")
    cb = _pa("GetCountyBreak", officeId=oid, districtId=1, electionid=state["eid"], electiontype="G", isactive=1)
    t = now()
    if "prior" not in state and state.get("prior_eid"):
        old = _pa("GetCountyBreak", officeId=oid, districtId=1, electionid=state["prior_eid"],
                  electiontype="G", isactive=0)
        ps = sources.pa_county(old, t, "prior", by=rc["by"])
        state["prior"] = {n: {"total": u.total(), "split": {c: u.cand(c) / u.total() for c in u.votes}}
                          for n, u in ps.units.items() if u.total()}
    return sources.pa_county(cb, t, rc["office"], "PA GetCountyBreak", by=rc["by"]), ""


def snapshot(rc, state):
    try:
        return {"ev": ev_snapshot, "nc": nc_snapshot, "pa": pa_snapshot}[rc["feed"]](rc, state)
    except Exception as e:          # a feed hiccup on the night must not stop the other races
        return None, f"feed error: {type(e).__name__}: {e}"[:160]


# ---------------- Kalshi ----------------

def kalshi_quote(ticker):
    m = (http.kalshi(f"/markets/{ticker}") or {}).get("market") or {}
    f = lambda k: backtest._f(m.get(k + "_dollars", m.get(k)))
    bid, ask = f("yes_bid"), f("yes_ask")
    if bid is not None and bid > 1:      # older payloads give cents
        bid, ask = bid / 100, (ask or 0) / 100
    return m, bid, ask


# ---------------- one pass ----------------

def already_bet(path=LOG_PATH):
    return {r["market_ticker"] for r in log.read_log(path) if r.get("engine") == ENGINE and r["paper_contracts"] == "1"}


def run_once(races=LIVE_2026, states=None, path=LOG_PATH, write=True, out=print):
    states = states if states is not None else {}
    bet = already_bet(path)
    rows, lines = [], []
    for rc in races:
        st = states.setdefault(rc["id"], {})
        snap, why = snapshot(rc, st)
        if snap and snap.total() and now() < rc["polls_close"]:
            # PA's feed carried placeholder counts two weeks early (2026-10-22): nothing counts before polls close
            lines.append(f"{rc['id']:<12} waiting: polls close {rc['polls_close_et']} ET "
                         f"(feed shows {int(snap.total()):,} votes: test data, ignored)")
            continue
        if not snap or not snap.total():
            lines.append(f"{rc['id']:<12} waiting: {why or 'no votes counted yet'}")
            continue
        prior = st.get("prior") or {n: {"registered": v} for n, v in getattr(snap, "registered", {}).items()}
        proj = model.project(snap, prior, remaining_type=rc["remaining_type"])
        top = sorted(proj.final, key=lambda c: -proj.final[c])[:2]
        tot = proj.total() or 1
        head = (f"{rc['id']:<12} counted {int(proj.counted):>9,} of ~{int(tot):>9,} ({proj.share_in * 100:4.0f}%)  "
                f"{top[0]} {'+' if len(top) > 1 else ''}{((proj.final[top[0]] - proj.final[top[1]]) / tot * 100 if len(top) > 1 else 0):.1f} "
                f"+/- {proj.sd_margin / tot * 100:.1f}  units {proj.units_in}/{proj.units}")
        lines.append(head + ("" if backtest.gate(proj) else "   [gate: not enough counted yet]"))
        if not backtest.gate(proj):
            continue
        for ticker, q in rc["markets"].items():
            m, bid, ask = kalshi_quote(ticker)
            py = backtest.p_yes(proj, q)
            if py is None or not bid or not ask or ask - bid > backtest.MAX_SPREAD + 1e-9:
                lines.append(f"    {ticker:<18} no usable book (bid {bid} ask {ask})")
                continue
            side, p_side, qt, gap = backtest.decide(py, bid, ask)
            take = ticker not in bet and gap >= backtest.MIN_GAP_PTS - 1e-9 and qt.allowed
            flag = "PAPER CALL" if take else (qt.band.reason(qt.ask) if gap >= backtest.MIN_GAP_PTS else "-")
            lines.append(f"    {ticker:<18} model YES {py * 100:5.1f}%  Kalshi {bid:.2f}/{ask:.2f}  "
                         f"best {side} gap {gap:+5.1f} after fee  {flag}")
            rows.append({
                "logged_at_utc": snap.t_known.strftime("%Y-%m-%dT%H:%M:%SZ"), "engine": ENGINE,
                "series": ticker.split("-")[0], "event_ticker": m.get("event_ticker", ticker.rsplit("-", 1)[0]),
                "market_ticker": ticker, "range_label": (m.get("yes_sub_title") or "")[:40], "side": side,
                "model_prob": f"{p_side:.4f}", "market_price": f"{qt.ask:.4f}", "gap_pts": f"{gap:+.1f}",
                "worth_a_look": "1" if take else "0", "paper_contracts": "1" if take else "0",
                "fee_per_contract": f"{qt.taker_fee:.2f}", "anchor_price": f"{proj.share_in:.3f}",
                "anchor_kind": "share_counted", "close_time_utc": m.get("close_time", ""),
                "notes": "; ".join([f"race={rc['id']}", f"tier={'county' if proj.units > 1 else 'statewide'}",
                                    f"p_yes={py:.4f}", f"yes_mid={(bid + ask) / 2:.4f}", f"yes_bid={bid:.2f}",
                                    f"yes_ask={ask:.2f}", f"t_known={snap.t_known:%Y-%m-%dT%H:%M:%SZ}",
                                    f"counted={int(proj.counted)}", f"projected={int(tot)}",
                                    f"units_in={proj.units_in}/{proj.units}", f"source={snap.source}"]),
            })
            if take:
                bet.add(ticker)
    out(f"--- {now():%Y-%m-%d %H:%M:%S} UTC ({now().astimezone(PT):%I:%M %p} Pacific) ---")
    for x in lines:
        out(x)
    if write and rows:
        log.append(rows, path)
    return rows


def run(interval=DEFAULT_INTERVAL_S, once=False, write=True, only=None):
    interval = max(interval, MIN_INTERVAL_S)
    races = [r for r in LIVE_2026 if not only or r["id"] in only]
    states = {}
    while True:
        run_once(races, states, write=write)
        if once:
            return
        time.sleep(interval)


# ---------------- settle ----------------

def settle(path=LOG_PATH):
    """Fill results for pending live rows from Kalshi (paper P/L uses the fee stored on each row)."""
    rows = log.read_log(path)
    done, cache = 0, {}
    for r in rows:
        if r.get("engine") != ENGINE or r["status"] != "pending":
            continue
        t = r["market_ticker"]
        if t not in cache:
            cache[t] = (http.kalshi(f"/markets/{t}") or {}).get("market") or {}
        res = cache[t].get("result")
        if res not in ("yes", "no"):
            continue
        won = (res == "yes") == (r["side"] == "YES")
        n = int(r["paper_contracts"] or 0)
        r.update(status="settled", settle_value=res, won="1" if won else "0",
                 pnl_after_fees=f"{fees.pnl(won, float(r['market_price']), n, float(r['fee_per_contract']) * n):.2f}"
                 if n else "0.00")
        done += 1
    log.write_log(rows, path)
    return done
