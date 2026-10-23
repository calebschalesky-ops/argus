# Sources for how_to_find_edges.md

Gathered 2026-10-22, public pages only, no logins. "Read" says how much was actually seen this session:
**full** (page or paper text fetched), **abstract** (abstract or publisher listing), **summary** (only a search-engine
summary of the page), **memory** (a standard reference cited from prior knowledge, not re-opened today; check before
quoting). Vendors selling bots, signals or courses are marked **marketing**.

## Academic: prediction-market and betting efficiency

| Source | Link | Read | Used for |
|---|---|---|---|
| Bürgi, Deng, Whelan, "Makers and Takers: The Economics of the Kalshi Prediction Market" (2026) | https://www.karlwhelan.com/Papers/Kalshi.pdf | full (by the maker thread, research/maker_longshot.md) | maker vs taker, longshot losses on Kalshi |
| Diercks, Katz, Wright, "Kalshi and the Rise of Macro Markets", FEDS 2026-010 | see research/claims_check.md | full (claims-check thread) | Kalshi vs Bloomberg consensus |
| Moshrefi, "Prices, Probabilities, and Parlays: Systematic Bias in Sports Prediction Markets", arXiv 2607.14430 (Jul 2026) | https://arxiv.org/abs/2607.14430 | abstract | calibration by time to expiry; parlays above product of legs (idea 2.3, 2.6) |
| Page, Clemen, "Do Prediction Markets Produce Well-Calibrated Probability Forecasts?", Economic Journal 2013 | https://people.duke.edu/~clemen/bio/Published%20Papers/45.PredictionMarkets-Page&Clemen-EJ-2013.pdf | summary | bias grows with time to expiry (2.6) |
| Snowberg, Wolfers, "Explaining the Favorite-Longshot Bias: Is it Risk-Love or Misperceptions?", JPE 2010 / NBER w15923 | https://www.nber.org/papers/w15923 | abstract | favourite-longshot bias is misperception |
| Smith, Paton, Vaughan Williams, "Market Efficiency in Person-to-Person Betting", Economica 2006 | https://ideas.repec.org/a/bla/econom/v73y2006i292p673-689.html | abstract | exchanges show smaller biases than bookmakers |
| Levitt, "Why are Gambling Markets Organised So Differently from Financial Markets?", Economic Journal 2004 | https://www.nber.org/papers/w9422 | abstract | bookmakers shade prices toward bettor biases (2.8) |
| Saguillo et al., "Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets", arXiv 2508.03474 (2025) | https://arxiv.org/abs/2508.03474 | summary | ~$40M arbitrage on Polymarket, dependent markets (2.2) |
| Cheng, Yang, Zou, "Arbitrage Analysis in Polymarket NBA Markets", arXiv 2605.00864 (2026) | https://arxiv.org/pdf/2605.00864 | summary | single-market anomalies rare, median 3.6 s |
| Zitzewitz, "Limits to Arbitrage and Prediction Market Efficiency: the Case of PredictIt", NBER w35845 | https://www.nber.org/papers/w35845 | summary | bids summing over 110; position limits weaken arbitrage |
| Sethi, "Fee-Structure Distortions in Prediction Markets" (2016 blog, reposted) | https://www.nakedcapitalism.com/2016/04/rajiv-sethi-fee-structure-distortions-in-prediction-markets.html | summary | PredictIt sum-to-one violations |
| Rothschild, Sethi, "Trading Strategies and Market Microstructure" (Intrade 2012) | https://rajivsethi.substack.com/p/the-romney-whale-13-09-26 | summary | the "Romney whale"; manipulation fades |
| Berg, Nelson, Rietz, "Prediction Market Accuracy in the Long Run" (2008) | https://iemweb.biz.uiowa.edu/about-iem/research/prediction-market-accuracy-in-the-long-run/ | summary | IEM beats polls 74% of the time |
| Berg, Rietz, "The Iowa Electronic Markets: Stylized Facts and Open Issues" | https://iemweb.biz.uiowa.edu/?p=377 | summary | prices react quickly to information |
| Polymarket-v1 Database paper, arXiv 2606.04217 (2026) | https://arxiv.org/pdf/2606.04217 | summary | late convergence; low toxic flow (VPIN) = steadier prices (2.9) |
| "Resolution Is Not Settlement, Part I", arXiv 2609.15368 | https://arxiv.org/pdf/2609.15368 | summary (title only) | UMA adjudication |
| UMA dispute LLM study, arXiv 2604.15674 | https://arxiv.org/pdf/2604.15674 | summary | disputes hard to predict in advance |
| Galaxy Research on Polymarket resolution dispute (May 2026) | https://www.galaxy.com/insights/research/strategy-bitcoin-sale-polymarket-resolution-dispute-may-2026 | summary | top-10 wallets cast most UMA votes (citing WSJ) |
| Gómez-Cram, Guo, Kung, Jensen (Polymarket skill vs luck), via press | https://www.kucoin.com/news/flash/3-14-of-polymarket-traders-account-for-30-of-profits-67-of-users-bear-all-losses | summary (secondary) | ~3% of accounts skilled |
| Polymarket loss-share reports (Bloomberg, Sergeenkov, others), via press | https://www.financemagnates.com/fintech/100000-polymarket-wallets-lost-at-least-1000-bloomberg-analysis-shows/ ; https://www.casino.org/news/84-of-polymarket-traders-arent-profitable/ | summary (secondary) | 67-84% of traders lose |
| Croxson, Reade, "Information and Efficiency: Goal Arrival in Soccer Betting", Economic Journal 2014 | https://doi.org/10.1111/ecoj.12033 | memory | exchange prices update within seconds of goals |
| Kaunitz, Zhong, Kreiner, "Beating the bookies with their own numbers" (2017) | https://arxiv.org/abs/1710.02824 ; press: https://www.technologyreview.com/2017/10/19/67760/the-secret-betting-strategy-that-beats-online-bookmakers/ | summary (press) | consensus-outlier method; accounts limited |
| Underreaction claim (unnamed Polymarket study, ~64% initial move, ~80 min) | https://en.cryptonomist.ch/2026/08/10/polymarket-price-repricing-delay/ | full (article); **study itself not identified** | idea 2.7, labelled unverified |

