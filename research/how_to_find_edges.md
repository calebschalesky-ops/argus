# How people find and validate edges, and what Argus should do next

Written 2026-10-22 by the argus-edge-survey session. Read-only web research: public pages only, no logins,
nothing bought. Every source is in [how_to_find_edges_sources.md](how_to_find_edges_sources.md), with how much of it was
actually read (full text, abstract, or a search-engine summary). Sellers of signals, bots and courses are treated as
marketing, not evidence. This file builds no code.

Evidence labels used below:
- **strong**: several peer-reviewed or large-sample studies agree, or Argus measured it itself on hundreds of events.
- **moderate**: one solid study, or a platform's own documentation, or Argus's own result on a small sample.
- **anecdotal**: interviews, blog posts, forum threads, press reports of one trader.

## 0. The short version

1. Across a hundred years of betting research and our own day-one tests, the same picture repeats: **the price of a
   liquid market is a better forecast than anyone's model**, and the money is made by people who (a) get paid to take
   the other side of impatient or biased flow, (b) read rules, data releases and odd structures more carefully than the
   crowd, or (c) are faster. A $10 hand-traded account can only use (b), and a little of (a).
2. The fastest way to tell a real edge from noise is not win/loss. It is **closing-line value** (did the price move
   toward you after you bought?) plus a pre-written hurdle that counts every variant you tried. Win/loss needs
   thousands of bets to see a 1-cent edge; closing-line value needs about a hundred, but it is necessary, not enough
   (section 3.3).
3. One thing found today changes numbers already on the board: **Kalshi's fee rounding is per order, not per contract**
   (section 4.1). Every Argus backtest charged one contract per order. At 90c and up, a 5-10 contract order pays
   0.3-0.7c a contract in fees instead of 1c. That alone is the same size as the late-favourite edge.

## 1. Edge types, ranked for a $10 hand-traded account

Rank = how likely it is that Caleb, trading by hand with $10, can actually collect it. "Evidence" is about whether the
edge type exists at all, not whether Argus has found one.

| # | Edge type | What it is | Evidence it exists | $10 by hand? | What Argus already knows |
|---|---|---|---|---|---|
| 1 | **Rules and settlement reading** | The contract pays on a source, a time or a wording the crowd misreads (rounding, "or above", sworn-in vs called, which station, early-close terms) | moderate (UMA dispute studies; Polymarket rule-change losses; Argus crossvenue found 1,280 of 2,963 pairs with a hard rule difference) | **yes**: no speed or size needed | rulequirks session running |
| 2 | **Information already public before the close** | The outcome, or most of it, is published or observable before trading stops (weather obs, trackers, counts, results posted before settlement) | moderate (Argus weather map: Kalshi value = NWS CLI on 3,834 of 3,841 days; practitioners) | **yes**, if the window lasts minutes, not seconds | known-before-close running (weather only so far) |
| 3 | **Behavioural biases in pricing** | Favourite-longshot bias, bias growing with time to expiry, parlays priced above their legs, popular-team money | **strong** that the biases exist (Snowberg-Wolfers; Page-Clemen; Bürgi-Deng-Whelan on Kalshi); **weak** that they beat Kalshi's fee | yes, small | favourites basket and sports favlong: no edge at the ask; longshot fade running |
| 4 | **Structure: identities between prices** | Prices that must add up (mutually exclusive outcomes sum to $1; "by March" cannot be above "by June"; daily and weekly on the same settle) | **strong** on Polymarket and PredictIt (Saguillo et al.: ~$40M extracted Apr 2024-Apr 2025; Zitzewitz on PredictIt); on liquid books gaps last seconds (NBA study: median 3.6 s) | sometimes: only slow, thin markets | WTI daily/weekly consistency: 2 locks in 2 months; sports stale ladder checks |
| 5 | **Risk transfer and liquidity provision (being the maker)** | Get paid the spread for resting orders that impatient takers hit | **strong** (Bürgi-Deng-Whelan: makers beat takers on Kalshi; Argus house map: takers -2.2c, makers +0.9c per contract) | partly: resting orders by hand are fine, but you cannot re-quote fast, so you get picked off when news hits | maker bucket tests: no bucket significantly positive with strict fills |
| 6 | **Reward and incentive programs** | Exchange pays for resting size near the best price, or for volume | moderate (Kalshi and Polymarket documentation) | mostly no: target sizes are 100-20,000 contracts | quoting-rewards session running |
| 7 | **Cross-venue price differences** | Same event priced differently on Kalshi and Polymarket | moderate (gaps exist; most big ones are rule differences) | rarely: both legs need two accounts, and Polymarket's U.S. status is unverified | crossvenue: 0 WORTH A LOOK on 2,963 pairs |
| 8 | **Better forecasting model** | Your probability beats the crowd's | **strong evidence against** on liquid markets (Fed paper: Kalshi ties or beats Bloomberg; Argus: oil, commodities, weather, 15-minute crypto all lost) | no, except in niche thin markets nobody models | dropped |
| 9 | **Speed** | See the price move elsewhere first (exchange spot for 15-minute crypto, in-play score feeds) | **strong** that it exists (Croxson-Reade: Betfair reprices within seconds of a goal); bots took it, Polymarket added taker fees to kill it | **no**: Argus has no streaming key by hard rule, and a human is too slow | 15-minute lag strategy lost |
| 10 | **Copying winners** | Follow the wallets or traders with the best record | weak: only ~3% of Polymarket accounts look skilled after luck is removed (Gómez-Cram et al., press reports); leaderboards are survivorship | yes in principle | smartmoney running, nothing settled |
| — | Private or insider information | Trading on non-public facts | exists (Maduro trade press reports) | **excluded**: against Kalshi's rules and possibly the law | not pursued |

