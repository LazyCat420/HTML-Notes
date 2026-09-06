# Audit 2026-09-05 — tool-calling relevance and latency

Brief: *"articles tend not to be relevant to the question / a bunch of random
news / basically ads"*, and *"how do we increase the speed"*. Standardise on the
Jetson while we're in here.

Everything below was measured by driving the deployed container, not by reading
the code. Where a number appears, it came from a probe.

---

## The first surprise reframed the question

Three probes through `scripts/sse_probe.py` against `10.0.0.16:8035`:

| ask | path taken | elapsed | tools called |
|---|---|---|---|
| "latest news on the israel hamas ceasefire" | **fast-path** | 54.4s | none |
| "explain how perovskite solar cells degrade…" | **fast-path** | 201.2s | none |
| "build me a custom widget that counts down…" | **agent** | 39.6s | `canvas_add_widget` |

**Both slow turns never reached the agent.** They were caught by the regex
cascade and served by server-side builders. The agent was never the bottleneck —
on the one turn that did reach it, 21.0s of the 39.6s was the tier-2 classifier
running *before* the `StreamingResponse` was constructed, i.e. frozen UI.

---

## Latency: one parameter, and the workaround that made it worse

`nemotron35` is a reasoning model. It was spending its entire token allowance on
the trace and emitting **no content at all**:

```
max_tokens=550  -> completion_tokens=550,  content_len=0,    finish=length
max_tokens=700  -> completion_tokens=700,  content_len=0,    finish=length
max_tokens=4096 -> completion_tokens=4096, content_len=3498, 61.2s
```

That empty-content failure is why `fast_llm_json` had grown
`token_budget = max(max_tokens, 4096)`, silently overriding every caller (the
router asks 550, the vision gate 120). **The workaround for the bug became the
dominant cost of the app**: every call on every path paid for a 4096-token
generation.

Only one spelling turns the trace off on this box. All measured, same prompt:

| variant | time | content |
|---|---|---|
| `chat_template_kwargs {"enable_thinking": false}` | **0.8s** | valid JSON |
| `reasoning_effort: "none"` | **0.8s** | valid JSON |
| `chat_template_kwargs {"thinking": false}` | 7.9s | NONE |
| `reasoning_effort: "low"` | 8.4s | NONE |
| system `/no_think` | 7.9s | NONE |
| system `detailed thinking off` | 8.4s | NONE |
| baseline | 7.8s | NONE |

This is the **opposite** of the DeepSeek case the vllm shim handles, where
`thinking` is live and `enable_thinking` is ignored. Both keys are now sent; a
template ignores the one it does not read.

On the real router prompt: **17.6–25.6s → 1.2–1.7s, identical decisions.** Tool
calling is unaffected — `tool_choice:"auto"` returns parsed `tool_calls` either
way.

### The 201s turn, explained

```
fast_llm_json failed on http://10.0.0.30:8000: ReadTimeout      (90s)
fast_llm_json failed on http://10.0.0.141:8000: ReadTimeout     (90s)
fast_llm_json http://10.0.0.30:8001 returned HTTP 404
```

Three compounding faults: the Jetson blew a 90s read timeout because of the
4096-token floor; Gold Spark is head-of-line blocked (`/v1/models` answers
instantly, `num_requests_waiting=3 reason="deferred"`, `prompt_tokens_total` flat
across a 10s sample — nothing is being prefilled); and `10.0.0.30:8001` serves
**embeddinggemma**, which has no chat endpoint at all. The card that finally
rendered was the fallback.

### Shipped

- `enable_thinking:false` on every `fast_llm_json` / `_fast_multimodal_json` call
- the 4096 floor removed — the caller's budget is the budget
- read timeout 90s → 20s
- `:8001` removed from the **chat** pool (the model still runs there for its real
  consumers; it was only ever a wasted round-trip as a chat target)
