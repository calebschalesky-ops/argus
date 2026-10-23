# Be the maker, avoid longshots: what the paper says, and what our own data says

Written 2026-10-22 by the argus-maker thread. Paper read in full as text (three versions downloaded).
Our numbers come from `python -m argus.buckets` (code in `argus/buckets.py`) and
`python -m argus.buckets --filtered`. Paper only; Argus never trades.

## 1. The paper, checked

**"Makers and Takers: The Economics of the Kalshi Prediction Market"**, Constantin Bürgi, Wanying Deng,
Karl Whelan (University College Dublin).

| Version | Date | Link |
|---|---|---|
| Author copy | January 2026 | https://www.karlwhelan.com/Papers/Kalshi.pdf |
| CESifo Working Paper 12122 | original Sept 2025, this version Jan 2026 | https://www.ifo.de/DocDL/cesifo1_wp12122.pdf |
| GWU Center for Economic Research WP 2026-001 (titled "Makers or Takers") | Feb 2026, same text | https://www2.gwu.edu/~forcpgm/2026-001.pdf |
| UCD WP2025_19 (first version) | 2025 | https://www.ucd.ie/economics/t4media/WP2025_19.pdf (link redirected; not read) |
| VoxEU column | 2026 | https://cepr.org/voxeu/columns/economics-kalshi-prediction-market (HTTP 403 on 2026-10-22; not read) |

The sibling claims check (`research/claims_check.md`, section 9) reached the same reading; this adds the tables.

**Sample.** Kalshi's own API (they registered for a key), transaction-level, from launch in 2021 to
**April 2025**. Kept: contracts with at least $1,000 final volume, final bid-ask spread of 20c or less,
markets open at least 24 hours (hourly crypto/index markets dropped). 12,403 events, 46,282 Yes contracts.
Prices are **last traded prices** on the closing day and at 24-hour steps back to 10 days before close:
156,986 Yes prices, 313,972 counting the No side of each trade. Two thirds of observations are under 10c or
over 90c (Table 2: 106,209 in 1-10c, 106,209 in 90-99c, only 8,351 in 50-59c).

**Fees used.** Taker fee 0.07 x P x (1-P), computed on a 100-contract order and rounded up. Makers paid
nothing in the sample; Kalshi started charging some makers after April 2025, which is why the sample stops there.

**Numbers (exact):**

| Claim Caleb brought | What the paper says | Match? |
|---|---|---|
| Average contract return about -20% | "the average pre-fee return on a Kalshi contract in our data is -20%". Equal-weighted per contract price observation, **before fees**. Dollar-weighted it is zero by construction (traders swap money). | Number right; it is pre-fee and per contract, not "you lose 20% of money staked" |
| Under 10c lose 60%+ | "contracts costing less than 10c lose over 60 percent" (after taker fees, all contracts, Figure 5) | Yes |
| Above 50c earn a small positive return | "small positive returns for contracts above 50c"; **statistically significant only above 70c** | Yes, with that qualifier |
| Takers lose about 32%, makers about 10% | Takers **-31.46%**, Makers **-9.64%** (average post-fee return per contract) | Yes |
| (not in the claim) | Makers buying at **50c and over earn +2.6%**; standard deviation of that return 33% | — |
| Pattern may be shrinking | "some evidence that the bias in prices is diminishing over time": the 2025 slope is 0.021 (p<0.10) vs 0.048 in 2024 (Table 9). 2025 here means January-April only. | Weak evidence, four months |

**Caveats the headline drops:**
1. **Maker returns are conditional on being filled.** The paper sees completed trades only. Orders that rested and
   never filled are invisible, and filled maker orders are the ones a taker chose to hit. Their +2.6% is what filled
   makers earned, not what posting orders earns.
2. Sample ends April 2025, before maker fees on some series and before the 2025-26 growth (sports especially).
3. Last *traded* prices, not quotes. A strategy that reads a quote and acts on it is a different thing.
4. Below-10c losses are biggest on the closing day (a "Yogi Berra" effect); a day or more out they are smaller
   and for makers mostly not significant.
5. Bias appears in every category (Table 8), weakest and not significant on its own for Politics and Entertainment.
6. Section 6 gives three reasons it is not competed away: small volumes (top-decile markets average $526k lifetime),
   high risk (33% standard deviation per bet), and lack of awareness; the authors expect publication may erode it.

## 2. Our own data (measured 2026-10-22)

