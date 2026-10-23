# Argus: a strategy-testing engine for prediction markets

[![tests](https://github.com/calebschalesky-ops/argus/actions/workflows/tests.yml/badge.svg)](https://github.com/calebschalesky-ops/argus/actions/workflows/tests.yml) ![license](https://img.shields.io/badge/license-MIT-blue)

Argus reads public prediction-market data from Kalshi and Polymarket. It turns trading ideas into backtests and live paper tests, then judges every strategy against one pass/fail gate. It is **read-only by design**: it can't place an order and never asks for an account key.

**Python · ~20,000 lines · 462 tests passing · 15+ strategy studies · built in 2 days with parallel Claude Code agents**

## Why it exists

Prediction-market social media is full of "free money" strategies: cross-app arbitrage, buying late favourites, fading longshots, copying top wallets. Argus checks whether any of them would have made money after fees, on real settled markets, before a cent goes in.

## The gate (`argus/board.py`)

Every strategy writes its calls to a `*_log.csv` file with the same columns. The leaderboard reads all of them and gives each strategy one verdict:

| Verdict | Rule |
|---|---|
| TOO EARLY | fewer than 20 settled events |
| NO EDGE | the model's Brier score is no better than the market price's |
| BETTER ODDS, NO PROFIT YET | lower Brier than the market, but no profit after fees |
| **PASS** | lower Brier than the market **and** paper profit after Kalshi's fees |

So a strategy has to predict better than the crowd *and* make money. Making money by luck isn't enough.

## What the studies found

| Study | Sample | Result after fees | Verdict |
|---|---|---|---|
| Oil price-range model (Student-t, event calendar, Claude news scoring) | 163 events | about −40% on paper bets | NO EDGE |
| 15-minute crypto: lag vs fair value (private repo only) | 6,385 markets, 1,456 bets | −$69.30, −9.0% ROI | NO EDGE |
| 15-minute crypto: late favourites | 474 bets | −1.0% ROI | NO EDGE |
| Fade the longshot / be the maker (oil, commodities) | 56–807 orders per variant | −23% to +1% | NO EDGE |
| Known before close: observed temperature | 2,137 bets, 526 events | −7.8% ROI | NO EDGE |
| Known before close: observed rain | 971 bets | +9.1% ROI, but Brier worse than market | fails on odds |
| "Will X say Y" mention markets | up to 584 bets | −0.7% to +10.4%, Brier worse | NO EDGE |
| Newly listed markets | 37–255 per variant | −6.3% to −2.3% | NO EDGE |
| Pricing off a deeper market (crypto options) | 78–107 bets | −16.5% to +0.5% | NO EDGE |
| Election-night count vs price | 7 races, 9 events | — | NO EDGE (too few nights) |

Write-ups with method, tables and "what would change the verdict" are in [`research/`](research/).

**The takeaway.** In liquid markets the crowd price is hard to beat once fees are counted. The gaps that look like free money are usually two contracts that pay out on slightly different rules (see `research/rule_quirks.md` and the cross-venue section below). Killing a strategy cheaply on paper is the result the system is built to produce.

## How it's built

- **Data:** Kalshi public REST endpoints (GET only), Polymarket public Gamma/CLOB price pages, NOAA/NWS weather, AAA gas, free price feeds. Every outbound call is logged.
- **Engines:** each strategy is its own package under `argus/` (`commodities`, `weather`, `mentions`, `smartmoney`, `lab`, ...), and all of them write to the shared prediction-log format.
- **Lab:** `argus/lab` keeps a local data lake. It mines candidate rules, runs a holdout split, and sends survivors to a live paper pipeline.
- **Claude:** news headlines are scored for direction, size and how long they will last, through the Anthropic API.
- **Tests:** `python -m pytest -q`, 462 tests.

## How I built it

I wrote the spec and the hard rules: read-only, public data only, never delete a log row, one gate for everything. Then I ran the build as parallel Claude Code sessions, one per study: oil engine, cross-venue, smart money, weather, commodities, 15-minute crypto, mentions, new listings and more. Each session owned its files, wrote its findings to `research/`, and had to put its strategy through the same gate. `SPEC.md` is the contract they all built against.

## Quick start

```bash
pip install -r requirements.txt
python -m pytest -q          # 462 tests
python -m argus markets      # open WTI and Brent markets
python -m argus scorecard    # every logged call, scored
```

Every command, the odds model in plain words and the cross-venue scanner are in [`docs/USAGE.md`](docs/USAGE.md).

The `*_log.csv` files at the root are the strategies' real logged calls, so the verdicts above can be re-checked.

## About this repo

This is a cleaned public snapshot of a private working repo (38 commits, 2026-10-22 to 2026-10-23). Local paths, request logs and third-party wallet data were removed before publishing. The 15-minute crypto lag study (first row of the results table) lives only in the private repo, code and write-up both.

MIT licensed.
