# Kalshi Mentions markets: map, base rates, backtest

Written 2026-10-23 by the mentions thread. Code: `argus/mentions/` (`python -m argus.mentions scan | fedcheck | backtest | report`).
Log: `mentions_log.csv` (on the board). Every network call: `data/mentions/api_calls.csv`. Paper only; Argus never trades.

## 1. What the category is (measured 2026-10-22/23, public Kalshi API)

A Mentions market asks "Will X say Y during event Z?". Each event is a list of 10-50 word markets.

- **464 series** in the Mentions category; 4,165 settled events and 82,180 settled word markets since the first one
  closed on 2025-01-30 (live tier from 2026-08-23 plus Kalshi's historical tier). Full table: `data/mentions/series_map.csv`.
- **41.6% of word markets resolved Yes.**
- **Volume is concentrated.** About 1.26 billion contracts traded in total. By family:

| Family | Series | Settled events | Word markets | Contracts traded | Share |
|---|---:|---:|---:|---:|---:|
| Trump (speeches, rallies, interviews, monthly) | 34 | 510 | 13,071 | 456M | 36% |
| Sports broadcasts (NBA, NFL, MLB, World Cup, NCAA, fights) | 22 | 1,123 | 20,736 | 338M | 27% |
| Other politicians, people, companies | 161 | 1,331 | 30,628 | 220M | 17% |
| Earnings calls | 193 | 465 | 6,420 | 72M | 6% |
| TV news shows | 21 | 398 | 5,405 | 65M | 5% |
| TV / entertainment | 20 | 204 | 3,076 | 60M | 5% |
| White House press secretary | 4 | 89 | 1,765 | 28M | 2% |
| Fed Chair press conferences | 9 | 45 | 1,079 | 25M | 2% |

  Top series by volume: KXTRUMPMENTION 238M, KXWCMENTION 146M, KXTRUMPSAY 85M, KXTRUMPMENTIONB 80M, KXNBAMENTION 61M,
  KXNFLMENTION 60M, KXMAMDANIMENTION 29M, KXSECPRESSMENTION 27M, KXVANCEMENTION 25M, KXBERNIEMENTION 22M, KXFEDMENTION 21M.
- **Earnings series are one-company, one-call:** 193 series, 465 events, about 2.4 calls each. Too few repeats for a base rate.
- **Repeat words.** In the big recurring series 80-99% of word markets reuse a word listed at an earlier event of the same series.
  That is what makes a base rate from Kalshi's own past results possible.
- **Spreads.** 106 series had open markets at scan time (1,865 word markets); median Yes spread across those series 9c
  (Fed 4c, Trump 5c, press secretary 15c, Vance 20c). Thin books are common.
- **Fees.** Every Mentions series is `fee_type: quadratic`: taker fee 0.07 x P x (1-P) rounded up, **no maker fee**.

## 2. How words are matched (rules text, `rules_secondary`, read on every series)

- "The exact phrase/word, or a plural or possessive form of the phrase/word, must be used. Grammatical/tense inflections
  are otherwise not included." So *tariffs* and *tariff's* count for "Tariff"; *tariffed* and *said* (for "say") do not.
- Slashes are alternatives: "AI / Artificial Intelligence" is Yes on either.
- Plural nouns inside proper nouns and noun phrases count.
- Count strikes: "Tariff 5+ times", "Cut 3+" need that many uses.
- **Who must say it** differs by series: the Fed Chair only (not reporters) "at his post-FOMC introductory remarks and Q+A";
  the Press Secretary in an official capacity at a briefing (gaggles excluded); "any company representative (including the
  operator of the call)" for earnings calls, Q&A included; for sports "any official broadcast commentator" from first pitch /
  kickoff to the end. Hot-mic and inaudible private talk do not count; replayed or archival footage does not count.
- **What settles it:** video of the live broadcast first; "if a consensus by Kalshi employees cannot be reached using video,
  internal & external (official) transcripts" (earnings: "transcripts ... according to the news publications listed in the
  contract"). A cancelled or non-qualifying event resolves the "Event does not qualify" market Yes.
- Markets "close and expire early if the event occurs", but in practice many events close all their markets together after
  the event (Kalshi reviews the video), so a word can trade at 99c for hours before its market closes.

`argus/mentions/words.py` implements these rules: case ignored, whole words only ("tax" is not in "taxation", "AI" is not in
"said"), hyphens split words, only the last word of a phrase may take -s / -es / y->ies / 's / s'. Irregular plurals are not handled.

**Check against Kalshi's rulings:** official Fed transcripts vs Kalshi results on every Fed Chair word market with a posted
transcript: **496 of 498 agree (99.6%)**, 13 press conferences, Jan 2025 to Sep 2026 (`python -m argus.mentions fedcheck`).
The two misses are words Kalshi heard on video that are not in the edited transcript (Jun 2026 "Uncertainty", "Projection").
The first version missed five more, all count strikes ("Inflation 40+ times"); those are handled now.

## 3. Base rates (no look-ahead)

Two sources, each using only events before the call time (tests in `tests/test_mentions.py`):

1. **Official transcripts.** Federal Reserve FOMC press-conference transcripts (federalreserve.gov, the FINAL PDF), the Chair's
   words only. Base rate = share of the last 8 press conferences held before the call in which the Chair said the word,
   smoothed (k + 0.5)/(n + 1). On all 542 settled Fed word markets: **Brier 0.126**.
