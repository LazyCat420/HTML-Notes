# "Top stories" returned four random articles — what was wrong and what shipped

**2026-09-05/06.** HTML-Notes `e42aeba`+`69b5e6a`, lazy-agent-service
`7b5d315`+`1e67d44`, both deployed and verified live.

The report the owner asked for: **where does the news actually come from, why is
it worse than the trading dashboard's chat, and is it fixed** — with numbers.

---

## The complaint

Asked for "top stories", the card showed four items: a college football recap,
the Lindsay Clancy mistrial, and a story about the Constitution and a right to
clean water. Not wrong articles — *random* ones, and only four of them.

## Where the news comes from (measured, not assumed)

The ask never reaches an agent, prism, or a model-chosen tool.

```
POST /session/message
  -> classify_news_ask            app/main.py           deterministic pre-router
  -> build_news_card              app/config_builders.py
  -> news_search("")              app/services/search.py
  -> POST :5591/execute/news_search   {"topic": "", "limit": 8}
       lazy-agent-service, NATIVE tool (not a proxy)
```

scraper-service is only fallback tier 3, and prism is not involved at all. The
debug frame reads `{"path":"fast-path","id_prefix":"news"}` — that frame is the
only instrument that says which of several news builders ran, and it is worth
reading before believing any routing claim.

## Why it was bad — six defects, each hiding the next

**1. The provider answered a different question.** An empty topic went to
whichever keyed provider had a top-headlines endpoint and a live quota. With
gnews cooling (its key returns HTTP 403) that was **currentsapi
`/v1/latest-news` — a recency feed**. It answers "what was published in the last
few minutes", not "what is the news today", and its country filter did not bind.
One live call:

```
nwfdailynews.com   Michael J Thomas (Solo Show) @Ruth's Chris   (an events listing)
espn.com           Cards, Rockies tilt caps in protest against ump
espn.com           Michigan stuns Western Michigan on Hail Mary
bloomberg.com      Constitution doesn't include right to clean water
washingtonpost.com What happens to Lindsay Clancy now?
```

Minutes later: BYU football, Utah Buddhist monks, a 10,000 Maniacs anniversary
show — with **six of eight rows the same article repeated**, because nothing on
that path deduped.

**2. Sections did not exist.** "world", "us", "america" and "global" were
general-ask *filler*, so "world news" and "us news today" made the identical
undifferentiated call and returned the identical three stories. "business" and
"tech" were in no list at all, so they left a residual and became a **literal
keyword search for the word**: "business news" returned a Flipboard page about
street flooding hurting a nearby business; "tech news" returned a Nike Air Max
post.

**3. A general ask could still become a keyword search.** Fallback tier 3
searched the literal words `"news top stories"` and tier 5 `"top news
headlines"`. That is the same defect found and fixed in tier 1 earlier, left in
place two tiers down — where it fires only in the degraded state, the moment
quality matters most.

**4. The card was capped at six, and the editor was told to show fewer.** The
summariser's instruction — *"returning FEWER, on-topic entries is correct and
expected"* — is right for a subject ask and wrong for a front page, where there
is no topic to be off and variety is the point. Ten sources in, and it returned
ten, then six, then four, for the same request.

**5. The section died one function short of the card.** Found only by driving
the deployed app: every badge on every card read "Top Stories", including on the
world and business cards. `_normalise_news_item` rebuilds each item field by
field and had no line for the section, so it survived the classifier, the
provider call and the ranking, and was dropped by the last function before
render. The badge is the only thing that makes a mixed front page read as one.

**6. Two wasted fetches per card, one of which could never work.** Enrichment
fetched every article page including Google redirect links, which do not resolve
server-side and whose `og:image` is Google's own logo. That fetch was the
dominant latency term: a general ask measured **31.2s**.

## Why the trading dashboard's chat is better

Its AI Strategy Chat does not keyword-search live. It **pre-loads a curated
corpus** into the prompt — Mongo `news_articles`, filled by ~25 curated
finance/macro RSS feeds plus a keyed rotator, quality-gated at write,
ticker-attributed, roughly 1,580 articles/day — sorted newest-first and split
into "LAST 48 HOURS" versus "OLDER, BACKGROUND ONLY" with explicit age labels.
Its one search tool is keyless Bing/Google News RSS with over-fetch and
title-skeleton dedup. It never touches the keyed providers' "top" endpoints.

The lesson copied here is **curated editorial sources over a vendor's idea of
"top"**, not the corpus itself: HTML-Notes needs general news, not finance news.

## What shipped

**Gateway (`lazy-agent-service`).** A new `EditorialHeadlinesService` answers an
empty topic from feeds whose *order is a newsroom's judgement*: Google News front
page and sections, plus NYT / BBC / NPR / ABC. Stories are merged by
headline-token overlap and ranked by **consensus** — how many independent
newsrooms led with the same event. Two traps are encoded rather than
rediscovered: a Google `/rss/articles/CBMi…` link is a stub (marked `stub: true`,
no image, never scraped, and replaced by the publisher's real URL and photo
wherever a publisher feed carries the same story), and consensus counts
**newsrooms, not feeds**, since Google's top and world sections are one newsroom.
A section ask is answered by its section — publisher front pages may *enrich* but
never *seed*, or "business news" comes back as Putin and the Nepal floods.
Shopping and deals posts are filtered, because Google's business and technology
sections carry them and no structural signal separates them from reporting.
Keyed providers are untouched for topic searches and remain the fallback.