What the ranking means in practice: the edges that a slow, tiny account can use are the ones where **reading** beats
**speed** and **size**: rules, already-published data, and slow structural mispricings in thin markets. Practitioners
who made money by hand say the same thing (section 5).

## 2. New strategy ideas Argus has not tested

Checked against the whole THREAD-LOG, `research/`, and the package docstrings of the sessions still running
(knownbefore, rewards, mentions, rulequirks, longshot, housemap, refprice, electionnight, decay, newlistings, lab).
Where an idea is next to one of those, the overlap is said. Each test is written so the edge factory (`argus/lab`) can
run it: rule fixed in advance, train on earlier data, judge on a holdout it never saw, taker fees from `fees.py`,
clustered by event, and every variant counted (section 3).

Ranked by (chance it is real) x (whether $10 by hand can use it) / (cost to test).

### 2.1 Bigger orders, same strategies (fee rounding re-score)
Kalshi charges the fee per order and rounds the whole order to the cent once, through a per-order accumulator
(section 4.1). One contract at 95c pays a full 1c; five contracts in one order pay 2c (0.4c each); ten pay 4c (0.4c each).
Every 90c+ result on the board (favourites basket 95-99c at 1 h: -0.7%; late favourites: +2.4% to +5.1%; 15-minute late
favourites: about break-even) was scored at 1 contract per order. **Test:** re-score those exact logs with
`fees.taker_fee(price, contracts=N)` for N = 3, 5, 10, divided by N, and require the candle's volume at that hour to be at
least 3N contracts so the size was plausibly there. No new data, no new rule, so no new multiple-testing cost. Report
which verdicts change. A $10 account can buy 10 contracts at 95c, so this is directly usable.

### 2.2 Nested-contract violations across all of Kalshi
Some prices are nested: "by March 31" cannot be worth more than "by June 30"; "above $100" cannot be worth more than
"above $95"; "at least 3 cuts" cannot beat "at least 2". Argus checks this only on WTI daily vs weekly and inside one
sports game. Polymarket studies found most arbitrage profit in exactly these cross-market dependencies. **Test:** from
the lake, group every series whose events or markets are nested by date, strike or count (parse from titles and rules;
hand-check 50). At every hourly candle, flag Yes bid(narrower) > Yes ask(wider) + both fees. Record how often, how many
cents, how long it lasted (hours), and the smaller leg's volume. Pass = at least 20 events with a gap lasting 1+ hour
after fees on the holdout months. Both legs settle against the same fact, so this is close to riskless when real; read
both rule texts (the deadlines and "by" wording must truly nest).

