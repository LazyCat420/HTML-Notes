"""LLM-as-judge: score a chosen video against the four-axis rubric, 0–10 each.

The judge sees the request and the FULL metadata of the pick (title, channel,
views, duration, age, verified, language) but NOT which strategy produced it, so it
can't be biased toward "the fancy one". Runs once per (query, strategy) pick.

A judge is imperfect — it can't watch the video — so it grades on what's knowable
from metadata + title, which is exactly the information the strategies themselves
had. That keeps the comparison fair: everyone is judged on the same surface.
"""
from __future__ import annotations

import re
from typing import Optional

from bench import llm

_JUDGE_SYS = (
    "You are grading how well a chosen YouTube video answers a user's request. "
    "You cannot watch it; grade on the title, channel, and metadata given — the "
    "same information a picker would have. Be strict and calibrated: 10 is a clearly "
    "ideal pick, 5 is mediocre/acceptable, 0 is wrong (off-topic, wrong language, "
    "spam, or wrong format).\n"
    "Score FOUR axes 0–10:\n"
    "  intent      — matches the request's topic AND format AND language.\n"
    "  authority   — verified/reputable channel, healthy views (not obscure spam).\n"
    "  freshness   — appropriately recent IF the request implies it; else score 8 "
    "(freshness irrelevant, don't penalize).\n"
    "  watchability— duration fits the ask, title isn't clickbait/misleading.\n"
    'Reply ONLY with JSON: {"intent":n,"authority":n,"freshness":n,'
    '"watchability":n,"overall":n,"note":"<short>"}. overall is your holistic 0–10.')


def _describe(video: dict) -> str:
    if not video:
        return "(no video was chosen)"
    dur = video.get("duration_sec")
    dur_s = f"{dur // 60}m{dur % 60:02d}s" if dur else ("LIVE" if video.get("is_live") else "unknown")
    views = video.get("views")
    views_s = "{:,}".format(views) if views is not None else "unknown"
    age_s = "%dd" % int(video["age_days"]) if video.get("age_days") is not None else "unknown"
    return (
        f"title: {video.get('title')!r}\n"
        f"channel: {video.get('channel')!r}{' (verified)' if video.get('verified') else ''}\n"
        f"views: {views_s}\n"
        f"duration: {dur_s}\n"
        f"age: {age_s}\n"
        f"live: {video.get('is_live')} | short: {video.get('is_short')}")


AXES = ("intent", "authority", "freshness", "watchability", "overall")


async def judge(request: str, lang: str, intent_notes: str, video: dict) -> dict:
    """Return {axis: float,...} + note. Missing/failed → zeros so it counts as bad."""
    if not video:
        return {a: 0.0 for a in AXES} | {"note": "no video chosen"}
    prompt = (
        f'User request: "{request}"\n'
        f"Content language expected: {lang}\n"
        f"Request implies: {intent_notes}\n\n"
        f"Chosen video:\n{_describe(video)}\n\nGrade it.")
    data = await llm.chat_json(prompt, max_tokens=220, temperature=0.0, system=_JUDGE_SYS)
    if not data:
        return {a: 0.0 for a in AXES} | {"note": "judge failed to respond"}
    out = {}
    for a in AXES:
        try:
            out[a] = max(0.0, min(10.0, float(data.get(a, 0))))
        except (TypeError, ValueError):
            out[a] = 0.0
    out["note"] = str(data.get("note", ""))[:120]
    return out
