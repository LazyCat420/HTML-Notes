"""News relevance bake-off. Answers "are the articles about the question?" with
a number instead of an impression.

    cd HTML-Notes
    .venv/bin/python -m bench.news.run              # all 15 queries
    .venv/bin/python -m bench.news.run --limit 4
    .venv/bin/python -m bench.news.run --only C

Needs network (news providers) and a reachable vLLM for grounding, the gate and
the judge. Every strategy is judged by the SAME judge on the SAME rubric, and
the judge is never told which strategy it is looking at.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "data/bench_news.db")

from bench.news import judge as J
from bench.news.strategies import STRATEGIES

QUERIES = Path(__file__).parent / "queries.jsonl"


def load(limit=None):
    rows = [json.loads(l) for l in QUERIES.read_text().splitlines() if l.strip()]
    return rows[:limit] if limit else rows


def _bar(x, width=10):
    return "█" * int(round(x / 10 * width)) + "·" * (width - int(round(x / 10 * width)))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only", help="run one strategy by leading letter (A/B/C)")
    args = ap.parse_args()

    rows = load(args.limit)
    strategies = [s for s in STRATEGIES
                  if not args.only or s.__name__.endswith(
                      {"A": "legacy", "B": "grounded", "C": "gated", "D": "production"}[args.only.upper()])]

    agg = {}
    for i, row in enumerate(rows, 1):
        print(f"\n{'='*80}\n[{i}/{len(rows)}] {row['message']!r}")
        if row.get("trap"):
            print(f"    trap: {row['trap']}")
        picks = []
        for fn in strategies:
            try:
                picks.append(await fn(row["message"], finance=bool(row.get("finance")),
                                      general=row.get("general")))
            except Exception as e:
                print(f"    {fn.__name__} ERROR {type(e).__name__}: {e}")
        # Judge in random order so a judge with any positional bias cannot
        # systematically favour the strategy that always goes last.
        for pick in random.sample(picks, len(picks)):
            jd = await J.judge(row["message"], row.get("subject_notes", ""), pick.items)
            a = agg.setdefault(pick.strategy,
                               {ax: [] for ax in J.AXES} | {"lat": [], "calls": 0, "empty": 0})
            for ax in J.AXES:
                a[ax].append(jd[ax])
            a["lat"].append(pick.latency_ms)
            a["calls"] += pick.llm_calls
            a["empty"] += 0 if pick.items else 1
            titles = ", ".join((it.get("title") or "")[:38] for it in pick.items[:3])
            print(f"    {pick.strategy:12s} q={pick.query[:44]!r:46s} "
                  f"n={len(pick.items)} overall={jd['overall']:.0f} "
                  f"on_topic={jd['on_topic']:.0f} {pick.note}")
            if titles:
                print(f"                 -> {titles}")

    print(f"\n\n{'='*80}\nAGGREGATE over {len(rows)} queries\n")
    print(f"{'strategy':13s} {'overall':>8s}  {'':12s} {'on_topic':>9s} "
          f"{'not_ad':>7s} {'subst':>6s} {'p50 ms':>7s} {'calls':>6s} {'empty':>6s}")
    print("-" * 88)
    for name in sorted(agg):
        a = agg[name]
        mean = lambda xs: sum(xs) / len(xs) if xs else 0.0
        lat = sorted(a["lat"])[len(a["lat"]) // 2] if a["lat"] else 0
        print(f"{name:13s} {mean(a['overall']):8.2f}  {_bar(mean(a['overall'])):12s} "
              f"{mean(a['on_topic']):9.2f} {mean(a['not_ad']):7.2f} "
              f"{mean(a['substance']):6.2f} {lat:7d} {a['calls']:6d} {a['empty']:6d}")
    print("\n'empty' counts query sets that returned nothing — read it next to "
          "on_topic:\na gate that scores well by returning nothing is not a win.")


if __name__ == "__main__":
    asyncio.run(main())
