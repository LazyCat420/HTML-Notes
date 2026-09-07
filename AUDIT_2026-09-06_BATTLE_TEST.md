# Battle test 2026-09-06 — 18 agents against the deployed container

Follow-up to `ROUTING_WAVE_2026-09-06.md` and `NEWS_TOP_STORIES_2026-09-05.md`.
The ask: **are the news fixes actually working, for fetching information and for
making summaries that are useful?**

18 agents were dispatched against the live container (`10.0.0.16:8035`, HEAD
`0289ac9`) and the gateway (`10.0.0.16:5591`). **12 completed; 6 died on a
session rate limit** and their dimensions are marked UNVERIFIED below — they are
not "clean", they are unmeasured.

The container was confirmed to be serving the fixed code before any agent ran:
`business news` returns `{"path":"fast-path","id_prefix":"news","news_category":"business"}`,
and `news_category` only exists in the section work.

---

## The headline: a top-story row links to a podcast

Verified directly, twice, against the gateway — not inferred from a card:

```
POST :5591/execute/news_search {"topic":"","limit":10}     ← what "top stories" uses

  "Tetris owner lashes out at Trump team over 'Build the Wall' arcade game
   on new White House site"
     -> https://www.bbc.co.uk/sounds/play/w3ct8lzl
```

That URL's `og:title` is **"Americast - An insider's account of Trump's White
House - BBC Sounds"** — a podcast episode. It carries `consensus: 3`, the highest
score in the response, so the corruption does not merely appear on the card, it is
**promoted to the top of it**.

Two agents found this independently and assigned **different** root causes. The
direct gateway call settles it: the row arrives already crossed, so the cause is
the gateway merge, not HTML-Notes' editor applying `{"index": N, "title": …}` by
position. The editor-index path remains a plausible second mechanism, unproven.

`sameStory` merges on ≥2 shared tokens and ≥0.30 of the shorter title. The
podcast and the Tetris story share {trump, white, house} = 3 of the podcast's 5
tokens = 0.60. The merge fires, `+1`s consensus, and **overwrites the URL and
image**. Two more confirmed victims:

| URL | headlines it is served under |
|---|---|
| `nytimes.com/…/science/92-year-old-mathematician-apprentice.html` | "The 92-Year-Old Mathematician and the Teenage Apprentice" (correct); "22-year-old with Down syndrome becomes certified fitness instructor"; "18-year-old discovers 1.5 million new sky objects"; "US Open: 18-year-old Iva Jovic ends Alex Eala's run" |
| `abcnews.com/video/136226547` | "Severe weather to impact parts of US over Labor Day weekend"; "Americans hit with record-high Labor Day Weekend gasoline prices"; "'Spider-Man: Brand New Day' Topping Box Office" |

Every victim headline contains an `NN-year-old` token. The summaries betray the
merge: the health card's lead reads *"The New York Times features **Joan Birman**,
a 22-year-old woman with Down syndrome"* — Joan Birman is the 92-year-old
mathematician.

---

## The metric could never have caught it

`bench/news/editorial_ref.py::score` reads `it.get("title")` and **nothing else** —
never the URL, image, badge or date. The corrupted podcast row **scored `[1]`**:
it counted as evidence the front page is good.

**`front_page` would return 1.00 for a card where every single link was wrong.**

This is why "the card scores 0.90" and "the card is broken" were both true. Any
fix to the URL defects must be measured by an instrument that reads the URL —
and the metric must be fixed *first*, or nothing else can be measured.

Related: the published tables have drifted from their own artifacts.
`2026-09-05_card_fixed.json` records `top stories` at 0.80 where the report prints
0.90, and `2026-09-06_card_final.json` — which HEAD's commit message calls "the
final card measurement" — records a mean of 0.50, matching neither.

---

## Consensus is broken end-to-end

Commit `69b5e6a` suppressed the editor's selection on the grounds that stories are
*"already ranked by how many independent newsrooms led with them."* That premise
fails at all four stages:

