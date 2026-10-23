# Election night: do official counts beat Kalshi's price while the count comes in?

Written 2026-10-22 by the election-night session. Code: `argus/electionnight/`. Tests: `tests/test_electionnight.py`.
Log: `electionnight_log.csv` (engines `electionnight-backtest`, `electionnight-backtest-rest`, `electionnight-live`).

## Verdict

**NO EDGE, and too few nights to say more.** On 7 replayed races (9 Kalshi events) the market's prices were
much better probabilities than the model's (Brier 0.026 market vs 0.092 model; lower is better). The model
made 8 paper bets: 5 won, **−$1.46 after fees on $6.34 staked (ROI −23%)**. Resting orders one cent over the
bid did a little better and still lost: 11 filled, 8 won, **−$0.72 on $8.72 (−8%)**. The board says TOO EARLY
(9 of 20 events), and the direction of the evidence is against the idea.

What happened, in plain words: Kalshi traders already price the count very well. The model's mistakes are
the two classic election-night traps. In Georgia (rural counties count first) it read Trump +13 at 8:12 PM ET
when the final was +2.2. In Pennsylvania (mail ballots counted first) it read Harris ahead until about
11:45 PM ET. It was right in Georgia and North Carolina for partly wrong reasons, and wrong in the
Pennsylvania Senate race (it bet Casey at 74c with 18% counted; McCormick won).

Do not run this with money. Run it on Nov 3 as a paper forward test (run sheet below); it costs nothing and
adds 5 races to a sample that badly needs them.

## 1. Which past nights can be replayed, and which states make it possible

The hard part is the official timeline: what the state had published, minute by minute. No state keeps
that history itself. The usable record is the **Internet Archive's timestamped captures of the state's own
results feed** (the JSON/text files the official results page loads). A capture is a copy of what the
state's server returned at that second, so the counts in it were public at or before the capture time.
That capture time is the only clock the backtest uses. (Measured 2026-10-22 with the Archive's public CDX index.)

| State, night | Official feed | Captures during the count | Usable? | Kalshi markets |
|---|---|---|---|---|
| **Georgia 2024 general** | results.sos.ga.gov (Enhanced Voting) | 39 county-level (president), 178 statewide, 1-3 min apart, 7 PM-2:30 AM ET | **Yes, best night** | PRESPARTYGA, POPVOTEMOVGA (14 margin buckets) |
| **Pennsylvania 2024 general** | electionreturns.pa.gov | 51 county-level (president), 71 statewide (all offices) | **Yes** | PRESPARTYPA, POPVOTEMOVPA (20 buckets), SENATEPA |
| **North Carolina 2024 general** | er.ncsbe.gov results_0.txt | 81 statewide, split by vote type; county files rarely | Yes, statewide only | PRESPARTYNC, GOVPARTYNC |
| Virginia 2025 general | enr.elections.virginia.gov | 1 full export (8:38 PM ET), 1 county-level governor (9:26 PM) | Thin: 1-2 snapshots | GOVPARTYVA, KXLTGOVVA, KXATTYGENVA |
| Georgia 2025 PSC | results.sos.ga.gov | 1 county-level (7:38 PM ET, under half the counties in) | Too early to call | KXGAPUBLICSERVICE3 |
| Virginia 2026-04-21 referendum | enr.elections.virginia.gov | 1 county-level (8:34 PM ET), 1 export (10:49 PM) | Thin | KXVIRGINIAREDISTRICTING |
| Georgia 2026-05-19 primary | results.sos.ga.gov | captures only 2 days later | **No** | KXGAPRIMARY1R, KXGOVGANOMR/D |
| Virginia 2024 general | old site results.elections.virginia.gov | 1 data file | No | |
| Virginia 2025 June primary | enr.elections.virginia.gov | 0 data files | No | |
| Arizona 2024 | results.arizona.vote | 0 data files (page shells only) | No | |
| Texas 2026-03-03 primary | results.texas-election.com | 2 | No | |
| NYC 2025 mayor | enr.boenyc.gov / web.enrboenyc.us | 0 data files | No | |
| Florida, Michigan, Nevada 2024 | state results sites | 0 data files | No | |
| Clarity/Scytl states and counties (KY, AR, WV, many counties) | results.enr.clarityelections.com | Archive index refused the query (kept failing); live site returns HTTP 403 to scripts | No (dated 2026-10-22; re-test: one CDX query) | |

States that make this possible: **Georgia, Pennsylvania, North Carolina** (dense), **Virginia** (thin). Every
other state checked has no usable public timeline. NJ governor 2025 is county-by-county with no state feed.

Pre-election inputs (all published before polls closed): Georgia's and Virginia's registered voters per county
(in the same feed, captured before close); Pennsylvania's 2020 presidential result by county (live PA API,
election 83); North Carolina's registered voters (the board's own `county.txt`).

