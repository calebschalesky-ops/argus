# Politics sources for Argus

Read-only source list for the politics engine: who to read when a political market moves, and which official feeds settle it. Built 2026-10-22, nine days before the 2026-11-03 midterms. The machine-readable version is [sources.csv](sources.csv).

**Counts.** 112 screened. 70 kept: 40 analysts (A 28, B 12, C 0) and 30 official feeds (A 28, B 2). 59 rows have a feed URL; every one of them was fetched on 2026-10-22 and returned items. The rest are `manual`. 42 candidates were screened out (list at the bottom).

**How ratings work here.**
- **A**: track record or institutional process you can check, and cites primary data. Safe to move the baseline on.
- **B**: reliable on facts but has a lean, a short record, or mixes opinion in. Move the baseline only when an A source or the primary record agrees.
- **C**: context only.

**Rules for the engine.**
1. Settle on the source the Kalshi market rules name. For elections that is usually AP or the state; for mentions markets it is usually the live broadcast. Analysts change probabilities. They never settle a market.
2. Score a B source's claim with less weight unless an A source or an official feed confirms it.
3. Pair a lean with its opposite. Ruffini/Soltis Anderson/Dispatch/Fox (center-right) go next to Morris/Downballot/Bolts/Law Dork (center-left).
4. For polls, weight by the pollster's Silver Bulletin grade (Jan 2026 update), not by who posted it.
5. Bluesky feeds use the public `bsky.app/profile/<handle>/rss` endpoint, which needs no login. X has no free read API, so every X-only row is `manual`.


---

# A. Analysts and reporters


## Analysts and reporters, rated A

### Silver Bulletin (Nate Silver) (pol-a01), rating A

- **Where:** Substack, @NateSilver538, https://www.natesilver.net
- **Feed:** `https://www.natesilver.net/feed` (substack_rss)
- **Focus:** forecasts; polling averages; pollster ratings
- **Why trust it:** Built the 538 models (2008-2023); publishes probabilistic forecasts, generic-ballot/approval averages and the annual pollster ratings (Jan 2026 update covers 500+ pollsters); methodology posts public
- **Red flags:** Adviser to Polymarket (prediction-market conflict; disclose when he comments on markets); many posts paywalled; opinion columns mixed in with model output
- **Checkable calls:** yes. **Cadence:** several/week
- **How to check them:** Read the model/average numbers, not the commentary. Pollster grades are an input to Argus poll weighting.

### Strength In Numbers (G. Elliott Morris) (pol-a02), rating A

- **Where:** Substack, gelliottmorris.com, https://www.gelliottmorris.com
- **Feed:** `https://www.gelliottmorris.com/feed` (substack_rss)
- **Focus:** forecasts; polling averages; public opinion
- **Why trust it:** Ran 538 2024 model and Economist 2020 model; publishes his own averages and data with code/method notes; shows his numbers
- **Red flags:** Commentary leans left; forecast and opinion sit in the same newsletter
- **Checkable calls:** yes. **Cadence:** several/week
- **How to check them:** Use the averages/tables; treat framing paragraphs as opinion. Bluesky feed (row pol-a03) is faster for chart drops.

### Cook Political Report (Amy Walter, Dave Wasserman, Jessica Taylor) (pol-a04), rating A

- **Where:** Web, @CookPolitical, https://www.cookpolitical.com
- **Feed:** `https://www.cookpolitical.com/rss.xml` (rss)
- **Focus:** House/Senate/governor race ratings; PVI
- **Why trust it:** Nonpartisan handicapper since 1984; Split Ticket review (2016-2022) found Solid ratings almost never wrong; publishes dated rating changes
- **Red flags:** Ratings are categorical not probabilities; full ratings paywalled
- **Checkable calls:** yes. **Cadence:** weekly; daily near election
- **How to check them:** Treat a rating shift (e.g. Lean->Toss-up) as the event; map categories to probabilities in Argus, do not read text.

### Sabato's Crystal Ball (Kyle Kondik, J. Miles Coleman) (pol-a06), rating A

- **Where:** Web, @LarrySabato / @kkondik, https://centerforpolitics.org/crystalball/
- **Feed:** `https://centerforpolitics.org/crystalball/feed/` (rss)
- **Focus:** race ratings; electoral history
- **Why trust it:** UVA Center for Politics; free; Split Ticket review found its only wrong Safe call 2016-2022 was the 2022 AK-AL special; publishes every rating change
- **Red flags:** Categorical ratings; a few posts are guest opinion
- **Checkable calls:** yes. **Cadence:** weekly (Thu)
- **How to check them:** Rating changes table in each issue; parse rating moves.

### Inside Elections (Nathan Gonzales, Jacob Rubashkin) (pol-a08), rating A

- **Where:** Web, @InsideElections, https://insideelections.com
- **Feed:** `https://insideelections.com/feed` (rss)
- **Focus:** race ratings; candidate fundraising
- **Why trust it:** Nonpartisan newsletter (formerly Rothenberg); rating changes dated and archived on 270toWin trackers
- **Red flags:** Ratings are categorical; some content paywalled
- **Checkable calls:** yes. **Cadence:** weekly
- **How to check them:** Parse rating changes; cross-check with Cook and Crystal Ball for consensus moves.

### Pew Research Center (pol-a13), rating A

- **Where:** Web, @pewresearch, https://www.pewresearch.org
- **Feed:** `https://www.pewresearch.org/feed/` (rss)
- **Focus:** public opinion; demographics; validated voters
- **Why trust it:** Nonpartisan fact tank; publishes full toplines, methods and weighting; AAPOR Transparency Initiative member
- **Red flags:** Not election-horserace; slower cadence
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Use toplines and the methodology PDF; Bluesky @pewresearch.org also works (fetched OK).

### Gallup (pol-a14), rating A

