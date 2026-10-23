# Claims check: Kalshi research summary

Checked 2026-10-22. Read-only, public pages only, no logins. Labels: **VERIFIED** (source and date), **CONTRADICTED**, **UNVERIFIED** (with what was tried). Quotes are under 15 words and exact, except where marked.

Primary documents were downloaded and read as text (Fed paper, Bürgi paper, five CFTC filings). Where I only had a search-engine summary of a page, I say so.

## Scoreboard

| # | Claim | Verdict |
|---|---|---|
| 1 | Fed paper: Kalshi beats Bloomberg on headline CPI, matches on core CPI and unemployment | **VERIFIED**, with weak significance on headline |
| 2 | Bloomberg: Kalshi no better than experts on payrolls | **VERIFIED** (Bloomberg's own analysis, May 2026) |
| 3 | Liquidity Incentive Program ends Sept 1, 2026, no extension | **CONTRADICTED** |
| 4 | Volume rewards ended early, about Oct 13, 2026 | **VERIFIED as filed**; actual end **UNVERIFIED** |
| 5 | Only a contracted-market-maker program remains; nothing for retail | **CONTRADICTED** (three retail-eligible items exist); fee discount **UNVERIFIED** |
| 6 | 15-minute crypto is bot-dominated, fast, taker-heavy | **UNVERIFIED** (taker cost supported, bot share not) |
| 7 | Cross-venue gaps shrank from 5-10% to 1-2%, wider on thin markets | **UNVERIFIED** as a trend; persistence of gaps supported |
| 8 | Weather: about $3M/day, NWS bias, no audited P&L | Volume roughly **VERIFIED** (secondary); bias **UNVERIFIED**; no audit found |
| 9 | Bürgi/Deng/Whelan headline numbers | **VERIFIED**, with three caveats that change how to use them |

---

## 1. Fed paper: Kalshi vs Bloomberg consensus on CPI and unemployment — VERIFIED

- **Paper:** "Kalshi and the Rise of Macro Markets", Anthony M. Diercks (Federal Reserve Board), Jared Dean Katz (Northwestern), Jonathan H. Wright (Johns Hopkins, NBER). FEDS Working Paper 2026-010, dated February 12, 2026. The paper is marked preliminary; its authors write for themselves, not the Board.
- **Sample:** releases "since 2022" through 2025 (Figure 14 caption). Day-of-release forecasts, before the number comes out. The Fed funds set is 2022 on. The paper does not print the number of CPI or unemployment releases in the accuracy table (the regression tables show 113 observations for CPI/PCE and 108 for payrolls, which are a different exercise).
- **Exact findings (Table 3, day of release):**
  - Headline CPI mean absolute error: Bloomberg 0.081, Kalshi mean 0.069, median 0.063, mode 0.063. Significance: median and mode MAE at p<0.10 only; Kalshi mean RMSE at p<0.05.
  - Core CPI: MAE 0.070 vs 0.070 (mean), 0.080 (median), 0.070 (mode). No star anywhere. Kalshi median is numerically worse.
  - Unemployment: MAE 0.109 vs 0.117 (mean), 0.107 (median, mode). No star.
  - The paper does not test payrolls against Bloomberg consensus in this table.
- **Quote:** "statistically significant improvement over the Bloomberg consensus forecast" (headline CPI). Also "statistically similar for core CPI and unemployment".
- **Link:** https://www.federalreserve.gov/econres/feds/files/2026010pap.pdf (secondary: Bloomberg, 2026-01-28, "Kalshi Fed Forecasts Are as Good as Wall Street's", headline seen in search only, page returned 403)
- **Caveats:** the edge is about 1 basis point of CPI error, and for headline it is significant at the 10% level on MAE. The Kalshi forecast is built from market-implied distributions at the moment of release.
- **What it changes for Argus:** the market's CPI price is a decent anchor, so a model has to beat it, not Bloomberg. It is no reason to expect an edge by forecasting macro releases ourselves.

## 2. Bloomberg: Kalshi no better than experts on payrolls — VERIFIED

- **Source:** Bloomberg News piece (Alex McIntyre, Justina Lee) carried by Bloomberg Línea, **May 7, 2026**, titled "Análise ou aposta? Kalshi não supera especialistas em previsão sobre empregos nos EUA". I read the page through a summarizer, so the points below are paraphrase; I captured no verbatim quote.
- **Findings:** over **33 months** of monthly estimates, Kalshi and economists "missed by similar" margins, economists had a slight edge that was not statistically significant, both averaged errors above 60,000 jobs. For the latest report Kalshi was slightly closer than the median of 79 economists (178,000 reported; both far off). The payrolls market averaged about **$435,000 per month** in volume over three years.
- **Link:** https://www.bloomberglinea.com.br/mercados/analise-ou-aposta-kalshi-nao-supera-especialistas-em-previsao-sobre-empregos-nos-eua/
- **Note:** this is consistent with claim 1, not in conflict. "No better" is the accurate reading; "worse" is not supported.
- **What it changes for Argus:** payrolls is a thin market ($435k/month) that tracks consensus. No separate edge expected; do not spend model time here.

## 3. Liquidity Incentive Program ends Sept 1, 2026 — CONTRADICTED

- The Kalshi help-center page for the program now lists **"Program end: January 1, 2027."** (read 2026-10-22). Search-engine snapshots of the same page and the July 2026 CFTC filing still showed September 1, 2026, so the page was changed after that.
- **October 14, 2026 CFTC self-certification** (Re: "Amendment to August 2025 Liquidity Incentive Program"): amended program effective **October 28, 2026**, and "the Program expires no later than January 1, 2027."
- Rewards pay resting orders (no fill needed), $1 to $1,000 per market per day, scored from once-per-second book snapshots. Most regular U.S. members are eligible; affiliates, introducing brokers, FCMs and their customers, and non-U.S. users are excluded.
- **Links:** https://help.kalshi.com/en/articles/13823851-liquidity-incentive-program ; https://www.cftc.gov/filings/orgrules/rules10142633813.pdf
- The amendment's example schedule includes "Climate and Weather: Daily Temperature" with a $15,000 pool over a five-day window. The filing marks the example as hypothetical, so it proves nothing about real payouts.
- **What it changes for Argus:** the claim that resting-order rewards are gone is wrong. A resting-order strategy still has a possible subsidy through at least Dec 31, 2026. The terms can change on notice, so any plan must not depend on it.

## 4. Volume rewards ended early, around Oct 13, 2026 — VERIFIED as filed; actual end UNVERIFIED

- **Filing:** KalshiEX notice dated **September 28, 2026**, "Notice of Termination of the Volume Incentive Program": "will go into effect no earlier than October 13, 2026."
- Press (gambling.com, The Block, Unchained, SBC Americas, Oct 2026) reports the end date was pulled forward from October 1, 2027 and ties it to wash-trading scrutiny. The filing itself gives no reason; the reason is press inference, so treat it as UNVERIFIED.
- Not confirmed: that the program actually stopped paying on Oct 13. "No earlier than" is a floor. The help-center page for it returned 404 when I fetched it.
- **Link:** https://www.cftc.gov/filings/orgrules/rules09282630083.pdf
- **What it changes for Argus:** never count volume cashback in any expected-profit calculation. It is gone or going, and was never available to market makers with agreements.

## 5. Only a contracted-market-maker program remains — CONTRADICTED for retail; fee discount UNVERIFIED

- **Designated Liquidity Provider (DLP) program:** true that it exists and is for market makers. The Oct 14, 2026 filing renames the May 2026 Liquidity Provider Program, effective no earlier than Oct 28, 2026, runs to **December 1, 2027**, selects DLPs by a public bidding period, and says the criteria are "designed to encourage participation in the Program amongst Kalshi's market makers." Link: https://www.cftc.gov/filings/orgrules/rules10142633803.pdf
- **But retail users still have, as of 2026-10-22:**
  1. **Liquidity Incentive Program** (claim 3 above): resting-order rewards for "most regular U.S. Kalshi members", through Jan 1, 2027.
  2. **Interest (APY):** **3.50% APY**, variable, on cash and positions; average monthly balance must be at least **$250**; SSN needed above $10 a year. Source: https://help.kalshi.com/en/articles/13823847-apy-on-kalshi (page says "Updated yesterday", read 2026-10-22). A separate perps promo of up to 7% needs perps approval and is not for event contracts.
  3. **Deposit and Trading Reward Program** (CFTC filing, Oct 14, 2026): implemented "on or after October 28, 2026", runs to October 28, 2028. Time-limited deposit rewards and trading rewards, limited to Self-Clearing Members "who are natural persons"; trading rewards count fills priced $0.03 to $0.97. Terms and amounts are in a confidential appendix. Link: https://www.cftc.gov/filings/orgrules/rules10142633807.pdf . **Not live yet** as of today; amounts unknown.
- **Fees:** the help-center fee page states only that Kalshi charges "a transaction fee on the expected earnings on the contract", that maker fees apply on some markets only, and that cancelling a resting order is free (https://help.kalshi.com/en/articles/13823805-fees). Taker formula quoted by third parties: 0.07 × contracts × P × (1−P), maker 0.0175 on selected series. **I could not read Kalshi's own fee-schedule PDF** (HTTP 429 on 2026-10-22), and found no retail fee discount or rebate anywhere. That is "not found", not "does not exist". Re-test cost: one fetch of kalshi.com/docs/kalshi-fee-schedule.pdf later.
- **What it changes for Argus:** the interest rate matters in one narrow way: money held to settlement earns about 3.5% a year, so a short-dated edge must clear that, not zero. Everything else is small or not yet live; no strategy should assume a fee break.

## 6. 15-minute crypto markets are bot-dominated, fast, taker-heavy — UNVERIFIED

(Argus skips them by rule; this only records the evidence.)

- **Supported:** taker cost is real. A bot vendor measured 6,298 windows: buying the leading side was about +0.67¢ per contract gross, against an average taker fee of about 1.55¢, so taking was net negative at every entry time tested. An independent-looking analysis of 2,764 windows found prices well calibrated, so the fee is the whole drag. Vendor analyses, not audited. Link: https://www.oddsshopper.com/articles/prediction-markets/are-kalshi-15-minute-bitcoin-markets-rigged ; https://www.thelines.com/prediction-markets/kalshi/15-minute-markets/
- **Volume:** Bitcoin.com News estimates 6.74 billion contracts and $60.4M in fees for September 2026, from Predict Charts data. Secondary, estimate only. Link: https://news.bitcoin.com/igaming/kalshis-15-minute-gold-bets-top-ether-fast-markets-drive-fees/
- **Not found:** any measurement of bot share of order flow or the maker/taker split on Kalshi 15-minute series. The only hard participant data found (Telonex, Dune) is Polymarket, one week in February 2026, where 36.8% of wallets finished in profit.
- **Also:** the Bürgi paper explicitly excludes these markets ("markets that reset every hour"), so its numbers say nothing about them.
- **What it changes for Argus:** the skip rule stands on cost grounds, which are measurable (fee about equal to or larger than the gross edge). The "bots dominate" half is a story, not a finding.

## 7. Cross-venue gaps shrank from 5-10% (2024) to 1-2% — UNVERIFIED as a trend

- **Not found:** any source with that before/after comparison. Tried: three searches on Kalshi/Polymarket gaps, the 2024 election arbitrage literature, and the arXiv paper below.
- **Related evidence:**
  - Gebele and Matthes, arXiv 2601.01706 (Jan 5, 2026): over 100,000 events on ten venues, 2018 to 2025, about 6% listed on more than one venue, "persistent execution-aware price deviations of 2-4% on average." The abstract names no venue pair, so it is not Kalshi-vs-Polymarket specifically. Link: https://arxiv.org/abs/2601.01706
  - Practitioner blog (SimpleFunctions, April 2026): venues "usually within 2-5 cents", no methodology.
  - 2024 election: a Clinton and Huang (2025) paper is cited second-hand as showing gaps peaking in the last two weeks; I did not find the original.
- **Our own data (SPEC.md, crossvenue thread, 2026-10-22):** 143 matched midterm outcomes, biggest gap +0.7 points after fees, range −2.5 to +0.7. That is consistent with "1-2% on major markets now", for one election and one day. Not a trend.
- **"Bigger gaps survive where resolution wording differs":** supported in the sense that the SPEC records real rule differences (swearing-in vs AP call), but no measured gap size.
- **What it changes for Argus:** treat cross-venue gaps as small (under about 2 cents) and mostly rule-risk, not free money. The scheduled election-night re-runs are the only evidence we will have; do not size anything off this claim.

## 8. Weather markets — split verdict

- **~$3M/day (early 2026):** Roughly consistent, not exact. The Wall Street Journal (via Benzinga/Webull and Edgen, WSJ itself not reachable) reports **$564 million** of weather volume **through July 2026**, above all of 2025. 564M over 212 days is **about $2.7M/day on average** (my arithmetic). "Early 2026" is therefore too early: the average over Jan to Jul, not a figure for early 2026, and it was lower early and higher later. Kalshi and The Weather Company say volume is up 500% year on year. Link: https://www.webull.ca/news-detail/15475678967514112
- **NWS bias (Miami about 3°F warm):** UNVERIFIED. The only source is a vendor page (Weather Edge MCP: "roughly 3°F at Miami airport", "about 1°F in Central Park") describing its own calibration, plus a paid picks service claiming a 67% win rate with no methodology. No independent study found. Link: https://mcpservers.org/es/servers/rjw34/weather-edge-mcp
- **No audited P&L:** none found, which matches the claim. Not proof that none exists.
- **What it changes for Argus:** volume is large enough to be worth a look, but the bias is a hypothesis to be measured with our own settled logs, not a fact. The weather session should test it against NWS station records before anything else.

## 9. Bürgi/Deng/Whelan: what the paper itself says — VERIFIED, with caveats

- **Paper:** "Makers and Takers: The Economics of the Kalshi Prediction Market", Constantin Bürgi, Wanying Deng, Karl Whelan (University College Dublin), January 2026 version (also CESifo WP 12122 and a CEPR discussion paper). Link: https://www.karlwhelan.com/Papers/Kalshi.pdf
- **Sample:** Kalshi from 2021 **through April 2025**; **313,972** Yes and No contracts (46,282 Yes contracts from 12,403 events); at least $1,000 final volume; final bid-ask spread under 20c; markets open at least 24 hours only.
- **Numbers in the paper:**
  - Under 10c: "lose over 60 percent of their money." (Abstract-level text: "costing less than 10c lose over 60 percent".)
  - Above 50c: "earn a small positive rate of return"; above 70c, small but statistically significant post-fee gains. Makers buying at 50c and over earn **2.6%**.
  - Takers **−31.46%**, makers **−9.64%** (average post-fee return).
  - Overall average about **minus 20%**.
  - Trend: "some evidence that the bias in prices is diminishing over time"; the 2025 bias coefficient "is smaller and less statistically significant".
- **Three caveats the one-line summary drops:**
  1. The −20% is an **equal-weighted per-contract** pre-fee average. The paper says the dollar-weighted pre-fee return is **zero by construction** (traders swap money), so −20% mostly reflects many cheap losing contracts, not a loss of 20% on dollars staked.
  2. The data **ends April 2025**, the month Kalshi began charging fees to makers. The paper says results are not sensitive to cutting the data at December 2024 or dropping sports, but it cannot show what the market looks like in late 2026.
  3. Hourly crypto and index markets are excluded, and thin markets (under $1,000) are dropped, so it does not describe 15-minute or illiquid markets.
- **What it changes for Argus:** the long-shot pattern is a documented prior, so default to fading cheap contracts and be skeptical of them, but argus-maker is testing whether it holds in our data and period; this check only confirms what the paper claims.

---

## What is actually established (5 lines)

1. Kalshi's CPI price is about as accurate as Bloomberg's consensus (slightly better on headline, only at the 10% level), and no better than economists on payrolls over 33 months: the macro anchor is hard to beat.
2. Volume cashback is being terminated (filed Sept 28, effective no earlier than Oct 13); the Liquidity Incentive Program was not ended, it runs to Jan 1, 2027 with an amended version from Oct 28.
3. Retail still has resting-order rewards, 3.50% interest on balances of $250+, and a deposit/trading reward program that starts no earlier than Oct 28 (terms confidential); no retail fee discount was found, but Kalshi's fee-schedule PDF was unreadable.
4. The long-shot bias paper is real, but its −20%, −32% and −10% figures are pre-April-2025 per-contract averages, not current dollar returns; the effect is described as shrinking.
5. Not established: bot dominance of 15-minute markets, the 5-10% to 1-2% cross-venue trend, and the Miami NWS warm bias. Weather volume is large (about $2.7M/day average through July).
