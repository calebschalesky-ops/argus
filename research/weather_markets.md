# Kalshi weather markets: the map

Read from Kalshi's public API on 2026-10-22 (about 1 PM Pacific). Read-only, no login.
Re-run with `python -m argus.weather markets --all`.

## What is listed

Kalshi lists 458 series in "Climate and Weather". Most have no open market. The ones that trade every
day are **daily temperature** markets for U.S. cities (a high and a low for each), plus one big
**daily rain** market. Weekly averages, monthly snow and rain, hurricanes, foreign cities and ski
resort openings exist but are thin or closed.

### Shape of a daily temperature event (example `KXHIGHNY-26OCT23`, NYC high on Oct 23)

| Market | Kalshi strike | Pays YES if the settled whole-degree value is |
|---|---|---|
| `-T57` | less, cap 57 | 56 or below |
| `-B57.5` | between 57–58 | 57 or 58 |
| `-B59.5` | between 59–60 | 59 or 60 |
| `-B61.5` | between 61–62 | 61 or 62 |
| `-B63.5` | between 63–64 | 63 or 64 |
| `-T64` | greater, floor 64 | 65 or above |

- Six markets per event: two tails and four 2-degree ranges, centred on the expected value.
- Opens 10:00 AM New York the day **before** the target day. Closes at local midnight standard time
  at the end of the target day (05:00 UTC for Eastern cities, 08:00 UTC for Pacific). Expected payout
  about 19:00 UTC the next day.
- Two events are open at any time (today and tomorrow).

### Settlement (read from the rules text, 2026-10-22)

> "If the maximum temperature recorded at New York City (CLINYC) for Oct 23, 2026, is greater than 64°
> fahrenheit according to The Weather Company, then the market resolves to Yes."

- Every temperature market names an **NWS Daily Climate Report (CLI)** station, e.g. CLINYC = Central
  Park, CLIMDW = Chicago **Midway** (not O'Hare), CLIHOU = Houston **Hobby** (not Bush), CLIDCA = Reagan
  National, CLIMSY = New Orleans airport.
- The official number comes from **The Weather Company** (weather.com/kalshi), not straight from NWS.
- **Checked (MEASURED-HERE, 2026-10-22):** Kalshi's settled value vs the NWS CLI report (Iowa State
  archive of the CLI products), 2025-01-01 to 2026-10-21, seven series:

| Series | Station | Days compared | Same | Different |
|---|---|---|---|---|
| KXHIGHNY | KNYC | 589 | 588 | 1 (2025-06-18: Kalshi 84, CLI 83) |
| KXHIGHMIA | KMIA | 580 | 578 | 2 (2026-08-29: Kalshi 90, CLI 85) |
| KXHIGHCHI | KMDW | 595 | 594 | 1 |
| KXHIGHLAX | KLAX | 591 | 589 | 2 |
| KXLOWTNYC | KNYC | 298 | 298 | 0 |
| KXHIGHDEN | KDEN | 597 | 596 | 1 |
| KXHIGHAUS | KAUS | 591 | 591 | 0 |

  So the claim "settles on the NWS climate report" is **true in practice 99.8% of the time** (3,834 of
  3,841 days). The rare differences are usually 1 degree, so they matter only at range edges.
- Rain (`KXRAIN`): "total precipitation at CLIxxx ... strictly greater than 0 inches"; a trace counts as 0.

### Liquidity (24-hour volume, contracts, both open events added up, 2026-10-22)

| Series | What | 24h volume | Matters? |
|---|---|---|---|
| KXRAIN | Will it rain today, ~35 cities | 432,981 | Yes (not priced by Argus yet) |
| KXHIGHLAX | LA high (KLAX) | 193,360 | Yes |
| KXHIGHNY | NYC high (Central Park) | 140,033 | Yes |
| KXHIGHMIA | Miami high | 103,794 | Yes |
| KXHIGHCHI | Chicago high (Midway) | 58,498 | Yes |
| KXHIGHTATL | Atlanta high | 33,860 | Yes |
| KXHIGHPHIL | Philadelphia high | 29,703 | Yes |
| KXHIGHTPHX | Phoenix high | 24,638 | Yes |
| KXHIGHAUS | Austin high | 24,620 | Yes |
| KXLOWTNYC | NYC low | 19,297 | Borderline |
| KXHIGHTSEA | Seattle high | 18,082 | Borderline |
| KXHIGHDEN | Denver high | 17,728 | Borderline |
| KXLOWTPHIL | Philadelphia low | 17,279 | Borderline |
| ~30 more city highs/lows | MSP, BOS, SFO, DFW, HOU, MSY, OKC, DCA, SAT, EWR, TTN, SDF, LAS, SAN... | 1,000–16,000 each | Thin |
| Weekly averages, monthly snow/rain, hurricanes | | 0–8,000 | No |

Adding up every open weather series gives about 1.4 million contracts in 24 hours (6 series were missed
on a rate limit; none of them are big). Each contract costs 1–99c, so the money traded is under
$1.4M a day. The "~$3M/day" claim from a trading site is **not supported** by this count (MEASURED-HERE,
one day only; volume swings with the weather).

The 12 series marked "Yes"/"Borderline" are Argus's default set (`stations.LIQUID`).

### History available

Kalshi keeps every settled market. NYC highs go back to 2021-08-06 (old ticker `HIGHNY-...`), Chicago to
2021-08-19, Austin to 2023-05, Denver to 2024-11, LA to 2025-01, NYC lows to 2025-12-13. Hourly price
candles are available for each settled market (used by the backtest).

### Not covered and why

- **15-minute and hourly markets** (`KXTEMPNYCH` etc.): skipped, hard rule.
- **Rain** (`KXRAIN`): the most traded weather market. NOAA's forecast has a rain chance (P12/P24), so it
  is the obvious next engine; not built in this pass.
- **Foreign cities** (London, Tokyo, ...): no NWS station or CLI report.
