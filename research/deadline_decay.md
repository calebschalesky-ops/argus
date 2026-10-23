# Deadline decay: is No underpriced late in "Will X happen by <date>?" markets?

Session argus-decay, 2026-10-22. Code: `argus/decay/`. Tests: `tests/test_decay.py`.
Log: `decay_log.csv` (backtest rows `engine=decay-<variant>`, live rows `decay-<variant>-live`).

## The idea in plain words

A "by Friday" question that has not happened yet should get cheaper every day, because there is
less time left for it to happen. If the chance per day is steady (a constant hazard), a market at
30c with 20 days to go should be about 16c with 10 days to go, if nothing happened in between.
If the crowd is slow to mark that down, buying No late in the window would make money.

## How it was tested

- **Which markets.** Kalshi tags these markets with an early-close condition: "This market will close
  and expire early if the event occurs." Every settled market since 2025-01-01 carrying it, in
  Politics, World, Companies, Science and Technology, Entertainment, Economics, Health, Social and
  Transportation (no sports, crypto, weather, mentions, commodities, elections or financials; no
  daily/weekly/hourly series), whose deadline could be read from its rules ("before Jul 1, 2026").
- **The trap that matters.** These markets close the minute X happens. "3 days before the close" of
  a market that closed early is a moment nobody could pick in advance, and it leaks the answer
  (the favourites session found 95-99c early-closers winning 100% from exactly this). So every
  moment here is counted back from the **scheduled deadline read from the rules**. A market already
  closed at a check moment is not traded (you would have seen it resolve). A market still open is
  traded with its real result: if X happens afterwards, it closes early as Yes and **the No bet loses**.
  Tests prove both (`test_early_yes_close_is_a_no_loss_when_open_at_the_check`,
  `test_market_already_closed_at_the_check_is_not_traded`, `test_not_measured_from_the_close`,
  `test_check_grid_comes_from_the_deadline_not_the_close`).
- **Deadline check.** Kalshi's `latest_expiration_time` must sit between 1 day before and 8 days
  after the deadline read from the rules (it is the deadline, or the deadline + 7 days). NOT
  `expected_expiration_time`: on ladders that is a far-off fallback date, and on at least one early
  Yes close (`KXTARIFFLENGTHCAN-25-APR01`) Kalshi overwrote it with the close date, so a check on it
  would quietly drop the No losers. The chosen check keeps 98% of early Yes closes and 99% of No
  markets: the same rate, so it does not leak the result.
- **Prices.** Daily candles; the price at a moment is the last candle that ended at or before it
  (at most 2 days old), only with a real Yes bid (1c+) within 5c of the Yes ask. Buying No costs
  1 - Yes bid (the No ask). Never the last trade. Taker fee from `argus/fees.py` with the series'
  fee multiplier.
- **Rules (written down before any result was seen).**
  - `hazard`: last 25% of the scheduled life, checked once a day. Hazard from the Yes mid 7 days
    earlier: fair Yes now = 1 - (1 - p0)^(time left now / time left then). Take No at the ask on the
    first day the No edge vs that fair price is 3c+ after the fee and Yes is 60c or less. One bet per market.
  - `hazard-maker`: same signal, rest a No bid 1c above the No bid. Counted as filled only if a
    later daily candle printed a Yes trade at or above (1 - our No price). Queue position is not
    proven, so this is the best case for fills.
  - `simple-Kd`: No at the ask exactly K days before the deadline (K = 3, 7, 14) when Yes is 10-40c.
- **Statistics.** ROI = P/L after fees / money paid including fees. 95% range from resampling whole
  events (bets on one event move together). Both halves split at the median bet date. Recent = bets
  placed since 2026-07-22. Worst cluster = the event that lost the most.

## Results (run 2026-10-23, all 6,255 kept series listed)

**Data.** 28,989 settled markets carry the early-close condition. Most are award, ranking and
"winner" markets that close when a ceremony happens; 7,405 have a dated "before/by" deadline. 7,227
pass the deadline check, 6,406 have a price history, across 1,526 events: Politics 3,429,
Entertainment 2,166, Science and Technology 485, Companies 103, Economics 94, World 65, Social 42.
2,133 resolved Yes, and 1,691 of those closed early (1+ day before the deadline). Every one of them
that was still open at a check moment is counted as a No loss.