## Validation and sizing

| Source | Link | Read | Used for |
|---|---|---|---|
| Bailey, López de Prado, "The Deflated Sharpe Ratio", JPM 2014 | https://papers.ssrn.com/abstract=2460551 | abstract | correcting for number of trials |
| Bailey, Borwein, López de Prado, Zhu, "The Probability of Backtest Overfitting", J. Computational Finance 2017 | https://papers.ssrn.com/abstract=2326253 | abstract | CSCV / PBO for the rule miner |
| `pbo` R package vignette | https://archive.linux.duke.edu/cran/web/packages/pbo/vignettes/pbo.html | summary | implementation reference |
| Harvey, Liu, Zhu, "...and the Cross-Section of Expected Returns", RFS 2016 | https://www.nber.org/papers/w20592 | abstract | t > 3 hurdle |
| White, "A Reality Check for Data Snooping", Econometrica 2000 | https://doi.org/10.1111/1468-0262.00152 | memory | best-of-many bootstrap |
| Hansen, "A Test for Superior Predictive Ability", JBES 2005 | https://doi.org/10.1198/073500105000000063 | memory | SPA, improved Reality Check |
| Benjamini, Hochberg, "Controlling the False Discovery Rate", JRSS-B 1995 | https://doi.org/10.1111/j.2517-6161.1995.tb02031.x | memory | FDR control over the registry |
| Wald, sequential probability ratio test (1945) | https://doi.org/10.1214/aoms/1177731118 | memory | stopping rule for forward tests |
| MacLean, Thorp, Ziemba, "Good and bad properties of the Kelly criterion" | https://www.stat.berkeley.edu/~aldous/157/Papers/Good_Bad_Kelly.pdf | summary | Kelly risk; errors in means dominate |
| Whelan, "The Truth about Closing Line Value" | https://www.karlwhelan.com/?p=2595 | full | CLV not enough; 3,670 NBA games |
| Data Golf, "Analyzing betting odds" | https://datagolf.com/analyzing-betting-odds | summary | Buchdahl-style book comparison |
| Browne et al., "Delusions of expertise: the high standard of proof needed to demonstrate skill at horserace handicapping", J. Gambling Studies 2015 | https://www.springermedizin.de/delusions-of-expertise-the-high-standard-of-proof-needed-to-demo/8917822 | summary | sample sizes to show skill |
| "A view from the Pinnacle" (Buchdahl backtest discussed) | https://networked.substack.com/p/a-view-from-the-pinnacle | summary | soft-book vs Pinnacle 3.6% realised vs 3.8% expected |