- **Where:** Web, @Gallup, https://news.gallup.com
- **Feed:** *manual* (manual)
- **Focus:** presidential approval; party ID; economic confidence
- **Why trust it:** Longest-running approval series; full methods published
- **Red flags:** Stopped presidential horserace polling after 2012; no working RSS found today (rss.aspx 404)
- **Checkable calls:** yes. **Cadence:** monthly
- **How to check them:** Check the monthly approval release by hand; settles some approval markets.

### Marquette Law School Poll (pol-a15), rating A

- **Where:** Web, @MULawPoll, https://law.marquette.edu/poll/
- **Feed:** `https://law.marquette.edu/poll/feed/` (rss)
- **Focus:** Wisconsin and national polls; SCOTUS approval
- **Why trust it:** Ranked No. 2 in Silver Bulletin pollster ratings Jan 2026 (top 10 every year since 2019); full crosstabs
- **Red flags:** Few releases a year
- **Checkable calls:** yes. **Cadence:** monthly
- **How to check them:** Read toplines PDF on release.

### YouGov (US) (pol-a16), rating A

- **Where:** Web, @YouGovAmerica, https://today.yougov.com
- **Feed:** `https://today.yougov.com/rss` (rss)
- **Focus:** polls (Economist/YouGov weekly, CBS/YouGov)
- **Why trust it:** High-volume online panel with published crosstabs; well rated in 538/Silver Bulletin histories
- **Red flags:** Feed mixes politics with lifestyle polls (filter by topic); online opt-in panel
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Filter feed items to politics; read crosstab PDF.

### Decision Desk HQ (pol-a22), rating A

- **Where:** Bluesky/X/Web, @DecisionDeskHQ, https://decisiondeskhq.com
- **Feed:** `https://bsky.app/profile/decisiondeskhq.bsky.social/rss` (bluesky_rss)
- **Focus:** race calls; results; polling average
- **Why trust it:** Results/calls provider for many outlets; claims 100% call accuracy in 2024 and first to call the presidency at 1:22am (self-reported); runs public polling averages
- **Red flags:** Accuracy claims are self-reported; X account sometimes posts breaking political news beyond results
- **Checkable calls:** yes. **Cadence:** daily; bursty on election nights
- **How to check them:** Use for early call signal; settle on AP or the state.

### Ballotpedia (pol-a23), rating A

- **Where:** Bluesky/Web, @ballotpedia.org, https://ballotpedia.org
- **Feed:** `https://bsky.app/profile/ballotpedia.org/rss` (bluesky_rss)
- **Focus:** candidate lists; ballot measures; election calendars
- **Why trust it:** Nonpartisan encyclopedia with primary-source citations; tracks every candidate/measure. news.ballotpedia.org/feed returned no items today
- **Red flags:** Wiki pages can lag
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Use to resolve "who is on the ballot" and dates; cite the linked primary source.

### POLITICO Europe (incl. Poll of Polls) (pol-a27), rating A

- **Where:** Web, @POLITICOEurope, https://www.politico.eu
- **Feed:** `https://www.politico.eu/feed/` (rss)
- **Focus:** EU and European national politics
- **Why trust it:** Large Brussels newsroom; Poll of Polls is a documented polling average
- **Red flags:** Poll of Polls has no feed of its own; read by hand
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Feed for news; check pollofpolls.eu pages by hand.

### Associated Press (pol-a28), rating A

- **Where:** Bluesky/YouTube/Web, @apnews.com, https://apnews.com
- **Feed:** `https://bsky.app/profile/apnews.com/rss` (bluesky_rss)
- **Focus:** wire news; election race calls
- **Why trust it:** AP race calls are the default settlement source for most US election markets; two-person rule plus decision editor before any call
- **Red flags:** apnews.com RSS returned 403 today; use Bluesky feed or YouTube (UC52X5wxOL_s5yw0dQk7NtgA, fetched OK)
- **Checkable calls:** yes. **Cadence:** continuous
- **How to check them:** For elections, AP call = settle signal (check market rules for the named source).

### Reuters (pol-a29), rating A

- **Where:** Bluesky, @reuters.com, https://www.reuters.com
- **Feed:** `https://bsky.app/profile/reuters.com/rss` (bluesky_rss)
- **Focus:** wire news; foreign leaders and elections
- **Why trust it:** Global wire with corrections policy; strongest for foreign governments and elections
- **Red flags:** Reuters public RSS discontinued (404 today); Bluesky feed works
- **Checkable calls:** yes. **Cadence:** continuous
- **How to check them:** Use for foreign-event confirmation.

### Nate Cohn (NYT / NYT-Siena) (pol-a30), rating A

- **Where:** X/Bluesky, @Nate_Cohn, https://www.nytimes.com/by/nate-cohn
- **Feed:** `https://bsky.app/profile/natecohn.bsky.social/rss` (bluesky_rss)
- **Focus:** polling; NYT/Siena polls; election-night needle
- **Why trust it:** Chief political analyst; NYT/Siena among top-rated pollsters (Silver Bulletin Jan 2026 top 3); runs the results needle
- **Red flags:** Bluesky account barely used (1 post); main output is NYT (paywall) and X
- **Checkable calls:** yes. **Cadence:** weekly
- **How to check them:** Watch for NYT/Siena releases; the NYT politics feed (pol-a35) picks up his articles.

### Punchbowl News (pol-a31), rating A

- **Where:** Web, @PunchbowlNews, https://punchbowl.news
- **Feed:** `https://punchbowl.news/feed/` (rss)
- **Focus:** Congress: votes, leadership, schedule
- **Why trust it:** Founded by former Politico Playbook/Hill reporters (Sherman, Palmer, Zanona); deep leadership sourcing; vote counts usually right
- **Red flags:** Most content is paid; public feed carries headlines
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Use for "will X pass by date" timing; settle on Congress.gov/clerk votes.