**HTML-Notes.** A section classifier; the section reaches the provider and is
asserted in the debug frame; no tier may invent a query for an empty topic; a
general card keeps 10 stories with section badges; enrichment skips stubs and
already-complete items; and **the editor writes the front page but no longer
chooses it** — the ranking decides, and a source the model declined to write up
keeps its provider snippet instead of vanishing.

## Numbers

`front_page` is the share of a card's stories that an **independent** newsroom
also led with — reference feeds (CBS, Guardian US+World, PBS, Politico, The Hill,
LA Times, NBC) deliberately disjoint from the feeds the fix reads, or the metric
would be a tautology. Thresholds were calibrated by sweeping a positive and a
negative control; the positive control's ceiling is ~0.58, not 1.0, because a
real front page carries exclusives no one else has.

**The card the user sees** (`bench/news/card_probe.py`, deployed container):

| ask | before: items / front_page | after: items / front_page |
|---|---|---|
| top stories | 3 / 0.00 | 10 / 0.90 |
| top news | 3 / 0.00 | 10 / 0.80 |
| world news | 3 / 0.00 | 10 / 0.70 |
| us news today | 3 / 0.00 | 10 / 0.80 |
| business news | 1 / 0.00 | 10 / 0.20 |
| tech news | 1 / 0.00 | 10 / 0.00 |
| **mean** | **0.00** | **0.55** |

Before, the first four asks returned the *same three stories*.

**The source, blind LLM judge over 12 general asks** (`bench.news.run --rows
general --only P,T`; P is the old keyed path, kept reachable behind a debug-only
pin so the baseline stays measurable):

| strategy | overall | on_topic | front_page | items | p50 |
|---|---|---|---|---|---|
| P keyed-top (old) | 1.67 | 1.17 | 0.10 | 10 | 441 ms |
| T editorial (new) | 4.92 | 5.00 | 0.47 | 10 | 30 ms |

P returned the *same* stories for every section — Astronomy Picture of the Day,
a Trump-backed ad buy, German voters — because the keyed providers ignore the
section entirely.

**Latency**, per phase: fetch 0.03-0.29s (was seconds), enrichment 0.01-0.21s
(was the dominant ~31s term), summariser 6-12s. End-to-end 5-13s against 31.2s.

**Read `business` and `tech` at 0.20 / 0.00 correctly.** The reference feeds are
general-news front pages and do not carry section stories, so the metric is not
meaningful there. That is the metric behaving correctly, not a verdict about
those cards — judge them by the blind judge's on-topic score instead.

## Gates

- `tests/test_golden_routing.py` — nine new rows for the owner's own utterances,
  now asserting the **section** in the debug frame as well as the builder.
  `scripts/golden_routing_live.py` runs the same table against the deployed
  container: **22/22 pass**.
- `tests/test_news_classifier.py` — section rows derived from
  `_NEWS_CATEGORY_WORDS` itself rather than transcribed beside it, plus negatives
  ("small business tax updates", "world cup qualifiers") that must NOT be section
  asks.
- `tests/test_news_general_path.py` — no tier invents a query; the section
  reaches the provider; a stub is never fetched; the editor cannot shrink a
  general card; a subject ask is unchanged in every one of those respects.
- Gateway: 381 tests. Every load-bearing rule was **sabotaged and shown to fail
  its own test** — newsroom-vs-feed consensus, publisher-URL adoption, the
  title-suffix strip, the stale fallback, empty-topic routing, the optional
  `topic` in the schema.
- HTML-Notes suite parity: 34 pre-existing failures before the work and 34
  after, none new, 898 passing.

## Three harness defects found on the way

- A source guard read `app/main.py` instead of `app/services/search.py`, because
  search.py opens by copying `main.__dict__` over its own and takes `__file__`
  with it. **It passed with both literal queries still in the file.** It now
  locates the file through the function's own code object, parses it, and
  ignores docstrings — otherwise the comment explaining the fix satisfies the
  guard.
- A probe on `asyncio.get_event_loop()` passed alone and raised only when
  another module ran first — a harness failure that reads exactly like the code
  under test being broken.
- `test_build_stock_news_config_prioritizes_shared_news` made a **live network
  call on every run**: the second half of its fetch was never stubbed. It only
  became visible when the mock stopped matching the call signature and the live
  call answered instead, failing the assertion against a real Indian markets
  article.

## Still open

- **The gnews API key returns HTTP 403.** It is the best keyed provider for
  topic searches and is currently dead weight in the rotation; nothing here
  depends on it, but a topic search is worse without it.
- **Two rotators spend the same keys.** This gateway and
  `trading-service/app/collectors/news_api_rotator.py` track the same free-tier
  budgets in separate processes against identical daily limits, so the real
  combined ceiling is roughly half what each believes. The top-stories path no
  longer spends any of it.
- **trading-client's Web Search toggle is dead code** — `app.services.web_search`
  was deleted in `cdad9da0` and the import raises `ModuleNotFoundError`, which is
  swallowed; turning the toggle on *removes* the database news and substitutes
  nothing. Not touched here.
- `front_page` needs section-specific reference feeds before it can judge a
  business or technology card.
