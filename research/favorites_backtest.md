# Favorites basket backtest (2026-10-22)

**Question:** if you spread money over many high-probability Yes contracts across Kalshi
categories, do the favorites win often enough to beat the taker fee?

**Short answer: no.** Paying the ask, favorites from 70c to 99c lost 1.6 cents on the dollar
after fees (886 contracts across 566 events). In Caleb's 80-95c band they lost 3.1 cents on the dollar
(229 contracts). No bucket with a decent sample beat the fee. The few cells that show a profit
have 5 to 46 contracts, and their 95% ranges include the break-even rate.

Full tables, with n for every cell: [favorites_tables.md](favorites_tables.md).
Code: `argus/favorites.py`, re-run with `python -m argus.favorites --report-only`.

## What was tested

- **Sample.** 927 Kalshi series picked at random, 60 per category (every category,
  no 15-minute series, no multi-leg combos). Their settled markets that closed between
  2025-04-01 and 2026-10-20 came to 66,361 markets. From those, 6 events per category per week and
  3 markets per event were drawn at random, 5,396 markets in all. The draw never looked
  at price or result.
- **Prices.** The Yes ask at 1 hour, 1 day, 7 days and 30 days before close, from Kalshi's
  public candles. Only candles that ended at or before that moment were used. A price
  counted only if there was a bid within 5c under it. If the candle had no ask, the last
  trade counted, under the same bid rule.
- **Fee.** Kalshi's taker fee, 7% x P x (1-P) rounded up to the cent (`argus/fees.py`).
  That is 1c a contract at 90-99c and 2c at 70-85c.
- **Break-even.** You need to win more often than price + fee. At 95c that is 96%. At 98c it is 99%.

## Two traps the first run fell into (fixed, kept here so nobody repeats them)

1. **Empty order books look like favorites.** A dead market often shows a 99c ask over a
   1c bid. The first run counted those as 99c favorites and found 98c contracts winning only
   85% of the time. Of the 95-99c contracts that lost at the 1-hour mark, 84 of 94 had no bid or a spread of 20c or more.
   The 5c spread rule removed them. With real books, favorites are priced about right.
2. **Early closes leak the future.** A "will X happen by December" market closes the day X
   happens, so its outcome is a Yes. Measuring "7 days before close" on that market picks a moment
   nobody could have known in advance. These markets were kept out of the main table. Their
   own table shows the bias: 95-99c early-closers won 100% at every horizon (174 contracts).
   That is the look-ahead, not an edge.

## Results (all categories pooled, priced at the ask, after fees, early closes excluded)

| band | horizon | n | events | actual win | break-even | return per $ |
|---|---|---|---|---|---|---|
| 80-95c | 1 hour | 87 | 85 | 86.2% | 89.0% | -3.1% |
| 80-95c | 1 day | 115 | 103 | 84.3% | 88.4% | -4.6% |
| 80-95c | 7 days | 17 | 15 | 94.1% | 89.1% | +5.6% (too few to trust) |
| 80-95c | 30 days | 10 | 8 | 90.0% | 90.4% | -0.4% |
| 95-99c | 1 hour | 357 | 297 | 98.9% | 99.5% | -0.7% |
| 95-99c | 1 day | 138 | 115 | 97.8% | 98.9% | -1.1% |
| 95-99c | 7 days | 46 | 38 | 100.0% | 99.1% | +0.9% (range 92-100% includes break-even) |
| 95-99c | 30 days | 4 | 4 | 100.0% | 98.5% | +1.5% (4 contracts) |
| 70-99c | all | 886 | 566 | 92.2% | 93.7% | -1.6% |

**Does any bucket beat the fee?** Not with a sample big enough to say so.
- The biggest cell, 95-99c at 1 hour (357 contracts), loses 0.7 cents per dollar. Favorites
  there win about 99% of the time, which is what the price says, and the 1c fee is the loss.
- The best-looking cell is 95-99c at 7 days: 46 out of 46 won, +0.9% per dollar over 7.9 days.
  But a 1-in-100 loser is normal at that price, so 46 straight wins is close to what fair
  pricing predicts. One loss in 46 would turn it to about -1.3%. It is not evidence of an edge.
- At 7 and 30 days the samples are small (5 to 46 per cell). Few markets have a real
  two-sided book a week or a month before they close.

**Last trade vs ask.** Priced at the last trade, the same favorites show +0.4% (324 contracts).
Priced at the ask, they show -2.9% (562). You can only buy at the ask, so a backtest on last
prices would have shown a profit that does not exist.

## The weekly basket

Each week, buy one Yes contract of every market in the bucket, at most 5 per category per
week and 3 per close date. The full grid is in the tables file. The lines that matter:

