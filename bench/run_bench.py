"""Three-way YouTube selection bake-off.

For every query in queries.jsonl:
  1. detect language (+ honor explicit overrides), build the shared candidate pool
  2. run strategy A (heuristic), B (LLM rerank), C (agent loop) on that pool
  3. LLM-judge each strategy's pick on the 4-axis rubric (0–10)
Then print per-query picks and an aggregate table: quality, latency, LLM calls,
and total tokens per strategy — so "is the agent worth it?" is answered by numbers.

Run:
  cd HTML-Notes
  ../scraper-service/.venv/bin/python -m bench.run_bench            # full set, live LLM
  ../scraper-service/.venv/bin/python -m bench.run_bench --limit 4  # first 4 queries
  ../scraper-service/.venv/bin/python -m bench.run_bench --no-llm   # heuristic only (no gateway)

Needs network (YouTube) and, unless --no-llm, VLLM_URL reachable.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from app.youtube_search import Intent, detect_language, clean_for_bench, fetch_videos
from bench import llm, judge
from bench.strategies import strategy_heuristic, strategy_rerank, strategy_agent

QUERIES = Path(__file__).parent / "queries.jsonl"


def load_queries(limit: int | None) -> list[dict]:
    rows = [json.loads(l) for l in QUERIES.read_text().splitlines() if l.strip()]
    return rows[:limit] if limit else rows


async def build_intent(row: dict) -> tuple[Intent, str]:
    """Shared upstream: language detect+override + order. Returns (intent, order).
    All three strategies start from the SAME intent — only selection differs."""
    msg = row["message"]
    lang, explicit = detect_language(msg)
    order = "live" if row.get("want_live") else "date" if row.get("want_fresh") else "relevance"
    query = clean_for_bench(msg, explicit_lang=explicit)
    return Intent(
        query=query, lang=lang,
        want_fresh=bool(row.get("want_fresh")), want_live=bool(row.get("want_live")),
        want_short=bool(row.get("want_short")), explicit_lang=explicit,
    ), order


def _bar(x: float, width: int = 10) -> str:
    n = int(round(x / 10 * width))
    return "█" * n + "·" * (width - n)


async def run(limit: int | None, use_llm: bool):
    rows = load_queries(limit)
    if use_llm and not await llm.gateway_up():
        print(f"⚠  LLM gateway {llm.VLLM_URL} unreachable — running heuristic only.\n")
        use_llm = False

    # agg[strategy] = {"scores":[...per axis dicts], "latency":[...], "calls":n, ...}
    agg: dict[str, dict] = {}

    def _acc(pick, jd):
        a = agg.setdefault(pick.strategy, {"overall": [], "axes": {k: [] for k in judge.AXES},
                                           "latency": [], "llm_calls": 0})
        a["latency"].append(pick.latency_ms)
        a["llm_calls"] += pick.llm_calls
        for k in judge.AXES:
            a["axes"][k].append(jd[k])
        a["overall"].append(jd["overall"])

    for i, row in enumerate(rows, 1):
        intent, order = await build_intent(row)
        pool = await fetch_videos(intent.query, limit=10, order=order, lang=intent.lang)
        print(f"\n{'='*78}\n[{i}/{len(rows)}] {row['message']!r}")
        print(f"    lang={intent.lang} explicit={intent.explicit_lang} order={order} "
              f"query={intent.query!r} pool={len(pool)}")

        picks = [await strategy_heuristic(intent, pool)]
        if use_llm:
            picks.append(await strategy_rerank(intent, pool))
            picks.append(await strategy_agent(intent, pool))

        for pick in picks:
            jd = (await judge.judge(row["message"], intent.lang, row.get("intent_notes", ""), pick.video)
                  if use_llm else {k: -1 for k in judge.AXES} | {"note": "no-llm"})
            _acc(pick, jd)
            v = pick.video or {}
            extra = ""
            if pick.searches:
                extra = f"  [{pick.llm_calls} calls, searches: {'; '.join(pick.searches)}]"
            elif pick.llm_calls:
                extra = f"  [{pick.llm_calls} call]"
            score_s = f"J={jd['overall']:.0f}" if use_llm else "J=—"
            print(f"  {pick.strategy:12} {score_s} {pick.latency_ms:5}ms  "
                  f"{(v.get('title') or '(none)')[:46]!r}{extra}")
            if use_llm and jd.get("note"):
                print(f"               ↳ {jd['note']}")

    # ── Aggregate table ──────────────────────────────────────────────────────
    print(f"\n\n{'='*78}\nAGGREGATE  ({len(rows)} queries)\n{'='*78}")
    if not use_llm:
        print("(judge disabled — quality columns unavailable; ran heuristic pick + latency only)")
    hdr = f"{'strategy':12} {'overall':>8}  {'intent':>7} {'auth':>5} {'fresh':>6} {'watch':>6}  {'p50ms':>6} {'calls':>6}"
    print(hdr + "\n" + "-" * len(hdr))
    for strat in ("A_heuristic", "B_rerank", "C_agent"):
        a = agg.get(strat)
        if not a:
            continue
        import statistics
        def avg(xs): return sum(xs) / len(xs) if xs else 0.0
        ov = avg(a["overall"])
        lat = statistics.median(a["latency"]) if a["latency"] else 0
        axv = {k: avg(a["axes"][k]) for k in judge.AXES}
        print(f"{strat:12} {ov:7.2f}  {axv['intent']:6.1f} {axv['authority']:5.1f} "
              f"{axv['freshness']:5.1f} {axv['watchability']:6.1f}  {int(lat):6} {a['llm_calls']:6}")
    if use_llm:
        print(f"\noverall bars:")
        for strat in ("A_heuristic", "B_rerank", "C_agent"):
            a = agg.get(strat)
            if a:
                ov = sum(a["overall"]) / len(a["overall"])
                print(f"  {strat:12} {_bar(ov)} {ov:.2f}/10")
        print(f"\nLLM tokens this run: prompt={llm.usage['prompt']:,} "
              f"completion={llm.usage['completion']:,} calls={llm.usage['calls']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()
    asyncio.run(run(args.limit, use_llm=not args.no_llm))