### 2.3 Sell parlays: buy No on Kalshi combos
A July 2026 paper on 23 million Kalshi sports moneyline trades (Moshrefi) reports cross-game parlays priced above the
product of their leg prices, more so with more legs, even when the legs themselves are well calibrated. Argus's
favourites test excluded combos, so this is untested. **Test:** for settled combo markets in the lake, at a fixed time
(1 h before the first leg starts), compute product of leg mids (cross-game legs only; same-game legs are correlated and
need their own test). Rule: buy the combo's No when combo Yes bid exceeds the leg product by K points, K chosen on the
first half of the sample from {3, 5, 8}. Score on the second half after fees. Also log the No's closing-line value.
Usable by hand (the No is usually 70-95c).

### 2.4 Known-before-close outside weather
Known-before-close only covers weather observations. The same mechanism exists in every market that settles on a public
tracker: TSA checkpoint counts, AAA daily gas price, Rotten Tomatoes scores, box office, Spotify/Billboard charts, app
store ranks, post counts. **Test:** list every non-weather series whose rules name a public source; for each, write down
(a) when the source publishes or shows a running value, (b) when Kalshi trading stops. Where (a) is before (b), replay
50+ past events: at the moment the source showed a value that fixed the outcome, what was the best ask on the winning
side, and how long until it reached 99c? Pass = median window of 5+ minutes with the winning side at 95c or less, on
10+ events. This is the purest "reading beats speed" edge. Hand off to known-before-close rather than building twice.

### 2.5 Low-attention markets: is calibration worse where few people trade?
Practitioner accounts (Domer, Peabody) put the edge in markets the big money ignores. Argus has measured returns by
price bucket and category but never by **attention**. **Test:** in the lake, bin settled markets by total volume decile
(and by number of distinct trade prints). For each decile, compute calibration of the hourly mid 1 day before close and
the taker return by price bucket. Rule search only inside the bottom 3 deciles (price bucket x side), train on months
1-12, holdout months 13-18, BH at 10% over all cells tried. A $10 account's tiny size is no handicap in thin markets,
which is why this is worth a lake query.

### 2.6 Favourite-longshot bias by time to expiry
Page and Clemen (Economic Journal 2013) found prediction markets well calibrated close to expiry but biased far from it:
favourites underpriced, longshots overpriced. Moshrefi finds Kalshi sports calibration changes with time left in the
contract. Argus's favourites test looked at 1 h, 1 d, 7 d and 30 d before close, but the 7 d and 30 d cells were too thin
to judge. **Test:** time to scheduled close in {1-7 d, 7-30 d, 30-120 d} x side price {70-80, 80-90, 90-97}; price at the
ask with a real bid within 5c; measure from the scheduled end, never the actual close (the early-close leak). Report
return per dollar per month held, since money is locked up. Overlaps with the decay session for "by date" questions;
this covers everything else (awards, season futures, economics).

### 2.7 Underreaction after big moves
A press report of an unnamed Polymarket study says prices move only about 64% of the way at first after major news and
take about 80 minutes to finish (unverified: no authors given). If Kalshi is the same, a big move continues. **Test:** in
the lake's hourly candles (non-sports, non-15-minute), event = mid moves 10+ points within one hour on a market with a
real book. Buy in the direction of the move at the next hour's ask; exit at the mid 3 h later or at settlement (two
pre-written exits). Compare with the opposite rule (fade the move). Train/holdout split by month. Hours, not seconds,
so a human can act.

### 2.8 Popular-team money in sports
Levitt (2004) showed bookmakers shade prices against the side the public loves and still win. On an exchange like Kalshi
there is no bookmaker to shade, so if the public overbets popular teams, the other side should be cheap. **Test:** rank
NFL, NBA and MLB teams by their share of Kalshi game volume in the previous season (fixed before the test season). For
each game, buy the less-popular side at the ask 1 h before start; also the subset where the popular side is the
favourite. Score per contract and against the no-vig sportsbook close where `sports/gap.py` has it. This differs from
sports favlong (which bins by price, not by who the team is).

