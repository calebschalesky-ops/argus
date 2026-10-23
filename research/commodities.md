# Commodities deep dive

Written 2026-10-22 by the argus-commodities thread. Code: `argus/commodities/` (`python -m argus.commodities scan | backtest | report | live | settle`).
Tests: `tests/test_commodities.py` (25). Paper only. Argus never trades. Every network call is in `data/commodities/api_calls.csv`.

## The short version

- **Forecasting commodities lost before (oil -49%, other commodities -40%). I did not redo it.** This study looked only for
  *structural* reasons a price could be wrong.
- **One strategy passes the gate, narrowly: late-day favourites on exchange-priced ladders.** This means buying the side priced
  85-97c in the last 30-60 minutes before a gold, silver, copper, natgas, Brent or WTI settle. It made **+2.4 to +2.8% on
  money staked, about 2-2.6c per contract after fees**, over 194-233 settled events. It is small and fragile, and it still
  has to be proven on new calls before any money goes in (section 5).
- **Everything else fails.** Same-settlement arbitrage almost never shows up (2 locks in 2 months). Resting maker orders at
  bid + 1c lose 16%. No price bucket makes money except the 90-99c buckets, and those only barely.
- **AAA gas prices are 80% of the category's volume and a trap.** The books are mostly empty, with spreads like 15c bid / 94c ask.
  Taking any side lost 15-60%, and "favourites" at 93c won only 75% of the time.

## 1. The map: every non-15-minute Commodities series

`python -m argus.commodities scan` prints the board and saves `data/commodities/series_map_<date>.csv`. That file has all 127
series, with the exact rules sentence, source, settle time, liquidity, spread, depth, fee type and history count.
15-minute series (gold, silver, WTI, Brent, copper, natgas, platinum, palladium) are left to argus-15m.

**What exists (2026-10-22):**

| Group | Series | What they ask | Settles on | Live? |
|---|---|---|---|---|
| Oil | KXWTI (daily), KXWTIW (weekly) | WTI above $X (daily ladder) / in a $1 range (weekly) | **ICE daily settlement** of the front WTI future, 2:30 PM ET | Yes, the most liquid |
| Oil | KXBRENTD/W/MON | Brent above $X | **Pyth** 1-minute close, 5:00 PM ET | Yes |
| Oil | KXWTIH (hourly) | WTI above $X on the hour | Pyth "PythOil" 1-minute close | Thin |
| Oil | KXWTIMAX/MIN, KXWTIDIRY, KXWHENWTI, ... | Highest/lowest WTI settle by a date, year-end | ICE | Long-dated, wide (11c+) |
| Natgas | KXNATGASD/W/MON | Henry Hub natgas above $X | Pyth, 5:00 PM ET | Yes |
| Heating oil | KXHOILW/MON | above $X | Trading Economics | Nothing open; 4 settled events |
| Metals | KXGOLDD/W/H/MON, KXSILVERD/W/H/MON, KXCOPPERD/W/MON | above $X | Pyth 1-minute close, 5:00 PM ET (hourly: on the hour, last at 4 PM) | Yes |
| Metals | platinum/palladium hourly | above $X | Pyth | Hourly only |
| Metals | nickel, cobalt, lithium, steel, aluminium premium, tungsten APT | above $X | Trading Economics, Pyth, CME, Fastmarkets, SMM | Nothing open or dead |
| Ags | corn, wheat, soybeans, sugar, coffee, cocoa, live cattle, beef (USDA, EU) | above $X | Pyth, Trading Economics, USDA, European Commission | **Nothing open**; 0-4 settled events |
| Power | ERCOT Houston/North/West, PJM West/Dominion/AEP-Dayton, MISO Indiana (weekly, monthly) | weekly all-hours average real-time price above $X/MWh | the grid operator's published prices | Open, but spreads of 86-96c and **0 settled events** |
| Gasoline | KXAAAGASD** (30 states, daily), Texas monthly, NJ year-end | state average regular gas above $X.XXX | AAA's posted state average (trading stops 11:59 PM ET the night before; read at 10 AM) | **Huge volume, empty books** |
| AI compute | Opus/GPT/Gemini token prices, B200, Vera Rubin | price or launch | the vendor's pricing page | Tiny |

Every commodity series charges the plain taker fee (`quadratic`: 0.07 x P x (1-P), rounded up, so at least 1c on a 1-contract
order). There is **no maker fee**.

