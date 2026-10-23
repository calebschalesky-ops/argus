# The edge factory (`argus/lab/`)

Built 2026-10-22. Caleb's blocker was finding edges, as many as possible, as fast as possible.
On day one about 20 strategies were tested by separate sessions, each downloading its own data
and writing its own backtest; one passed, and one "passed" only because of a look-ahead bug.
The edge factory replaces that with one data store, one test bench and one rule miner, all
plain Python. A new idea now costs seconds of local maths, not a Claude session and an hour of
downloads.

Read-only, public Kalshi data, no key, every network call logged to `data/lake/api_calls.csv`.
It never trades.

## The four parts, in plain words

**1. The lake** (`python -m argus.lab sync`). One SQLite file, `data/lake/lake.db` (not in git),
holding Kalshi's settled history since 2025-04-01: every series, every settled market that is
not a 15-minute market or a multi-leg combo (times, result, settle value, volume, strike, rules
text), hourly prices for the last 9 days of every market that ever traded (Yes bid, Yes ask,
trade low/high, volume), and trade tapes where one exists. It first loads everything sibling
sessions had already downloaded under `data/cache/`, so none of that is fetched twice. Every
step saves its place, so a run that is stopped or rate-limited picks up where it left off.
`python -m argus.lab status` prints row counts and disk size.

**2. The bench** (`argus/lab/bench.py`). An idea is a small Python function: given what was
known about a market at one moment (never its result, its actual close time or its final
volume) and its price history up to that moment, it says "buy Yes", "buy No", or nothing, and
whether it takes the ask or rests a limit order. The bench scores it and blocks every trap that
fooled us on day one:

| Trap | What the bench does |
|---|---|
| Empty order books (a 1c bid under a 99c ask looks like a 99c favourite) | A price counts only with a real bid within 5c of the ask |
| Early-close look-ahead (markets that close when the thing happens) | Times are measured back from the **scheduled** close, never from an early one; a market already shut at that moment is skipped |
| Pricing at the last trade | You always pay the ask (the No ask is 1 minus the Yes bid) |
| Missing fees | Kalshi's taker fee on every taker bet (with the series multiplier); the maker fee only on series that charge it |
| Assumed maker fills | A limit order fills only if a **later** trade went strictly through it (trade tape when the lake has it, else later hourly trade lows/highs) |

It reports: bets, events, win rate, ROI after fees, a 95% interval that resamples whole events
(a ladder of 30 strikes on one oil price is closer to one bet than to 30), a p-value, the
Brier score against the market, first half vs second half, the last 3 months, and every
category. `tests/test_lab.py` proves each trap is blocked, including a test that rewrites every
holdout result and checks the mining numbers do not move.

**3. The miner** (`python -m argus.lab mine`). Tries every simple rule on the old data only:
buy Yes or No in a price band (1-5c up to 95-99c), buy the favourite late, buy No on longshots,
bet against (or with) a 5/10/20-cent move over 1/6/24 hours; each at 1, 2, 3, 6, 12, 24, 48, 96
or 168 hours before the close; as taker or maker; over all markets, each category, and each
series with enough history. Trying that many rules guarantees some look good by luck, so:
- a rule needs 40+ events in the mining period to be tested at all;
- the survivors must pass a false-discovery control (Benjamini-Hochberg at 5%) on their mining p-values;
- the survivors are then scored **once** by the bench on the holdout (data after the holdout
  date, which mining never saw), and must pass again: 20+ events, ROI above zero, and
  Benjamini-Hochberg at 5% across the survivors.
The lake remembers how many times a holdout date has been looked at; a second look needs `--force`
and is recorded. `mine --dry` mines without looking at the holdout.
Output: `lab_survivors.csv` (every survivor, mining and holdout numbers side by side) and
`lab_holdout_log.csv` (every holdout call, in the prediction-log format, so `python -m argus board`
shows each rule as its own line).

**4. The pipeline.**
- `python -m argus.lab promote`: rules that passed the holdout become live paper loggers.
- `python -m argus.lab live`: each promoted rule looks at the open markets whose close is
  `hours` to `hours + 1` away and makes paper calls with the same function the bench used,
  into `lab_live_log.csv` (on the board). Run it hourly so every decision time gets covered.
- `python -m argus.lab settle`: fills in results; maker calls count only if a later trade on the
  public tape went through the limit.
- `python -m argus.lab nightly`: sync (90 minutes by default) + mine (only when the last cohort is
  28+ days old, with a fresh holdout date) + promote + settle + live + the lab lines of the board.
  The summary is also saved to `data/lake/nightly_last.txt`.

## Scheduling (Caleb approves; not created by this session)