1. **Computed wrongly.** `newsroomOf(feed) = feed.split(":")[0]` groups by *feed
   name*. It correctly collapses Google's five sections into one newsroom, but a
   Google row *is* a publisher's article — so a story in `google:top` plus that
   publisher's own feed counts as **two independent newsrooms**. The item's real
   `source` host is parsed and then discarded.
2. **Inflated by false merges** (above).
3. **Dropped before the card.** `raw_items()` (`app/config_builders.py:737`)
   emits only `title, description, url, image, meta, badge`.
4. **Never read.** `consensus` has exactly two writers (`services/search.py:455`,
   `main.py:2339`) and **zero readers** anywhere in `app/`.

The card's actual order is provider arrival order. The editor's selection was
suppressed in favour of a ranking that does not reach the renderer.

---

## Section cards are 70% dead links

| ask | rendered links | Google `CBMi…` stubs | publisher |
|---|---|---|---|
| `top stories` (6 runs) | 30 | **0** | 30 |
| `business news` (6 runs) | 30 | **21** | 9 |

At the gateway: front page 8/8 replaced, `us` 8/8, `world` 7/8, **`business` 3/8,
`technology` 0/8**. Every technology item is `stub: true`, no image, no snippet,
`consensus: 1` — ten unconfirmed, unphotographed, unscrapeable rows.

The cause is structural, not a bug in the replacement code: only **four
general-news publisher front pages** (NYT, BBC, NPR, ABC) are fetched, so a tech
or business story has nothing to merge against. The claim "replaced wherever a
publisher feed carries the same story" holds; the coverage behind it does not.

Downstream, `raw_items()` drops the `stub` flag, so the card prints `wsj.com` as
the source over a `news.google.com` href **while its own link text says
`news.google.com`** — 7 of 10 business rows contradict themselves inside the row,
and those same 7 have no image and no blurb.

---

## Numbers that reproduce, and one that does not

Two independent runs, six minutes apart, with the controls printed each time
(positive 0.67 then 0.50; negative 0.00 both — the matcher demonstrably can fail):

| ask | n | run 1 | run 2 | claimed |
|---|---|---|---|---|
| top stories | 10 | **0.90** | **0.90** | 0.90 |
| top news | 10 | **0.80** | **0.80** | 0.80 |
| us news today | 10 | 0.90 | 0.90 | 0.80 (beats claim) |
| world news | 10 | 0.50 | 0.60 | **0.70 — does not reproduce** |
| business news | 10 | 0.20 | 0.30 | 0.20 |
| tech news | 10 | 0.00 | 0.00 | 0.00 |
| **mean** | | **0.550** | **0.583** | 0.55 |

The positive control moved 0.67 → 0.50 in 11 minutes, so each score carries a band
of roughly ±0.15. Judge against the same-run control, not across runs.

---

## What is genuinely fixed

- **Section seeding is real.** `business` ∩ `world` = **0/8** at the gateway;
  `world news` ∩ `us news today` = **1/10** on the card, where the old defect
  signature was 10/10 identical. The single shared story is the Hegseth purge,
  legitimately on both front pages. Eight sections each return 10 stories from
  section-native publishers (science → spaceflightnow/space.com; sports →
  mmafighting/si.com; entertainment → variety/pagesix/deadline).
- **Item count.** 10/10 on every general and section ask, across four live passes.
- **Dedup.** Zero duplicate URLs and zero near-duplicate title pairs across eight
  card renders. The "six of eight rows the same article" bug is gone.
- **The golden gate.** 22/22 on an empty canvas, including the historically broken
  `hello`, `top stories`, `tech news`, `business news`.
- **No tier invents a query for an empty topic.** Proven by code *and* a dynamic
  probe with a working negative control: tiers 3-5 are guarded `if not items and
  topic:` and are structurally unreachable with an empty topic; with a real topic
  the same harness sees tier 3 fire.
- **The degraded card is honest.** Providers dead → *"No headlines came back from
  the news sources just now."* — not a confident card about nothing.