**Tradability ranking.** Liquidity is the larger of the 24-hour volume and the last settled event's volume, because daily
series have nothing open between sessions. A series must also have a near-money spread of 5c or less, at least 25 contracts
at the top of the book, and 20+ settled events.

| Rank | Series | Last event volume | 24h volume | Open interest | Typical spread | Top depth | Settled events |
|---|---|---|---|---|---|---|---|
| 1 | **KXWTI** WTI daily | 220,649 | 30,662 | 456,276 | 1c | 66 | 862 |
| 2 | **KXBRENTW** Brent weekly | 196,385 | 20,317 | 18,205 | 3c | 90 | 30 |
| 3 | **KXWTIW** WTI weekly ranges | 128,282 | 22,902 | 53,191 | 3c | 500 | 164 |
| 4 | **KXGOLDW** gold weekly | 122,137 | 9,671 | 11,600 | 3c | 200 | 32 |
| 5 | **KXCOPPERW** copper weekly | 98,427 | 4,144 | 23,443 | 3c | 100 | 26 |
| then | KXNATGASW, KXSILVERW, KXGOLDH | 70k, 42k, 14k | | | 1-4c | 50-290 | |

The daily Pyth ladders (gold 149k a day, Brent 56k, natgas 57k, copper 42k, silver 27k) are as liquid as the weeklies. They
miss tier 1 only because nothing was open at scan time, so no live spread could be read. Re-run `scan` during the trading
day to see them rated properly. AAA gas has the biggest raw volume (Louisiana 95k, Wisconsin 72k in 24 h). Every AAA state
fails on spread (5-71c) or on history (13-29 events, since mid-September).

## 2. Watching the underlying live (free, official, no sign-up)

Checked 2026-10-22. MEASURED = fetched today; STATED = the provider's docs. Full notes:
`side-session-notes/.../commodities_live_sources.md`. Nothing was signed up for, no key entered, no terms accepted.

| Settles on | Free way to watch it | Delay | Better version costs |
|---|---|---|---|
| **Pyth** (gold, silver, copper, natgas, Brent, hourly WTI) | The web page `app.pyth.com/explore/...` (no login, MEASURED). The free API (Hermes) now answers **401** to price requests: since 2026-08-26 it needs a key (MEASURED/STATED). Feed IDs are still open. | Near real time on the page | Commodity feeds need **Pyth Pro, from $2,500/month** (STATED). Pyth's robots.txt blocks `/api/`, so do not script the page. |
| **ICE WTI settle** (KXWTI, KXWTIW) | ICE product-guide JSON (no key): last trade per WTI month (MEASURED). It is last trade, not the official 2:30 PM settle. The official report sits behind a disclaimer and a reCAPTCHA (not touched). CME's delayed CL quotes in a normal browser. | Last trade near live; CME 10+ min | Real-time CME/ICE data licence (paid). **CME blocks scripted requests** ("scraping strictly prohibited", MEASURED). |
| **AAA** state gas | `gasprices.aaa.com/state-gas-price-averages/` (MEASURED). EIA weekly retail gasoline as a cross-check (free API key). | Once a day | n/a. AAA's robots asks for a 10-second crawl delay. |
| **ERCOT** hubs | `ercot.com/content/cdr/html/real_time_spp.html`, every 15 min, no login (MEASURED) | ~2 min | Public API needs a free account and key (STATED) |
| **PJM** hubs | Data Miner 2 page, no login. The named feed `rt_hrl_lmps` posts the *next business day*; the unverified hourly feed is about 1 h behind (MEASURED). | 1 h to next day | Free account for the API (STATED) |
| **MISO** Indiana Hub | Public API `GetExAnteLmp`, no key (MEASURED). It is the 5-minute ex-ante price, not the settled hourly one. | Live | Data Exchange key (cost unconfirmed) |
| Trading Economics (heating oil, softs, cattle, metals) | The commodity web pages (built from OTC/CFD prices) | Not stated | Free API discontinued (HTTP 410); paid plans |
| Fastmarkets / SMM tungsten | Nothing free; SMM locks the price (MEASURED) | Weekly / daily | Paid subscriptions |
| USDA beef, EU beef | USDA AMS Datamart report 2451 (no key), EC agri-food API (no key) (MEASURED) | Daily 2:30 PM CT / weekly | none |

For Caleb: **you need nothing new to run the late-favourite test.** It only reads Kalshi's own prices. Watching the Pyth price
yourself is free in a browser tab. An automated Pyth feed would cost $2,500/month, which is not worth it at this edge size.

