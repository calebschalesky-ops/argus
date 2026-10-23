# Kalshi liquidity rewards: can a $10-$500 hand quoter get paid?

Written 2026-10-22 (Pacific) / snapshot 2026-10-23 01:49 UTC. Read-only research: public pages, the public
API and Kalshi's CFTC filings. No login and no orders. Code: `argus/rewards/`. Regenerate the tables at the
bottom with `python -m argus.rewards today`.

## 1. Answer

**On paper, yes, by a lot more than maker fills ever paid. It is unproven until one real payout lands.**

- Kalshi publishes every rewarded market and its pool in a public API (`/incentive_programs`). Tonight
  7,252 markets carry a liquidity reward; leaving out 15-minute markets and periods with under 6 hours left,
  5,694 remain, with pools adding up to about **$127,000 a day**. The median pool is about $14 a day.
- The score counts **contracts, not dollars**, and a bid gets full credit if it sits at the "Reference
  Price", which on lopsided markets is often 1-8c. So a few dollars of 1c-8c bids can win a real share
  of a $100-$600/day pool. Kalshi's published formula, run on tonight's books with our order added,
  gives the $10 account $3-$35 a day (table below). Maker-fill P/L by comparison is cents.
- The answer rests on assumptions nobody has tested yet (section 5). The cheapest test is to place the
  $10 hand list once and see whether a reward credit shows up within a day of the period ending.