**Return per dollar** = (payout - price - fee) / (price + fee), the paper's measure. Fee per contract as for a
100-contract order. Each market counts both ways you could buy it (Yes and No). Intervals are 95% bootstrap
intervals that resample whole events, because the ranges of one event win or lose together.

**Fill assumptions, stated plainly.** TAKER = pay the ask, certain fill. MAKER = a resting buy one cent above the
best bid (or joining the bid if the spread is one cent), maker fee only where the series charges one. We cannot see
the order queue, so the maker side is shown two ways:
- *every order fills*: an upper bound that ignores adverse selection. Do not use it as a result.
- *fills only if traded through*: the order counts as filled only if a later trade printed at or through our price
  before the close (we would have been alone at a new best price, so that trade hits us). Orders on markets that
  never traded again never fill. This is the honest version, and it still flatters makers a little (queue position
  and partial fills are ignored).

### 2a. All categories, quote 24 hours before close

`python -m argus.buckets`. 30 random series per Kalshi category (fixed seed), no 15-minute, hourly or multi-leg
combo series, 1,000+ contracts traded, closing 2025-05-01 to 2026-10-21, all **after** the paper's sample, so this
is out of sample. At most 3 events per category per week and 3 markets per event. 1,607 markets sampled; 873 kept
(332 that closed a day or more early were left out, because "one day before an early close" is not a moment anyone
could pick in advance; the rest had no quote in that hour or a spread over 20c). 397 events, 102 series.
Mix: Commodities 284, Sports 224, Mentions 79, Economics 52, Science/Tech 50, Financials 47, Crypto 32, Weather 28,
other 77. The sample is small (Kalshi rate limits were shared with five other sessions), so read the intervals,
not the point estimates.

| Bucket | Taker n | Taker return [95%] | Maker, every order fills | Maker fill rate | Maker, fills only if traded through [95%] |
|---|---|---|---|---|---|
| <10c | 298 | **-60.4%** [-92, -23] | +150.0% | 79% | **-29.9%** [-90, +47] |
| 10-30c | 262 | -13.4% [-37, +6] | +18.9% | 93% | +4.8% [-20, +33] |
| 30-50c | 197 | -8.0% [-23, +8] | +9.6% | 88% | -4.3% [-23, +15] |
| 50-70c | 188 | **-11.5%** [-21, -2] | +3.1% | 92% | -2.4% [-14, +8] |
| 70-90c | 283 | **-8.5%** [-16, -2] | -3.2% | 91% | -5.9% [-15, +1] |
| 90-99c | 202 | -1.1% [-6, +2] | +1.0% | 71% | -0.5% [-5, +4] |
| All | 1,430 | -19.5% [-27, -12] | +31.7% | 86% | -6.0% [-16, +7] |

Sports alone (224 markets): taker 50-70c +0.6% [-9, +12], 70-90c -5.5% [-13, +3]; maker (traded through) 50-70c
+9.4% [-7, +26], 70-90c -4.4% [-15, +5]. Nothing significant.

### 2b. Oil and commodity ladders, at the backtest's replay time

Rebuilt from the backtest logs (each row holds the replay-time ask of the chosen side and the Yes midpoint, which
together give bid and ask; `buckets.obs_from_log_rows`). Replay = 10:00 AM New York on the settle day (dailies) or the
Wednesday (weeklies). The maker fill could not be checked market by market here (the logs hold no later trades);
it was checked for every flagged bet in section 4.

| Bucket | Oil taker (7,402 markets, 341 events) | Oil maker, every order fills | Commodity taker (7,237 markets, 163 events) | Commodity maker, every order fills |
|---|---|---|---|---|
| <10c | **-80.6%** [-88, -70] | -28.5% | **-87.9%** [-95, -78] | +27.9% |
| 10-30c | -34.7% [-47, -20] | -13.1% | -41.7% [-58, -23] | +17.6% |
| 30-50c | -19.3% [-26, -13] | -3.1% | -26.3% [-39, -15] | +9.3% |
| 50-70c | -4.3% [-8, -0] | +5.6% | -8.2% [-14, -3] | +11.3% |
| 70-90c | +1.5% [-2, +5] | +6.1% | -8.1% [-14, -3] | +8.6% |
| 90-99c | +0.2% [-1, +1] | +2.3% | -6.2% [-11, -2] | +2.2% |

### 2c. What our data says about the claims

- **Longshots lose: confirmed, and worse than the paper on ladders.** Taking the ask under 10c lost 60% across
  categories and 81-88% on the oil and commodity ladders, where cheap strikes have wide spreads.
