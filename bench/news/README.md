# News relevance bake-off

Answers **"are the articles actually about what was asked?"** with a number.

Until this existed, "the news card returns random stuff / ads" was an
impression, and every attempted fix was judged by squinting at one card. That is
how the underlying defect survived so long: the query was being mangled and
nothing downstream checked the results, but each individual card looked
*plausible* because a summarising LLM wrote confident prose over whatever
arrived.

## Layout

- `queries.jsonl` — 15 asks, seeded with the failures measured on 2026-09-05.
  Each row carries `subject_notes` (what the ask actually means, for the judge)
  and `trap` (the specific way this one used to break).
- `strategies.py` — **A** legacy · **B** grounded subject · **C** grounded +
  PR-spam drop + relevance gate. A ⊆ B ⊆ C, so the table separates *"the query
  was wrong"* (A→B) from *"the results were unfiltered"* (B→C).
  `strategy_legacy` reproduces the pre-fix derivation verbatim — including
  `extract_topic`'s music stopword list — so A is a real baseline and not a
  caricature of one.
- `judge.py` — blind LLM-as-judge over the **set**, on `on_topic`, `not_ad`,
  `substance`, `overall`. It grades the set rather than one article because the
  complaint is a property of the set.
- `run.py` — runs each strategy per query, judges them **in random order** so a
  judge with positional bias cannot favour whichever strategy always goes last,
  and prints an aggregate.

## Run

```bash
cd HTML-Notes
.venv/bin/python -m bench.news.run             # all 15
.venv/bin/python -m bench.news.run --limit 4
.venv/bin/python -m bench.news.run --only A    # baseline alone
```

Needs network (the news providers) and a reachable vLLM for grounding, the gate
and the judge.

## Reading the table

**`empty` is not a footnote.** A gate can score a perfect `on_topic` by
returning nothing at all, which is why the count of empty result sets sits next
to the score. An empty set is judged as 0 rather than skipped, so a strategy
cannot improve its average by declining to answer — the exact failure mode that
"strict filtering" invites.

`p50 ms` and `calls` are the price. The YouTube bench next door concluded that
one-shot rerank captured nearly all of the agent loop's value at half the cost;
the same question is live here, and the same answer is not guaranteed.

## What it measured

First run, 3-query smoke (2026-09-05, nemotron35 judge):

| strategy | overall | on_topic | not_ad | substance | empty |
|---|---|---|---|---|---|
| A legacy | 2.67 | 3.00 | 10.00 | 3.00 | 0 |
| B grounded | 4.00 | 4.00 | 9.33 | 5.00 | 0 |
| C gated | 6.00 | 6.33 | 9.00 | 6.00 | 0 |

The A→B→C progression is the shape the fix predicted: grounding the query
recovers some relevance, and gating the results recovers more. `not_ad` starts
high because the sampled queries were not ad-magnets — `nvidia earnings` and
`fed interest rate decision` are in the full set for exactly that reason.