| account | low (3x crowd, drifted order, 70% eligible) | central (tonight's books) | per month, low to central (x30) |
|---|---|---|---|
| $10 | $3/day | $34/day | ~$100 to ~$1,000 |
| $100 | $21/day | $212/day | ~$600 to ~$6,000 |
| $500 | $62/day | $619/day | ~$1,900 to ~$18,000 |

Read the monthly column as an upper bound on a single snapshot, not a forecast. It assumes the same
opportunity comes back every day. Most rewarded periods here are one day long on daily markets that do
relist (Trump approval, Rotten Tomatoes scores), but competitors can respond. The $500 central row is the
least believable: it would take a large share of the best pools, and that is where other farmers would
show up first. The program ends no later than **Jan 1, 2027**, so there are about 10 weeks left at most.

## 2. The rules (official sources, read 2026-10-22)

Sources:
- **[H]** Help center, "Liquidity Incentive Program", marked "Updated over 2 weeks ago":
  https://help.kalshi.com/en/articles/13823851-liquidity-incentive-program
- **[F]** CFTC self-certification dated Oct 14, 2026, "Amendment to August 2025 Liquidity Incentive
  Program", effective Oct 28, 2026: https://www.cftc.gov/filings/orgrules/rules10142633813.pdf
- **[A]** Kalshi OpenAPI spec, `IncentiveProgram` schema: https://docs.kalshi.com/openapi.yaml
  (endpoint page: https://docs.kalshi.com/api-reference/incentive-programs/get-incentives)
- **[W]** Help center, "where to find rewards" (rewards page `kalshi.com/incentives`):
  https://help.kalshi.com/en/articles/16076644-where-to-find-rewards-and-incentive-info

**Who can join.** "Most regular U.S. Kalshi members" [H]. Excluded: Kalshi affiliates and employees,
introducing brokers, FCMs and their customers, and non-U.S. users [H][F]. Market makers with an agreement
in a market get no reward in that market [F]. Caleb, as a regular U.S. member, is eligible.

**Which markets.** "All Kalshi markets are potentially eligible" [H]. From Oct 28 the list is narrowed to
named subcategories in eleven categories (Appendix C), with contracts picked for "nascency, observed
bid-ask spread patterns" [F]. Each rewarded contract is posted "no later than twenty-four (24) hours" before
its period starts [F].

**How the score works** [H][F]:
- "a snapshot of the order book once per second, at a random moment" [H].
- Reference Price: walking down from the best bid, the first level where cumulative size "reaches one
  fifth of the Target Size" [H]. A yes ask counts as a no bid [F].
- Qualifying orders: whole price levels from the top down until cumulative size reaches the Target Size.
  "If a side of the book never reaches the Target Size, no orders on that side qualify" [H].
- Orders at or better than the Reference Price get "full credit (1.0x multiplier)" [H]. Below it, "the
  Discount Factor raised to the number of ticks away" [H]. From Oct 28 the factor "is set for all Eligible
  Markets at 0.5" [F]. Every live program already uses 0.5 (5000 bps), except 24 that use 0.4.
- Your share of a side = your size x multiplier / everyone's. "What counts is your share" [H]. A snapshot
  is worth "at most 2.0 across all participants", one point per side [H].
- Excluded snapshots: market closed, or either side short of Target Size. Those cut the payout pro rata [H].

**How much and when.**
- Reward = your period score x period reward x (non-excluded / all snapshots), paid only "if the result is
  greater than or equal to $1.00" [F], "rounded down to the nearest cent" [H].
- Paid "no later than 1 day following conclusion of a Time Period" as an account credit [F].
- Until Oct 27: periods up to 31 days, "$1-$1,000 per market, per day" [H].
- From Oct 28: periods of 1 to 7 days [F]. A per-market cap of up to "$10,000 per business day" for the
  most volatile markets, lower caps elsewhere, set in a confidential appendix [F]. Target Size stays
  between 100 and 20,000 contracts [F].
- Taxes: "A verified Social Security Number (SSN) must be on file" above IRS reporting thresholds [H].
- Kalshi "can end or modify the program at any time" [H]. Rewards are withheld for orders under inquiry
  for spoofing, wash trading and similar conduct [F].

**Other retail programs** (from research/claims_check.md, re-checked here):
- Volume Incentive Program: termination filed Sept 28, effective no earlier than Oct 13. In the public API
  the last volume program ended **2026-09-20 03:59 UTC**, and none are active. Count it as zero.
- Deposit and Trading Reward Program: starts "on or after October 28, 2026", but the amounts are in a
  confidential appendix and it is not live yet. Not modelled.
- Interest: 3.50% APY on balances averaging $250 or more, about $1.46 a month on $500. Not modelled.

## 3. Published reward list (yes, it is public)

`GET /trade-api/v2/incentive_programs?status=active&type=liquidity` needs no key. Each row has the market
ticker, start and end, `period_reward`, `discount_factor_bps`, `target_size_fp` and (optionally)
`max_reward_per_account`. **`period_reward` is "Total reward for the period in centi-cents"** [A], so
1,000,000 = $100. That also matches the filed $1-$1,000/day cap; cents would break it. No live program
sets `max_reward_per_account`.

Census, 2026-10-23 00:40 UTC:
- 7,252 active liquidity programs: 6,260 `series_lip`, 949 `new_event`.
- Target size: 1,000 contracts on 7,131 of them; 84 use 2,500; 25 use 300 (15-minute markets).
- Period length: 2,506 are 7 days, 730 are 14 days, 345 are 1 day, plus 15-minute and hourly windows.
- 15-minute markets carry $50 per 15-minute window (target 300). They are excluded here by SPEC rule 4
  and could not be worked by hand anyway.
- Leaving out 15-minute markets and periods with under 6h left: 5,694 programs, about $127k/day.
- History in the API: 362,484 programs since Sept 2025 (340,124 liquidity, 22,360 volume). Monthly counts
  rose from 180 (Sep 2025) to about 96-99k (Sep and Oct 2026).

Order books for 5,670 of the 5,694 were read at 01:49 UTC. Both sides met the Target Size on 9,974 sides,
so a resting bid there scores right now. The 1-contract table and hand list are at the bottom.

## 4. The model (argus/rewards/model.py)

1. **Scoring:** `argus/rewards/score.py` transcribes the filing's algorithm. Our order is added to the real
   book, then the Reference Price, qualifying levels and shares are recomputed. Five unit tests check it
   against the filing's own wording (`tests/test_rewards.py`).
2. **Placement:** one resting bid per (market, side), at the Reference Price and at the back of that
   price's queue: full credit, filled last.
3. **Reward/day** = pool per day x our side share / 2 x uptime.
4. **Fills (assumption):** a share `hit` of the market's 24h volume trades into our side. It fills
   everything ahead of us first, then us.
5. **Fill P/L:** from the house map (`argus.housemap.analyze.load`, imported, not edited). Average maker
   P/L per contract by the price the maker paid, after fees, over 312,140 non-15-minute fills:

   | maker price | 1c | 2c | 3-5c | 6-9c | 10-29c | 30-49c | 50-69c | 70-89c | 90-99c |
   |---|---|---|---|---|---|---|---|---|---|
   | P/L per contract | -0.71c | -0.84c | +0.76c | -0.21c | -1.65c | +0.99c | +2.13c | +1.90c | +1.72c |

   A filled 1c bid loses about 71% of its 1c. That is the long-shot bias, and it costs at most 1c per contract.
6. **Allocation:** greedy, a budget/60 slice at a time, into the order with the highest added net $/day.
   At most 5 / 10 / 15 orders for $10 / $100 / $500, because Caleb re-checks every one by hand. Any market
   whose remaining-period reward is under the $1.00 floor is dropped and the allocation redone.
7. **Scenarios:**
   - **high:** hit 25%, house-map fill P/L.
   - **central:** hit 50%, house-map fill P/L, tonight's competition.
   - **low:** hit 75%, every fill loses its full price; competitors' size x3; uptime 70%; our order one tick
     below the reference (half credit).

**Is competition stable? (measured, one hour, at night)** The 15 sides chosen for the three budgets were
re-read every 15 minutes, 01:02-02:02 UTC (`persist.csv` in the session scratchpad, numbers copied here):
- The 1c walls on the Rotten Tomatoes markets did not move at all. For example KXRT-WILD-87 No stayed at
  1,253, and KXRT-WIC-82 No at 3,161.
- Some sides doubled: KXRT-KLA-27 Yes 465 to 966, KXRT-IPL-82 Yes 231 to 666, KXRT-CLA-80 Yes 24.8k to 49.3k.
- KXTRUMPAPPROVE-E38.4 Yes swung from 2,269 to 7,761 and back to 4,563.
- References mostly held. KXTOKENUSE-T184 No moved 3c to 7c, which would leave a hand order 4 ticks below
  (1/16 credit).

A sixth read at 02:17 UTC, after the commit window, showed someone adding size to the static walls. Three
KXRT No walls each grew by exactly 1,000 (WIC-80 2,096 to 3,096; WIC-82 3,161 to 4,161; WIC-85 2,171 to
3,171), and WILD-87 No grew from 1,253 to 1,853. That looks like one farmer, or a bot, topping up. It is
still inside x3. So the low case's x3 crowd covers what 75 minutes showed. Daytime and multi-day behaviour are not measured.

## 5. What it rests on (ranked by how much the answer moves)

1. **The formula as published is the formula Kalshi pays on.** Nothing here has been checked against a real
   credit. If 1c bids are treated differently in practice (the confidential Appendix D covers "contract
   selection methodology", not scoring, but it is unseen), the cheap-side edge disappears.
   *Test: one $10 hand list, one day.*
2. **Competition stays thin on the cheap sides.** A $50/day side resting $12.53 of 1c bids is a 400%/day
   return to whoever adds size. That it has not been competed away is the strangest fact here. Possible
   reasons: few farmers know the API is public, bots focus on liquid markets, or the snapshot is unusual.
   *Test: run `today` at several hours across a few days and compare.*
3. **Opportunities recur daily.** The x30 monthly figure needs similar pools every day. The 1-day periods
   are on daily-relisting series, so plausible, but unmeasured.
4. **The hand order stays near the reference.** Lopsided books barely move at 1c, but contested middle
   prices (Trump approval at 29-36c) drift. The low case assumes 1 tick (half credit).
5. **Fills.** Cheap bids cap the loss at the price per contract. A 29c-36c bid that fills is a real
   coin-flip-sized position: the $10 hand list avoids these, the $100/$500 lists include them.
6. **Rules change Oct 28 and end by Jan 1, 2027.** New per-subcategory caps and a narrower market list
   could cut pools. Re-run `today` after Oct 28 before trusting any number here.

## 6. What Caleb would do by hand each day (about 10-15 minutes)

1. Run `python -m argus.rewards today` (reads about 800 books, roughly 3-15 minutes depending on rate
   limits; `--top 0` reads all of them, about an hour). It writes `alerts/<date>_rewards.md` with the hand
   list.
2. On kalshi.com, logged in as himself: for each row, place a **limit buy** of the listed side (Yes or No)
   at the listed price for the listed contracts. Leave it resting. Don't place anything above the listed
   price.
3. Next day: check the account activity for reward credits (paid within a day of each period's end). Cancel
   orders whose period ended, and place the new list.
4. Log what was placed and what was paid. Do it in a file Caleb keeps by hand, because Argus never sees
   the account. The first credit, or its absence, is the measurement that settles section 5.1.

Before the first try: an SSN on file is required for reward credits above the IRS reporting threshold.
Rewards are taxable income.

## 7. Today's output (generated)

<!-- rewards-today:begin -->
Liquidity rewards, snapshot 2026-10-23 01:49 UTC: 5670 rewarded books read (15-minute markets skipped), 9974 sides where both sides meet the Target Size, so a 1-contract bid at the reference price scores now.

### Best reward per dollar resting (1 contract)

| market | side | bid at | pool $/day | qualifying size ahead | 1 contract $/day | $/day per $1 resting | hours left |
|---|---|---|---|---|---|---|---|
| KXRT-WILD-87 (Above 87) | no | 1c | 100 | 1,253 | 0.0399 | 3.99 | 22 |
| KXRT-KLA-32 (Above 32) | yes | 1c | 100 | 1,432 | 0.0349 | 3.49 | 19 |
| KXTRUMPAPPROVE-26OCT23-E38.4 (38.4%) | yes | 8c | 600 | 967 | 0.2448 | 3.06 | 12 |
| KXRT-WIC-80 (Above 80) | no | 1c | 100 | 2,096 | 0.0238 | 2.38 | 17 |
| KXRT-WIC-85 (Above 85) | no | 1c | 100 | 2,171 | 0.0230 | 2.30 | 17 |
| KXBIGGESTQUAKE-23OCT26-5.2 (Magnitude of at least 5.2) | no | 6c | 43 | 101 | 0.1177 | 1.96 | 22 |
| KXTRUMPAPPROVE-26OCT23-E38.6 (38.6%) | yes | 29c | 600 | 482 | 0.5622 | 1.94 | 12 |
| KXTRUMPACT-26OCT18-T9 (At least 9) | yes | 4c | 100 | 505 | 0.0702 | 1.75 | 15 |
| KXMLBWSMVP-26-GJAX22 (Griffin Jax) | yes | 0.1c | 14 | 4,450 | 0.0016 | 1.60 | 112 |
| KXRT-WIC-82 (Above 82) | no | 1c | 100 | 3,161 | 0.0158 | 1.58 | 21 |
| KXTRUMPAPPROVE-26OCT23-E38.5 (38.5%) | yes | 9c | 600 | 2,455 | 0.1222 | 1.36 | 12 |
| KXRT-KLA-30 (Above 30) | yes | 3c | 100 | 1,296 | 0.0386 | 1.29 | 21 |
| KXVHGCB-26OCT23-E8.5 (8.5%) | yes | 8c | 78 | 317 | 0.0999 | 1.25 | 19 |
| KXTRUMPAPPROVE-26OCT23-A38.7 (Above 38.7%) | yes | 36c | 600 | 395 | 0.4306 | 1.20 | 12 |
| KXRT-KLA-17 (Above 17) | no | 3c | 100 | 1,441 | 0.0347 | 1.16 | 19 |
| KXYTDAILYTOPVIDEOG-26OCT23-DAI (Dai Dai) | yes | 1c | 28 | 1,250 | 0.0113 | 1.13 | 26 |
| KXYTTOPVIDEOG2D-26OCT23-CAS (Casa Tupka Anthemo) | yes | 1c | 28 | 1,250 | 0.0113 | 1.13 | 26 |
| KXYTTOPVIDEOG2D-26OCT23-RAM (Ramayana: Ravana Tandav (Song) | Namit M, Nitesh T | Ranbir, Yash | AR Rahman, Vishal D, Kumar V) | yes | 1c | 28 | 1,351 | 0.0105 | 1.05 | 26 |
| KXUSDJPY-26OCT2310-B158.125 (158.000 to 158.249) | yes | 1c | 25 | 1,200 | 0.0104 | 1.04 | 12 |
| KXTRUMPVH-26OCT23-T37.4 (Above 37.4%) | yes | 6c | 120 | 334 | 0.0619 | 1.03 | 12 |
| KXRT-KLA-35 (Above 35) | yes | 1c | 100 | 4,885 | 0.0102 | 1.02 | 21 |
| KXRT-WILDW-95 (Above 95) | no | 1c | 100 | 4,908 | 0.0102 | 1.02 | 20 |
| KXSOFRD-26OCT23-E3.88 (Exactly 3.88%) | yes | 1c | 20 | 1,002 | 0.0100 | 1.00 | 10 |
| KXMLBWSMVP-26-DMAY3 (Dustin May) | yes | 0.2c | 14 | 3,700 | 0.0019 | 0.96 | 112 |
| KXTOKENUSE-26OCT26-T180 (Above 180T tokens) | no | 2c | 44 | 1,170 | 0.0186 | 0.93 | 74 |
| KXSOFRD-26OCT23-T3.85 (3.85% or Below) | yes | 1c | 20 | 1,098 | 0.0091 | 0.91 | 10 |
| KXSOFRD-26OCT23-E3.86 (Exactly 3.86%) | yes | 1c | 20 | 1,098 | 0.0091 | 0.91 | 10 |
| KXSOFRD-26OCT23-E3.87 (Exactly 3.87%) | yes | 1c | 20 | 1,098 | 0.0091 | 0.91 | 10 |
| KXMLBWSMVP-26-DHALL37 (DL Hall) | yes | 0.1c | 14 | 8,000 | 0.0009 | 0.89 | 45 |
| KXRT-CLA-65 (Above 65) | no | 1c | 100 | 5,771 | 0.0087 | 0.87 | 22 |

### Hand list

**$10 account, at most 5 orders** (central case): rests $9.98, reward $34.72/day, expected fill P/L $-0.69/day, net $34.03/day.

| market | buy | limit price | contracts | ties up | reward $/day | hours left in period |
|---|---|---|---|---|---|---|
| KXTRUMPAPPROVE-26OCT23-E38.4 | YES | 8c | 78 | $6.24 | 22.40 | 12 |
| KXRT-WILD-87 | NO | 1c | 221 | $2.21 | 7.50 | 22 |
| KXRT-KLA-32 | YES | 1c | 153 | $1.53 | 4.83 | 19 |

### Budgets and scenarios (assumptions: research/rewards.md section 4)

| account | scenario | orders | resting | reward/day | fill P/L/day | net/day | paid for periods running now |
|---|---|---|---|---|---|---|---|
| $10 | low | 1 | $9.86 | $3.27 | $0.00 | $3.27 | $2.53 |
| $10 | central | 3 | $9.98 | $34.72 | $-0.69 | $34.03 | $22.26 |
| $10 | high | 3 | $9.98 | $34.72 | $-0.16 | $34.56 | $22.26 |
| $100 | low | 6 | $98.62 | $20.70 | $0.00 | $20.70 | $16.04 |
| $100 | central | 9 | $99.61 | $214.32 | $-1.83 | $212.49 | $129.90 |
| $100 | high | 9 | $99.61 | $214.32 | $-1.30 | $213.02 | $129.90 |
| $500 | low | 15 | $492.40 | $62.08 | $0.00 | $62.08 | $50.46 |
| $500 | central | 15 | $496.51 | $615.37 | $3.70 | $619.06 | $370.71 |
| $500 | high | 15 | $496.57 | $617.25 | $-0.48 | $616.76 | $370.05 |

Read-only estimate from one order-book snapshot. Argus places no orders. Rewards pay only if a period's total is at least $1.00, and Kalshi can end or change the program at any time.
<!-- rewards-today:end -->
