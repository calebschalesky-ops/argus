# Smart money (Argus Stage 4)

**What this is for.** Caleb wants to line up with the traders who do best. The catch is that a
leaderboard is partly luck, and copying always lags: the leader moves the price before you can
follow. So Argus never copies anyone. Every wallet it watches, and every big Kalshi trade it
flags, is treated as a **source with its own scorecard**. A source only counts as worth following
once its record, measured *after* we started watching and *after* our delay and fees, says so.

Nothing here can trade, connect a wallet, sign anything or log in. It reads public data
only, through a helper that refuses every address that is not on a short read-only list.
Every request it makes goes into `smartmoney/api_calls.csv`.

## The five commands

Run them from the `argus` folder:

```
python -m argus.smartmoney freeze      # once: write down today's best wallets
python -m argus.smartmoney track       # often: log what those wallets bought since
python -m argus.smartmoney bigtrades   # often: log big trades on Kalshi
python -m argus.smartmoney settle      # fill in which calls won
python -m argus.smartmoney score       # scorecard per wallet (runs settle first)
```

### 1. `freeze`: write down the leaders, on a date

Reads Polymarket's public leaderboard (top 100 all-time and top 100 this month, by profit),
then checks each wallet's history. A wallet makes the list only if:

- it has **at least 30 markets that have already ended**,
- across **at least 15 different events**,
- **no single event gave it more than half its profit**, and
- it made money on those closed markets overall.

That filter exists because one lucky bet is not skill. The all-time #1 wallet made $22 million,
but on 14 markets that were all one election, so it is rejected. The best 50 that pass go into
`smartmoney/frozen_YYYY-MM-DD.csv` with the time of the freeze.

### 2. `track`: only what they do after the freeze

For each frozen wallet, Argus reads the trades it made **after** the freeze time. Nothing
before the freeze can get in (a test checks this). That is the whole point: their past wins
got them on the list, so the past can't also be used to score them.

- Only **buys** count. A buy is a new opinion; a sell is just getting out.
- **One call per wallet per market outcome.** A trader who fills an order in 20 pieces made
  one bet, not 20.
- **15-minute (and 5-minute) markets are skipped**, as everywhere in Argus.
- **Our price is the price 10 minutes later**, not theirs. That's the realistic lag for someone
  following by hand. Kalshi's taker fee is added on top as a stand-in for any fee. The log
  records both prices, so you can see how much the delay cost (`delay_cost_pts` in the notes).

Each call goes into `smartmoney_log.csv`, which has the same columns as `prediction_log.csv`.

### 3. `bigtrades`: big money on Kalshi

Kalshi publishes every trade, but without names: just the market, size, price and which side
the buyer took. Argus reads the last 2 hours and flags:

- every **block trade**, and
- every order at least **10 times the market's usual order size** (the median over the past
  week) that is also worth **$1,000 or more**.

Each flag is logged as a call on the big buyer's side, again at the price 10 minutes later plus
the fee, along with how far the price moved afterwards. Only the **first** flag per market and side
is scored, because a busy live match can throw up 30 big orders in an hour, and those are one
opinion repeated.

### 4. `score`: who, if anyone, is worth following

For each wallet (and for big trades overall), the scorecard shows calls, settled calls, win rate,
the average price we paid, **edge** (how many points more often they won than the price said they
should), a **z-score** (how sure we can be that edge isn't luck), profit or loss after the delay
and fees, and the Brier score of the price.

A source is **FOLLOWABLE** only when all three hold:

1. **100 or more settled calls**,
2. **z of 3 or more**, and
3. **a profit after fees and delay.**

Why so strict: around 50/50, a run of 100 calls swings by about 5 points of win rate on luck
alone. And because we are testing 50 wallets at once, one of them will look like a genius at the
usual "2 standard errors" bar purely by chance. A bar of 3 makes that unlikely (about a 6% chance
that any of the 50 gets there by luck). In practice a wallet needs roughly a 15-point edge after
100 calls, or about 7.5 points after 400.

## What it can't tell you (yet)

- **Polymarket's history gives one price per minute, not the ask.** Our entry price is the
  traded price 10 minutes later, not what you would actually have paid to buy. Real entries would
  be a little worse.
- Kalshi's taker fee stands in for Polymarket's own fees, which differ by market.
- A sold-before-the-end position still counts toward the "30 ended markets" in `freeze`.
- Some top wallets look like market makers (huge volume, thin profit). Their calls will probably
  show no edge, and the scorecard will show that.
- Kalshi's trade feed is anonymous. "Big trade" is a crowd signal, not a person.
- Kalshi Social profiles are **on hold** until someone reads Kalshi's terms for them.

## Files

| File | What's in it |
|---|---|
| `smartmoney/frozen_YYYY-MM-DD.csv` | The wallets frozen that day and why they passed |
| `smartmoney_log.csv` (project folder) | Every wallet and big-trade call, never deleted. Rows marked `void` are kept for the record but not scored |
| `smartmoney/scorecard_YYYY-MM-DD.csv` | The latest scorecard |
| `smartmoney/api_calls.csv` | Every request made, with the time and result |