### Roll Call (pol-a32), rating A

- **Where:** Web, @rollcall, https://rollcall.com
- **Feed:** `https://rollcall.com/feed/` (rss)
- **Focus:** Congress; legislation; appropriations
- **Why trust it:** Capitol Hill paper of record since 1955; also owns Factba.se (transcripts of Trump remarks)
- **Red flags:** none noted
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Legislative schedule; Factba.se for mentions markets (manual, row pol-o27).

### POLITICO (US) (pol-a33), rating A

- **Where:** Web, @politico, https://www.politico.com
- **Feed:** `https://rss.politico.com/politics-news.xml` (rss)
- **Focus:** Congress; White House; campaigns
- **Why trust it:** Large DC newsroom; Playbook is the scoop-of-record for many DC events; corrections published
- **Red flags:** Scoops on anonymous sourcing; headline speed > certainty
- **Checkable calls:** yes. **Cadence:** continuous
- **How to check them:** Treat a scoop as probability shift, not settlement.

### Kyle Cheney and Josh Gerstein (POLITICO legal) (pol-a34), rating A

- **Where:** Bluesky, @kyledcheney / @joshgerstein, https://bsky.app/profile/kyledcheney.bsky.social
- **Feed:** `https://bsky.app/profile/kyledcheney.bsky.social/rss` (bluesky_rss)
- **Focus:** federal court rulings; Jan. 6 and executive-power cases
- **Why trust it:** Post court filings and rulings within minutes with links to PACER/CourtListener docs; Gerstein feed also works (joshgerstein.bsky.social)
- **Red flags:** none noted
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Follow the linked docket entry; the ruling text settles the market.

### New York Times Politics (pol-a35), rating A

- **Where:** Web, @nytpolitics, https://www.nytimes.com/section/politics
- **Feed:** `https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml` (rss)
- **Focus:** national politics
- **Why trust it:** Large politics desk; published corrections; houses NYT/Siena polling
- **Red flags:** Paywall; opinion separate but cross-linked
- **Checkable calls:** yes. **Cadence:** continuous
- **How to check them:** Headlines are enough for signal.

### Wall Street Journal Politics (pol-a36), rating A

- **Where:** Web, @WSJPolitics, https://www.wsj.com/politics
- **Feed:** `https://feeds.content.dowjones.io/public/rss/socialpoliticsfeed` (rss)
- **Focus:** national politics; policy
- **Why trust it:** Straight-news desk with strong policy/economics sourcing; editorial page (right) is separate
- **Red flags:** Paywall; keep editorial page out
- **Checkable calls:** yes. **Cadence:** continuous
- **How to check them:** Headline signal; check for opinion section tag.

### Sahil Kapur (NBC News) (pol-a37), rating A

- **Where:** Bluesky, @sahilkapur.bsky.social, https://bsky.app/profile/sahilkapur.bsky.social
- **Feed:** `https://bsky.app/profile/sahilkapur.bsky.social/rss` (bluesky_rss)
- **Focus:** Congress; votes; legislative strategy
- **Why trust it:** Senior NBC Capitol Hill reporter; posts on-record quotes and vote whip counts
- **Red flags:** none noted
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Quotes from senators are checkable against C-SPAN/Congressional Record.

### SCOTUSblog (Amy Howe et al.) (pol-a38), rating A

- **Where:** Web/Bluesky/YouTube, @scotusblog.com, https://www.scotusblog.com
- **Feed:** `https://www.scotusblog.com/feed/` (rss)
- **Focus:** Supreme Court: cases, orders, opinions
- **Why trust it:** Reporting since 2002; links every filing and opinion; now owned by The Dispatch with editorial independence. Bluesky (scotusblog.com) and YouTube (UCTCX7WBcfT-jSAlyvEiBq-A) feeds also fetched OK
- **Red flags:** none noted
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Opinion-day live coverage; settle on supremecourt.gov opinion text.

### One First (Steve Vladeck) (pol-a39), rating A

- **Where:** Substack/Bluesky, @stevevladeck, https://www.stevevladeck.com
- **Feed:** `https://www.stevevladeck.com/feed` (substack_rss)
- **Focus:** Supreme Court; emergency docket
- **Why trust it:** Georgetown law professor, CNN analyst; tracks shadow-docket applications with docket numbers
- **Red flags:** Critical of the current Court majority; legal analysis is sound, predictions are his own view
- **Checkable calls:** yes. **Cadence:** weekly (Mon) + extras
- **How to check them:** Use the docket tracker; Bluesky stevevladeck.bsky.social fetched OK.

### Election Law Blog (Rick Hasen) (pol-a41), rating A

- **Where:** Web/Bluesky, @rickhasen, https://electionlawblog.org
- **Feed:** `https://electionlawblog.org/?feed=rss2` (rss)
- **Focus:** election litigation; voting rules; redistricting
- **Why trust it:** UCLA law professor; group blog with links to every filing; long record since 2003
- **Red flags:** Hasen writes with alarm about democracy risks; contributors span views
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Link-through to the filing; Bluesky rickhasen.bsky.social fetched OK.

### Lawfare (pol-a42), rating A

- **Where:** Web/Bluesky, @lawfaremedia.org, https://www.lawfaremedia.org
- **Feed:** `https://www.lawfaremedia.org/feeds/articles` (rss)
- **Focus:** national security law; executive power; trackers
- **Why trust it:** Brookings-affiliated origin; publishes litigation trackers with document links
- **Red flags:** Contributors lean institutionalist/critical of the administration
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Use trackers and docs.

### The Economist (US) (pol-a52), rating A

- **Where:** Web, @TheEconomist, https://www.economist.com/united-states
- **Feed:** `https://www.economist.com/united-states/rss.xml` (rss)
- **Focus:** US politics; forecast models; world leaders
- **Why trust it:** Publishes documented forecast models and polling trackers; global coverage of foreign elections
- **Red flags:** Paywall; editorial voice is house view
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Models/trackers by hand.