Both run as plain Python, so they cost nothing (no Claude session). Paste into a Command Prompt:

```
schtasks /Create /TN "argus-lab-nightly" /SC DAILY /ST 01:30 /F /TR "cmd /c cd /d C:\Users\caleb\Work\argus && C:\Users\caleb\AppData\Local\Programs\Python\Python312\python.exe -m argus.lab nightly >> data\lake\nightly.log 2>&1"
```

```
schtasks /Create /TN "argus-lab-live" /SC HOURLY /MO 1 /F /TR "cmd /c cd /d C:\Users\caleb\Work\argus && C:\Users\caleb\AppData\Local\Programs\Python\Python312\python.exe -m argus.lab live >> data\lake\live.log 2>&1"
```

Windows Task Scheduler runs these whether or not the Claude app is open (unlike the desktop
scheduled tasks), as long as the PC is on and logged in. To remove: `schtasks /Delete /TN argus-lab-nightly /F`.

## Results

### The lake on 2026-10-23 03:15 UTC (first night, still filling)

| | Count |
|---|---|
| Series | 15,370 |
| Settled markets since 2025-04-01 (no 15-minute, no combos) | 493,242 |
| ...of which ever traded | 187,184 |
| ...with hourly candles | 50,732: every traded non-hourly live-tier market (since 2026-08-23) plus about 11,500 older ones |
| Hourly candles | 2.2 million |
| Trade tapes | 556 markets, 312,115 fills (from argus/housemap) |
| Disk | 1.02 GB (`data/lake/lake.db`) |

About 740,000 market rows and 1.2 million candles came from sibling caches for free. Two dated
findings from building it:
- **Kalshi's batch candle route returns nothing for historical-tier markets** (closed before
  2026-08-23): 14,046 of 17,157 came back empty on 2026-10-22, including markets with 30,000
  contracts traded. `/historical/markets/{ticker}/candlesticks` works, one request per market.
  The sync now uses it, which makes the older 55,000 traded non-hourly markets a multi-night job
  (about 500 an hour at the shared rate limit). Re-test cost: one batch call with an old ticker.
- **Rate limit:** 60-70% of calls got HTTP 429 all evening; seven sibling sessions were calling Kalshi.

### The miner's first cohort (`20261023-h20261001`)

Mining: markets that closed before 2026-10-01 (51,556 market-times). Holdout: decisions from
2026-10-01 to 2026-10-22.

- Rules tried: **32,240** (the enumerated grid over all eligible scopes); 14,569 had at least one
  mining bet; **2,773** had the 40+ events needed to be tested.
- **Survivors of the false-discovery control: 0.** So nothing was scored on the holdout and
  nothing was promoted.
- Of the 2,773 tested rules, 677 made money in mining and 52 had p < 0.05. Luck alone would
  give about 139 at p < 0.05, so as a whole the simple-rule space loses to the fee. That matches
  the day-one sessions' one-at-a-time findings, now on one bench and all at once.
- Closest misses (mining only, NOT evidence of an edge): buying NO on weather longshots
  (YES at or under 15-30c) as a maker 24 hours before close, +3.4% to +3.9% on 265-334 events,
  p about 1e-3 against a bar of 1.8e-5. The 20c version is paper-logged live as
  `lab-watch:longshot|category:Climate and Weather|24h|no|maker|yes<=20c`, labelled as a watch
  (it did not pass) so it collects forward evidence without being counted as a survivor.
- A second cohort (`20261023-h20260625`, 914 rules tested, 0 survivors) was mined by mistake when
  a bug made the nightly run miss today's cohort. It looked at no holdout data (no survivors) and
  is recorded. The bug is fixed and tested.

**Why zero is a real answer.** An early version used a plain t-test, which found 11 "survivors".
Most of them were 95c+ favourites that had won every bet. A t-test sees no variance in 50
straight wins; a 97c favourite with no edge wins 50 in a row about 22% of the time. Every
p-value now also has to beat the null "each bet wins exactly at its break-even rate"
(price + fee), and a no-loss streak can never be rarer than that streak at break-even. With
that, all 11 went away.

### Reference check: late-day commodity favourites

Same rule as research/commodities.md (favourite at 85-97c, 60 minutes before close, Pyth/ICE
commodity ladders, taker), run through the bench:

| | Bets | Events | Won | Avg price | P/L | ROI | 95% CI (events) |
|---|---|---|---|---|---|---|---|
| research/commodities.md, 2026-08-23..10-22 | 537 | 233 | 96% | 92.8c | +$12.01 | +2.4% | |
| Bench, same window | 511 | 221 | 96.7% | 92.9c | +$14.32 | +3.0% | +0.6% to +4.9% |
| Bench, whole lake | 546 | 236 | 96.2% | 92.8c | +$12.60 | +2.5% | +0.3% to +4.3% |