## 2. The model (simple on purpose)

`argus/electionnight/model.py`, one county at a time:
1. **Expected final votes.** Georgia/Virginia publish precincts reporting: early and mail votes are in once
   the county reports at all, election-day votes grow with precincts (election-day final = counted ÷ share
   of precincts in). A county with nothing yet: registered voters × the turnout of counties already
   complete (65% before any are). Pennsylvania: the county's 2020 total. Statewide-only feeds (North Carolina,
   PA Senate): registered voters × turnout, or the presidential expectation × the Senate's share of
   presidential votes so far.
2. **Votes still out** = expected − counted.
3. **How they split.** Like the votes of the same type already counted in that county (election-day votes
   look like election-day votes); a county with no election-day votes yet takes its early split plus the
   election-day shift seen elsewhere. Pennsylvania ("mixed": either kind can still be out) uses the county's
   overall split.
4. **Uncertainty**, a normal curve with three parts: each county's leftover can split differently (SD 8 points
   of margin, 15 if borrowed), all leftover votes can lean together (SD 4 points), turnout can be off (SD 8%).

The constants were written down before the backtest ran. Two things were added after the **first** Georgia
run and before any other race was replayed, and they are disclosed here: (a) counties with nothing counted
were sized at the median county, which hid Fulton/Cobb/Gwinnett; replaced by registered voters × turnout
(`TURNOUT_PRIOR = 0.65`); (b) the statewide-only rule for North Carolina. Georgia's first run made the same
3 bets with the same result. Nothing was tuned to make the results look better.

## 3. Backtest rules (strict timestamp alignment)

`argus/electionnight/backtest.py`; each rule has a test:
- A count is used only from its **capture time**, never the state's earlier "as of" stamp.
- The Kalshi price is the close of the first one-minute candle ending **at least 60 s after** the capture
  (time to read it and act), and within 10 minutes, else no call.
- The book needs a real bid within 5c of the ask (empty books show 1c/99c).
- Calls start only after **real reporting**: 10% of the projected vote counted and half the counties in.
- Take the side when model − ask − taker fee ≥ 10 points and the side's price is 50-99c (the shared band
  in `filters.py`). One paper contract per market, the first time it qualifies.
- "Rest" variant: bid + 1c, counted only if a later trade in the next 15 minutes printed at that price.
- Margin-bucket markets (POPVOTEMOV*) are priced from the same projected margin and its spread.

## 4. Results (`python -m argus.electionnight report`)

| Cut | Kalshi events | Rows | Bets | Won | P/L after fees | ROI | Brier model / market |
|---|---|---|---|---|---|---|---|
| Take, all | 9 | 295 | 8 | 5 | −$1.46 | −23% | 0.092 / 0.026 |
| Take, county feeds (GA, PA pres, VA) | 6 | 206 | 4 | 3 | −$0.49 | −14% | 0.091 / 0.020 |
| Take, statewide-only feeds (PA Sen, NC) | 3 | 89 | 4 | 2 | −$0.97 | −34% | 0.095 / 0.042 |
| Rest (bid + 1c, fill checked) | 5 | 13 | 11 filled of 13 | 8 | −$0.72 | −8% | 0.230 / 0.162 |

The bets (times UTC, ET = UTC − 5 in November):
- GA 01:12 (8:12 PM ET, 36% truly counted): Trump Yes at 86c, Harris No at 85c, "Trump by 1-2%" No at 88c. All won.
- NC 01:15: Trump Yes at 71c, Harris No at 69c. Both won.
- PA Senate 02:15-02:23 (18-20% counted): Casey Yes at 74c, McCormick No at 75c. Both lost.
- PA 03:12: "Trump by 1.5-2%" No at 86c. Lost (it was the winning bucket).

Per race, the model's Brier was worse than the market's midpoint everywhere except North Carolina's
presidential race (right early for the wrong reason: it read rural election-day votes as the whole remaining
vote) and Virginia attorney general (2 rows, both near certain).

Not tested: VA 2025 governor and lieutenant governor (captures came after the book had gone to 99c/100c, so no
quote passed the 5c rule), GA PSC (gate never passed), GA 2026 primary (no captures on the night).