### 2.9 Maker orders only when the flow is not "toxic"
The house map says makers net about +0.9c a contract overall, but a resting order loses when the person hitting it knows
something. Market-making research measures this with order-flow imbalance (VPIN). Polymarket's database paper links low
toxic flow with steadier late prices. **Test:** in the house-map trade tape, for each fill, compute the taker imbalance
over the previous 30 and 120 minutes and the hour of the week. Rule: rest a maker order only when imbalance is under a
threshold chosen on training months. Score maker P/L per contract on holdout months with the strict fill rule. A human
can rest an order before a game and walk away, so a "when to post" rule is usable by hand; continuous quoting is not.

### 2.10 Does Kalshi lag Polymarket on matched pairs?
Crossvenue showed prices agree to 1-3 points. It never measured **who moves first**. If Kalshi's thin markets lag
Polymarket by several minutes after news, the Polymarket move is a signal to act on Kalshi alone (one account, one
leg). **Test:** for the 1,795 high-confidence pairs, pull Polymarket 1-minute price history and Kalshi 1-minute candles
(7-day windows). Find Polymarket moves of 5+ points within 5 minutes; measure Kalshi's mid change over the next 1, 5, 15,
60 minutes. Pass = Kalshi still 3+ points short of the Polymarket move after 5 minutes, at the ask after fees, in 30+
episodes on a holdout week. Under 5 minutes is bot territory and fails for a human.

### 2.11 Free economic nowcasts against Kalshi's CPI and GDP ladders
The refprice session covers crypto (Deribit) and the Fed. The Cleveland Fed inflation nowcast and the Atlanta Fed GDPNow
are free and famous. The Fed's own paper says Kalshi already ties or beats the Bloomberg consensus on CPI, so expect no
edge, but the test is cheap. **Test:** each release since 2023, turn the nowcast into a distribution using its own past
errors (only errors known before that release), compare with the Kalshi ladder the day before, same 10-point rule as
oil, Brier vs market. Pass needs the usual gate. Hand to refprice.

Not new (already tested or running), so not listed: sportsbook no-vig consensus vs Kalshi (`sports/gap.py`, which is
the Kaunitz method), sum-of-asks within one event (`scan`, `sports/stale.py`), deadline decay (decay), mentions base
rates (mentions), rewards (rewards), new listings (newlistings), election night (electionnight), wallet copying and big
trades (smartmoney), favourites, longshots, makers by price bucket, and every forecasting model.

## 3. Process upgrades for Argus

### 3.1 Put closing-line value on the board (top priority)
For every paper call, store the market's mid at a fixed later moment: the last mid before the scheduled close, or
before the game starts for sports (Kalshi game markets keep trading in-play, so "close" must mean the start). CLV =
that mid minus the price paid, for the side bought, in cents. Show mean CLV, its standard error and the share of calls
with positive CLV next to P/L on `board`.

Why: the bet's payoff is 0 or $1, so its noise is huge (standard deviation 22c a contract at 95c, 50c at 50c). The
later price moves only a few cents, so its noise is small. With a CLV standard deviation of 5c, a 1c average CLV shows
at t = 2 after about **100 calls**; the same 1c edge in win/loss needs about **1,900 calls at 95c and 10,000 at 50c**
(table in 3.3).

Two cautions, both from the literature:
- Karl Whelan (the same economist as the Kalshi maker paper) tested CLV on 3,670 NBA games and found most positive-CLV
  bets still lost: only the top decile, averaging well above the margin, made money. **CLV must beat the fee plus the
  spread you crossed, not just be positive.**
- CLV against Kalshi's own close only shows you beat Kalshi's later price. If Kalshi's close is itself biased (the
  favourite-longshot pattern near expiry), CLV can mislead. Use it to kill ideas early, never to promote one alone.

### 3.2 Count every try, and raise the bar
Day one tested ~20 strategies with several variants each (horizons, price bands, AAA in or out). With that many tries,
a t-statistic of 2 means little. Harvey, Liu and Zhu argued for t > 3 on a new finance factor for exactly this reason.
Concretely:
- **Trial registry**: one CSV row per variant ever scored (`lab` already has survivors; add every non-survivor too), with
  the date the rule was written down and the date it was first scored.