The bench reproduces it within noise (it uses hourly candles where the original used minute
candles, and the scheduled close where the original dropped early closers). Two honest notes:
the original split (AAA gas out) was chosen after seeing results, and the miner's nearest
equivalent rule (Commodities category, which includes AAA and the hourly ladders) is not
significant. The live forward test in research/commodities.md is still the real test.

### How to test your own idea (one minute)

Copy `argus/lab/ideas/example_longshot_no.py`, change the function, then:

```
python -m argus.lab bench my_idea.py
```

The example (weather longshot NO, 6 hours out, taker) prints: 494 bets on 245 events,
ROI +0.2%, CI -2.4% to +1.9%. No edge.

## Known limits (dated 2026-10-22)

- **Kalshi settles 100,000+ markets a day**, nearly all zero-volume strikes of its 78 hourly
  series (crypto, FX, hourly temperature). A full listing of every settled market is days of
  calls at the shared rate limit. So: every non-hourly series is listed in full; hourly series
  are listed 10 pages (10,000 markets) per run, newest first, and their candles come last.
- **Rate limit.** With five sibling sessions reading Kalshi from this PC, 60-70% of calls got
  HTTP 429 on 2026-10-22 (about 0.15 useful calls per second). The lake fills over several
  nights; days are visited in a fixed shuffled order, so a half-full lake is an even sample over
  time rather than only the newest weeks.
- **Selection by "ever traded".** Candles are fetched only for markets with volume above zero.
  That uses each market's final volume, which is future information, to decide what is in the
  sample. It cannot pick winners (volume is not the result), but a market that never traded is
  missing even if its book was tight. Rules are never allowed to read the volume.
- **Hourly resolution.** The finest decision time is 1 hour before close. The commodities 30- and
  15-minute versions need minute candles (argus/commodities keeps those).
- **Maker fills from candles** ignore queue position (someone ahead of us at the same price) and
  only count trades strictly through the limit; with the tape the same strict rule is used. That
  is conservative on fills and still optimistic on adverse selection being the only cost.
- **Scheduled close** comes from Kalshi's expected expiration minus the series' usual gap. If
  Kalshi ever rewrote a market's expected expiration after the fact, that market would leak.
  Checked 2026-10-22 on the lake: early closers (SpaceX launch dates, Rivian deliveries, BTS
  release dates, "WTI hits $X when") are caught as off schedule.

## Update 2026-10-23: the edge survey folded in (research/how_to_find_edges.md, sections 2, 3, 7)

**New in the bench.**
- **Order size.** `Hypothesis(..., contracts=N)` charges the fee once per order, as Kalshi does
  (`fees.taker_fee(price, N) / N` a contract). An N-contract order is allowed only if 3N contracts
  traded in the decision hour; candles hold no book depth, so volume stands in for it.
- **Closing-line value** on every call: the side's mid at the last real two-sided quote after the
  decision and before the scheduled close, minus the price paid. The report shows the mean CLV, its
  standard error, the share of calls that were positive, and CLV net of the fee. Caution: on markets
  traded while the outcome is being decided (games in play, a last-hour favourite), that last quote
  already holds most of the result, so there CLV is close to the P/L, not a cleaner signal.
- **Exits.** `Call(exit_hours=3)` sells at the side's bid 3 hours later and pays a second taker fee.
  It holds to settlement if there is no real quote then.
- **The trial registry** (`lab_trials.csv`). One row for every rule or variant ever scored: miner rules,
  `bench` runs, order-size re-scores, scans, holdout scores. It also holds placeholder rows (p = 1) for
  the first cohort's 2,773 rules, the 914 rules of the mined-by-mistake cohort, and every day-one engine
  on the board. The miner's false-discovery control now runs over the whole registry: 8,211 trials as of
  this run, so the best rule needs p ≤ 6.1e-6.
- **Live loggers** now store the top 3 book levels at call time (`book=` in notes, from
  `GET /markets/{ticker}/orderbook`) and the CLV at settle (`clv=` in notes).

**Order-size re-score** (`python -m argus.lab ordersize`). Every (strategy, N) is its own engine in
`lab_ordersize_log.csv` and its own row in the registry. ROI is after fees, per dollar staked;
CI resamples events. "Log" rows re-score the exact logged bets, with no depth check (the logs hold
none). "Lake" rows re-run the rule with the 3N volume cap.