- agent model selection walks `PREFERRED_AGENT_PROVIDERS` (Jetson first) and
  refuses a model that cannot chat, instead of taking whatever the catalog's dict
  order yielded. See "an invariant" below.
- the two blocking `httpx.Client` calls in the async handler made async

| ask | before | after |
|---|---|---|
| news | 54.4s | **8.0s** |
| research answer | 201.2s | **22.0s** |
| agent turn | 39.6s | **7.9s** |
| dead air before first byte | 21.0s | **5.0s** |

---

## An invariant: a declared capability is not a measured one

`GET :5591/config-local`, verbatim:

| provider | model | modelType | tools |
|---|---|---|---|
| `vllm` | `nemotron35` | conversation | `['Tool Calling']` |
| `vllm-2` | `GLM-5.3-Flash-EXL3` | conversation | `['Thinking','Tool Calling']` |
| `vllm-3` | `embeddinggemma` | conversation | `['Tool Calling']` |

One of those three declares a capability it provably does not have —
embeddinggemma 404s on `/v1/chat/completions`. Selection took the **first** entry
with `modelType=="conversation"` and `"Tool Calling"`, so it landed on nemotron by
luck, not policy. **Never gate on a catalog's self-description when the capability
is checkable.**

---

## Relevance: six defects, and only three were visible from the code

**1. The query was destroyed before it left the process.** The news topic came
from `extract_topic` — whose `TOPIC_STOPWORDS` is `MUSIC_FILLER_WORDS |
{widget adjectives}`. Run against the real code:

```
news about track and field world championships -> "field world championships"
latest news on the player transfer window      -> "transfer window"
latest news on us china trade talks            -> "china trade talks"
what is the latest news on the israel hamas ceasefire
                                       -> "what is israel hamas ceasefire"
```

"track", "player", "us", "search" are subject words the *music* widget considers
filler. The scaffolding that should go ("what is") survives. Live, that last one
returned **one** article for one of the largest running stories.

**2. Nothing checked the results.** Five provider tiers, first non-empty wins; no
dedup, no relevance scoring, no ad filter. And the summariser was told "write one
entry per distinct story" — **it had no authority to drop anything**, so it wrote
confident prose over whatever arrived. That is why the junk read as deliberate
rather than as an error.

**3. The one ad filter defeated itself.** `filtered or raw_yahoo` reinstated every
ad when the page was all ads, and only ran on the fallback tier.

**4. An expanded query is the wrong instrument for a news API.** `ground_query`'s
`retrieval_query` is documented as "an expanded, unambiguous WEB-SEARCH query".
News providers keyword-match:

```
"US China trade talks"                                  -> 6 items, 4 on-topic
"latest news US China trade talks negotiations updates" -> 4 items, 0 on-topic
```

**5. The fail-open floor defeated the gate at the one moment it mattered.**
`min_keep=1` meant "if the model rejects everything, show everything". Observed
live: the provider returned a jobs report, shipping chokepoints, crude oil and a
Fed speech for a trade-talks query; the gate correctly rejected all four; the
floor put all four back. *"Every story is off-subject"* is a verdict about the
result set; *"the model did not answer"* is a grading failure. Fail open on the
second, escalate on the first.

**6. English is not a region.** See `lazy-agent-service/docs/`. Briefly: every
provider was sent `language:"en"` and no country, so Indian English-language
outlets owned any generic query — the user's "random Indian company" was Tata
Technologies via Economic Times. **Those articles genuinely are stock market
news**, which is why no relevance gate could fix it: they are on-topic and still
wrong. Fixed with a region parameter, default `us`.

### Measured — `bench/news/`, 15 queries, blind judge

| strategy | overall | on_topic | not_ad | substance | empty |
|---|---|---|---|---|---|
| A legacy | 2.67 | 2.47 | 9.67 | 3.53 | 0 |
| B grounded subject | 4.00 | 4.07 | 9.13 | 5.53 | 0 |
| C + drop-gate | 4.80 | 5.33 | 9.73 | 6.27 | 0 |

