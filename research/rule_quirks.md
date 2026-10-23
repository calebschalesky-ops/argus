# Rule quirks: where Kalshi's rules differ from the casual reading

Thread argus-rulequirks, 2026-10-22. Code: `argus/rulequirks/` (`python -m argus.rulequirks scan|mattered|backtest|report`).
Tests: `tests/test_rulequirks.py` (35). Log: `rulequirks_log.csv` (on the board). Read-only, public data, every call
logged to `data/rulequirks/api_calls.csv`. It never trades.

**Bottom line.** Kalshi's rules are full of quirks: 19 types, found in 4,936 open series. But where a quirk actually
decided a settled market, the price beforehand had mostly already priced the rule. None of the three strategies built on
quirks makes money after fees. Verdict: no edge. The useful output is the catalogue, which works as a reading checklist,
plus six live series whose title and rules disagree.

## 1. Method

- **Open markets:** every open market with no combos (`mve_filter=exclude`), downloaded 2026-10-22 22:00 ET:
  165,968 markets, 16,264 events, 4,936 series. The 27 open 15-minute markets were skipped.
- **Classifier** (`classify.py`): plain regex rules over `rules_primary` + `rules_secondary`, plus Kalshi's strike
  fields. For thresholds, the rules sentence decides, not the strike field. Example: Kalshi stores "greater than 39.5"
  for a market whose rules say "at least 40 goals". Those are the same contract, so the classifier reads it as
  inclusive at 40. A half-point line on a count ("over 33.5 points") is not flagged, because a count can never land on it.
- **Settled corpus:** 459,648 settled markets, taken from listing pages the favorites and commodities threads had
  already cached (about 800 series across every category, 2021 to 2026). No new listing calls were made.
- **Price check, no look-ahead:** the Yes bid and ask 1 hour and 1 day before close, read from candles that ended before
  that moment. A price only counts with a real book, meaning a bid within 5c of the ask. Markets that closed early (the
  event happened) are left out of every price test.

## 2. The catalogue (open markets, 2026-10-22)

Counts are per series, event and market. One rule text gets copied onto every strike of a ladder, so the series count
is the honest one.

| Quirk | Series | Events | Markets | Casual reading | What the rules say | Example |
|---|---:|---:|---:|---|---|---|
| Strict threshold | 1,160 | 2,379 | 27,313 | landing on X counts | "above X": X itself is No | KXCPI "more than 0.6% (single-decimal)" |
| Counting exclusions | 611 | 1,294 | 13,857 | any mention counts | exact/plural/possessive forms only; replays and prior footage excluded | mention markets, NFL props ("does not count") |
| Fair-value settlement | 593 | 5,127 | 39,997 | every contract pays $1 or $0 | some cases settle at a fair or last price | KXNCAAFGAME: not played in 48 h, fair price |
| Postponement / delay | 571 | 5,584 | 33,665 | a delayed event still counts | deadline moves or the payout changes | KXNCAAFGAME 48-hour window |
| Tie rule | 529 | 3,025 | 19,404 | someone wins | both No, 50/50, proportional, or a Tie market | KXNCAAFWINS "ties do not count as wins" |
| Clock time / zone | 485 | 1,505 | 9,012 | the calendar day | a stated clock time (ET; sometimes UTC) | KXCPI closes 8:29 AM ET |
| Cancellation | 481 | 4,060 | 19,504 | void and refunded | resolves No, 50/50 or to a stated market | KXMIDTERMVOTETURN: canceled, all No |
| Inclusive threshold | 408 | 3,040 | 32,942 | X is a bar to beat | "at least X": X itself is Yes | KXVOTEGENERAL "at least 34%" |
| Rounding / precision | 364 | 2,516 | 31,662 | the raw number decides | rounded or read at stated decimals | CPI single-decimal; KXMIDTERMMOV "no rounding" |
| Regulation time only | 339 | 2,471 | 10,682 | the final result | 90 minutes plus stoppage; no extra time or penalties | KXMLSGAME, KXESOCCERGAME |
| First release only | 323 | 579 | 8,906 | the true, revised number | first published value; revisions ignored | KXUFCFIGHT, KXHOUSEWINSTATE |
| Averaged / windowed value | 304 | 422 | 9,704 | the price at the moment | 60-second average, 1-minute candle close, monthly average | KXBNB, KXDDR5EMS |
| No-data rule | 193 | 295 | 4,130 | it waits for the data | falls back to the last published value or a fair price | KXWTIH, KXLFPRATEEOY |
| Specific station / report | 117 | 185 | 1,191 | "the temperature in Austin" | one NWS CLI station, or The Weather Company | KXHIGHAUS (CLIAUS), KXRAINNAPAM (KAPC) |
| Specific contract / index | 87 | 106 | 4,545 | "the oil price" | Dec-26 contract; CF Benchmarks RTI; Pyth; Truflation | KXWTI (December 2026 contract) |
| Can close early (stated) | 81 | 81 | 559 | trading runs to the close | resolves immediately once known | combo / "becomes impossible" markets |
| Touch / any time | 45 | 77 | 794 | judged at the end | touching once is enough | KXYTVIEWSW "at any point during" |
| 'None' / 'does not qualify' | 29 | 36 | 647 | a named outcome wins | "Event does not qualify" takes the payout | KXTRUMPMENTION |
| **Title vs rules mismatch** | **6** | **8** | **90** | the title says what pays | the title and the rules disagree about X itself | see below |