## 5. Nov 3 2026: what is covered live

`python -m argus.electionnight live` reads each state's official feed directly (no archive), every 120 s:

| Race | Feed | Polls close (Pacific) | Kalshi market (price 2026-10-22) | Tier |
|---|---|---|---|---|
| Georgia Governor | results.sos.ga.gov (found by date; not posted yet on 2026-10-22) | 4:00 PM | GOVPARTYGA-26 (Bottoms 60c / Jackson 40c) | county |
| Georgia Senate | same | 4:00 PM | SENATEGA-26 (Ossoff 95c) | county |
| Virginia Senate | enr.elections.virginia.gov (2026-November-General already listed) | 4:00 PM | SENATEVA-26 (Warner 98c) | county |
| North Carolina Senate | er.ncsbe.gov/enr/20261103 (folder already up) | 4:30 PM | SENATENC-26 (Cooper 96c) | statewide |
| Pennsylvania Governor | electionreturns.pa.gov, election 117; 2022 governor by county as the prior | 5:00 PM | GOVPARTYPA-26 (Shapiro 97c) | county |

Only Georgia Governor is close. The others are 95c+ favourites, where a 10-point gap after fees is nearly
impossible. Pennsylvania's 2026 feed already shows placeholder counts (115,378 votes on 2026-10-22): the live
command ignores every count before that state's polls close.

Polling limits: none of the four sites publishes a rate limit, and all four robots.txt files were empty on
2026-10-22. 120 s is our choice; the command refuses anything under 60 s. About 15 requests every 2 minutes
in total: 1-2 per state feed, 1 per Kalshi market. Every request goes to `data/electionnight/api_calls.csv`.

## 6. Nov 3 run sheet (Pacific times; Nov 1 ends daylight time, so ET = PT + 3)

| When (PT) | Do |
|---|---|
| Mon Nov 2, any time | `python -m argus.electionnight live --once --dry`. Expect GA "election not posted yet" or found, VA and NC "no votes counted yet", PA "test data, ignored". Any "feed error" means a URL changed: tell a session before Tuesday. |
| Tue Nov 3, 12:00 PM | Same check again. Georgia should now be found. |
| 3:45 PM | Open a terminal in the argus folder and start `python -m argus.electionnight live`. Leave it running. It prints every 2 minutes and logs to `electionnight_log.csv`. |
| 4:00 PM | Georgia and Virginia polls close. Georgia posts early and mail votes first (by law, within about an hour). |
| 4:30 PM | North Carolina polls close. Early votes come first. |
| 5:00 PM | Pennsylvania polls close. Mail ballots often come first in the big counties, so the early count leans Democratic. |
| 4:15-7:30 PM | The window that matters: calls appear only after 10% is counted. A "PAPER CALL" line is a paper bet only. Nothing is ever placed. |
| ~11:00 PM, or once every race shows 99c | Ctrl+C to stop. |
| Each day after | `python -m argus.electionnight settle`. These markets settle on swearing-in or inauguration (Jan 2027), so rows stay pending until Kalshi settles them. `python -m argus board` shows them. |

If the computer sleeps, the command stops polling; a missed stretch is lost (no backfill).

## 7. Negative findings (dated 2026-10-22, re-testable)

- Clarity/Scytl: `results.enr.clarityelections.com` returns HTTP 403 to scripted reads, and the Archive's index
  kept refusing the domain query. Re-test cost: one CDX query and one GET.
- Georgia 2020 results (Clarity) unreadable for the same reason, so Georgia uses registered voters, not 2020.
- Georgia 2026 primary: the Archive has no captures on the night. The "won outright" markets (KXGAPRIMARY1R)
  would be a clean test of `p_majority` if a capture source appears.
- AP and decision-desk feeds are paid; not used.

## 8. What would change the verdict

More nights, mostly. Two model changes are worth writing down now and testing only on Nov 3 data, not tuned on
2024: (1) Pennsylvania: split the leftover by vote type using each county's 2020 mail/election-day mix;
(2) Georgia: treat early votes as incomplete in counties that post them in batches (Fulton, DeKalb, Cobb,
Gwinnett). Even fixed, a model has to beat a market that already gets Georgia right to within a few points,
after a 1-2c fee. The likelier edge, if any, is speed in the first 30 minutes after a big county posts.
This backtest cannot measure that: captures are minutes apart and the 60-second reading delay is generous.
