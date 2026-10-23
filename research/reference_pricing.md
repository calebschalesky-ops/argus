# Reference pricing: Kalshi vs a deeper market's implied distribution

Paper research only. Argus never trades.

## 0. Pre-registration (written 2026-10-22 before the crypto backtest was run)

Only one settled event (KXBTCD-26OCT2117) was priced by hand before this was written, to check the
code ran and the numbers were sane. Nothing below was changed after the backtest results were seen.

**Markets.** The 5 PM New York event of each day of `KXBTCD`, `KXETHD` (above/below ladders) and
`KXBTC`, `KXETH` (ranges). Every market settles on the simple average of the 60 seconds of CF
Benchmarks' real-time index (BRTI / ETH RTI) before 5 PM ET. Other hours of these series and the
15-minute series are out. Kalshi has no separate weekly BTC/ETH price ladder (only "max this week"
one-touch markets, a different question), so "weekly" = the Friday 5 PM events, which are in the sample.

**Moments priced.** 1 h, 4 h and 12 h before the 5 PM close (all on hourly marks).

**Kalshi price at that moment.** Close of the hourly candle ending at that moment (batch
`GET /markets/candlesticks`). A market counts only if already listed and its book is real: a bid, and
the ask within 5c of it. No last-trade prices.

**Reference.** Deribit BTC/ETH option trades in the 60 minutes BEFORE the moment (free archive
`history.deribit.com`, `public/get_last_trades_by_currency_and_time`; the same call is used live, so the
backtest and the live log use one method). Expiries from Kalshi's close to 3 days after (Deribit's daily
08:00 UTC expiries). Out-of-the-money options only. Each trade carries Deribit's IV and index price.
A smile IV(u) = a + b·u + c·u², u = ln(K/S)/√τ, is fitted (weights √size; falls back to a line or a flat
level if the curve turns over). Volatility per √year is assumed flat from now to Deribit's expiry. Spot =
Deribit index on the last trade before the moment. P(above K) = −dC/dK of Black-76 prices built from the
smile (so the skew counts). 60-second averaging: 40 s off the horizon. Rates zero. Basis between
Deribit's index and CF Benchmarks' index: not corrected; measured afterwards as a diagnostic.

**Rules** (each its own engine on the board, per horizon):
- `spec`: buy at the ask when `filters.check()` says WORTH A LOOK (gap 10+ pts, side ask 50–99c).
- `ev3`: buy at the ask when EV after the taker fee ≥ 3c and the side ask is 10–90c.
- `maker3`: rest a buy at bid + 1c when EV ≥ 3c (no maker fee on these series) and the price is 10–90c;
  it counts only if a later hourly candle before the close printed a trade strictly through the order.
One contract per market. Side = whichever side has the larger EV under that rule's pricing.