- **"Above 50c earns a small positive return": not confirmed for takers.** Across categories, 50-70c and 70-90c
  lost 8-12% at the ask (significant). Only oil 70-99c is about break-even. Taking the ask lost money at every
  price in our sample.
- **Makers do better than takers: confirmed,** by about 5-15 points per bucket. But once fills are checked, the
  maker side is around zero or negative everywhere (all buckets: -6.0% [-16, +7]), and no bucket is significantly
  positive. The "every order fills" column is badly misleading: under 10c it says +150%; with fills checked it is
  -30%. The orders that fill are the ones that should not have.
- **Coordination:** argus-favorites (`argus/favorites.py`) measures Yes-side 70-99c at 1 h, 1 d, 7 d and 30 d before
  close, with the taker fee. Its report did not exist yet when this was written (no `research/favorites_backtest.md`
  on 2026-10-22), so the 70-99c rows above come from our own sample. Read them next to that report when it lands.

## 3. The filter (live in the engine)

`argus/filters.py`, shared by every engine:
- **Price band.** By default, flag only a side whose price is **50c or more** (up to 99c), and **never one under
  10c**, even if someone configures a lower minimum. A 10+ point gap on a cheaper side shows as `UNDER 50c`, not
  WORTH A LOOK. CLI: `run --min-price 0.6`, `run --no-band` (the 10c floor stays).
- **Maker pricing.** `filters.maker_price(bid, ask)` = bid + 1c, never crossing the ask. `fees.maker_fee()` =
  ceil(0.0175 x C x P x (1-P)), charged only where the series' `fee_type` includes maker fees
  (`fees.series_maker_fees(series)`). On 2026-10-22 that was 160 of 15,354 series, and no oil or commodity series.
  The alert shows a **Maker** column next to the asks and a `Maker:` line under the top call.
  `run --pricing maker` makes paper bets assume the resting order fills (noted `maker_fill=assumed` in the log).
- **For other engines:** `filters.check(p_side, ask, bid, band, maker_fees)` returns both prices, both fees, both
  EVs and `.flag(pricing)`.

## 4. The backtests through the filter

`python -m argus.buckets --filtered` re-scores every replayed market with the same model probabilities and the same
replay-time quotes. Only the side choice, the price paid and the bets change. Maker bets count only if a later trade
printed at or through the resting price before the close; this was checked from Kalshi's hourly candles for every
flagged bet. Results go to `backtest_filtered_log.csv`, one strategy per line on `python -m argus board`.

| Strategy | Flagged | Bets | Won | Events with a bet | P/L | ROI | Brier model / market | Board |
|---|---|---|---|---|---|---|---|---|
| Oil, band, taker | 56 | 56 | 32 | 21 | -$9.15 | **-23%** | 0.0709 / 0.0631 | NO EDGE |
| Oil, band, maker (fill checked) | 225 | 124 (55% filled) | 88 | 38 | +$1.22 | **+1%** | 0.0879 / 0.0774 | NO EDGE |
| Commodity, band, taker | 94 | 94 | 61 | 15 | -$9.75 | **-14%** | 0.0853 / 0.0748 | NO EDGE |
| Commodity, band, maker (fill checked) | 807 | 115 (14% filled) | 70 | 24 | -$15.92 | **-19%** | 0.1172 / 0.1032 | NO EDGE |

**Verdict: the filter does not turn NO EDGE into an edge.** It cuts the damage (the unfiltered commodity run lost
40% on 434 bets) mainly by betting far less. On every version the model's probabilities are still worse than the
market's (higher Brier score), so the +1% oil maker result is noise on 124 bets, not a strategy.

The maker fill check matters. Assuming every commodity maker order filled would have shown +28% on 806 bets. Only
115 of them would actually have filled, and those lost 19%.

**Bug found and fixed on the way:** when nobody was selling Yes, the backtest writes the Yes midpoint as equal to the
bid. My first rebuild turned that into an ask equal to the bid, which showed a fake +22% for the commodity taker
run. `obs_from_log_rows` now returns no ask in that case, and a test covers it.

## 5. What this means for money

- Never take the ask on anything under 30c. The paper and our data agree on that.
- Taking the ask above 50c is not a free edge in 2025-26 data: it lost money in most of our buckets.
- Resting orders beat taking, but in our sample they only roughly break even once you count which orders fill.
- So the band is a guard rail, not a strategy. An engine still needs a model that beats the market's Brier score.
