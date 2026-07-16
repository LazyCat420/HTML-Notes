# YouTube selection bake-off

Benchmarks three ways to pick "the best" YouTube video for a request, on top of a
shared enrichment + multilingual layer. Answers: **is an LLM in the selection loop
worth its latency/tokens, and where?**

## Layout
- `../app/youtube_search.py` — the shared layer (used by both the bench and, once
  wired, the live app): enriched scrape (views/duration/age/verified/live/short),
  language detect + explicit override (`in Hindi`, `en español`), `hl`/`gl`-biased
  search, and the heuristic 4-axis scorer.
- `strategies.py` — **A** heuristic · **B** one-shot LLM rerank · **C** agent loop
  (may issue extra searches). A ⊆ B ⊆ C in capability.
- `judge.py` — LLM-as-judge, blind to which strategy made the pick; 0–10 on intent
  / authority / freshness / watchability.
- `queries.jsonl` — 15 queries: English, news/recency, live, Shorts, and 6
  non-English incl. one explicit English→Hindi override.
- `run_bench.py` — runs all three per query, judges, prints an aggregate table.

## Run
```bash
cd HTML-Notes
../scraper-service/.venv/bin/python -m bench.run_bench             # full, live LLM+judge
../scraper-service/.venv/bin/python -m bench.run_bench --limit 6   # first N
../scraper-service/.venv/bin/python -m bench.run_bench --no-llm    # heuristic + latency only
```
Needs network (YouTube) and, unless `--no-llm`, `VLLM_URL` reachable (default
`http://10.0.0.141:8000`; override with `VLLM_URL=` / `BENCH_MODEL=`).

## Result (15 queries, gemma-4-26B judge)
| strategy | overall | intent | auth | fresh | watch | p50 latency | LLM calls |
|---|---|---|---|---|---|---|---|
| A heuristic | 9.20 | 9.1 | 9.4 | 8.3 | 9.7 | ~0 ms | 0 |
| B rerank | 9.68 | 10.0 | 9.7 | 8.3 | 9.8 | ~1000 ms | 1/query |
| C agent | 9.79 | 9.7 | 9.7 | 8.3 | 9.9 | ~1000 ms | 1–2/query |

**Takeaways**
1. On clear queries the heuristic already ties the LLM (9–10). The LLM's ~0.5-pt
   lift is concentrated, not uniform.
2. The LLM earns its cost on two failure modes the heuristic structurally can't fix:
   - **format/intent nuance** — "nba highlights today": heuristic picked a recent
     *awards* clip (5/10); rerank picked actual game highlights (10/10).
   - **language verification** — Hindi override: heuristic picked a video whose
     Hindi-ness wasn't confirmable from metadata (7/10); rerank read the title's
     script and picked a clearly-Hindi video (10/10).
3. Agent (C) barely beats one-shot rerank (B) — extra searches rarely change the
   pick. **One-shot rerank captures ~all the value at half the latency/tokens.**

**Recommended production shape:** heuristic by default; escalate to one-shot rerank
only on "hard" queries (non-English / explicit-language, or intent-ambiguous words
like *highlights/news/best/vs*). Reserve the agent loop for live/recency asks where
a query refinement (order=`date`/`live`) actually helps.
