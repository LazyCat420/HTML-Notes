"""Replay benchmark for the html-notes router / widget pipeline.

POSTs a canonical utterance set at a deployed /session/message endpoint and
measures, per utterance: the route taken (from the `debug` frame), time to the
first `component` frame (the widget), and any error. Exists because the 2026-10-08
regression ("play smooth jazz" -> 105s agent turn -> "Shared agent runtime
unavailable") was only diagnosable by grepping docker logs; this makes the
routing split and latency observable in one run.

Usage:
    python3 bench/replay_benchmark.py [--base http://10.0.0.16:8035] [--only music,news]

Sessions are throwaway (`diag-bench-*`); widgets committed there don't touch
real canvases. Each utterance runs in its own session so follow-up targeting
can't couple runs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

import httpx

#: (label, utterance, expected_route) — expected_route is the router tier the
#: deterministic gate or classifier SHOULD pick: "tier2" (fast local build),
#: "agent" (research/deferred), or None when either is defensible.
UTTERANCES = [
    ("music_keywordless", "play smooth jazz", "tier2"),
    ("music_keyword", "play smooth jazz music", "tier2"),
    ("music_pronoun", "I want smooth jazz please", "tier2"),
    ("music_listen", "listen to some reggae", "tier2"),
    ("music_guard_news", "play the news", None),
    ("music_guard_podcast", "play a podcast", None),
    ("news_finance", "pull up bloomberg news", "tier2"),
    ("news_top", "top stories", "tier2"),
    ("video_live", "Bloomberg Live News", "tier2"),
    ("video_general", "a cookie recipe video", "tier2"),
    ("clock", "set a timer for 5 minutes", "tier2"),
    ("weather", "weather in tokyo", "tier2"),
    ("stock", "AAPL stock chart", "tier2"),
    ("apps", "show my apps", "tier2"),
    ("research", "who won the last f1 race and why", "agent"),
]

TIER2_MARKERS = ("tier2-local", "build ask")
AGENT_MARKERS = ("tier3-agent",)


def route_from_debug(dbg: dict) -> str:
    path = dbg.get("path", "")
    note = json.dumps(dbg.get("router") or {})
    if path != "agent":
        return "tier2"
    blob = f"{path} {note}"
    if any(m in blob for m in AGENT_MARKERS) or "no fast-path matched" in json.dumps(dbg):
        return "agent"
    return path or "unknown"


async def run_one(base: str, idx: int, label: str, msg: str) -> dict:
    sid = f"diag-bench-{idx}-{label}"
    t0 = time.time()
    route = first_widget = err = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(210.0, connect=5.0)) as c:
        async with c.stream(
            "POST", f"{base}/session/message",
            json={"session_id": sid, "message": msg, "current_canvas": ""},
        ) as r:
            async for line in r.aiter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    ev = json.loads(line[6:])
                except ValueError:
                    continue
                if ev.get("type") == "debug" and route is None:
                    route = route_from_debug(ev)
                if ev.get("type") == "component" and first_widget is None:
                    first_widget = time.time() - t0
                if ev.get("type") == "error":
                    err = str(ev.get("message", ""))[:160]
                    break
                if ev.get("type") == "done":
                    break
    return {
        "label": label, "msg": msg, "route": route,
        "first_widget_s": round(first_widget, 1) if first_widget else None,
        "total_s": round(time.time() - t0, 1), "error": err,
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://10.0.0.16:8035")
    ap.add_argument("--only", default=None, help="comma-separated labels")
    args = ap.parse_args()
    wanted = set(args.only.split(",")) if args.only else None

    rows = []
    for i, (label, msg, _exp) in enumerate(UTTERANCES):
        if wanted and label not in wanted:
            continue
        # Sequential on purpose: concurrent turns compete for the same local
        # vLLM/agent runtime and would measure contention, not the router.
        row = await run_one(args.base, i, label, msg)
        rows.append(row)
        flag = "" if not row["error"] else f"  ERROR: {row['error']}"
        print(f"{label:22} route={str(row['route']):6} "
              f"widget={str(row['first_widget_s']):>6}s total={row['total_s']:>6}s{flag}",
              flush=True)

    print("\nSummary")
    tier2 = [r for r in rows if r["route"] == "tier2" and not r["error"]]
    agent = [r for r in rows if r["route"] == "agent" and not r["error"]]
    errs = [r for r in rows if r["error"]]
    if tier2:
        print(f"  tier2 widget latency: min {min(r['first_widget_s'] for r in tier2 if r['first_widget_s'])}s "
              f"max {max(r['first_widget_s'] for r in tier2 if r['first_widget_s'])}s (n={len(tier2)})")
    if agent:
        print(f"  agent widget latency: max {max(r['first_widget_s'] for r in agent if r['first_widget_s'])}s (n={len(agent)})")
    print(f"  errors: {len(errs)}")
    for r in errs:
        print(f"    {r['label']}: {r['error']}")


if __name__ == "__main__":
    asyncio.run(main())