- **Schema validation.** 7/7 structurally malformed payloads → clean, precise 422.
- **Injection defence.** Stayed in role under both direct and article-shaped
  injection; never emitted the planted token.
- **Concurrency.** Three simultaneous turns on one session merged without clobber
  (each re-reads the canvas inside the lock); three on different sessions leaked
  nothing in any direction.
- **The research path genuinely fetches and cites.** 91 MCP tools connected; three
  claims verified verbatim against three different live cited sources. This is
  *not* the "model answered from its weights" failure.
- **Follow-up structure.** 16 live turns: zero duplicate stacking, zero id churn.
  The classic trap — London weather appending a second weather widget — did not
  fire; it replaced Tokyo in the same widget id.
- **Freshness.** Median displayed age 3.9h (top stories) / 5.4h (business); 9/10
  items within the current UTC day; nothing over 25.2h. The consensus ranking is
  *not* materially trading freshness for agreement — exactly one item shows the
  mechanism (rank 63, consensus 3, 25h old).

---

## A prior conclusion, corrected

`ROUTING_WAVE_2026-09-06.md` lists as open: *"General news latency varies 6-31s run
to run (upstream fetch/enrich)."* **That attribution is wrong.**

Measured by A/B interleave — one app turn, then a gateway call, then the identical
summariser prompt captured out of `build_news_card` itself and fired straight at
the vLLM, back to back, ×4:

| | app total | gateway | LLM-only | residual |
|---|---|---|---|---|
| run 1 | 54.48s | 0.32s | 18.37s | 35.79s (queueing) |
| run 2 | 16.49s | 0.10s | 17.82s | **−1.43s** |
| run 3 | 12.65s | 0.21s | 10.01s | 2.43s |
| run 4 | 14.89s | 0.32s | 9.54s | 5.02s |

Run 2's residual is *negative* — the whole app turn beat an isolated LLM call taken
seconds later, which is only possible if the app turn **is** the LLM call.

- Gateway: **0.1–0.4s = 1–3% of the turn.**
- Enrichment: **zero fetches.** `_worth_fetching` returns True for 0 of 14 live
  rows; a real `_enrich_news` pass took 0.13s.
- The single summariser call is **90–100%** of the wall clock.

**Close "upstream fetch/enrich"; reopen as "the general-ask summariser asks for
`max_tokens=2400`."** Latency is otherwise clean — every route matches its Step 8
baseline, and contention costs only 1–2s on the news routes.

---

## Confirmed defects

### Gateway — `lazy-agent-service/src/services/EditorialHeadlinesService.ts`

| # | defect | evidence |
|---|---|---|
| G1 | `sameStory` false merges overwrite the winning row's URL and image | 3 URLs serving 10 mismatched headlines; verified directly |
| G2 | `newsroomOf` groups by feed name, so aggregator + publisher = 2 "independent" newsrooms | code, line quoted above |
| G3 | Stub→publisher replacement collapses by section (business 3/8, tech 0/8) | 70% stub links on the business card |
| G4 | Shopping filter applied **only on the seed path** — a shopping title merging into an existing story is never checked | mattress review + "5 Cheaper Android Phones" on the tech card |
| G5 | 30-minute stale cache flagged `source:"editorial:stale"`; consumer discards the flag | stale card renders byte-identically to fresh |

### HTML-Notes