The `can_close_early` flag is set on 99.7% of settled markets, so on its own it says nothing. Only rules that spell
out an early close are counted.

**Live title-vs-rules mismatches (read the rules before trading these):**
- KXGOLDDIRY (gold, Dec 31): subtitle "5,500 or above", rules "above 5500". At exactly $5,500.00, No.
- KXWTIDIRY (WTI, Dec 31): subtitle "95 or above", rules "above 95".
- KXINXDIRY (S&P, Dec 31 2027) and KXNDQDIRY (Nasdaq, Dec 31): subtitle "9,800 / 42,000 or above", rules "above".
- KXLAMOV, KXAKMOV (margin of victory): title "above 16% / 12%", rules "16% to 100%, inclusive of its lower bound".

Each of these turns on a value landing on a round number to the cent or the point. That is rare, so they are a
reading hazard, not a trade.

Per-series rows are in `data/rulequirks/scan_2026-10-22.csv`; totals are in `scan_2026-10-22_summary.json`.

## 3. Where a quirk decided the outcome (settled corpus)

| Case | Markets | Series | Price beforehand (rule side = the side the rules paid) |
|---|---:|---:|---|
| Strict threshold, settled exactly on X (No) | 430 | 42 | 1 day out: rule side favoured in 139/168 priced (median 83c). 1 hour out: 38/52 (median 77c); only 4 had the rule side under 20c |
| Inclusive threshold, settled exactly on X (Yes) | 42 | 5 | 1 hour out: rule side favoured 19/22 (median 94c) |
| Title-vs-rules mismatch, settled on X | 0 | 0 | never happened in the corpus |
| Settled between 0 and $1 (fallback clause) | 361 | 28 | 1 hour out: median gap to the settle price 2c; 39/47 within 10c |
| 'Tie' / 'None' outcome won | 196 | 15 | Tie priced at a median 33c (1 h) and 27c (1 d). A real chance, not ignored |
| Closed early (counted only: a look-ahead trap) | 5,966 | 175 | not priced; "N hours before close" there is the future |

Where the boundary decided it: KXWTIH 87, KXLOWTDC 36, KXHIGHNY 33, KXHIGHTNOLA 31, KXHIGHTSEA 31, CPI 31, KXCPI 20.
Fallback clauses, by what the rules say: tie 152, unstated 159 (mostly NASCAR pole at fair value), cancellation 25,
fair value 25. Examples: NFL preseason ties paid 50c to both sides and traded at 49.5c an hour before. CS2 cancellations
paid 40c/60c and traded at 37c/61.5c.

Boundary rungs were less certain than their neighbours: median 64c against 92c for the next rung of the same event
(29 pairs). That is the expected cost of sitting on the line, not a misreading. Across every type, the crowd mostly
priced the rule.

Not scored: the station, contract-month, averaging, first-release and clock-time quirks. Scoring them needs the
outside number a casual reader would have used (another airport, the revised print, the spot price). The weather and
commodities threads measured those sources.

## 4. Strategies (backtest, `rulequirks_log.csv`)

All bets are taker at the ask, 1 contract, fees in, real book only. model_prob is walk-forward: the strategy's own win
rate on bets already settled at the time of each call.

| Engine | Idea | Bets | Events | Win | Avg price | P/L | ROI | Brier model/market | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| rulequirk-strict-1d | strict "above X" where the series has landed on its own lines before: buy No at 15-85c | 108 | 98 | 53.7% | 59c | -$7.27 | -11.5% | 0.240/0.204 | **NO EDGE** |
| rulequirk-strict-1h | same, 1 h before close | 14 | 14 | 50% | 51c | -$0.39 | -5.5% | tie | TOO EARLY |
| rulequirk-inclusive-1d | "at least X" on the same kind of series: buy Yes | 11 | 10 | 27% | 53c | -$3.01 | -52% | tie | TOO EARLY |
| rulequirk-inclusive-1h | same, 1 h | 3 | 3 | 67% | 35c | +$0.89 | +84% | tie | TOO EARLY |
| rulequirk-tie-1h | buy the Tie/Draw outcome at 5-60c | 438 | 437 | 26.5% | 29c | -$18.67 | -14.8% | 0.196/0.186 | **NO EDGE** |
| rulequirk-tie-1d | same, 1 day out | 219 | 219 | 22.8% | 26c | -$11.27 | -19.7% | 0.177/0.170 | **NO EDGE** |

Sampled universes: strict 1,789 markets (2 rungs per event, 40 events per series), inclusive 95, tie 787 (150 per
series). Most had no real book in the band at the call time, so they placed no bet. No market went unpriced because
of rate limits.

What this says: the casual-reader mispricing is not there to take. Ties won 26.5% of the time at an average price of
29c, so if anything they are slightly overpriced, not underpriced. That was seen after the fact and is not a strategy
(the No side would clear about 2c before the spread). Strict-threshold No at 1 day lost 11.5%.

## 5. Next

1. Use the catalogue as a checklist before any hand trade: tie, regulation-only and fair-value clauses change what a
   contract is worth when something goes wrong.
2. If anyone revisits: the station and first-release quirks are the ones a price check here cannot see. Pair the
   weather thread's station data with NWS CLI reports, or BLS first prints with revisions.
3. Re-run `scan` occasionally to catch new title-vs-rules mismatches. They are rare but real.