| Variant | Bets | Events | Won | ROI after fees | 95% range (events resampled) | 1st / 2nd half | Since 2026-07-22 | Worst event | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| hazard | 260 | 164 | 83% | +3.1% | -4.0% to +9.5% | +1.3% / +4.9% | n=36, +4.5% | KXBIDENPARDON-25JAN21B, 11 bets, -$3.88 | NOT PROVEN |
| hazard-maker | 228 | 148 | 80% | +5.3% | -2.9% to +13.2% | +4.8% / +5.9% | n=30, +6.0% | same, -$3.58 | NOT PROVEN (best-case fills) |
| simple-3d | 135 | 71 | 85% | +3.2% | -7.3% to +12.0% | -8.1% / +14.4% | n=24, +11.3% | same, -$3.42 | NOT PROVEN |
| simple-7d | 171 | 102 | 85% | +2.7% | -7.0% to +11.0% | +4.4% / +1.0% | n=26, +4.7% | same, -$4.25 | NOT PROVEN |
| simple-14d | 207 | 125 | 89% | +6.4% | +0.5% to +11.9% | +6.4% / +6.5% | n=20, +6.0% | same, -$3.17 | LEAD: forward-test it |

(The board prints slightly different ROIs because it divides by price only, not price + fee.)

**Does the hazard model beat the market's price?** No. On the No side its Brier score is 0.1379
against 0.1276 for the market midpoint. The constant-hazard clock is a worse forecaster than the
crowd. Where the rule makes money, it is because Yes at 10-40c is too dear in general, not because
the clock is right.

**Why simple-14d is a lead, not a pass.**
- Five variants were tried. One clearing a 95% bar by +0.5% is about what luck alone delivers.
- Resampling whole **series** instead of events (one artist's album dates or Trump's meetings move
  together) widens the range to -0.7% to +12.0%. That crosses zero.
- It leans on Entertainment (30 bets, 100% won, +19%). Without Entertainment: +4.3%, range -3.0% to +10.3%.
- Politics carries the losses on every variant: the Biden-pardon ladder (`KXBIDENPARDON-25JAN21B`,
  the pardons that happened on his last day) is the worst event everywhere. That is the strategy's
  real risk: a 10-40c Yes that happens all at once, across a whole ladder.

**Calibration (Yes midpoint vs how often Yes happened, markets still open then):**

| Days to deadline | 10-25c band: price / happened | 25-40c band: price / happened | 2-10c band: price / happened |
|---|---|---|---|
| 14 | 16.2c / 9.1% (164 markets, 112 events) | 31.5c / 19.0% (42, 26) | 4.2c / 2.3% (563) |
| 7 | 15.9c / 10.1% (129, 81) | 30.9c / 30.0% (40, 31) | 4.2c / 1.3% (551) |
| 3 | 15.8c / 11.0% (100, 57) | 31.3c / 28.1% (32, 23) | 4.1c / 1.7% (517) |

Yes is overpriced at 10-25c by about 5-7 points at every horizon, and 2-10c longshots happen less than
half as often as priced. But a No bought at 84-96c pays back only a few cents per win, and the
taker fee plus one surprise Yes eats most of it. The 25-40c band is overpriced 14 days out, then
roughly fair at 3-7 days. That fits a crowd that does mark the clock down, just a little late.

Full tables, every category: `python -m argus.decay backtest` (writes `data/cache/decay/report.txt`).

## Verdict

- **hazard / hazard-maker: NO.** Small positive ROI, ranges include zero, and the model is a worse
  forecaster than the market. Do not fund. The maker version's fills are a best case (a later Yes
  print through our price), so its extra +2 points are not real until fills are proven.
- **simple-3d / simple-7d: NO.** Ranges straddle zero; 3d lost money in the first half.
- **simple-14d: forward-test only.** No at the ask 14 days before the deadline when Yes is 10-40c.
  +6.4% on 207 bets, but see the caveats above. It needs its own forward record before any money.

## Live

`python -m argus.decay live` lists today's qualifying open markets; `--log` adds them to
`decay_log.csv` as `decay-<variant>-live` rows (one per market per rule, ever);
`python -m argus.decay settle` fills in results. A full run reads ~100 pages of open events plus
recent candles for late-window markets: about 20 minutes while sibling sessions share the rate limit.

**Stopping rule for simple-14d (written before any live result):** after 60 settled live bets across
20+ events, keep it only if ROI after fees is above 0 **and** no single event lost more than 25% of the
total stake. Otherwise drop it. Do not tune the band or K on live results.

## Known gaps

- Daily candles only: a check moment uses the previous day's close, which may be up to 2 days old.
- Deadlines are read with patterns ("before Jul 1, 2026", "by March 31, 2026", "before 2027");
  "before the middle of 2026" and similar are skipped (counted in "deadline read").
- Series in elections, financials, sports, crypto, weather, mentions and commodities were left out
  by design; elections "will X announce a run by" questions are a possible extension.