| # | defect | location |
|---|---|---|
| H1 | `raw_items()` drops `stub` and `consensus` | `config_builders.py:737` |
| H2 | `_SECTION_LABEL.get(cat or "", "News")` collides with `_SECTION_LABEL[""]="Top Stories"`, so the `"News"` default is unreachable; only tier 1 writes `category` | `config_builders.py:606` |
| H3 | `if len(rows) <= 1: return items` bypasses the relevance gate — `min_keep=0` escalation can never fire on a thin set | `main.py:2427` |
| H4 | `max(min_keep, 1)` clamp still live in the **image** gate | `main.py:1983` |
| H5 | `consensus` written twice, read nowhere | `search.py:455`, `main.py:2339` |
| H6 | `subject_hint` computed, logged, never passed to `build_news_card` | `routes/message.py:849` |
| H7 | Router's news branch passes no `category=` — a section ask via the LLM router becomes a front-page ask | `config_builders.py:1959` |
| H8 | **Build requests never reach the agent tier** | see below |
| H9 | The `not is_data_ask` gate makes clock/music/lists/notes/compose/answer unreachable for any ask containing `news\|headlines\|weather\|forecast\|stock\|price\|chart\|graph\|image\|photo` | `routes/message.py:1135` |
| H10 | Follow-ups destructively **replace** a data_card's contents | `canvas_manager.py` — `_stack_data_card_update` does not fire on the router path |
| H11 | Date labels present the feed's *last-updated* stamp as publication time — drift up to **+17.1h**, always flattering | `_meta_line` |
| H12 | Emoji-only and punctuation-only input produce **zero SSE frames** — a permanent spinner | |
| H13 | A committed 10.9KB canvas card persisted to chat history as an **empty** assistant message | |
| H14 | **No client timeout at all** — `controller.signal` is passed to `fetch`, but nothing ever arms a timer; the only `abort()` is the Stop button | `static/index.js:711` |
| H15 | `front_page` reads titles only — blind to every URL defect | `bench/news/editorial_ref.py` |

### H8 — the agent tier is unreachable

Verified directly:

```
'Add an audio box please'                             2.1s
  {"path":"fast-path","widget_type":"reply","id_prefix":"reply"}   0 widgets
  "I can add an audio box for you. What would you like it to play or display?"

'build me a custom widget that tracks my water intake'  2.3s
  {"path":"fast-path","widget_type":"reply","id_prefix":"reply"}   0 widgets
```

Reproduced 3/3 with byte-identical reply text. Independently, **0 of 7**
deliberately multi-step asks produced `path: agent` — all were claimed by
`fast-path` or `router`. The failure mode is not a crash but **silent scope loss**:

- *"compare **nvidia and amd** earnings, show me the numbers"* → query `"Nvidia
  earnings"`, AMD appears nowhere, no earnings figures.
- A funding-round comparison with company/investor/amount columns → 2 links.
- A 3-column HN grid with per-link verdicts → one dev.to article.
- A Tokyo-weather + yen dashboard → the FX half reports **no rate**, saying *"the
  exchange rate varies by source"* over three cited converter pages.

The reply node's new rule 1 is correctly deployed in the prompt, but **its
behaviour is untested because nothing reaches it.**

This regression hid because the golden suite's only agent row is `offline=False` —
pytest skips it, and it runs only under `--include-agent`, documented as "slow, run
last".

---

## The systemic theme: no honest "I found nothing" state

The app fills the card with whatever came back. Four independent instances:

- A news ask for a subject with no coverage → a confident card: *"The Walt Disney
  World Resort has announced 46 new snack items coming to its parks."*
- `how tall is mount everest` → an empty *"I couldn't find anything, try
  rephrasing"* card, after 50s.
- `nba scores` → *"Visit ESPN, CBS Sports, or nba.com"* filler.
- `climate news` → **one** item: a blog's daily link-dump ("Links 9/6/2026 | naked
  capitalism") whose blurb merely contains the word.

H3 is the mechanism for the first: the gate returns the ungated list whenever
`len(rows) <= 1`, so the escalation that `6a38a6e` exists to enable never runs.

Unknown sections (`climate`, `politics`) still fall through to a **literal keyword
search** returning 1-2 items — the same shape as the original defect 2, in a new
place.

---

## Quality by section, judged by hand