2. **Kalshi's own past results** for the same word in the same series, counting only markets whose result was published before
   the call, the target's own event excluded, at least 3 past markets, shrunk toward the series' Yes rate (2 pseudo-events).
   Covers 38,345 of 77,508 word markets (49%). **Brier 0.204** vs 0.250 for the overall Yes rate. Well calibrated:
   base 0-10% -> said 9%, 30-40% -> 37%, 50-60% -> 54%, 80-90% -> 82%, 90-100% -> 92%.

Dated negative findings (2026-10-22, re-test cost one fetch each):
- whitehouse.gov does not post press-briefing transcripts as a series: its site search for "press briefing leavitt" returned
  one (January 2025).
- Earnings-call transcripts are not on company IR sites as a rule and are not in SEC filings; the free copies are on news sites
  whose terms were not read. Not used.
- Sports broadcasts have no public transcript.

## 4. Backtest

Sample: the 20 most traded series with 8+ settled events, the **8 most recent settled events of each: 160 events**, 3,972 rows.
(The plan was 30 series x 12 events; Kalshi's historical tier needs one candle request per market and returned HTTP 429 on a
third of requests while sibling sessions shared the limit, about 30 good calls a minute, so it was cut.)

Timing: Kalshi does not publish the event start. The anchor is the earlier of `occurrence_datetime` and the first close of
any market in the event. Calls at **2 h and 24 h before the anchor**, using the Yes bid/ask of the last hourly candle that ended
at or before the call, only with a real book (bid within 5c of the ask). Check for in-event information: 99 of 573 markets
that resolved Yes had a Yes ask of 95c+ at the 2 h call, and 105 of 645 at 24 h; the same share at both times, so these are
near-certain words priced before the event, not words already said.

- **mentions-baserate**: price = base rate (Fed transcripts for KXFEDMENTION, else Kalshi history). Buy the side whose ask is
  10+ points under the base rate after the taker fee, side price 10-95c. All quoted markets with a base rate are logged.
- **mentions-fade**: "dramatic word" longshots, Yes ask 2-15c. Buy No as a maker at No bid + 1c (= 1 - Yes ask + 1c), never
  reaching the No ask, no fee. **Fill rule:** filled only if a later Yes trade printed strictly above our implied Yes sale price,
  in an hourly candle that began after the call and ended by the anchor; anything in the event itself never fills. Unfilled =
  void. Queue position and partial fills are ignored, which flatters the maker a little.
- **mentions-fade-taker**: the same picks, No bought at the No ask with the taker fee (reference).

| Strategy | Horizon | Bets | Events | Wins | P/L | ROI | Brier model / market | Board |
|---|---|---:|---:|---:|---:|---:|---|---|
| mentions-baserate | 2 h | 584 | 95 | 189 | -$1.25 | -0.7% | 0.189 / 0.141 | NO EDGE |
| mentions-baserate-24h | 24 h | 475 | 77 | 197 | +$17.82 | +10.4% | 0.189 / 0.161 | NO EDGE |
| mentions-fade (maker) | 2 h | 125 of 213 filled | 59 | 114 | -$0.64 | -0.6% | 0.079 / 0.077 | NO EDGE |
| mentions-fade-24h (maker) | 24 h | 83 of 87 filled | 38 | 81 | +$4.83 | +6.3% | 0.027 / 0.029 | PASS |
| mentions-fade-taker | 2 h | 497 | 99 | 467 | -$3.01 | -0.6% | 0.057 / 0.056 | NO EDGE |
| mentions-fade-taker-24h | 24 h | 261 | 65 | 249 | +$2.64 | +1.1% | 0.045 / 0.044 | NO EDGE |

(Events = events with a bet. The board counts settled events of all calls, so it shows 112 and 95 for the base-rate rows.)

By source, base rate at 2 h: Fed transcripts 97 bets, -0.4%, Brier 0.159 vs market 0.119; Kalshi history 487 bets, -0.7%,
Brier 0.197 vs 0.146. **The market prices words better than either base rate**, at both horizons. The +10% at 24 h comes
with a worse Brier than the market and turns into -0.7% two hours later on the same events: noise or a 24 h-only drift,
not a forecast edge. By price at 2 h: 10-30c -7%, 30-50c +10%, 50-70c -12%, 70-90c +13% (n=19).

The fade: at 24 h the maker version passes the board on 83 bets / 38 events, winning 81 (97.6%) at an average 93c, about
+5.8c a bet. That is one cell of six tried, chosen after seeing them, with a model that is mostly its own price (walk-forward
win rate of earlier fade bets, used only after 30 had settled) and a Brier margin of 0.002. At 2 h the same idea loses 0.6%,
and the taker version is break-even at both horizons. Treat it as a hypothesis for a forward test, not an edge.

Bets per week, scaled to the category (the top-20 series settled 14.9 events a week over the last 8 weeks; 44.6 a week across
all Mentions series): baserate 2 h about 54, baserate 24 h about 44, fade maker 2 h about 12, **fade maker 24 h about 8**,
fade taker 2 h about 46, fade taker 24 h about 24.

## 5. What would change the verdict

- Forward-test mentions-fade-24h for 20+ settled events on new data (no live command built yet).
- A larger replay: `python -m argus.mentions backtest --top 30 --max-events 12` (cached; about 4 h at the shared rate limit).
- Better base rates: transcripts weighted to recent events and to the current speaker (Fed Chair changed from Powell to Warsh in
  June 2026; the 8-transcript window mixes both until Feb 2027).