### BBC News World (pol-a54), rating A

- **Where:** Web, @BBCWorld, https://www.bbc.com/news/world
- **Feed:** `https://feeds.bbci.co.uk/news/world/rss.xml` (rss)
- **Focus:** foreign leaders; foreign elections
- **Why trust it:** Global newsroom with correspondents in most capitals; corrections published
- **Red flags:** UK-centric framing
- **Checkable calls:** yes. **Cadence:** continuous
- **How to check them:** Foreign-leader event confirmation.


## Analysts and reporters, rated B

### Split Ticket (Lakshya Jain, Armin Thomas, Max McCall) (pol-a09), rating B

- **Where:** Web, @SplitTicket_, https://split-ticket.org
- **Feed:** `https://split-ticket.org/feed/` (rss)
- **Focus:** forecast model; race ratings; candidate quality (WAR)
- **Why trust it:** Publishes probabilistic models and a public review of handicapper accuracy; shows data and methods
- **Red flags:** Young shop (since 2021), short track record; some founders show Dem-leaning commentary
- **Checkable calls:** yes. **Cadence:** weekly
- **How to check them:** Use model probabilities as one input; weight below Cook/Sabato consensus until track record grows.

### Emerson College Polling (pol-a17), rating B

- **Where:** Web, @EmersonPolling, https://emersoncollegepolling.com
- **Feed:** `https://emersoncollegepolling.com/feed/` (rss)
- **Focus:** state and national polls
- **Why trust it:** Frequent state polls with released crosstabs; rated in the upper-middle tier
- **Red flags:** Mixed-mode/IVR; occasional sponsor-driven polls (check sponsor)
- **Checkable calls:** yes. **Cadence:** several/week
- **How to check them:** Note sponsor line; weight by pollster rating.

### AtlasIntel (pol-a18), rating B

- **Where:** Web/X, @atlas_intel, https://atlasintel.org
- **Feed:** *manual* (manual)
- **Focus:** US and Latin American polls
- **Why trust it:** Very accurate in 2020 and 2024 US presidential (per its own releases and the Silver Bulletin 2025 A+ grade); also polls Brazil, Argentina, Mexico
- **Red flags:** Opaque online recruitment method; company does a lot of self-promotion; X posts mix in editorial comment
- **Checkable calls:** yes. **Cadence:** bursty
- **How to check them:** Use the PDF releases, not the X commentary.

### Patrick Ruffini (The Intersection) (pol-a19), rating B

- **Where:** Substack/Bluesky, @patrickruffini, https://www.patrickruffini.com
- **Feed:** `https://www.patrickruffini.com/feed` (substack_rss)
- **Focus:** coalitions; voter data; polling
- **Why trust it:** Republican pollster (Echelon Insights cofounder); data-heavy, cites crosstabs and voter files; Bluesky feed also works (patrickruffini.bsky.social)
- **Red flags:** Works for GOP clients; read as informed center-right analysis
- **Checkable calls:** yes. **Cadence:** weekly
- **How to check them:** Pair with a center-left analyst on the same question.

### RealClearPolling averages (pol-a21), rating B

- **Where:** Web, @RCPolitics, https://www.realclearpolling.com
- **Feed:** `https://www.realclearpolitics.com/index.xml` (rss)
- **Focus:** polling averages; betting-odds aggregation
- **Why trust it:** Simple unweighted averages, long archive; often cited as the market's baseline
- **Red flags:** Editorial side leans right; includes low-rated GOP-leaning pollsters; homepage feed is mostly opinion links, so the averages must be read by hand
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Read the average tables by hand; compare with Silver Bulletin average to measure house-effect gap.

### The Downballot (pol-a24), rating B

- **Where:** Substack/Bluesky/YouTube, @the-downballot.com, https://www.the-downballot.com
- **Feed:** `https://www.the-downballot.com/feed` (substack_rss)
- **Focus:** special elections; down-ballot results; redistricting
- **Why trust it:** Daily Kos Elections alumni; known for special-election overperformance tracking and precinct data
- **Red flags:** Openly Democratic-leaning
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Use the data (special-election swing tables); discount the framing. Bluesky and YouTube feeds also work (the-downballot.com; channel UCaagYal_KYnKEV31FMMOafQ).

### Europe Elects (pol-a26), rating B

- **Where:** Bluesky, @europeelects.bsky.social, https://europeelects.eu
- **Feed:** `https://bsky.app/profile/europeelects.bsky.social/rss` (bluesky_rss)
- **Focus:** European/world polls and results
- **Why trust it:** Aggregates nearly every European poll with pollster and fieldwork dates; site feed returned no items today
- **Red flags:** Posts every poll, including weak ones; occasional errors
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Check pollster name and fieldwork date; weight by local pollster quality.

### Law Dork (Chris Geidner) (pol-a40), rating B

- **Where:** Substack/Bluesky, @chrisgeidner, https://www.lawdork.com
- **Feed:** `https://www.lawdork.com/feed` (substack_rss)
- **Focus:** federal courts; DOJ; executive-power litigation
- **Why trust it:** Former BuzzFeed/MSNBC legal reporter; links filings; fast on lower-court rulings against the administration
- **Red flags:** Openly critical of the Trump administration; opinion mixed into reporting
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Use only the linked rulings; discount framing.

### PBS NewsHour (YouTube) (pol-a44), rating B

