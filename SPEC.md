# Argus — SPEC

Source of truth for the Argus build. Written 2026-10-22 from Caleb's brief, after Caleb asked
for the spec to be built from research when no SPEC.md existed. Later threads: do not rewrite
this file; append to the THREAD-LOG at the bottom and raise changes there.

## 1. What Argus is

A read-only research and alert engine for Kalshi prediction markets. It prices markets with its
own model, compares that price to Kalshi's, prints an alert, and logs every call so the record
shows whether it actually has an edge. Oil is the first engine. Every later engine (politics,
world events, economics) plugs into the same base: fetch → price → compare → alert → log → score.

**Stage 1–2 (this build):** research and alerts only, paper trading, no real bets.
**Stage 3+ (later, gated on the scorecard):** see section 11.

## 2. Hard rules

1. Argus never places, cancels or modifies an order. The Kalshi client only has GET requests
   against the public market-data endpoints and sends no credentials. No Kalshi API key is
   requested or stored, ever.
2. Official, public data sources only. No scraping the Kalshi app or any site whose terms forbid it.
3. Every prediction goes in `prediction_log.csv`, win or lose. Rows are never deleted.
4. 15-minute markets (series frequency `fifteen_min`): paper research only, in their own engine, until they pass
   the board. Every other command still skips them. (Changed by Caleb 2026-10-22: "I want this agent to be able
   to do it all"; the original rule was "skip all 15-minute markets".)
5. The Claude API key comes only from the `ANTHROPIC_API_KEY` environment variable.

## 3. Markets covered (oil engine)

Measured from the public API on 2026-10-22.

| Series | What it is | Shape | Settles on |
|---|---|---|---|
| `KXWTI` | WTI daily | "Above $X" ladder, 50¢ steps | ICE daily settle of the nearest listed WTI contract (Dec-26 on 2026-10-22), 2:30 PM ET |
| `KXWTIW` | WTI weekly range | $1 ranges + "below"/"above" tails | Same ICE settle on the Friday |
| `KXBRENTD` | Brent daily | "Above $X" ladder | Pyth Brent index, 1-minute candle close at 5:00 PM ET |
| `KXBRENTW` | Brent weekly | "Above $X" ladder | Pyth Brent index, Friday 5:00 PM ET |

Every market is read with its range (or strike), Yes bid/ask, No bid/ask, rules text and close time.

**Same-settlement pairs.** `KXWTIW-<date>` and `KXWTI-<date>` settle on the same number, so the
daily ladder implies a price for every weekly range: P(range) = P(above low − 1¢) − P(above high).
When the two disagree by 10+ points, that is flagged as a CONSISTENCY gap. It needs no price model.

## 4. Data sources (approved by Caleb 2026-10-22: "the most optimal way we can do it for free")

| Need | Source | Why |
|---|---|---|
| Markets, prices, rules | Kalshi public API `api.elections.kalshi.com/trade-api/v2` | Official; no login |
| Settlement history | Settled Kalshi markets (`expiration_value`) | The exact number each market paid on |
| Long volatility history | FRED `DCOILWTICO`, `DCOILBRENTEU` CSV | Official (EIA via St. Louis Fed), free, no key. Spot, not futures: used for volatility only, never for the price level |
| Current price | Midpoint of the live Kalshi daily ladder (the 50% point) | Free and on the settlement contract; it is the crowd's number, and the alert says so |

**Negative findings (dated, re-testable):**
- Could not read Pyth prices on 2026-10-22: `hermes.pyth.network/v2/updates/price/latest` and
  `benchmarks.pyth.network` returned HTTP 401 "unauthorized" (feed list still public). Re-test: one curl.
  If a free key becomes available, Pyth `Commodities.CLLZ6/USD` (WTI Dec-26) and `Commodities.Index.BRENT/USD`
  are the live anchors to add.
- FRED spot WTI was $107.89 on 2026-10-20 while the Dec-26 contract settled $97.82: about $10 of
  backwardation. Never anchor on FRED.

## 5. Baseline model

1. **Anchor** = current price. Default `auto`: the live ladder midpoint for the same settlement;
   fallback `settle`: the last official settle.
2. **Volatility** = daily log-return volatility of the settlement contract itself: the larger of the
   plain stdev and the EWMA (λ = 0.94) of the last 40 Kalshi settle-to-settle returns, skipping contract-roll
   days. FRED spot is the fallback only below 15 returns (measured 2026-10-22: spot WTI 4.2%/day vs the Dec-26
   contract 2.4%/day, so spot overstates the move).
3. **Time** = trading days from now to the settlement time (weekend hours excluded).
4. **Events** add extra variance, counted in "normal trading days" (section 6).
5. **News** shifts the centre (section 7).
6. **Distribution** = Student-t on log price, 5 degrees of freedom (fat tails: oil makes big moves more
   often than a bell curve says), scaled to the volatility above.
7. P(range) = CDF(high + ½¢) − CDF(low − ½¢), because settlement is rounded to the cent.

## 6. Event calendar

`data/events.csv`, hand-maintained, plus generated recurring events:
- EIA Weekly Petroleum Status Report: Wednesday 10:30 ET (Thursday after a Monday or Wednesday federal holiday). +0.5 day.
- Baker Hughes rig count: Friday 1:00 PM ET. +0.1 day.
- OPEC+ meetings: +1.5 days. FOMC decisions: +0.5. CPI: +0.3. Jobs report: +0.4. U.S. election day: +0.5.

Each event before a market's close is listed in the alert and widens the odds by its weight.

## 7. News scoring

Headlines come from `--headline` (pasted), `--headlines-file`, or `--feeds` (approved feeds from
`research/sources.csv`). Claude (`claude-opus-5-5`, structured JSON output) scores each one:
direction (supply_up / supply_down / demand_up / demand_down / none), size (none/small/medium/large)
and persistence (0–1: how likely it still matters at the close). Shift = sign × size% × persistence,
with size% = 0.5 / 1.5 / 4.0. The total is capped at ±8%. The alert shows each headline's shift
and the probability change it caused in the top range.

## 8. Alert format (exact)

```
==================== ARGUS ALERT ====================
Market:    <event ticker> - <series title>
Closes:    <close time ET>   (<hours> h left)
Settles:   <settlement source, one line>
Anchor:    $<price> (<anchor kind>)   Last settle: $<price> (<date>)
Vol:       <x.x>%/day (<vol source>)   Horizon: <d.dd> trading days   Event days: +<e.e>
Events:    <name @ time ET>; ... | none
News:      <+/-x.x>% total  (<n> headlines)
  <+/-x.x>%  <headline, first 70 chars>   (top range <+/-n.n> pts)
-----------------------------------------------------
Range               Model  Yes ask  No ask   Gap  Side  EV/ct    Flag
<label, 18 chars>    <p>%    <n>c     <n>c  <+n.n> <YES|NO> <$+0.00>  <WORTH A LOOK|CONSISTENCY|->
...
-----------------------------------------------------
Top call:  <side> "<range>" at <n>c - model <p>% - gap <+n> pts - EV after fees $<x.xx>/contract
Verdict:   WORTH A LOOK | NO EDGE (best gap < 10 pts)
Paper only. Argus never trades.
=====================================================
```

Plain ASCII on purpose ("c" for cents) so it prints correctly in the Windows console.

- **Gap** = model probability of that side minus that side's ask, in points. The side shown is
  whichever has the bigger gap.
- **WORTH A LOOK** only when the gap is 10+ points (and the side has a real ask).
- **EV/ct** = expected profit of one contract after Kalshi's taker fee.
- Each alert is also saved to `alerts/YYYY-MM-DD_HHMM_<event>.md`.

## 9. Prediction log (`prediction_log.csv`, exact columns)

`call_id, logged_at_utc, engine, series, event_ticker, market_ticker, range_label, side, model_prob,
market_price, gap_pts, worth_a_look, paper_contracts, fee_per_contract, anchor_price, anchor_kind,
vol_daily, horizon_days, event_days, news_shift_pct, close_time_utc, status, settle_value, won, pnl_after_fees, notes`

- One row per range per run. `side` is the side with the bigger gap; `model_prob` is the model's
  probability for that side; `market_price` is that side's ask.
- `paper_contracts` = 1 for WORTH A LOOK rows, else 0. P/L counts paper bets only.
- `status` = pending → settled. `settle` fills `settle_value`, `won`, `pnl_after_fees`.

**Fees** (Kalshi fee schedule, checked 2026-10-22): taker fee = round up to the cent of
0.07 × contracts × P × (1 − P). Maker fees (0.0175×) are not used: paper calls assume taking the ask.

## 10. Commands

`markets`, `run`, `events`, `news`, `settle` (manual `--value`, or `--auto` from Kalshi results),
`scorecard` (total calls, paper-bet win rate, P/L after fees, calibration table, Brier score of the
model vs the market), `scan` (every open Kalshi market by category, skipping 15-minute series, with
volume, spreads and "sum of Yes asks" checks on mutually exclusive events), `feeds` (latest posts
from the creator/source lists).

## 11. Later stages (not built yet)

- **Stage 3, more engines:** politics (sources: `research/politics/sources.csv`), world events and
  economics, each as an engine that outputs probabilities per market into the same log and scorecard.
  Each engine has to show 50+ settled calls and a model Brier score better than the market's before
  its alerts are trusted.
- **Stage 4, smart-money sources (Caleb, 2026-10-22: "align myself with the people doing best").**
  Every followed person is a source with its own scorecard, never an automatic copy.
  - Creators (`research/sources.csv`, `research/politics/sources.csv`): log their checkable calls, score them.
  - Kalshi large-trade feed (public `GetTrades`, anonymous): flag block/outsized fills, log as calls, score.
  - Polymarket (read-only, public Gamma/CLOB/Data APIs and on-chain data; no wallet connected): (a) cross-venue
    price for events listed on both; (b) top wallets frozen on a date, then only their LATER trades scored, copied
    with a realistic delay and after fees. A wallet is followed only after enough settled calls beat the market.
  - Kalshi Social profiles: on hold until one is opened by hand and Kalshi's terms are read (not read 2026-10-22, HTTP 429).
  - Never: an auto-copy executor, paid trackers as a dependency, or logging in to read other users' data.
  Whether Caleb can legally trade on Polymarket from the U.S. is unverified and irrelevant to read-only use.
- **Real money** stays outside Argus. Any order is Caleb's, placed by hand.

---