**Gate** (board): 20+ settled events, Brier better than the market, paper P/L after fees > 0.
The board's Brier uses the side's price; the fair Brier (P(Yes) vs Kalshi's Yes midpoint) is reported too.

**Known risks written down in advance.** (1) Options carry a variance risk premium: implied vol is
usually above the vol that then happens, so the options-implied distribution should be too wide, and
the model would lean towards selling the middle and buying the tails. (2) Neighbouring strikes of one
event are the same bet; the t-stat is computed on per-event P/L. (3) Nine rule×horizon cells per coin:
one cell looking good by chance is expected.

## 1. Crypto: results (backtest run 2026-10-22/23, Kalshi live tier 2026-08-23 to 2026-10-21)

60 days x 4 series (KXBTCD, KXBTC, KXETHD, KXETH), 5 PM ET events only, priced 1 h, 4 h and 12 h
before the close. 14,949 logged calls in `refprice_crypto_backtest_log.csv` (on the board). 1,789
network calls, 825 of them HTTP 429 (Kalshi's limit is shared with sibling sessions); all in
`data/refprice/api_calls.csv`. Run output: `data/refprice/crypto_backtest_run_2026-10-22.txt`. Skipped: 8
event-moments where Deribit had too few trades in the hour (all at 1 h). No off-schedule or early closes.

**Settlement index check.** Kalshi's settle value (CF Benchmarks 60-second average) vs Deribit's index on
the last option trade before 5 PM ET: BTC mean -0.1 bp, sd 2.0 bp (n=57); ETH +0.2 bp, sd 3.6 bp (n=51).
Negligible next to a 1-hour move (about 50 bp).

**How close the two markets are.** Mean absolute gap between Deribit's P(Yes) and Kalshi's Yes midpoint on
markets with a real book: 2.8 pts at 1 h, 2.1 at 4 h, 1.8 at 12 h. Kalshi's crypto ladders already price off
the options market (or the same information) to within about one spread.

| Rule x horizon | Events | Paper bets | Won | P/L after fees | ROI | Edge/bet | t (per-event P/L) | Bets/week | Fair Brier model / Kalshi mid | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| ev3 1 h | 209 | 78 | 30 | +$0.13 | +0.5% | +0.2c | +0.0 | 9.3 | 0.0577 / 0.0574 | NO EDGE |
| ev3 4 h | 230 | 106 | 53 | -$7.52 | -12.8% | -7.1c | -1.7 | 12.6 | 0.0673 / 0.0666 | NO EDGE |
| ev3 12 h | 206 | 107 | 59 | -$11.36 | -16.5% | -10.6c | -1.8 | 12.7 | 0.0851 / 0.0841 | NO EDGE |
| maker3 1 h (fills proven) | 209 | 183 | 76 | -$3.43 | -4.3% | -1.9c | -0.6 | 21.7 | same as ev3 1 h | NO EDGE |
| maker3 4 h | 230 | 302 | 155 | -$9.47 | -5.8% | -3.1c | -1.2 | 35.8 | same as ev3 4 h | NO EDGE |
| maker3 12 h | 206 | 309 | 161 | -$24.92 | -13.4% | -8.1c | -2.0 | 36.7 | same as ev3 12 h | NO EDGE |
| spec 1 h | 209 | 5 | 3 | -$0.42 | -12.7% | -8.4c | | 0.6 | same | NO EDGE |
| spec 4 h | 230 | 9 | 6 | -$0.05 | -0.9% | -0.6c | | 1.1 | same | NO EDGE |
| spec 12 h | 206 | 3 | 1 | -$1.10 | -53.9% | -36.7c | | 0.4 | same | NO EDGE |

("Events" counts each series' event separately: KXBTC and KXBTCD on the same day are two board events.)

**Verdict: crypto reference pricing fails the gate in all nine cells.** Kalshi's midpoint is slightly better
calibrated than Deribit's distribution at every horizon, and every rule loses after fees except ev3 at 1 h,
which breaks even. The further out, the worse: at 12 h Kalshi's ladder is wider than the options (fatter
tails), and Kalshi was right more often. The pre-registered worry was the reverse (options too wide).

Calibration (one point per market and moment, all horizons):

| P(Yes) bin | n | Deribit model | Kalshi mid | Happened |
|---|---|---|---|---|
| 0-10% | 2,185 | 0.033 | 0.044 | 0.027 |
| 10-20% | 519 | 0.143 | 0.145 | 0.131 |
| 20-30% | 260 | 0.243 | 0.247 | 0.265 |
| 30-40% | 184 | 0.343 | 0.347 | 0.326 |
| 40-50% | 147 | 0.449 | 0.448 | 0.463 |
| 50-60% | 133 | 0.550 | 0.544 | 0.504 |
| 60-70% | 132 | 0.653 | 0.656 | 0.667 |
| 70-80% | 153 | 0.754 | 0.752 | 0.778 |
| 80-90% | 205 | 0.855 | 0.848 | 0.849 |
| 90-100% | 1,065 | 0.975 | 0.963 | 0.990 |

The one place the options beat Kalshi is the cheap tail: Kalshi's under-10c markets average 4.4c where 2.7%
happened (the known longshot bias); Deribit says 3.3%. That is 1-2c a contract, under the taker fee and below
the 10c floor every engine respects, so it is not a strategy.

**Post-hoc slice (found AFTER seeing the results: a hypothesis, not a pass).** ev3 at 1 h on the above/below
ladders only: KXBTCD 43 bets +$1.57 (+10.6%), KXETHD 10 bets +$0.43 (+12.6%), the model's fair Brier slightly
better than Kalshi's on both (0.0446 vs 0.0453; 0.0442 vs 0.0446). 53 bets, about 6 a week. A slice this small
and picked after the fact should be expected to vanish. The live logger runs exactly these rules, so the forward
record will settle it.

**Depth.** Candles do not show depth. The live logger reads Kalshi's public order book for each paper bet; a dry
run on 2026-10-23 01:40 UTC found 1,259 to 1,599 contracts at the quoted price on the BTC 5 PM ladder. Size is not
the constraint; the edge is.

**What Deribit gives for free (checked 2026-10-22).** `history.deribit.com/api/v2/public/get_last_trades_by_currency_and_time`
returns every option trade with IV, mark price and index price, no key. Live `www.deribit.com/api/v2/public/*`
(book summary with mark IVs, index price) needs no key either. The free archive has no historical order books
or mark-IV surfaces, which is why trade IVs are used (live too, so both use one method).

## 2. Fed: not testable on free official data

**What would be needed.** Fed funds futures (CME ZQ, one contract per calendar month) give per-meeting odds;
that is how CME FedWatch works. CME's data needs a paid subscription: the FedWatch API (CME's page says "as little
as $25/month"; higher tiers from a third-party listing, STATED not checked) or CME DataMine, ordered through the
CME Group Data Services Portal with an API ID. Scripted access to CME's website is blocked (commodities thread,
2026-10-22). **Caleb would need a CME FedWatch API subscription to run this test.** Nothing was signed up for.

**Free official alternative tried: Atlanta Fed Market Probability Tracker.** Daily distributions of the average
3-month SOFR rate over each quarterly CME contract window, from CME SOFR options (used by the Atlanta Fed with
CME's permission). Full history since 2023-03-29 is one public file, `mpt_histdata.xlsx`, no key. Kalshi's Fed
decision markets (`FEDDECISION`, `KXFEDDECISION`): 28 decided meetings May 2023 to Sep 2026, deep books (the Sep
2026 "maintains rate" market traded 48M contracts).

**Why it cannot price one meeting.** The tracker only lists windows that have not started yet, and each window is
13 weeks long while meetings are 6-8 weeks apart. For all 28 meetings the first window after the decision also
contains the next meeting (in 24 the next meeting comes before the window even starts; in the other 4 it falls 38-46% of the way in). The pre-registered rule
(price only if the next meeting leaks into under 15% of the window) priced 0 of 28, so there is no backtest and no
Fed log. Loosening the rule would mean assuming the next meeting's decision. `python -m argus.refprice fed-backtest`
reprints the table. Re-test cost: the same command, if the Atlanta Fed publishes the window containing the meeting.

Other free routes rejected: FRED has no futures; T-bill yields (FRED `DTB4WK`) give only a mean rate and carry
supply and debt-ceiling distortions about as big as one rate step.

## 3. Indices (S&P 500, Nasdaq-100): skipped, as the brief allows

Kalshi lists daily and weekly ranges and above/below ladders (`KXINX`, `KXINXU`, `KXINXW`, `KXINXWD`,
`KXNASDAQ100`, `KXNASDAQ100U`, `KXNASDAQ100W`, `KXNASDAQ100WD`, ...). No free official SPX/NDX option chain with terms
that allow scripted use was found on 2026-10-22: Cboe's delayed-quote JSON (cdn.cboe.com) has no published terms
allowing automated use (a third-party guide says it is barred), and historical option quotes are paid (Cboe DataShop,
LiveVol). Cboe's free VIX1D/VIX9D daily files are one volatility number a day, not a distribution at Kalshi's close.
Not built. **To test indices Caleb would need a paid options feed (Cboe DataShop or LiveVol) or a broker API whose
terms allow research use.**

## 4. Summary for the gate

| Market type | n (settled events / paper bets) | ROI after fees | Verdict |
|---|---|---|---|
| Crypto, best cell (ev3 1 h) | 209 / 78 | +0.5% | NO EDGE (Brier worse than Kalshi) |
| Crypto, the other eight cells | 206-230 / 3-309 | -0.9% to -54% | NO EDGE |
| Fed | 0 priced (28 meetings, none isolable) | n/a | NOT TESTABLE on free data |
| Indices | not built | n/a | SKIPPED (no free official chain) |

Kalshi's crypto ladders already sit on the options market. Borrowing the options price gives no edge after fees and
spread. Same lesson as oil, weather and the 15-minute engine: on a liquid ladder, the crowd already is the reference.

## 5. Commands

    python -m argus.refprice crypto-backtest [--since 2026-08-23 --until 2026-10-21] [--report-only]
    python -m argus.refprice crypto-live     # 1 h, 4 h or 12 h before 5 PM ET; logs refprice_crypto_live_log.csv
    python -m argus.refprice crypto-settle   # after 5 PM ET; maker rows get their fill proven from candles
    python -m argus.refprice fed-backtest    # prints why each meeting cannot be isolated
    python -m argus.refprice report
