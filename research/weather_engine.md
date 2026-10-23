# The Argus weather engine, in plain words

Written 2026-10-22. Paper only: Argus never trades.

## What it does

Kalshi runs a market every day on tomorrow's high (and low) temperature in about 25 U.S. cities. Each
day has six price boxes: "56 or below", "57 to 58", "59 to 60", ... "65 or above". Whichever box the
real temperature lands in pays $1.

Argus's weather engine makes its own odds for every box, compares them with Kalshi's prices, prints an
alert, and logs every call, so the record shows whether it has an edge.

## Where the numbers come from (all free, all public)

| Need | Source | Why |
|---|---|---|
| The forecast | NOAA's **National Blend of Models** (NBM), the station text bulletin ("NBS") | It forecasts the daytime high and overnight low **at the exact station the market settles on**, plus its own uncertainty. It is what the National Weather Service starts its own forecast from. |
| Live forecast | NOAA NOMADS server (official) | Newest run, every hour. |
| Past forecasts | Iowa Environmental Mesonet (Iowa State University) archive of the same NBM bulletins | NOAA keeps only 2 days of the bulletin and has no free archive of it (checked 2026-10-22). |
| What the market paid on | Kalshi's own settled results, back to 2021 for NYC | The number that actually paid. |
| Cross-check of the result | NWS Daily Climate Report (CLI), api.weather.gov | Kalshi's rules name the CLI station; the value comes via The Weather Company. They matched on 3,834 of 3,841 days checked. |
| For reference only | NWS point forecast, api.weather.gov | Printed on the alert; not used in the odds. |

## How the odds are made