## Platform mechanics

| Source | Link | Read | Used for |
|---|---|---|---|
| Kalshi API docs, "Fee rounding" | https://docs.kalshi.com/getting_started/fee_rounding | full | per-order accumulator; six-decimal fees (4.1, 2.1) |
| Allium docs, Kalshi series fee changes | https://docs.allium.so/historical-data/predictions/kalshi/series-fee-changes | full | fee formula, multipliers |
| Kalshi fee schedule PDF | https://kalshi.com/docs/kalshi-fee-schedule.pdf | **not read** (HTTP 429, 2026-10-22) | — |
| Kalshi help: Liquidity Incentive Program | https://help.kalshi.com/incentive-programs/liquidity-incentive-program | summary | scoring, target sizes |
| Polymarket docs: liquidity rewards | https://docs.polymarket.com/developers/market-makers/liquidity-rewards | summary | daily payout, $1 minimum |
| Kalshi first institutional market maker (SIG), Business Wire 2024-04-03 | https://www.businesswire.com/news/home/20240403664852/en/Kalshi-Onboards-Its-First-Dedicated-Institutional-Market-Maker | summary | risk transfer |
| InGame, Kalshi in-house trading arm "not profitable" | https://www.ingame.com/kalshi-in-house-trading-arm-not-profitable/ | summary | market-making difficulty |
| BeInCrypto on Polymarket arbitrage bots | https://beincrypto.com/arbitrage-bots-polymarket-humans/ | summary | bot dominance |
| MEXC news on 15-minute bot and dynamic taker fee | https://www.mexc.com/news/426113 | summary (secondary) | latency arb closed by fees |
| Third-party Kalshi API guides (rate limits, websocket auth) | https://www.quantvps.com/blog/kalshi-order-book-api-endpoints-explained ; https://zuplo.com/blog/2025/04/02/kalshi-api | summary; **marketing-adjacent** | websocket needs a key |

## Practitioners

| Source | Link | Read | Used for |
|---|---|---|---|
| ChinaTalk, "Prediction Markets 101" (Domer interview, Jul 2025) | https://www.chinatalk.media/p/prediction-markets-101 | summary (paywalled) | niche specialisation |
| MetaMask, "Advanced prediction market trading strategies" (Domer profile) | https://metamask.io/news/advanced-prediction-market-trading-strategies | summary; **sponsored** | record claims |
| VSiN on Rufus Peabody's prop approach | https://vsin.com/the-vsin-daily/inside-the-mind-of-sharpest-of-the-super-bowl-prop-sharps/ | summary | waits for markets to settle, targets props |
| InGame on Peabody taking ratings private | https://www.ingame.com/?p=53210 | summary | publishing moves markets |
| Wharton Moneyball podcast, prediction markets and sports betting analytics | https://knowledge.wharton.upenn.edu/podcast/moneyball/prediction-markets-and-the-future-of-sports-betting-analytics/ | summary (listing) | — |
| Kalshi young-trader story (MarketWatch, via iTiger) | https://www.itiger.com/hant/news/2537923561 | summary (secondary) | ~$150k market-maker claim |
| NPR on the Maduro Polymarket trade (Jan 2026) | https://www.kpbs.org/news/science-technology/2026/01/05/a-400-000-profit-on-maduros-capture-raises-insider-trading-questions-on-polymarket | summary | insider-looking trades (excluded edge) |
| Kalshi earnings-mention vendor guide | https://pro.stockalarm.io/blog/kalshi-earnings-call-markets-ai-analysis | summary; **marketing** | not relied on |
| SimpleFunctions cross-venue posts | https://simplefunctions.dev/technicals/cross-venue-edge-detection-kalshi-polymarket | summary; **vendor** | gaps 2-5c, rule mismatch the main cause |