| Strategy | N=1 | N=5 | N=10 | N=25 |
|---|---|---|---|---|
| log commodities latefav-60m (537 bets, 233 ev) | +2.41% | +2.88% | +2.94% | +2.97% [+0.4, +5.1] |
| log commodities latefav-30m (368 / 194) | +2.82% | +3.29% | +3.35% | +3.38% [+1.2, +5.3] |
| log commodities latefav-15m (291 / 184) | +2.04% | +2.51% | +2.58% | +2.61% [+0.1, +4.8] |
| lake latefav-60m with depth cap | +2.53% (562 bets) | +2.97% (513) | +3.53% (492) | +3.82% (452) [+2.1, +5.5] |
| log commodities bucket 90-99c taker (1,188 / 255) | +0.38% | +1.06% | +1.12% | +1.15% [+0.1, +2.1] |
| log commodities bucket 90-99c maker (291 / 155) | +1.67% at every N (no maker fee on these series) | | | |
| lake commodities 90-99c taker at 6 h | -0.79% | -0.58% | -0.72% | -0.81% |
| lake commodities 90-99c maker at 6 h | -1.39% | +0.23% | +0.46% | -0.84% |
| log fifteen latefav-backtest (474 / 474) | -0.96% | -0.47% | -0.41% | -0.37% |
| log fifteen maker-backtest (335 / 335) | +2.65% at every N (no maker fee), CI -6.9% to +12.0% | | | |
| log oil-filtered-maker (124 / 38) | +1.41% at every N, CI -16.5% to +20.5% | | | |
| favorites 95-99c at 1 h, saved obs (92 / 88) | -3.12% | -2.37% | -2.31% | -2.27% |
| lake favourites 95-99c at 1 h, all categories | -0.74% (4,955) | -0.28% (2,450) | -0.26% (2,229) | -0.25% (1,954) |

What changes:
- Bigger orders lift every taker favourite strategy by about 0.5 points of ROI.
- Late favourites get stronger: the 30-minute version reaches p = 0.007 at N ≥ 10, the 60-minute
  lake version p = 0.001 at N = 25.
- The 90-99c commodity taker bucket goes from break-even to about +1.1% on its logged rows, but not
  on the lake replay.
- 95-99c favourites go from losing to break-even. They never become positive.
- Maker strategies do not change, because these series charge no maker fee.
- None of it clears the registry-wide bar (p ≤ 6e-6). These are re-scores of rules that were picked
  after looking at results, so they stay hypotheses. The late-favourite forward test should log
  5-10 contracts per order (still 1 contract by hand).
- CLV net of fee is about 0 for late favourites at N ≥ 10 and about -3c for the 95-99c baskets.

**Nested-strike violations (2.2)** (`python -m argus.lab nested`). 3,528 ladder events checked.
After both taker fees: **2 violation episodes on 2 events, none lasting an hour. FAIL**; the pass bar
was 20 holdout events with a 1-hour episode.
- The first version "found" 94c violations. That was a false nesting: one KXRAIN event holds 20 cities,
  all "greater than 0".
- Now a pair counts only if the two rules texts match once the numbers are blanked out and the strikes differ.
- Kalshi's ladders are internally consistent at the hour scale.

**New miner families.**
- **movex (2.7):** the move signals, sold 3 hours later.
- **Low attention (2.5):** scopes restricted to markets whose volume traded before the decision is in
  the bottom 30% of mining rows. It uses volume up to t, never the final volume.
- **Cohort 202610231102-h20261001:** 59,048 rules enumerated, 4,390 tested (596 movex, 843 low-attention),
  **0 survivors** over 8,211 registry trials.
- Best overall: weather NO-longshot maker at 24 h again (329 events, +3.8%, p = 5.9e-4). Best movex and
  best low-attention: momentum after a 5c move in 6 h on low-attention markets (42 events, +10%,
  p = 0.012). Neither is close.
- This cohort reused the 2026-10-01 holdout (`--force`, a second recorded look). The first look had
  scored no holdout data, since it had no survivors.

**Survey ideas, status:**

| Idea | Status |
|---|---|
| 2.1 order size | done (above) |
| 2.2 nested strikes | FAIL |
| 2.5 attention | mined, nothing |
| 2.6 favourite-longshot bias by time to expiry | covered only to 7 days (the lake keeps 9 days of hourly candles); 7-120 days needs daily candles, not fetched |
| 2.7 underreaction | mined, nothing |
| 2.3 combo No | not done: the lake drops combo markets by design; needs a combo listing first |
| 2.8 popular teams | not done: needs team parsing and last season's volume shares |
| 2.9 toxic flow | not done: only 556 trade tapes in the lake |
| 2.10 Polymarket lead | not done: needs Polymarket minute history (crossvenue's data) |
| 2.4 and 2.11 | left to known-before-close and refprice, which own them |