1. **Forecast.** Take NOAA's latest NBM forecast for the station (say 59F for NYC tomorrow).
2. **Station habit (bias).** Look at past days: when NBM made this forecast the same number of hours
   ahead, how far off was it from what Kalshi settled on? Average the misses. If NBM has run 1F warm at
   Central Park, subtract 1F. Past days used: the last 60 days, plus the same 30-day stretch of the year
   in earlier years (a station's habit changes with the season).
3. **Spread.** How big were those misses? Their standard deviation (typically 2 to 3F) is how wide the
   curve is.
4. **Odds per box.** A bell curve centred on (forecast + bias). The settled value is a whole degree, so
   "57 to 58" means anywhere from 56.5 to 58.5.
5. **Compare with Kalshi.** For each box, the side (YES or NO) where the model's odds beat Kalshi's ask by
   the most. **WORTH A LOOK** only when it beats it by 10+ points **and** that side costs at least 10c
   (the longshot rule: cheap sides look like bargains far more often than they win).

**No peeking at the future.** In the backtest, and in every bias estimate, a past day only counts if its
result was already public at that moment, and a forecast only counts if NOAA had published it (run time
+ 2 hours). Tests check both (`tests/test_weather.py`).

## Commands

```
python -m argus.weather markets            open events, station, volume
python -m argus.weather run KXHIGHNY-26OCT23   one alert, saved to alerts/, logged to weather_log.csv
python -m argus.weather run --all-liquid   every open event of the 12 liquid series
python -m argus.weather settle --auto      fill in results from Kalshi (and check against the NWS report)
python -m argus.weather backtest           replay past events -> backtest_weather_log.csv
python -m argus.weather backtest --report-only
python -m argus board                      the weather rows show up next to every other strategy
```

## Backtest result

Run 2026-10-22. Every settled event from 2026-05-01 to 2026-10-21, 12 series, replayed at 4:00 PM
New York the day before (the market has been open 6 hours; NOAA's 18Z run is out). Prices are
Kalshi's real bid/ask from the hourly candle ending at that moment. Each call is scored against
Kalshi's actual result.

**Verdict: NO EDGE in all 12 series.** 2,086 events, 12,049 calls. The model's odds were worse than
Kalshi's in every city (Brier 0.127 vs the market's 0.110; lower is better). Paper bets: 2,612, won
44.5%, **-$144.78 after fees on $1,259 staked (-11.5%)**.

| Series | Events | Model beat market (Brier) | Station bias, settled minus NBM | Paper P/L (ROI) | Verdict |
|---|---|---|---|---|---|
| NYC high (KNYC) | 174 | 47 of 174 | **-1.13F** (NBM runs warm) | -$24.07 (-24%) | NO EDGE |
| LA high (KLAX) | 173 | 47 | **+0.64F** (NBM runs cold) | -$16.93 (-13%) | NO EDGE |
| Miami high (KMIA) | 174 | 62 | +0.07F | -$5.99 (-7%) | NO EDGE |
| Chicago high (KMDW) | 174 | 49 | -0.17F | -$15.33 (-15%) | NO EDGE |
| Atlanta high (KATL) | 174 | 80 | +0.02F | +$6.00 (+6%) | NO EDGE (odds worse; profit is noise) |
| Philadelphia high (KPHL) | 174 | 58 | -0.14F | -$18.41 (-15%) | NO EDGE |
| Phoenix high (KPHX) | 174 | 71 | +0.06F | +$2.24 (+3%) | NO EDGE (odds worse; profit is noise) |
| Austin high (KAUS) | 174 | 62 | +0.07F | -$16.81 (-16%) | NO EDGE |
| Denver high (KDEN) | 174 | 57 | +0.12F | -$13.67 (-12%) | NO EDGE |
| Seattle high (KSEA) | 174 | 62 | -0.29F | -$10.42 (-10%) | NO EDGE |
| NYC low (KNYC) | 174 | 47 | **-1.06F** | -$16.53 (-13%) | NO EDGE |
| Philadelphia low (KPHL) | 173 | 43 | **+0.79F** | -$14.86 (-15%) | NO EDGE |

What this says, plainly:

1. **The "station bias" idea mostly failed.** NOAA's NBM station forecast is already tuned to each
   station. Over May to October its average miss was under 0.3F at 8 of the 12. Only NYC (both high and
   low run about 1F warm at Central Park), LA (0.6F cold) and Philadelphia lows (0.8F cold) had a real
   lean. Correcting for it made the forecast *worse* in 10 of 12 series (the lean changes with the
   season, so last season's correction misfires). The "Miami runs ~3F warm" claim does not hold for NBM
   (+0.07F). It may hold for the raw NWS gridpoint forecast, which has no free archive to test; the
   live log now records the NWS point forecast with each call so this can be measured from today on.
2. **The market already knows more than NBM at 4 PM the day before.** NBM misses by 1.8F on average.
   Kalshi's prices are better forecasts than that: other weather models (HRRR, ECMWF) and traders who
   watch them are priced in.
3. **The model is overconfident when it disagrees with the market.** When it said 75%, those calls won
   61%. When it said 25%, they won 13%. The bigger the disagreement, the more likely the market was right.

**Limits, dated 2026-10-22 (re-test cost in brackets):**
- Past forecasts come from Iowa State's archive of the NOAA bulletin, not from NOAA itself (NOAA keeps 2 days).
  Same product, so the limit is the custodian, not the data.
- Replayed at one moment only (4 PM ET the day before). A morning-of replay may differ (one command:
  `backtest --hour 10` changes the hour but still the day before; a same-day replay needs a small code change).
- One season-half (May to October). Winter is untested (re-run with `--since 2025-11-01`, about 2 hours of
  Kalshi reads at the shared rate limit).
- The 2026-10-22 live run also priced **today's** events, whose high or low had already been observed.
  The model has no observations, so those calls (3 flagged: LA, Denver, Seattle Oct 22) are stale. They
  stay in the log (rows are never deleted). Live runs now skip any day that has already started.

**What would make it worth trusting:** 100+ settled live calls (Caleb's bar) with a lower Brier than the
market AND paper profit after fees, in the same city. On this evidence, don't expect it: put the live
runs on the board, and look at rain (`KXRAIN`, the most traded weather market) or same-day markets
with live observations before spending more effort on next-day temperature.