| basket | contracts | won | P/L | worst month | worst day | worst cluster | P/L without it |
|---|---|---|---|---|---|---|---|
| 95-99c, 1 day out | 133 | 130 | -$1.51 on $131.51 (-1.1%) | 2026-04 -$1.79 | 2026-04-20 -$1.94 | Economics, 2026-04-20: -$1.94 | +$0.43 |
| 95-99c, 1 hour out | 330 | 326 | -$2.42 on $328.42 (-0.7%) | 2026-08 -$0.91 | 2026-08-18 -$1.00 | Commodities, 2026-08-18: -$1.00 | -$1.42 |
| 90-95c, 1 day out | 42 | 36 | -$3.04 on $39.04 (-7.8%) | 2026-10 -$1.60 | 2026-10-13 -$0.94 | Sports, 2026-10-13: -$0.94 | -$2.10 |
| 95-99c, 7 days out | 45 | 45 | +$0.40 on $44.60 (+0.9%) | none lost | none | none | +$0.40 |

The 95-99c baskets have one shape: most months make 0 to 21 cents, and then one day takes
back a whole year. In the 1-day basket, one cluster of correlated Economics contracts
settling on 2026-04-20 lost $1.94. Every other month together made $0.43. That is the
correlation trap: the caps limit how many contracts sit on one date, but they cannot stop one
macro surprise hitting the contracts that are left.

## What it would make per month (realistic)

Based on the measured return per dollar at the ask, recycling the money once a week
(about 4.3 times a month):

| capital | 80-95c band (-3.1% per turn) | 95-99c (-0.6% per turn) | 95-99c if the 7-day cell were real (+0.9% per turn) |
|---|---|---|---|
| $10 | about -$1.30 a month | about -$0.30 | about +$0.35 |
| $500 | about -$65 | about -$14 | about +$17 |
| $5,000 | about -$660 | about -$135 | about +$170, if the books were deep enough, which they mostly are not |

The best case on $10 is about 35 cents a month, and a single 98c loser costs 98 cents. At
$5,000 you would need about 50 tight-book 95-99c favorites closing a week out every week.
The sample found 46 such contracts in 18 months of randomly drawn series. Liquidity runs out
long before the money does.

## Verdict

**Do not run the favorites basket as a money strategy.** On Kalshi, favorites are priced about
right once you use real asks, and the taker fee (1c at 90-99c, 2c at 70-85c) is larger than
any mispricing this sample can see. Diversifying across categories does lower the swings, but
it turns a small loss per contract into a steady small loss. It also keeps the tail: one
correlated bad day wipes out months of gains.

What could still change this (not tested here):
- **Maker orders.** Resting a limit at bid + 1c instead of taking the ask saves the spread.
  Most series also charge makers no fee. The fill is not guaranteed, and the fills you do get
  skew toward the trades that go against you. The live paper basket logs both prices
  (`favorites-basket` and `favorites-basket-maker`), so the board will measure it.
- **The 7-day 95-99c cell**, if a bigger sample confirms it. Re-test cost: re-run with
  `--events-per-cat 12`. About 2,800 more markets, roughly 1 hour at 4 requests a second
  when sibling sessions are not also reading Kalshi. That run was started on 2026-10-22 and
  stopped at 3,500/8,230 markets because of rate limits. The candles it fetched are cached.

## Live paper basket (started 2026-10-22, paper only)

`python -m argus.favorites basket` logs today's favorites to `favorites_log.csv`:
- open markets closing within 7 days, side price 80-95c, never under 10c
- 24h volume of 500 or more, and a bid within 5c of the ask
- at most 3 per category, 2 per close date, 2 per event

Each pick is priced two ways: taking the ask (with the taker fee), and a limit at bid + 1c
(with the maker fee only where the series charges one). `python -m argus.favorites settle`
scores them, and `python -m argus board` ranks them.

First run, 2026-10-22 21:36 UTC: 333 liquid favorites found (280 of them Sports), 9 picked
after the caps, 18 rows logged. All 9 settle within 7 days, by 2026-10-29. The picks were the
Fed holding rates on Oct 28 (Yes 94c) and no hike (No 95c), both in the same event. Also:
Giants-Texans (No Giants 81c), Dameon Pierce no TD (No 87c), Barcelona-Real Madrid no tie
(No 85c), a Rotten Tomatoes score above 70 (Yes 94c), Google share above 16.6% (Yes 91c),
Trump Truth Social posts above 240 (Yes 82c) and not 220-240 (No 86c).
The two Fed picks are one bet in two forms, and so are the two Truth Social picks. The event cap allows two per event,
so the real number of independent bets here is 7.
