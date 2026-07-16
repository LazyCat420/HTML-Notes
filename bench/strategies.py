"""The three selection strategies the benchmark compares. All share the SAME
enrichment + language handling (that's not what's under test); they differ only in
HOW the final video is chosen:

  A  heuristic   — score_videos() picks the top. No LLM. Fast, deterministic.
  B  rerank      — one LLM call reranks the enriched candidate pool. Smart, 1 call.
  C  agent       — a JSON-protocol tool loop: the LLM may issue extra searches
                   (refine query / change order / change language) before picking.

A ⊆ B ⊆ C in capability, so the table answers two clean questions: does LLM
reranking beat the heuristic, and does agentic refinement beat one-shot rerank —
and is either worth the added latency/tokens.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from app.youtube_search import (
    Intent, Video, Weights, fetch_videos, score_videos, detect_language,
)
from bench import llm


@dataclass
class Pick:
    strategy: str
    video: Optional[dict]           # the chosen Video.to_dict(), or None
    reason: str = ""
    latency_ms: int = 0
    llm_calls: int = 0
    searches: list = field(default_factory=list)  # queries the strategy issued
    pool_size: int = 0
    error: str = ""


def _fmt_candidates(vids: list[Video]) -> str:
    """Compact, signal-rich candidate table for the LLM — every axis it needs to
    judge on, one line each. Views/duration/age are the levers a title alone hides."""
    lines = []
    for i, v in enumerate(vids):
        dur = f"{v.duration_sec // 60}m{v.duration_sec % 60:02d}s" if v.duration_sec else ("LIVE" if v.is_live else "?")
        views = f"{v.views:,}" if v.views is not None else "?"
        age = f"{int(v.age_days)}d" if v.age_days is not None else "?"
        badge = " ✓verified" if v.verified else ""
        short = " [SHORT]" if v.is_short else ""
        lines.append(
            f"[{i}] id={v.video_id} | {v.title!r} — {v.channel or '?'}{badge}{short}\n"
            f"      views={views} duration={dur} age={age}")
    return "\n".join(lines)


# ── Strategy A: heuristic ────────────────────────────────────────────────────
async def strategy_heuristic(intent: Intent, pool: list[Video], w: Weights = Weights()) -> Pick:
    t0 = time.monotonic()
    ranked = score_videos(list(pool), intent, w)
    top = ranked[0] if ranked else None
    return Pick(
        strategy="A_heuristic",
        video=top.to_dict() if top else None,
        reason=f"score={top.score} {top.score_breakdown}" if top else "no candidates",
        latency_ms=int((time.monotonic() - t0) * 1000),
        pool_size=len(pool),
    )


# ── Strategy B: one-shot LLM rerank ──────────────────────────────────────────
_RERANK_SYS = (
    "You are a YouTube video picker. Choose the SINGLE best video for the user's "
    "request. Weigh four things, in priority order:\n"
    "1. INTENT MATCH — does it actually answer the request (topic, format, language)?\n"
    "2. AUTHORITY — verified channel and healthy view count beat obscure/low-view uploads.\n"
    "3. FRESHNESS — for news/'latest' asks prefer recent; otherwise ignore age.\n"
    "4. WATCHABILITY — sane duration for the ask (not a 3-hour stream for a quick "
    "recipe, not a 30-second Short when they want depth), and avoid clickbait titles.\n"
    'Reply ONLY with JSON: {"pick": <index>, "reason": "<one sentence>"}.')


async def strategy_rerank(intent: Intent, pool: list[Video]) -> Pick:
    t0 = time.monotonic()
    if not pool:
        return Pick("B_rerank", None, "no candidates", 0, 0)
    prompt = (
        f'User request: "{intent.query}" (content language: {intent.lang}'
        f'{", wants a live stream" if intent.want_live else ""}'
        f'{", wants the latest/newest" if intent.want_fresh else ""}).\n\n'
        f"Candidates:\n{_fmt_candidates(pool)}\n\n"
        "Pick the best index.")
    data = await llm.chat_json(prompt, max_tokens=200, system=_RERANK_SYS)
    idx = 0
    reason = "llm returned no valid pick; fell back to index 0"
    if data and isinstance(data.get("pick"), int) and 0 <= data["pick"] < len(pool):
        idx = data["pick"]
        reason = str(data.get("reason", ""))[:200]
    return Pick(
        strategy="B_rerank",
        video=pool[idx].to_dict(),
        reason=reason,
        latency_ms=int((time.monotonic() - t0) * 1000),
        llm_calls=1,
        pool_size=len(pool),
    )


# ── Strategy C: agentic tool loop ────────────────────────────────────────────
_AGENT_SYS = (
    "You are a YouTube research agent with a search tool. Find the SINGLE best "
    "video for the user's request, judged on intent match, authority (verified + "
    "views), freshness (only when the ask implies it), and watchability (duration "
    "fit, no clickbait).\n"
    "You may search MORE THAN ONCE to refine — change the wording, switch order to "
    "'date' for latest/news or 'live' for a stream, or change the content language "
    "code if the results are in the wrong language.\n"
    "Each turn reply ONLY with ONE JSON object, either:\n"
    '  {"action":"search","query":"<text>","order":"relevance|date|live","lang":"<code>"}\n'
    '  {"action":"pick","id":"<video_id from the results>","reason":"<one sentence>"}\n'
    "Stop and pick as soon as you have a clearly good result — do not waste searches.")


async def strategy_agent(intent: Intent, pool: list[Video], max_turns: int = 4,
                         per_search_limit: int = 8) -> Pick:
    t0 = time.monotonic()
    calls = 0
    searches: list[str] = []
    # id → Video across every search this run, so a pick by id always resolves.
    known: dict[str, Video] = {v.video_id: v for v in pool}
    transcript = (
        f'User request: "{intent.query}" (detected language: {intent.lang}'
        f'{", live stream wanted" if intent.want_live else ""}'
        f'{", latest wanted" if intent.want_fresh else ""}).\n\n'
        f"Initial results:\n{_fmt_candidates(pool)}\n")

    for _turn in range(max_turns):
        data = await llm.chat_json(transcript, max_tokens=220, system=_AGENT_SYS)
        calls += 1
        if not data:
            break
        action = data.get("action")
        if action == "pick":
            vid = data.get("id")
            chosen = known.get(vid)
            if chosen:
                return Pick("C_agent", chosen.to_dict(), str(data.get("reason", ""))[:200],
                            int((time.monotonic() - t0) * 1000), calls, searches, len(known))
            # Hallucinated id — nudge once with the valid set.
            transcript += (f'\n(You picked id="{vid}" which is not in the results. '
                           f"Valid ids: {', '.join(list(known)[:12])}. Pick one of these.)\n")
            continue
        if action == "search":
            q = str(data.get("query") or intent.query)
            order = data.get("order", "relevance")
            lang = data.get("lang") or intent.lang
            searches.append(f"{q} [{order}/{lang}]")
            new = await fetch_videos(q, limit=per_search_limit, order=order, lang=lang)
            for v in new:
                known.setdefault(v.video_id, v)
            transcript += f'\nResults for search "{q}" (order={order}, lang={lang}):\n{_fmt_candidates(new)}\n'
            continue
        break  # malformed action

    # Ran out of turns without a valid pick → best heuristic pick of everything seen.
    ranked = score_videos(list(known.values()), intent)
    top = ranked[0] if ranked else None
    return Pick("C_agent", top.to_dict() if top else None,
                "fell back to heuristic after exhausting turns" if top else "no candidates",
                int((time.monotonic() - t0) * 1000), calls, searches, len(known))