## 3. The four structural strategies (backtests, no look-ahead)

**Sample and method.** Kalshi's live data tier holds every commodity event that settled from **2026-08-23 to 2026-10-22**:
5,568 events listed, 799 priced ladders/ranges replayed (hourly series used only to find same-settlement pairs). Older events
sit in a "historical" tier with one request per strike, which was too slow to read with five sessions sharing the rate limit.
Each strategy acts at a moment fixed by the calendar, reads only Kalshi candles that ended at or before that moment, and is
scored on Kalshi's official result. Tests prove each rule (`tests/test_commodities.py`): quotes after the moment are never
used; fills need trades from whole periods after the moment; the "model" uses only results settled before the call; replay
times come from the calendar; and markets that closed early are dropped (judged against the series' own schedule).

**Fees:** 1-contract taker fee per leg (at least 1c). Maker orders pay no fee (commodity series charge none).

**How each engine is scored.** The board needs a *model* to compare against the market. Here the model is walk-forward: the
strategy's own win rate in that price band on earlier calls that had already settled at the time (Laplace-smoothed, needing
30+ earlier calls; before that, the price itself). "Better Brier than the market" therefore means the strategy's track
record, known at the time, was a better guide than the price.

**Two families, reported apart.** AAA state gas markets behave nothing like the exchange-priced ladders, so every engine is
split: `...-aaa-...` for AAA, no tag for Pyth/ICE ladders. Be clear about the order of events. The first full run, by
accident, contained no AAA markets: a bug treated their normal 10-hour gap between trading close and the AAA reading as an
early close. The late-favourite edge was measured on that run. When the bug was fixed, AAA turned out to lose heavily, and
only then was the split made. The exchange-ladder numbers were measured before AAA was seen, but **the decision to report
them separately came after**, so treat them as a hypothesis that the forward test (section 5) must confirm.

### a. Consistency: same-settlement markets that disagree

The search looked for any set of contracts that pays at least $1 whatever the settlement value and costs at most 99c at the
asks, including every leg's fee. It covered two-leg combinations, three-or-more-leg range covers, and "sell the set" on
exhaustive ranges. Payoffs are computed exactly. Rounding to the cent invents fake locks on AAA's half-cent strikes; my first
run did exactly that and showed 328 "locks" that lost money.

- **Same-settlement pairs found from the data, not assumed:** only **KXWTI and KXWTIW** (identical ICE settle on 8 of 8
  Fridays). Kalshi lays the others out so they do not overlap: gold daily does not settle on Fridays (the weekly does), gold
  hourly stops at 4 PM, and the monthlies did not land on another series' date in this window. KXWTIH (Pyth) matched the ICE
  settle on only 6 of 41 days: a different number.
- **Results:** 344 pair-hours gave 1 lock (+3c, one event). 799 ladder events over 28,261 hours gave 1 lock (+1c). No
  non-monotone ladder and no range set summing under (or over) $1 after fees.

| Engine | n bets | Events | P/L after fees | Verdict |
|---|---|---|---|---|
| consistency-pair (WTI weekly vs daily) | 2 | 2 | +$0.03 | TOO EARLY (2/20 events). In practice the gap almost never opens. |
| consistency-ladder | 2 | 1 | +$0.01 | TOO EARLY (1/20). Same. |

Hourly candle quotes cannot see depth, so even these two may not have been fillable.

### b. Late-day favourites (85-97c, 15/30/60 minutes before the settle)

| Engine | Bets | Events | Won | Avg price | P/L after fees | ROI | Edge/contract | Bets/week | Brier strategy/market | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| **latefav-30m** (Pyth/ICE ladders) | 368 | 194 | 96% | 92.8c | **+$9.62** | **+2.8%** | +2.6c | 44 | 0.0338 / 0.0346 | **PASS** |
| **latefav-60m** | 537 | 233 | 96% | 92.8c | **+$12.01** | **+2.4%** | +2.2c | 64 | 0.0385 / 0.0386 | **PASS** (Brier a tie) |
| latefav-15m | 291 | 184 | 96% | 93.0c | +$5.53 | +2.0% | +1.9c | 35 | 0.0400 / 0.0396 | NO EDGE (Brier worse) |
| latefav-aaa-60m | 1,593 | 397 | 75% | 93.4c | -$312.34 | -20.8% | -19.6c | 398 | 0.2024 / 0.2192 | FAIL |
| latefav-aaa-30m | 1,595 | 392 | 73% | 93.4c | -$342.35 | -22.7% | | | | FAIL |
| latefav-aaa-15m | 1,609 | 396 | 74% | 93.5c | -$325.14 | -21.4% | | | | FAIL |