- **Board hurdle**: a strategy is "real" only at t >= 3 on its holdout (event-clustered), or after a Benjamini-Hochberg
  correction at 10% over every row in the registry. The Deflated Sharpe Ratio (Bailey and López de Prado) is the same idea
  for returns that are not normal; it needs the number of trials, which the registry gives.
- **Probability of backtest overfitting** (Bailey, Borwein, López de Prado, Zhu): for the rule miner, split history into
  16 blocks, pick the best rule on every half, check its rank on the other half. If the in-sample winner is below the
  median out of sample more than ~half the time, the miner is fitting noise.
- **White's Reality Check / Hansen's SPA**: when picking the best of many rules on the same data, bootstrap the whole
  set to ask whether the best one beats zero after the search. Worth adding to `lab/stats.py` once the registry exists.

Applied to today's one pass: late favourites at 30 and 60 min have t of about 1.8-2.7 before clustering by event
(368 bets +2.8%: t ~ 1.8; 204 bets +5.1%: t ~ 2.7), and the band and AAA split were chosen after seeing results. By
this bar it is a hypothesis, which is how the commodities thread already treats it. The forward test is the right
call; its stopping rule should be a sequential test (below), not 20 events.

### 3.3 Minimum sample sizes per edge size
Number of independent bets needed so the average edge shows at t = 2 / t = 3 (binary payoff only; cluster by event,
so correlated bets on one event count once):

| Price you pay | 0.5c edge | 1c edge | 2c edge | 5c edge |
|---|---|---|---|---|
| 50c | 40,000 / 90,000 | 10,000 / 22,500 | 2,500 / 5,625 | 400 / 900 |
| 80c | 25,600 / 57,600 | 6,400 / 14,400 | 1,600 / 3,600 | 256 / 576 |
| 90c | 14,400 / 32,400 | 3,600 / 8,100 | 900 / 2,025 | 144 / 324 |
| 95c | 7,600 / 17,100 | 1,900 / 4,280 | 476 / 1,070 | 77 / 172 |
| 97c | 4,660 / 10,480 | 1,170 / 2,620 | 292 / 655 | 47 / 105 |

Same edge measured as CLV with a 5c standard deviation: 400 / 100 / 25 bets for 0.5c / 1c / 2c at t = 2.

Use: before any backtest, write the edge you expect and look up n. If the data cannot reach it, do not run the test
(it can only produce noise to over-read). For forward tests, use a sequential probability ratio test (Wald): set
"edge = 0" vs "edge = the backtest's edge", and stop the moment either is accepted at 5% error, instead of a fixed count.

### 3.4 Fee model by order size
Make `fees.taker_fee(price, contracts=N)` the only way any log charges fees, store `contracts_per_order` in every row,
and add a board column that re-scores at N = 1, 5, 10 (section 2.1). Kalshi's docs: fees are computed to six decimals
and the balance is rounded once per order, with a per-order accumulator (section 4.1).

### 3.5 Record the book, not just the candle
Hourly candles hide whether the ask had any size. For every live paper call, store the top 3 levels of the order book
(`GET /markets/{ticker}/orderbook`, public). That ends the "was the quote real?" question that cost the favourites thread
a whole re-run, and gives the depth needed for 2.1.

### 3.6 Pre-registration and holdout discipline (already partly done; make it a rule)
- Write the rule, the price, the horizon, the exit and the expected edge into the registry **before** the first score.
- Keep the last 25% of the lake's months as a holdout that only `lab` can open, once per rule.
- Walk-forward by calendar week or month, as sports favlong already does; never pick thresholds on the whole sample.

### 3.7 Sizing: fractional Kelly, and why it hardly matters at $10
Kelly for a binary contract bought at price q with true probability p: bet f = (p - q) / (1 - q) of the bankroll. At
late-favourite numbers (q = 92c, p = 94.6c) that is **32%** of the bankroll, because the payout is small and the win rate
high. That is exactly where Kelly is most dangerous: the edge estimate itself has a standard error of about 1.4c, and
MacLean, Thorp and Ziemba show errors in the mean dominate everything. Rule for Argus's hand list: at most quarter-Kelly
**computed on the lower end of the edge's confidence interval**, which today means 1-3 contracts, never the whole $10 in
one market, and never more than one position per event.