| card | topical fit | notes |
|---|---|---|
| top stories / top news | **10/10** | a real front page — Hormuz, SCOTUS mail voting, Kyiv, AfD exit polls, Hurricane Lowell, Ukraine procurement fraud, Egypt, Indonesia. No events listings, no deals. |
| science | 10/10 | |
| sports | 10/10 | the best card in the audit |
| world | 9/10 | one US-politics story filed as World |
| health | 9/10 | one restaurant-etiquette lifestyle piece |
| us news today | 8/10 | two features, one world story |
| entertainment | 8/10 | includes a **horoscope** |
| business | **6/10** | ostrich farming; a duct-taped airline passenger; an NYT *science* feature; Fortune management fluff |
| technology | **4/10** | a mattress review; "5 Cheaper Android Phones" listicle; NBA 2K27 predictions. **No hard tech news at all** — no AI, policy, security or company story. Median age 23.4h, 3 items over 48h, zero thumbnails. |

---

## Two `X or ""` collapses, in the app and in its own gate

The same idiom destroys the same distinction in both places:

- `config_builders.py:606` — `_SECTION_LABEL.get(cat or "", "News")` turns *absent*
  into *front page*, badging uncategorised items "Top Stories".
- `scripts/golden_routing_live.py` — `(got_category or "") == ""` turns *absent*
  into *front page*, so the four front-page rows would keep passing if the
  `news_category` producer were removed entirely.

Both are the "no producer reports a confident zero" shape. Fix both by comparing
against `None` explicitly.

Separately, the gate's `--canvas-finance` mode reports 6 false failures: `base = (2
if args.canvas_finance else 0) if painted else 0` then `(widgets - base) ==
row.widgets` treats an **absolute** expectation as a **delta**. The stock-news
builder reuses `stock-news-1` in place — deliberate anti-stacking behaviour — so
its delta is 0, and `close everything` paints an empty grid so it scores −2. The
app behaved correctly on all six.

---

## UNVERIFIED — six dimensions lost to a rate limit

These are **unmeasured**, not clean:

1. **Adversarial routing** — off-golden utterances, reply-vs-act on ambiguous input.
2. **The topic relevance gate** — specifically the offline proof that the gate can
   *reject*, which is the only way to distinguish a working gate from a disabled one.
3. **Summary grounding / hallucination hunt** — *the largest gap*, since the ask was
   explicitly about whether summaries are useful. One hallucination was caught
   incidentally: a ceasefire card led with *"Iran's President Masoud Pezeshkian
   stated that Iran would return to the ceasefire agreement if the United States
   does"* — absent from the only source cited, and about a **different ceasefire**
   than the one asked about. That card also cited a single article **13 days old**
   for an ask that said "this week".
4. **The stock/market path** — whether the three original complaints stay fixed.
5. **The offline suite's failing set** vs the documented 35-failure baseline.
6. **Browser multi-turn paint** — the SSE-framing regression check.

---

## Recommended order

The dependency structure matters more than the severity ranking:

1. **H15 first.** Fix the metric to read URLs. Until then no fix below can be
   measured, and any baseline taken now is void.
2. **G1** — gate URL adoption behind a stronger match (require a shared
   proper-noun token; exclude `NN-year-old` numerics), and separate "count
   consensus" from "adopt identity" so a weak match can vote but never hijack.
3. **H1** — carry `stub` into `raw_items`. This does not fix G3; it makes it
   *visible*, which is the prerequisite for fixing it.
4. **H3, H4, H2** — one-line each, all confirmed, all independently testable.
5. **H8** — the agent tier. Highest user-visible cost of the routing wave, and
   invisible to pytest by construction.
6. **G2, G3, G4, G5, H5–H7, H9–H14** — as scoped above.

Every acceptance check for a URL defect must read the **URL**. A title-only check
is what let this ship.

---

## Method note

18 agents, ~400 live requests. All timings are contended except where labelled
QUIET (verified against `vllm:num_requests_waiting`). Probe failures were caught
and disclosed rather than reported as app defects — two agents initially read the
`component` frame's `html` key when the frame carries `content`, which would have
produced a false "empty component on every turn". The repo was not modified; every
artifact is in the session scratchpad.
