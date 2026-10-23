# Using Argus

## Setup


```bash
cd argus
python -m pip install -r requirements.txt
python -m pytest -q
```

News scoring needs a Claude API key in an environment variable (nothing else does):

```bash
export ANTHROPIC_API_KEY="your-key-here"      # macOS / Linux
setx ANTHROPIC_API_KEY "your-key-here"        # Windows (reopen the terminal after)
```

## Commands

| Command | What it does |
|---|---|
| `python -m argus markets` | Lists open WTI and Brent markets: every range, Yes/No prices, ticker. Add `--rules` for the rules text. |
| `python -m argus run --series KXWTIW` | Prices this week's WTI range market, prints and saves the alert, logs every call. |
| `python -m argus run KXWTIW-26OCT2314` | Same, for one exact event. Series: `KXWTIW` (WTI weekly), `KXWTI` (WTI daily), `KXBRENTW`, `KXBRENTD`. |
| `... --headline "text"` | Adds a news headline (repeat for more). `--headlines-file news.txt` reads one per line. `--feeds` pulls recent posts from the creator list. |
| `... --anchor settle` | Uses the last official settle instead of the live crowd price (a purer model test). |
| `python -m argus events` | Scheduled events in the next 14 days and how much each one widens the odds. |
| `python -m argus news --headline "text"` | Scores headlines without running a market. |
| `python -m argus settle --auto` | Fills in results for every finished market from Kalshi's public results. |
| `python -m argus settle KXWTIW-26OCT2314 --value 98.73` | Enters a result by hand. |
| `python -m argus scorecard` | Total calls, win rate, profit/loss after fees, calibration, and model vs market. |
| `python -m argus scan` | Every open Kalshi market by category (skips 15-minute ones). `--category Politics` to narrow it. |
| `python -m argus feeds` | Latest posts from the trusted creator list (`--topic politics` for politics). |
| `python -m argus crossvenue` | Compares Kalshi with Polymarket on the 2026 midterms (see below). `--no-log` to only look. |
| `... run --min-price 0.6` / `--no-band` | Changes the price band (default: only flag sides costing 50c+, never under 10c). |
| `... run --pricing maker` | Prices calls as a resting buy at bid + 1c (maker fee) instead of paying the ask. |
| `python -m argus.buckets` | The longshot study: return by price bucket, taking the ask vs resting at bid + 1c (see below). |
| `python -m argus.buckets --filtered` | Re-scores the oil and commodity backtests through the band into `backtest_filtered_log.csv`. |

Alerts are saved in `alerts/`, scans in `scans/`, and calls in `prediction_log.csv`.

## How the odds are worked out (plain words)

1. **Today's price.** Kalshi's daily WTI market is a ladder of "Above $X" questions. Where that
   ladder crosses 50% is the crowd's best guess of the settle price, so that is the starting point.
   (Free live futures prices need a paid key; see SPEC.md.)
2. **How much oil moves.** From Kalshi's own past settlements Argus measures the typical daily move
   (about 2.4% a day in October 2026).
3. **Time left.** Counts the trading time until settlement. Weekends don't count.
4. **Scheduled events.** The weekly U.S. oil inventory report, rig count, OPEC+ meetings, Fed decisions
   and big economic data each add extra spread, as if extra trading days happened before the close.
   An OPEC+ meeting adds 1.5 days; the inventory report adds half a day.
5. **News.** Claude scores each headline: does it push supply or demand up or down, how big is it, and
   will it still matter at the close? Each headline moves the centre a little. The alert shows each
   headline's effect.
6. **The curve.** Together those give a curve of where the price could settle. Argus uses a curve with
   "fat tails" (a Student-t), because oil makes big jumps more often than a normal bell curve allows.
   The chance of each range is the share of the curve that falls inside it.

**Reading the alert.** *Gap* = Argus's chance for a side minus what that side costs, in points.
A 10+ point gap is marked **WORTH A LOOK**. *EV/ct* is the expected profit per contract after Kalshi's
fee. **CONSISTENCY** means the weekly range and the daily ladder (they settle on the same number)
disagree by 10+ points, so one of Kalshi's own prices is off, with no model needed.

**The price band and the Maker column.** Cheap contracts on Kalshi lose most of the money put
into them (research: contracts under 10c lost over 60%), so Argus only marks **WORTH A LOOK** when
the side you would buy costs 50c or more. A big gap on a cheaper side shows as e.g. **UNDER 50c**
instead. Nothing under 10c is ever flagged, whatever you set. The **Maker** column is the price if
you rest a buy order one cent above the best bid instead of paying the ask: cheaper, and on most
Kalshi series no fee, but it only fills when someone chooses to sell to you, which tends to be when
you are wrong. Details and our own numbers: `research/maker_longshot.md`.

