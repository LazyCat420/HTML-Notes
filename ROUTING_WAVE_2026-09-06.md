# Routing wave 2026-09-06 — the routing layer was the bug, not any one builder

Follow-up to `AUDIT_2026-09-05_RELEVANCE_AND_LATENCY.md`. That wave shipped
real fixes — latency, the music stopword list, region, top-headlines — and the
owner retested and hit three failures those fixes never touched:

| ask | what rendered | the branch it actually took |
|---|---|---|
| `hello` | **NEWS: HELLO (GREETING)** — an NYT piece on Gen-Z phone etiquette | LLM router composed `['news','stock_trending']` "lead with news + trending stocks", because the canvas held finance widgets |
| `stock market for the day please` | three widgets incl. a chart of NKE/VST/SCHD/KO/ETN | LLM router → `stock_trending` (Yahoo trending/US, "unscoped most-viewed noise") |
| `market news` | one generic sentence ("biotech catalysts and semiconductor rotations") over a 162%-upside analyst promo | `build_stock_news_config` — no gate, music stopwords, ad filter only in its Yahoo `else`, overview prompt with no grounding rules |

The structural fact: **six news-ish builders behind six regexes**, each with its
own query derivation, provider chain, filter and summariser. Fixing one per
complaint put every fix on a branch the next sentence did not take.

## What shipped (each step deployed and checked live before the next)

| step | commit | live check |
|---|---|---|
| 1 model picker | `dbae0d2` | `/models` first row `vllm/nemotron35`; embeddinggemma gone; dropdown lands on Jetson |
| 2 my own bugs | `6a38a6e` | `max(min_keep,1)` clamp — escalation had never run; dead `is_general_news` clause |
| 3 `build_news_card` | `082f143` | "market news": subtitle `4 stories · Seeking Alpha, Business Insider, WTOP`; overview names Oracle $638B / Broadcom $21.7B; 10.9s |
| 4 `classify_news_ask` | `19c6c1d` | "stock market for the day please" → `fast-path/stock-news`, 1 widget, 10.9s (was router, 3 widgets, 26.9s) |
| 5 reply verdict | `77db7e0` | "hello" → `reply`, 2.0s, no widget — also on a populated finance canvas (1.5s) |
| 6 router constraints | `f4593bd` | one widget by default; `stock_trending` only on the word; explicit empty query honoured |
| 7 golden suite + live gate | `e109975` | `scripts/golden_routing_live.py` — the acceptance gate |

### The decision tree now

```
canvas control (close/remove)         deterministic
  └─ classify_news_ask                deterministic: news / stock-news / stock-report / brief
       └─ cascade                     weather · timer · sports · video · music · map …
            └─ route_with_llm         FIRST: reply or act?  → reply: bubble, no widget
                 └─ act               ONE widget by default
                      └─ compose      only for an explicitly broad ask
                           └─ agent   multi-step research / custom builds
```

The owner's steer — *"the system should be smart enough to know whether to use
tools or not; don't force it"* — is why the reply node is a model verdict, not
a regex list of greetings, and why the agent's rule 1 no longer reads "call the
tool immediately".

## Things I had to own

- `1fa33b3` (reasoning off) **caused** the "hello" failure: the router's
  `fast_llm_json` had returned `None` for everything, so every ask deferred to
  the agent. Making it work exposed a router that composes dashboards.
- `PREFERRED_AGENT_PROVIDERS` never ran for a human: `if not req.model`, and the
  browser always sends `model`.
- Two tautological guards: `"_drop_pr_spam" in source` (a comment satisfies
  it) and a `min_keep` test that used `1`. Both replaced with AST/behavioural
  checks, and the guards for the retired regexes check *bindings*, not
  substrings — the comment explaining their retirement names them.
- `bench/news` graded a parallel pipeline (`news_search` direct). Strategy D
  now calls `build_news_card`.

## Verification

- `pytest tests/` — failing set is a strict subset of the Step 0 baseline (35);
  five previously failing tests now pass. Node suites all green.
- `tests/test_golden_routing.py` 12/12 offline; `scripts/golden_routing_live.py`
  against the container — see the Step 8 record below.
- `scripts/news_check.py` on the four screenshot asks, with the overview judged
  by `_overview_is_grounded` (must name an entity from its own sources).

## Open

- The full single-intent registry (AGENT_ROADMAP 1.2): news is structural now;
  ~20 non-news regexes remain hand-ordered.
- "what is on my screen" on an EMPTY canvas builds an `answer` card via
  `ANSWER_ASK_RE` before the router can reply. Harmless, slightly odd; the reply
  node would answer it if the cascade did not claim it first.
- `bench/news` D-row scores not yet recorded across the 18-query set.
- General news latency varies 6-31s run to run (upstream fetch/enrich), see the Step 8 record.
- The pre-existing 35 failures (widget identity, follow-up targeting, trending
  stocks — the latter hit the live Yahoo feed from a unit test).

## Step 8 record — against the deployed container (`e109975`, 2026-09-06)