`empty` is a column on purpose: a gate can score a perfect `on_topic` by
returning nothing, so an empty set is judged 0 rather than skipped. C is better,
**not good** — the remaining ceiling is upstream provider quality.

---

## The trap that cost the most time

**The general news ask never reaches `build_news_config` at all.**

`_NEWS_SYNTH_RE` catches "what's going on in the news" first and routes it to
`build_news_brief_config` — the debug SSE frame says `id_prefix: "news-brief"`,
not `"news"`. That builder calls the SDK's `grounded_research(max_articles=8,
scrape_top_n=3)`: a written synthesis, ~32s because it scrapes three articles.

So every relevance fix above was **invisible on the one ask the user tested**.
There are at least four news-ish branches reached by different regexes. **Read
the debug frame's `id_prefix` to learn which builder actually ran before
concluding a news fix works.**

---

## Verification layer

It was not merely red, it was **not running**.

- The suite **could not be collected**: `tests/test_news_scraping_llm.py` imports
  `respx`, never added to `requirements.txt`, and pytest aborts the whole run on a
  collection error. No test had run since that commit landed.
- ~30 guards still read `app/main.py` after the request path moved to
  `app/routes/`, `app/services/` and `app/llm.py`. The loud ones raised
  `ValueError: substring not found`; the quiet ones asserted a string was present
  in a file that no longer defined it.
- **~48 mocks were aimed at a module the callers do not resolve against.** These
  modules do `sys.modules[__name__].__dict__.update(main.__dict__)`, so a function
  reports `__module__ == "app.main"` while its `__globals__` is the other module's
  dict. `monkeypatch.setattr(m, "fast_llm_json", fake)` rebound a name nothing
  read — the fake was called **zero** times and the test hit the live Jetson.

Three flavours of consequence, all worth recognising:

1. **Slow** — whole files hung 90-180s on unintended live calls.
2. **A false green** — one test asserted something the live model happened to
   satisfy. It had never tested its fixture.
3. **Green because the app was broken** — two `/health` tests mocked a dead
   dependency, missed, and asserted `ok is False`, which the *real* function
   returned because of an unrelated bug (a renamed MCP server). Fixing the app
   turned the tests red.

`tests/conftest.py` now provides `patch_server`, which **discovers** the loaded
`app.*` modules rather than listing them — the first version used a hand-written
tuple, it was missing `app.routes.health`, and the miss was invisible again.
An autouse guard fails any unmarked test that reaches `10.0.0.30`/`10.0.0.141`;
it must stand down while respx is intercepting, because **respx patches httpcore,
below httpx**, so a guard on `AsyncClient.send` sits in front of respx and eats
the calls respx was set up to serve.

**Suite: uncollectable → 33s, 794 passed, 35 failed.**

---

## Open items

- **35 pre-existing failures** remain, in areas this wave did not touch: widget
  identity/targeting, follow-up targeting, trending stocks, and a compare-config
  alignment test. Now visible rather than hidden behind a suite that would not run.
- **`bench/news/` C scores 4.80/10.** The ceiling is provider quality. Next lever
  is upstream (better sources or a cross-provider merge), not more filtering.
- **General news is ~32s** because the brief scrapes three articles. Not addressed
  — the complaint was relevance, not speed, and the brief is a richer product.
- **`.venv/` is committed** — ~1,379 tracked `.pyc` files. Only
  `tests/__pycache__` was untracked here (it blocked every deploy). The rest is a
  separate cleanup.
- **The general feed leaned sporty** in one sample (2 of 4 items). One snapshot;
  needs more samples before engineering around it.
- **Gold Spark tool-calling is untested** — it was head-of-line blocked
  throughout. Per the owner's instruction it stays a configured fallback and will
  not be exercised while the Jetson is up.