Checks on the exchange-ladder result:
- **Event-level bootstrap** (95% interval on ROI, resampling whole events): 60m -0.1% to +4.5% (chance it is really zero or
  worse: 3%); 30m +0.6% to +4.8% (1%); 15m -0.5% to +4.3% (5%).
- **Both halves of the sample** (before and after 2026-09-26) are positive at every horizon: 60m +$3.91 / +$8.10,
  30m +$5.09 / +$4.53, 15m +$3.45 / +$2.08.
- **By price:** the profit sits at 87-95c. At 96-97c it is break-even or slightly negative: the 1c fee eats it.
- **By series (60m):** Brent daily +$3.51, gold daily +$2.85, WTI daily +$2.09, silver daily +$1.48, copper daily +$0.60,
  natgas daily -$0.78. Small samples each; no single series carries it.
- **Not tested:** depth. Candles do not show how many contracts sat at the ask. The live scan shows a median of 28-66
  contracts at the top of daily ladders, enough for 1-contract orders.

AAA: the "favourite" at 11 PM is often a lone order, for example a 94c ask against a 15c bid on a strike nobody else quotes.
Asks are often not even in order across strikes. The AAA reading moves a cent or more overnight, and half-cent strikes put
several "sure things" on the wrong side.

### c. Maker at bid + 1c, near the money

Rest a buy one cent above the best bid on both sides of every strike with a Yes midpoint of 25-75c, at the replay time
(10 AM New York on the settle day for dailies; 2 days before for weeklies; 7 days before for monthlies). It counts as filled
only if a later trade printed **strictly through** the limit before the close. Unfilled orders are logged as `void`.

| Engine | Orders | Filled | Events | Won | Avg price | P/L | ROI | Verdict |
|---|---|---|---|---|---|---|---|---|
| maker-bid+1 (Pyth/ICE) | 1,806 | 1,491 (83%) | 236 | 40% | 47.7c | -$114.58 | -16.1% | FAIL |
| maker-aaa-bid+1 | 5,126 | 1,592 (31%) | 320 | 8% | 15.0c | -$113.12 | -47.3% | FAIL |

The orders that fill are the ones the market moved against. This is the same adverse-selection result as
`research/maker_longshot.md`, now on a bigger commodity sample.

### d. Favourite/longshot buckets inside commodities

At the same replay time: every two-sided quote, both sides, at most 8 strikes per event (fixed seed). Taker at the ask, and
maker at bid + 1c with the strict fill rule. `argus/buckets.py` covered only four metals plus natgas and oil from the old
backtest logs, so this widens it to every commodity series in the live tier.

| Bucket | Taker n | Taker ROI | Maker filled n | Maker ROI | AAA taker ROI |
|---|---|---|---|---|---|
| <10c | 1,005 | -70.0% | 552 | -91.8% | -87.5% |
| 10-30c | 652 | -27.7% | 313 | -23.4% | -61.0% |
| 30-50c | 358 | -6.3% | 229 | -3.7% | -72.0% |
| 50-70c | 313 | -8.7% | 223 | -15.8% | -57.3% |
| 70-90c | 560 | -2.4% | 242 | -9.4% | -61.6% |
| 90-99c | 1,188 | **+0.4%** | 291 | **+1.7%** | -15.0% |

Verdicts: every bucket FAILS except taker 90-99c and maker 90-99c, which are "profit, Brier not better" (155-255 events).
Longshots lose badly, as everywhere on Kalshi. Only near-certain sides break even. Strategy (b) is the sharper version of the
same effect: near-certain, close to the settle, away from 96c+.

## 4. Verdicts against the gate