## 4. Platform mechanics that create (or kill) edges

### 4.1 Kalshi fees and rounding (read 2026-10-22)
- Formula (Kalshi schedule as summarised by Allium's docs, matching `fees.py`): taker 0.07 x C x P x (1 - P) x the
  series' taker multiplier; maker 0.0175 x C x P x (1 - P) x maker multiplier, zero on most series. The `fee_type`,
  `fee_multiplier` fields come with each series.
- Rounding (Kalshi API docs, "Fee rounding"): fees are six-decimal amounts; a retail balance is kept to the cent, so a
  rounding fee brings each fill's balance change to the cent, and "the fee accumulator is maintained per order across
  all fills". Over many fills of one order the total converges to the exact fee. **Many one-contract orders each pay a
  full rounding cent; one ten-contract order pays it once.** At 95c: 1c a contract for 1-lots, 0.4c for a 10-lot.
- Kalshi's fee-schedule PDF returned HTTP 429 again today; the per-series maker list was not read.

### 4.2 Rewards
Kalshi's Liquidity Incentive Program scores resting orders once a second by size and distance from the best price, per
market target size of 100-20,000 contracts (help centre). The claims-check thread found it runs to 2027-01-01, amended
2026-10-28. Polymarket pays daily with a $1 minimum payout and a maximum spread per market. Both reward size; a $10
account holds 10-100 contracts, so it sits under most targets. The rewards session will say whether any market's target
is small enough.

### 4.3 Settlement sources and timing
- Kalshi names a source per series (`settlement_sources`) and closes most "will it happen by" markets early when it
  happens. Two Argus traps came from this already (early-close look-ahead; empty-book 99c). The same thing is an edge
  when the source publishes before trading stops (2.4).
- Polymarket settles through UMA's optimistic oracle; disputes are voted, and a WSJ investigation reported in Galaxy
  Research found the 10 largest wallets cast over half the votes in most disputed markets. A near-certain Polymarket
  price can sit a few points short of $1 for settlement risk, which shows up as a cross-venue "gap" that is not an edge.

### 4.4 Order types and data access
- Public, no key: markets, events, series (with fees and settlement sources), the order book, recent trades, candles
  (1-minute only within about 7-day windows; hourly and daily longer), and the historical tier for markets settled before
  the cutoff. That supports every test in section 2.
- Websocket streams need an API key, which Argus's hard rule 1 forbids. So Argus can never see sub-minute moves live;
  any edge that needs seconds is out by design.
- Limit orders rest at no fee on most series and pay the maker fee on about 160 series (maker thread, 2026-10-22).
  Combos (multi-leg) are a separate product with their own pricing; their fee treatment varies between third-party
  guides, so read Kalshi's fee page before testing 2.3 with real money.

### 4.5 Cross-venue structure
Polymarket's dynamic taker fee on its 15-minute crypto markets (reported January 2026) was introduced to kill latency
arbitrage, a reminder that a visible, bot-friendly edge gets closed by the venue. Kalshi's in-house market maker was
reported "not profitable" by its co-founder (InGame), which says how hard plain market making is even with the best seat.

## 5. Practitioners: who made money, on what kind of edge, and how they found it

Only accounts with some checkable detail. None is proof; they show where people look.

| Who | Venue | Edge type | How they found it | Evidence |
|---|---|---|---|---|
| Domer (Polymarket @ImJustKen) | Polymarket, since 2007 on earlier venues | information + behavioural: foreign elections, bills, wars; reads rules and slow news; poker background | specialising in niches big money ignores; many small positions | anecdotal (ChinaTalk interview; Polymarket-sponsored MetaMask profile: ~10,000 predictions, $2.5M+ reported) |
| Rufus Peabody | U.S. sportsbooks, prediction markets | model + timing: props and alt lines priced off game-script models; waits for the market to settle before betting | builds his own ratings; stopped publishing them because publishing moved the market | anecdotal (VSiN, InGame, Wharton Moneyball) |
| Kaunitz, Zhong, Kreiner | soccer bookmakers | structure: bet when one bookmaker is far off the average of all bookmakers | scraped odds, used the crowd of books as fair value | moderate (paper; 3.5% simulated over 10 years, real money for 5 months, then accounts limited) |
| Princeton student (MarketWatch, via press) | Kalshi | liquidity provision (market making) | quoted spreads from the 2024 election on | anecdotal (~$150,000 claimed) |
| Kalshi Trading (in-house) | Kalshi | liquidity provision | — | reported "not profitable" (co-founder, InGame) |
| Susquehanna (SIG) | Kalshi | risk transfer: committed institutional market maker since April 2024 | contract with the exchange | press release; P/L not public |
| Polymarket arbitrageurs | Polymarket | structure: Yes+No not $1, and logically linked markets | LLM-assisted search for dependent markets | strong-ish (Saguillo et al.: ~$40M over a year; arbitrage trades mostly 1-5%) |
| 15-minute crypto bots | Polymarket | speed: exchange spot moves before the market | latency code | anecdotal ($313 to $414k claim, no primary wallet analysis); venue then added fees |
| Data Golf | golf books | structure: compare each book with the sharpest (Pinnacle-style) close | Buchdahl's method on 106k matchups | moderate (public method, large sample) |

Pattern: the hand-scale winners specialise and read; the big winners provide liquidity or arbitrage with code; nobody
credible claims a general-purpose model beats liquid prices.

## 6. Red flags of fake edges

Each one has already bitten Argus or a study it read.

1. **Last-trade prices instead of the ask.** Favourites went from -2.9% to +0.4% just by pricing at the last trade.
2. **Empty books.** A 1c/99c book is not a 99c favourite; 98c "favourites" won 85% until a 5c spread rule was added.
3. **Measuring from the actual close.** "By Friday" markets close when the thing happens; time-before-close leaks the
   answer (95-99c early-closers won 100%).
4. **Splitting the sample after seeing results** (the AAA split in late favourites). Count it as another trial.
5. **High win rate mistaken for edge.** At 95c you must win 96%; a 95% win rate is a loss.
6. **Per-contract vs per-dollar returns.** The paper's -20% is per contract; dollar-weighted it is zero by construction.
7. **Ignoring fills.** A maker backtest that counts every touch as a fill flatters makers; you only get filled when
   someone wants to trade with you, often because they know more.
8. **Ignoring fee rounding**, in either direction: 1-lot fees at 1c per contract, or assuming 10-lot fees on a 1-lot.
9. **"Arbitrage" across venues with different rules.** The biggest gaps in the crossvenue scan were all rule differences.
10. **Correlated bets counted as independent.** One tennis match produced dozens of big-trade "calls"; cluster by event.
11. **Leaderboards and screenshots.** Survivorship: thousands of losing wallets are not on the board. The all-time
    Polymarket #1 made $22M on one election.
12. **Edges that need speed or size you do not have.** If the backtest assumes a fill within seconds or 1,000 contracts at
    the quote, a $10 hand account cannot collect it.
13. **Sellers.** Anyone selling the signal, the bot or the course has a reason to show only the good months.
14. **A model that "beats" the market on Brier only because the market's spread was counted against it.** Compare to the
    mid ("fair" Brier), as the oil backtest does.
15. **One regime.** Two months of oil in backwardation is one regime; ask whether the edge needs that regime to last.

## 7. Suggested order of work

1. Re-score the 90c+ logs at 5 and 10 contracts per order (2.1, 3.4). Hours of work, no new data.
2. Add CLV and the book snapshot to every live logger and the board (3.1, 3.5).
3. Start the trial registry and the t >= 3 / BH hurdle in `lab` (3.2).
4. Lake queries: nested violations (2.2), combo No (2.3), attention deciles (2.5), time-to-expiry (2.6). All four run on
   data the lake already holds.
5. Hand non-weather known-before (2.4) to the known-before-close session and nowcasts (2.11) to refprice.
