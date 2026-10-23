# Known before close: Kalshi markets whose answer is partly public before the market shuts

Written 2026-10-22 (the scan ran 2026-10-23 01:00 UTC, which is the evening of 2026-10-22 in Caleb's time).
Paper only: Argus never trades. Code: `argus/knownbefore/` (`python -m argus.knownbefore <command>`).

## The idea, plainly

Most Kalshi markets ask about something that hasn't happened yet. A few ask about a number that is
being written down *while the market is still open*: today's high temperature, whether it rained today,
the gas price, a review score. If the number so far already settles most of the question, the price
should be near 0 or 100. Where it isn't, someone is either slow or knows something we don't. This file
ranks those markets and reports what happened when Argus priced three of them from the public record.

## Step 1: the scan

`python -m argus.knownbefore scan` read all 15,370 Kalshi series and all 16,282 open events, sorted
them into families, and measured live liquidity (`data/knownbefore/scan_2026-10-22.csv`). "Tight" means a
two-sided quote with the bid within 5c of the ask (the rule that keeps empty books from faking prices).
Top-of-book sizes are the median number of contracts at the best ask among tight markets.

| Rank | Family | Main series | 24h volume | Median spread | Size at best price | Settles on | When the answer is visible vs the close | Free official data to backtest? |
|---|---|---|---|---|---|---|---|---|
| 1 | Daily high/low temperature | KXHIGH*, KXLOWT* (49 series) | 1.48M | 1.5c | 49 | NWS Daily Climate Report (CLI) | Station reports every hour; the high is usually set by mid-afternoon, the low by dawn; the market closes 00:59 local daylight time | Yes: station METARs + CLI (Iowa State archive of NWS reports) |
| 2 | Daily rain | KXRAIN (one market per city per day) | 0.74M | 18c (1c on the busy cities) | 12 (70 on tight ones) | CLI precipitation > 0 (trace = 0) | Locked the moment the gauge measures 0.01 in; close is ~midnight-1 AM local | Yes: same reports (hourly P group) + CLI |
| 3 | AAA gas | KXAAAGASD (daily), KXAAAGASW (weekly), 85 state series | 2.33M | 2c | ~2,000 | AAA national/state average for the named day | AAA posts once a day before 8:30 AM ET; the daily market closes 11:59 PM ET the night before, so yesterday's print is public and one day's move is not | Partly: Kalshi's own settled values are AAA's prints; no free intraday or station-level data |
| 4 | Trump approval (RCP) | KXTRUMPAPPROVE (daily), KXAPRPOTUS (weekly) | 0.20M | 1-2c | ~1,500-3,800 | RCP average at 1 PM / 11 AM ET | The average changes only when a poll is added; the current value is on RCP's page all morning | No archive of intraday RCP values; RCP's terms on automated reads not checked |
| 5 | Rotten Tomatoes | KXRT | 0.61M | 1c | ~16,500 | Tomatometer at 10 AM ET the Monday after wide release | Reviews land days before; the score moves less as the count grows | No public API; automated reads of RT not attempted (terms not read). Live-only, by hand |
| 6 | Mentions | KXTRUMPMENTION etc. (105 series) | 1.49M | 9c | 46 | What is said at a named event | Visible live while the event runs | No: needs live audio/transcripts |
| 7 | Truth Social post counts | KXTRUTHSOCIAL (weekly), KXTRUTHSOCIALD | 0.08M | no tight quotes | - | Roll Call's count | Accumulates in public over the week | No official archive used here |
| 8 | Netflix top 10 / Billboard | KXNETFLIX*, KXTOPSONG | 0.03-0.04M | 4-9c | 40-350 | Netflix / Billboard weekly lists | Daily lists hint at the weekly one | Not tested: too thin |
| 9 | TSA checkpoint counts | KXTSAD, KXTSAW | 2.2k | 6c | 4 | TSA daily throughput | Next-morning release; the weekly average sees 6 of 7 days before close | Yes (tsa.gov), but there is nothing to trade |
| - | Monthly snow | KXCHISNOWM etc. | 16k | 10c | - | CLI snowfall | Running total in the daily CLI | Season not started (October) |
| - | Jobless claims | KXJOBLESSCLAIMS | 174 | 90c | - | DOL release | Closes at the release: nothing is known first | - |

Box office: no live series found (the regex hit boxing and Bond markets only).

Ranked by (how much of the answer is public before close) x (liquidity) x (can it be backtested from free
official data). Temperature, rain and gas were built; approval and Rotten Tomatoes are next if a permitted
data route turns up (see "Next").

## Steps 2-3: the three strategies

Each is a backtest with no look-ahead: at each replay moment the model sees only station reports
public 20+ minutes earlier, past days strictly before the target day, and the Kalshi price from the
hourly candle that ended at or before that moment, with a real bid within 5c. The bet rule was fixed
before the first run: buy the side whose model probability beats its ask by 5c or more after the taker
fee, only if that side costs 10c or more. Tests: `tests/test_knownbefore.py` (18), including one that
plants future reports, future prices and a future result and checks that no earlier call moves.
All rows: `knownbefore_log.csv`, which `python -m argus board` reads.

### Results against the gate (PASS = 20+ events, better odds than the market (Brier), profit after fees)

| Strategy | Period | Events | Paper bets | Bets/week | P/L after fees | ROI | Edge per contract | Brier model / market | Depth at best price (live, 2026-10-23 01:31Z) | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| observed-temp (9 city highs) | 2026-08-23..10-21 | 526 | 2,137 | 249 | -$74.53 | -7.8% | -3.5c | 0.192 / 0.118 | median 64 contracts (~$18) | **FAIL** (NO EDGE) |
| observed-rain (KXRAIN, 22 cities) | 2026-08-01..10-21 | 81 | 971 | 83 | +$42.34 | +9.1% | +4.4c | 0.127 / 0.082 | median 70 contracts (~$17), most cities 18c spreads | **FAIL on odds** (profitable, NO EDGE by the gate) |
| observed-gas (AAA national daily + weekly) | 2026-08-23..10-21 | 68 | 397 | 46 | -$29.18 | -24.6% | -7.4c | 0.182 / 0.077 | median ~2,100 contracts | **FAIL** (NO EDGE) |

Brier here is the fair one (model Yes probability and market midpoint against what happened); the
board's own column (side probability vs ask) says the same thing in every row.

### What the numbers mean

**Temperature: the market has priced the observation before we can see it.** By 4 PM local the
market's odds are much better than ours (Brier 0.074 vs 0.172), and after 7 PM there is almost nothing
left to buy: only 98 calls in 9 cities over two months still had a two-sided quote within 5c. The
answer is settled in the price about as fast as it becomes public in the hourly reports, and earlier
in the day the market knows the forecast, which "max so far + climatology" does not. When our model
said 95%+ it won 72%: those were days still warming (gap 0) where the forecast, not the record,
decides. Slots: 10 AM -6.8%, 1 PM -5.1%, 4 PM -20.2%. Tried only for highs: the 3 remaining series
(Seattle high, NYC/PHL lows) were stopped at 9 of 12 because Kalshi rate limits made each city take
10-60 minutes and the verdict was already clear (re-run cost:
`python -m argus.knownbefore temp-backtest --since 2026-08-23 --series KXHIGHTSEA,KXLOWTNYC,KXLOWTPHIL`, about an hour).

**Rain: profitable, but not for the reason it was built.** Once the gauge records 0.01 in, the market
is already at 95c+ (6 calls only). All the profit came from buying **No** before any rain fell:
845 No bets, +11.5%, in both halves of the period (+11.6% Aug 1-Sep 7, +6.7% Sep 7-Oct 22), and +7.2%
counting only the first bet per city-day. The market said 25% for rain on overcast days and 15% on
clear days; it rained 20% and 12%. Yes bets lost 42%. So the finding is "Kalshi rain Yes is a little
overpriced" (a longshot lean), while the model's odds are clearly worse than the market's overall.
It fails the gate on odds. It is the only one worth a forward test, and only as "buy No on rain at
70-90c", judged on its own record.

**Gas: the market knows tomorrow's AAA number before AAA posts it.** At 11 PM ET the market prices the
next morning's print with Brier 0.056; "last print plus a typical day's move" gets 0.222. Traders
evidently see real-time station prices (GasBuddy/OPIS-type data, not free) that AAA only summarizes
the next morning. The last AAA print is public, but it is not the information that matters.

### Dated negative findings (re-test costs in brackets)
- Kalshi's batch candle route (`GET /markets/candlesticks?market_tickers=`) returned no candles for
  historical-tier tickers on 2026-10-22; history before 2026-08-23 needs one call per market [1 call].
- Kalshi answered about 1 call in 2 with HTTP 429 on 2026-10-22/23 while 13 sibling sessions were reading,
  so the temperature backtest was cut to the live tier (from 2026-08-23) [May-Aug re-run: ~8,000 calls,
  several hours on a quiet API].
- Rotten Tomatoes and RCP were not read at all: no public API was found, and their terms on automated
  reads were not checked [read each site's terms by hand first].

## Next

1. Rain No: forward paper test only. The rule is written above (No at 70-90c, model edge 5c+ after fee,
   real bid within 5c); judge after 20+ new events, and pause if live ROI < 0.
2. Do not spend more on temperature or gas "known-before": the market already has the observation.
3. Approval (RCP) and Rotten Tomatoes are the next candidates by liquidity, but only by hand from
   their public pages until a permitted data route is confirmed.
4. `python -m argus.knownbefore temp-live` / `temp-settle` exist for a same-day forward test, but on this
   evidence there is little reason to run them.