```
utterance                                    expect                 got                     w      s  verdict
--------------------------------------------------------------------------------------------------------------
hello                                        fast-path/reply        fast-path/reply         0    2.2  PASS  reply='Hello! How can I help you today?'
thanks                                       fast-path/reply        fast-path/reply         0    2.0  PASS  reply="You're welcome! Let me know what you'd like to see on the dashboard."
stock market news                            fast-path/stock-news   fast-path/stock-news    1   11.0  PASS
market news                                  fast-path/stock-news   fast-path/stock-news    1    9.3  PASS
stock market for the day please              fast-path/stock-news   fast-path/stock-news    1   10.6  PASS
stock market news for the day please.        fast-path/stock-news   fast-path/stock-news    1    9.7  PASS
news about nvidia earnings                   fast-path/stock-news   fast-path/stock-news    1   11.1  PASS
whats going on in the news                   fast-path/news         fast-path/news          1   31.2  PASS
latest news on the israel hamas ceasefire    fast-path/news         fast-path/news          1    9.9  PASS
bloomberg live news                          fast-path/live         fast-path/live          1    0.8  PASS
cnn live news                                fast-path/live         fast-path/live          1    0.7  PASS
weather in tokyo                             fast-path/weather      fast-path/weather       1    1.7  PASS
close everything                             fast-path/clear        fast-path/clear         0    0.1  PASS
--------------------------------------------------------------------------------------------------------------
13/13 rows pass
```

"hello" on a pre-populated finance canvas (the context that biased the live failure), after fixing the live script's own scoring bug (a reply emits no component frame, so there is no widget delta to count):

```
hello                                        fast-path/reply        fast-path/reply         0    2.7  PASS  reply='Hello! How can I help you today?'
1/1 rows pass
```

The four screenshot asks, content-level (`scripts/news_check.py`):

```

=== 'hello'  1.8s  path=fast-path id_prefix=reply widgets=0
  reply   : Hello! How can I help you today?
  [PASS] no widget
  [PASS] reply text present

=== 'stock market news'  13.3s  path=fast-path id_prefix=stock-news widgets=1
  title   : Market News
  subtitle: 5 stories · Breitbart News Network, Reuters, Business Insider
  answer  : Wall Street declined on Friday as a surprisingly strong jobs report reinforced expectations of higher-for-longer interest rates, while Broadcom surged on upbeat AI chip guidance and Oracle maintained confidence in its cloud revenue conversion. The market also 
   - Labor Day market closures confirmed
   - New chairmen at Apple and BP highlight board shifts
   - Micro-caps touted as market's best-kept secret
   - Broadcom upgraded to strong buy on AI revenue surge
   - Stocks fell on Wall Street Friday as jobs data fuels rate fears
  [PASS] exactly 1 widget
  [PASS] news id_prefix
  [PASS] subtitle is provenance
  [PASS] subtitle != answer
  [PASS] no PR-spam signature
  [PASS] overview grounded (matches ['ai', 'apple', 'bp', 'broadcom', 'friday'])
  [PASS] latency <= 15s (brief <= 40s)

=== 'market news'  9.2s  path=fast-path id_prefix=stock-news widgets=1
  title   : Market News
  subtitle: 3 stories · Seeking Alpha, WTOP
  answer  : Oracle's $638 billion RPO, heavily indexed to OpenAI, underpins confidence in future revenue conversion, while Broadcom's Q4 guidance implies 93% sales growth with AI semiconductor revenue set to reach $21.7B, up 236% Y/Y, as stocks fell on Wall Street after a
   - Oracle's $638B RPO fuels buy thesis as OpenAI-linked revenue conversion unwinds
   - Broadcom Q4 guidance implies 93% sales growth on $21.7B AI semiconductor revenue
   - Stocks fall on Wall Street as strong jobs data fuels rate uncertainty
  [PASS] exactly 1 widget
  [PASS] news id_prefix
  [PASS] subtitle is provenance
  [PASS] subtitle != answer
  [PASS] no PR-spam signature
  [PASS] overview grounded (matches ['21.7', '236', '4', '638', '93'])
  [PASS] latency <= 15s (brief <= 40s)

=== 'stock market for the day please'  11.1s  path=fast-path id_prefix=stock-news widgets=1
  title   : Market News
  subtitle: 4 stories · Seeking Alpha, Business Insider, WTOP
  answer  : Oracle's $638 billion RPO and Broadcom's AI-driven revenue surge dominate market attention, while micro-cap stocks show resilience amid broader job market concerns.
   - Oracle's $638B RPO fuels confidence in revenue conversion
   - Micro-caps touted as market's best-kept secret amid tariff recovery
   - Broadcom Q4 guidance implies 93% sales growth on AI semiconductor boom
   - Stocks fall on strong jobs data, Treasury yields rise
  [PASS] exactly 1 widget
  [PASS] news id_prefix
  [PASS] subtitle is provenance
  [PASS] subtitle != answer
  [PASS] no PR-spam signature
  [PASS] overview grounded (matches ['638', 'ai', 'broadcom', 'oracle', 'rpo'])
  [PASS] latency <= 15s (brief <= 40s)

ALL PASS
```

Note the general ask (`whats going on in the news`) took 31.2s in this run against 6.3s earlier — same branch, same builder; the variance is upstream (top-headlines fetch + enrichment), not routing. Recorded as open.