**The honest limit.** Because today's price comes from the crowd, Argus mostly disagrees with Kalshi
about how wide the spread should be (events, fat tails, news), not where the centre is. Gaps of 10+ points
on liquid oil markets will be rare. The scorecard settles whether it has an edge: wait for 50+ settled
calls before trusting it.

## This week's WTI market: one full example

1. Open a terminal in the project folder: `cd argus`
2. See the markets: `python -m argus markets --series KXWTIW`
3. Check what's coming up: `python -m argus events`
4. Run it: `python -m argus run --series KXWTIW`
5. Optional, with news (needs the API key): `python -m argus run --series KXWTIW --headline "OPEC+ signals deeper cuts"`
6. After Friday 2:30 PM ET: `python -m argus settle --auto`
7. See how it did: `python -m argus scorecard`

## Kalshi vs Polymarket on the midterms (`crossvenue`)

The same election is often listed on both Kalshi and Polymarket. If the two crowds disagree a
lot about the same outcome, one of them may be wrong, or the two contracts pay on slightly
different things. `python -m argus crossvenue` checks every 2026 midterm race listed on both:
which party wins the Senate and the House, every Senate race and every governor race.

**What it does, in plain words.**
1. Reads both sites' public price pages. No login, no wallet, no key. Argus cannot trade on
   either site: the Polymarket part refuses any address that isn't a public price page.
2. Pairs up the outcomes. "Democrat wins Maine Senate" on Kalshi is paired with the same thing on
   Polymarket. When the two don't pay on exactly the same thing (for example Polymarket's
   "an independent wins" against Kalshi's "Dan Osborn wins"), the pair gets a `*` and the
   difference is written out under the table. Races where nothing pairs cleanly, like Alaska
   (Kalshi lists parties, Polymarket lists people), are listed as "Not compared" and left alone.
3. Works out the gap: Polymarket's chance for an outcome, minus what Kalshi charges for it,
   minus Kalshi's fee. A gap of 10 points or more is marked **WORTH A LOOK**, unless Polymarket's
   own buy and sell prices are more than 10 cents apart (then its price isn't reliable and the
   row says **WIDE PM SPREAD** instead).
4. Prints the table, saves it in `alerts/`, and writes every pair into `prediction_log.csv` as a
   call, so the scorecard later shows whether following the bigger gap would have paid.

**Read the rules notes before acting on a gap.** The two sites settle differently. Kalshi pays on
who is *sworn in* (Senate) or *inaugurated* (governor), and on who leads the chamber on 1 Feb 2027.
Polymarket pays on the race call by AP, Fox and NBC, and on the seat count. A death, a party
switch or an independent's caucus choice can make the two pay differently, which is a real reason
for a price gap and not a free lunch.

First run, 2026-10-22: 143 outcomes matched across 70 races; the biggest gap was under 1 point.
The two sites agree closely on every midterm race; a 10-point gap would be news.

## Every market on both sites (`python -m argus.crossvenue scan`)

The midterm check above, widened to everything Kalshi and Polymarket both list: sports, awards,
crypto prices, economic numbers, elections, entertainment.

**What it does, in plain words.**
1. Reads every open market on both sites (public pages, no login). Polymarket is large, so the
   first read takes about 5 minutes; a copy is kept for an hour and reused (`--pm-cache-min 0`
   forces a fresh read).
2. Pairs markets that ask the same question, and gives each pair a **confidence score** (0-100).
   It refuses a pair outright when the numbers differ ($80,000 vs $82,000), when one says "above"
   and the other "reaches", when one is "Round 3 leader" and the other "winner", when one is the
   US chart and the other global, or when a "what will he say" bundle lists different words.
3. Puts both sites' **rules side by side** and checks five things: who the source is, the date,
   what happens on a tie or a cancellation, whether one side has an "Other" outcome, and whether
   "80,000" counts as above. A dot means fine, `?` means one side doesn't say, `X` means they differ.
4. Ranks every pair by the gap after Kalshi's fee and shows how many dollars sit at the best price
   on each side (`K$`, `PM$`). `Lock` is the profit per $1 if you bought one side on Kalshi and the
   other side on Polymarket (Polymarket's own fee not included).

**Flags.** A gap of 10+ points is **WORTH A LOOK** only when the match confidence is 75+ AND no rule
check says `?` or `X`. Otherwise it says **CHECK MATCH** (the pairing itself is uncertain) or
**RULES DIFFER** (the two sites could pay differently on the same result, which can cost you both
legs). Polymarket books with more than 10c between buy and sell are not priced at all.

Saved: `scans/<date>_crossvenue.md` (the table) and `.csv` (every pair, with all five rule verdicts).
Flagged rows go in `prediction_log.csv`.