Gate (the board's): 20+ settled events, Brier better than the market's, and P/L positive after fees.

| Strategy | n (bets / events) | P/L after fees | Verdict |
|---|---|---|---|
| a. consistency (pair + ladder) | 4 / 3 | +$0.04 | TOO EARLY, and structurally rare: about 2 locks in 2 months |
| b. late favourites, Pyth/ICE, 30 min | 368 / 194 | +$9.62 (+2.8%) | **PASS** |
| b. late favourites, Pyth/ICE, 60 min | 537 / 233 | +$12.01 (+2.4%) | **PASS** (Brier tied; passes on profit) |
| b. late favourites, Pyth/ICE, 15 min | 291 / 184 | +$5.53 (+2.0%) | NO EDGE |
| b. late favourites, AAA (any horizon) | ~1,600 / ~395 | -$312 to -$342 | FAIL |
| c. maker bid + 1c (Pyth/ICE / AAA) | 1,491 / 236 ; 1,592 / 320 | -$114.58 ; -$113.12 | FAIL |
| d. buckets | see table | 90-99c only: +0.4% taker, +1.7% maker | FAIL (90-99c: profit, Brier not better) |

## 5. The one that passes: what a real test would look like

**What it flags:** about 44 calls a week at 30 minutes (64 at 60 minutes) across about 23 settles a week: WTI daily and
weekly at 2:30 PM ET; Brent, gold, silver, copper and natgas at 5:00 PM ET.
**Edge:** about +2.2-2.6c per contract after the 1c fee, on about 93c staked. Expect roughly +$1 a week at 1 contract per call.

**Step 1 (from Fri 2026-10-23, free): forward paper test.** `python -m argus.commodities latefav-live` runs 60 and 30
minutes before each settle: WTI 2:30 PM ET; Brent, gold, silver, copper, natgas 5:00 PM ET. It logs every qualifying
favourite to `commodities_latefav_live_log.csv` (engines `latefav-live-60m`, `latefav-live-30m`, scored apart from the
backtest on `python -m argus board`). It also saves a hand list to `alerts/YYYY-MM-DD_latefav.md`: market, side, price,
contracts at that price, settle time, 1 contract max. `python -m argus.commodities latefav-settle` fills in results
from Kalshi. Desktop scheduled tasks run all of this on weekdays (THREAD-LOG lists the times).

Live rules, fixed before the first live call: favourite side 87-95c at the ask; a real bid on that side within 5c; no
AAA, hourly or 15-minute markets. These are tighter than the backtest's 85-97c, and the cut-offs were chosen after
seeing the price breakdown. The backtest re-scored under exactly these rules:

| Horizon | Bets | Events | Won | Avg price | P/L after fees | ROI | 95% interval (events) | Bets / events a week |
|---|---|---|---|---|---|---|---|---|
| 60 min | 303 | 182 | 96.0% | 91.9c | +$9.55 | +3.4% | +0.4% to +5.9% | 35 / 21 |
| 30 min | 204 | 141 | 97.5% | 91.8c | +$9.62 | +5.1% | +2.6% to +7.2% | 24 / 16 |

**Stopping rule (Caleb, 2026-10-22).** After 20 live settled events for a horizon, compare the live result with the
table above. If live ROI is below 0, or the win rate is under 93%, that horizon is **paused**: no more paper calls and
no real money until it is re-examined. Re-check at every further 20 events. Twenty events is a tripwire, not proof. Even
a true +3-5% edge can show a loss over 20 events, so passing at 20 only means "keep going" toward about 200 calls.

**Step 2 (only if step 1 holds): $1-per-trade real test, placed by Caleb by hand.**
- Buy 1 contract of each line in that day's `alerts/YYYY-MM-DD_latefav.md` (already limited to 87-95c with a bid
  within 5c; no AAA gas, hourly or 15-minute markets), at the listed price or better.
- About 9 contracts a day, about $8.50 at risk a day, all settling the same afternoon. Weekly expectation about +$1. A
  single loss costs about 93c, so a bad week can be -$3 to -$5.
- Log every fill by hand next to the paper call, to see how much the real fill price differs from the quote.
- Stop if, after 200 real contracts, P/L is below zero, or if fills are regularly worse than the logged ask.

What would kill it: the edge is 2-3c on a 93c bet, so one or two cents of worse fills or a fee change erases it. A few big
overnight moves in gold or oil could wipe out a month.

## 6. Caveats

- Two months of data (live tier only). The historical tier (back to 2022 for WTI) was not read.
- Hourly candles cannot show depth, and minute candles exist only when something changed. A favourite's ask might have
  been 1 contract.
- Found while building: `argus/markets.contains()` (not this thread's file) rounds settlement values to the cent. That is
  wrong for AAA gas ($3.8950) and natgas ($1.999) strikes, and any engine that settles those markets with it can misjudge
  them. This package uses its own exact check.
- The Brier "tie" at 60 minutes is a tie: the strategy's walk-forward win rate is barely more informative than the price.