- **Where:** YouTube, @NewsHour, https://www.youtube.com/@PBSNewsHour
- **Feed:** `https://www.youtube.com/feeds/videos.xml?channel_id=UC6ZFN9Tx6xh-skXCuRHCDpQ` (youtube_rss)
- **Focus:** full speeches, hearings, pressers
- **Why trust it:** Posts unedited full-length events, so Argus can transcribe the speaker for mentions markets
- **Red flags:** Commentary segments mixed with raw feeds
- **Checkable calls:** no. **Cadence:** daily
- **How to check them:** Use full-event uploads for word-mention checks (confirm market's named source).

### Fox News Politics (pol-a47), rating B

- **Where:** Web, @FoxNews, https://www.foxnews.com/politics
- **Feed:** `https://moxie.foxnews.com/google-publisher/politics.xml` (rss)
- **Focus:** national politics; Fox News Poll; Decision Desk
- **Why trust it:** Fox News Poll and Fox Decision Desk are well rated; good access to GOP officials
- **Red flags:** Opinion programming is right-leaning; keep to news desk items
- **Checkable calls:** yes. **Cadence:** continuous
- **How to check them:** Balance check alongside CNN/NYT.

### CNN Politics (pol-a48), rating B

- **Where:** Web, @CNNPolitics, https://www.cnn.com/politics
- **Feed:** `http://rss.cnn.com/rss/cnn_allpolitics.rss` (rss)
- **Focus:** national politics; CNN/SSRS polls
- **Why trust it:** Large politics unit; CNN/SSRS poll; Harry Enten and other data reporters
- **Red flags:** Opinion and analysis mixed in; RSS feed is legacy and may lag
- **Checkable calls:** yes. **Cadence:** continuous
- **How to check them:** Balance check alongside Fox/WSJ.

### Matt Glassman (Georgetown GAI) (pol-a55), rating B

- **Where:** X, @MattGlassman312, https://x.com/MattGlassman312
- **Feed:** *manual* (manual)
- **Focus:** congressional procedure; how bills actually move
- **Why trust it:** Senior fellow at Georgetown Government Affairs Institute, ex-CRS analyst; explains procedure (cloture, discharge, reconciliation)
- **Red flags:** No feed found (Substack candidates were other people)
- **Checkable calls:** no. **Cadence:** weekly
- **How to check them:** Manual read when a procedure question decides a market.


---

# B. Primary official feeds


## Primary official feeds, rated A

### White House: Presidential Actions (pol-o01), rating A

- **Where:** Web, whitehouse.gov, https://www.whitehouse.gov/presidential-actions/
- **Feed:** `https://www.whitehouse.gov/presidential-actions/feed/` (rss)
- **Focus:** executive orders, proclamations, memoranda, nominations
- **Why trust it:** Primary source for signed actions
- **Red flags:** Posts before Federal Register publication; text can be revised in FR
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** First signal an EO was signed; settle on the market's named source (often FR or WH).

### White House: News (pol-o02), rating A

- **Where:** Web, whitehouse.gov, https://www.whitehouse.gov/news/
- **Feed:** `https://www.whitehouse.gov/news/feed/` (rss)
- **Focus:** briefings, statements, fact sheets
- **Why trust it:** Primary source
- **Red flags:** Advocacy framing in fact sheets (it is the administration's voice)
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Statements are the record of what the WH said, not proof of facts.

### White House: Remarks (pol-o03), rating A

- **Where:** Web, whitehouse.gov, https://www.whitehouse.gov/remarks/
- **Feed:** `https://www.whitehouse.gov/remarks/feed/` (rss)
- **Focus:** transcripts of presidential remarks
- **Why trust it:** Primary transcripts (feed had 2 items today, so low/irregular cadence)
- **Red flags:** Transcripts can lag or be edited
- **Checkable calls:** yes. **Cadence:** irregular
- **How to check them:** Mentions markets: compare against video (o04) since rules usually name the live broadcast.

### White House (YouTube) (pol-o04), rating A

- **Where:** YouTube, @WhiteHouse, https://www.youtube.com/@WhiteHouse
- **Feed:** `https://www.youtube.com/feeds/videos.xml?channel_id=UCYxRlFDqcWM4y7FfpiAN3KQ` (youtube_rss)
- **Focus:** livestreams of speeches, briefings
- **Why trust it:** Primary video of presidential events
- **Red flags:** Livestream audio gaps have caused Kalshi mention disputes
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Pull captions/transcribe for word-mention markets.

### Federal Register API: executive orders (pol-o05), rating A

- **Where:** JSON API, federalregister.gov, https://www.federalregister.gov/presidential-documents/executive-orders
- **Feed:** `https://www.federalregister.gov/api/v1/documents.json?conditions[presidential_document_type]=executive_order&order=newest&per_page=5` (json_api)
- **Focus:** executive orders with EO numbers and signing dates
- **Why trust it:** Official legal publication; free, no key
- **Red flags:** Publishes days after signing
- **Checkable calls:** yes. **Cadence:** daily (business days)
- **How to check them:** Count EOs and match EO numbers for "how many EOs" markets.

### Federal Register: all presidential documents (pol-o06), rating A

- **Where:** RSS, federalregister.gov, https://www.federalregister.gov
- **Feed:** `https://www.federalregister.gov/api/v1/documents.rss?conditions[type][]=PRESDOCU&order=newest` (rss)
- **Focus:** proclamations, memoranda, determinations
- **Why trust it:** Official legal publication
- **Red flags:** none
- **Checkable calls:** yes. **Cadence:** daily (business days)
- **How to check them:** As above.

### GovInfo: Daily Compilation of Presidential Documents (pol-o07), rating A

- **Where:** RSS, govinfo.gov, https://www.govinfo.gov/app/collection/cpd
- **Feed:** `https://www.govinfo.gov/rss/dcpd.xml` (rss)
- **Focus:** official transcripts of presidential remarks, interviews, statements
- **Why trust it:** GPO's official record of everything the president says publicly
- **Red flags:** Lags days to weeks
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Best after-the-fact transcript for checking what the president said.

### GovInfo: Public and Private Laws (pol-o08), rating A

- **Where:** RSS, govinfo.gov, https://www.govinfo.gov/app/collection/plaw
- **Feed:** `https://www.govinfo.gov/rss/plaw.xml` (rss)
- **Focus:** enacted laws
- **Why trust it:** Official GPO publication
- **Red flags:** Lags enactment
- **Checkable calls:** yes. **Cadence:** as enacted
- **How to check them:** Settles "will X become law".

### GovInfo: Congressional Bills (pol-o09), rating A

- **Where:** RSS, govinfo.gov, https://www.govinfo.gov/app/collection/bills
- **Feed:** `https://www.govinfo.gov/rss/bills.xml` (rss)
- **Focus:** bill text versions
- **Why trust it:** Official GPO publication
- **Red flags:** High volume
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Filter by bill number.

### GovInfo: Congressional Record (pol-o10), rating A

- **Where:** RSS, govinfo.gov, https://www.govinfo.gov/app/collection/crec
- **Feed:** `https://www.govinfo.gov/rss/crec.xml` (rss)
- **Focus:** floor speeches, votes, proceedings
- **Why trust it:** Official record of floor proceedings
- **Red flags:** Members can revise remarks; lags a day
- **Checkable calls:** yes. **Cadence:** daily in session
- **How to check them:** What a senator/representative said on the floor.

### Congress.gov: Bills Presented to the President (pol-o11), rating A

- **Where:** RSS, congress.gov, https://www.congress.gov
- **Feed:** `https://www.congress.gov/rss/presented-to-president.xml` (rss)
- **Focus:** bills sent to the president
- **Why trust it:** Library of Congress official tracker
- **Red flags:** Low item count
- **Checkable calls:** yes. **Cadence:** as it happens
- **How to check them:** Start the 10-day signing clock.

### House Majority Leader: Bills This Week (pol-o12), rating A

- **Where:** RSS, docs.house.gov, https://docs.house.gov/floor/
- **Feed:** `https://docs.house.gov/BillsThisWeek-RSS.xml` (rss)
- **Focus:** House floor schedule
- **Why trust it:** Official House floor schedule
- **Red flags:** Schedules change
- **Checkable calls:** yes. **Cadence:** weekly
- **How to check them:** Timing input for "will House vote by" markets.

### Senate roll call votes (119th Congress, 2nd session) (pol-o13), rating A

- **Where:** XML, senate.gov, https://www.senate.gov/legislative/votes_new.htm
- **Feed:** `https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_119_2.xml` (xml)
- **Focus:** every Senate vote incl. confirmations
- **Why trust it:** Official Senate record
- **Red flags:** URL changes each session (119_2 -> 120_1 in Jan 2027)
- **Checkable calls:** yes. **Cadence:** daily in session
- **How to check them:** Settles confirmation and passage markets.

### House Clerk roll call votes (pol-o14), rating A

- **Where:** Web, clerk.house.gov, https://clerk.house.gov/Votes
- **Feed:** *manual* (manual)
- **Focus:** every House vote
- **Why trust it:** Official House record
- **Red flags:** No working index feed found today (evs index 404); per-vote XML exists at clerk.house.gov/evs/YYYY/rollNNN.xml
- **Checkable calls:** yes. **Cadence:** daily in session
- **How to check them:** Fetch the per-vote XML by number when a market needs it.

### CourtListener: Supreme Court opinions (pol-o15), rating A

- **Where:** RSS, courtlistener.com, https://www.courtlistener.com/?court=scotus
- **Feed:** `https://www.courtlistener.com/feed/court/scotus/` (rss)
- **Focus:** SCOTUS opinions (mirror of supremecourt.gov)
- **Why trust it:** Free Law Project nonprofit mirror of official opinions
- **Red flags:** Mirror, minutes behind the Court
- **Checkable calls:** yes. **Cadence:** opinion days
- **How to check them:** Fast feed; confirm on supremecourt.gov.

### Supreme Court of the United States (pol-o16), rating A

- **Where:** Web, supremecourt.gov, https://www.supremecourt.gov/opinions/slipopinion/25
- **Feed:** *manual* (manual)
- **Focus:** slip opinions, orders lists, calendar
- **Why trust it:** The Court itself
- **Red flags:** No RSS for opinions; page fetched OK today
- **Checkable calls:** yes. **Cadence:** opinion/order days
- **How to check them:** Settlement source for court-ruling markets.

### FEC OpenFEC API (pol-o17), rating A

- **Where:** JSON API, api.open.fec.gov, https://api.open.fec.gov/developers/
- **Feed:** `https://api.open.fec.gov/v1/candidates/?api_key=DEMO_KEY&election_year=2026&office=S&per_page=1` (json_api)
- **Focus:** candidates, filings, fundraising
- **Why trust it:** Official campaign-finance data
- **Red flags:** DEMO_KEY is public and rate-limited (fine for occasional pulls); a personal key needs a sign-up, which is Caleb's call
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Fundraising-total markets; candidate filing status.

### State and county election offices (results/certification) (pol-o19), rating A

- **Where:** Web, various SoS sites, https://www.eac.gov/voters/register-and-vote-in-your-state
- **Feed:** *manual* (manual)
- **Focus:** official results and certification
- **Why trust it:** Legal final results
- **Red flags:** 50 different formats; many block bots
- **Checkable calls:** yes. **Cadence:** election nights; canvass
- **How to check them:** Final settlement for close races; list the specific SoS URL per market.

### U.S. State Department press releases (pol-o20), rating A

- **Where:** RSS, state.gov, https://www.state.gov
- **Feed:** `https://www.state.gov/rss-feed/press-releases/feed/` (rss)
- **Focus:** foreign policy actions; sanctions; visits
- **Why trust it:** Primary source
- **Red flags:** Administration voice
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Foreign-policy event confirmation.

### UK Prime Minister's Office (gov.uk) (pol-o21), rating A

- **Where:** Atom, gov.uk, https://www.gov.uk/government/organisations/prime-ministers-office-10-downing-street
- **Feed:** `https://www.gov.uk/government/organisations/prime-ministers-office-10-downing-street.atom` (atom)
- **Focus:** UK PM statements and speeches
- **Why trust it:** Primary source
- **Red flags:** none
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Foreign-leader statements.

### Kremlin: President of Russia news (English) (pol-o22), rating A

- **Where:** RSS, kremlin.ru, http://en.kremlin.ru
- **Feed:** `http://en.kremlin.ru/events/president/news/feed` (rss)
- **Focus:** Putin statements, meetings, decrees
- **Why trust it:** Primary record of what the Kremlin says Putin did/said
- **Red flags:** State propaganda channel: proves what was said, not that it is true
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Use for "will Putin meet/say X"; never as fact-check on claims.

### Congressional Budget Office (pol-o23), rating A

- **Where:** RSS, cbo.gov, https://www.cbo.gov
- **Feed:** `https://www.cbo.gov/publications/all/rss.xml` (rss)
- **Focus:** bill cost estimates; budget outlook
- **Why trust it:** Nonpartisan official scorekeeper
- **Red flags:** none
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Score releases move bill odds.

### Donald Trump on Truth Social (pol-o24), rating A

- **Where:** Truth Social, @realDonaldTrump, https://truthsocial.com/@realDonaldTrump
- **Feed:** *manual* (manual)
- **Focus:** the president's own posts
- **Why trust it:** The primary record for "will Trump post/say X" markets
- **Red flags:** Truth Social has no free public feed; site is behind bot protection
- **Checkable calls:** yes. **Cadence:** many/day
- **How to check them:** Use trumpstruth.org mirror (o25) for fetching; confirm the post on truthsocial.com by hand when it settles a market.

### President / White House on X (pol-o26), rating A

- **Where:** X, @POTUS / @WhiteHouse, https://x.com/WhiteHouse
- **Feed:** *manual* (manual)
- **Focus:** official statements and clips
- **Why trust it:** Primary accounts
- **Red flags:** No free X read API
- **Checkable calls:** yes. **Cadence:** many/day
- **How to check them:** Manual check when a market names X as the source.

### Factba.se (Roll Call) (pol-o27), rating A

- **Where:** Web, rollcall.com/factbase, https://rollcall.com/factbase/
- **Feed:** *manual* (manual)
- **Focus:** searchable transcripts of every Trump speech, interview, post
- **Why trust it:** Transcribes the president's public remarks with timestamps; the go-to tool for word-count/mention research
- **Red flags:** No feed found (feed URLs 404/timed out); not an official government record
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Search base rates: how often has Trump said word X per speech (prior for mentions markets).

### C-SPAN (YouTube) (pol-o28), rating A

- **Where:** YouTube, @cspan, https://www.youtube.com/@cspan
- **Feed:** `https://www.youtube.com/feeds/videos.xml?channel_id=UCb--64Gl51jIEVE-GLDAVTg` (youtube_rss)
- **Focus:** unedited floor, hearings, speeches
- **Why trust it:** Gavel-to-gavel nonprofit coverage; not government but the de facto primary video record
- **Red flags:** YouTube uploads are a subset; full archive at c-span.org
- **Checkable calls:** yes. **Cadence:** continuous
- **How to check them:** Transcribe events for mentions markets; c-span.org has searchable captions.

### Senate Press Gallery (pol-o29), rating A

- **Where:** X, @SenatePress, https://x.com/SenatePress
- **Feed:** *manual* (manual)
- **Focus:** real-time Senate floor schedule and vote results
- **Why trust it:** Run by Senate Periodical Press Gallery staff
- **Red flags:** No feed (X only)
- **Checkable calls:** yes. **Cadence:** continuous in session
- **How to check them:** Manual check for vote timing.

### Congress.gov API (pol-o30), rating A

- **Where:** JSON API, api.congress.gov, https://api.congress.gov
- **Feed:** *manual* (manual)
- **Focus:** bill status, actions, nominations
- **Why trust it:** Library of Congress official data
- **Red flags:** Needs a free api.data.gov key (sign-up = Caleb's decision); not used here
- **Checkable calls:** yes. **Cadence:** daily
- **How to check them:** Until a key exists, use the GovInfo and Congress.gov RSS rows.


## Primary official feeds, rated B

### U.S. Election Assistance Commission (pol-o18), rating B

- **Where:** RSS, eac.gov, https://www.eac.gov
- **Feed:** `https://www.eac.gov/rss.xml` (rss)
- **Focus:** election administration notices
- **Why trust it:** Federal agency
- **Red flags:** Not results
- **Checkable calls:** no. **Cadence:** weekly
- **How to check them:** Background only.

### Trump's Truth archive (trumpstruth.org) (pol-o25), rating B

- **Where:** RSS, trumpstruth.org, https://trumpstruth.org
- **Feed:** `https://trumpstruth.org/feed` (rss)
- **Focus:** mirror of every Trump Truth Social post
- **Why trust it:** Third-party archive that mirrors posts with timestamps and original links
- **Red flags:** Not official; could miss or lag posts; deleted posts may remain
- **Checkable calls:** yes. **Cadence:** many/day
- **How to check them:** Primary ingest for Trump posts; check against original link before settling.


---

# Screened out

| Candidate | Why it was left out |
|---|---|
| G. Elliott Morris (Bluesky) (cut to hold the list to 40) | Duplicate of Strength In Numbers; the Bluesky feed works and is listed as an alt. |
| Dave Wasserman (X) (cut to hold the list to 40) | Covered by Cook; his Bluesky feed has 1 post and X is manual. Watch @Redistrict by hand on election night. |
| Kyle Kondik (Bluesky) (cut to hold the list to 40) | Duplicate of Crystal Ball (Bluesky feed works: kkondik.bsky.social). |
| Race to the WH (cut to hold the list to 40) | Rated C: single-operator model, opaque method, no working feed. |
| GD Politics (Galen Druke) (cut to hold the list to 40) | Rated B but makes no calls of its own; it interviews people already on the list. Feed and YouTube both work. |
| GD Politics (YouTube) (cut to hold the list to 40) | Duplicate of GD Politics. |
| Kristen Soltis Anderson (Type A-) (cut to hold the list to 40) | Thin feed (4 items) and few checkable calls; Ruffini covers the same Echelon data. |
| Bolts (cut to hold the list to 40) | Overlaps The Downballot on local races, with a progressive lean. Feed works. |
| NPR Politics (+ NPR Politics Podcast) (cut to hold the list to 40) | NPR adds little over AP/NYT for market signal. Feed and podcast feed work. |
| Axios (cut to hold the list to 40) | Anonymous personnel scoops; nothing the official feeds and POLITICO miss. Feed works. |
| The Hill (cut to hold the list to 40) | High-volume aggregation of other outlets' scoops. Feed works. |
| The Dispatch (cut to hold the list to 40) | Opinion-heavy; its best asset, SCOTUSblog, is kept on its own. |
| Very Serious (Josh Barro) (cut to hold the list to 40) | Opinion newsletter with few checkable calls. |
| Philip Bump (Bluesky) (cut to hold the list to 40) | Commentary has leaned left since he went independent; few checkable calls. |
| Semafor (cut to hold the list to 40) | Young outlet that overlaps POLITICO/Axios. |
| Harry Enten (CNN) (cut to hold the list to 40) | TV framing; the polls he cites are already in the averages. |
| Steve Kornacki (NBC) (cut to hold the list to 40) | Live-TV results reading; AP/DDHQ cover it as data. |
| FiveThirtyEight | Shut down by ABC News in March 2025; archive only. Its staff now live on in Silver Bulletin, Strength In Numbers and GD Politics. |
| Rasmussen Reports | 538 dropped it from its averages in 2024 over methodology and coordination concerns (from memory, not re-checked today); heavy partisan commentary on its X account. |
| Trafalgar Group | Low transparency on methods and weighting; partisan commentary from its principal. |
| Big Data Poll / Rich Baris | Partisan framing, low transparency, engagement-driven X presence. |
| InteractivePolls (@IAPolls2022) and Polling USA (@PpollingNumbers) | Repost polls without sponsor/method and with clickbait framing; aggregators of other people's numbers. |
| Leading Report, Collin Rugg and similar breaking-news accounts on X | History of breaking claims later deleted or corrected; engagement bait; no primary sourcing. |
| Visegrad24 | Foreign-news account with documented false or unverified claims; propaganda concerns. |
| Democracy Docket | Run by Marc Elias's orbit (Democratic election litigator); advocacy. Its filings links are fine, but use the dockets directly. |
| Heather Cox Richardson (Letters from an American) | Partisan historical narrative; no checkable calls. |
| Message Box (Dan Pfeiffer) | Democratic strategist; advocacy. |
| Mark Joseph Stern (Slate) | Bluesky feed works and his court reporting is fast, but tone is advocacy; Gerstein/Cheney cover the same rulings straighter. |
| National Review: The Corner | Feed works, but it is opinion blogging; no data calls. |
| Tim Pool | Commentary with a record of spreading false claims. |
| Breaking Points / The Hill Rising | Talk-show commentary; no checkable calls. |
| Mark Halperin (2WAY) | Scoops on unverifiable sourcing; few checkable calls. |
| Data for Progress | Dem-aligned pollster; many releases are sponsored. Use only if a market names it. |
| Ann Selzer / Des Moines Register Iowa Poll | Retired after the 2024 Iowa miss (Harris +3 vs Trump +13); poll discontinued. |
| Prediction-market trader accounts (e.g. Polymarket whales, "Domer"-style traders) | They sell or tout positions; Argus must not take signals from market participants. |
| Paid election-betting tipsters / pick sellers | Excluded by rule. |
| Kalshi and Polymarket official blogs/newsletters | Conflict of interest; useful for rules text only. |
| r/fivethirtyeight, r/PoliticalDiscussion, r/politics (Reddit) | Anonymous; no primary sourcing; downvote dynamics reward partisanship. Fine for finding links, not as a source. |
| Henry Olsen (Henry's Substack) | Feed works but had 1 item; too thin to score. Recheck in 2027. |
| Matt's Five Points (mattglassman.substack.com) | Feed works but authorship unconfirmed; not the Georgetown Matt Glassman as far as I could verify. Glassman kept as X manual. |
| Echelon Insights (YouTube) | Channel RSS returned no items today; their polling covered via Ruffini and Soltis Anderson. |
| Sean Trende (Substack) | seantrende.substack.com fetched but returned no feed items; not a usable feed. Trende's RCP work is covered by the RCP row. |

## Feed notes

- A fetch counted as working when the URL returned HTTP 200 and the body held at least one `<item>`/`<entry>`, or a JSON result list. Run on 2026-10-22 with one request per URL.
- Broken today, so set to manual or swapped: apnews.com RSS (403), Reuters RSS (404), Gallup RSS (404), whitehouse.gov/feed (404, the section feeds work), Decision Desk HQ site feed (404; Bluesky works), news.ballotpedia.org/feed (no items; Bluesky works), Europe Elects site feed (no items; Bluesky works), House Clerk vote index (404), Factba.se (no feed found), Washington Post politics RSS (502).
- The Senate vote URL hard-codes the session (`vote_menu_119_2.xml`). It must change to `120_1` when the new Congress starts in January 2027.
- Low-activity Bluesky feeds that technically work: Nate Cohn (1 post), Dave Wasserman (1 post; kept as X manual instead), Nate Silver (2 posts; the newsletter is the feed).
