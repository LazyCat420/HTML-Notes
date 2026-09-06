"""LLM-as-judge for a SET of news articles, blind to which strategy produced it.

Scores the set, not one article, because the failure being measured is "the card
is full of things I did not ask about" — that is a property of the set. An empty
set is scored explicitly rather than skipped: returning nothing is sometimes the
right answer (no on-topic coverage exists) and sometimes a gate that over-fired,
and the aggregate has to be able to tell those apart via coverage.
"""
from __future__ import annotations

import json
import re

import app.main as m

AXES = ("on_topic", "not_ad", "substance", "overall")

_RUBRIC = (
    "You are grading a set of news articles returned for a user's request. Be "
    "strict and calibrated. Score FOUR axes 0-10:\n"
    "  on_topic  — what fraction of the articles are genuinely ABOUT what the "
    "user asked? 10 = every article is on-subject; 5 = about half; 0 = none. An "
    "article that merely mentions the subject in passing is NOT on-topic.\n"
    "  not_ad    — 10 = no press releases, no 'N stocks to buy' listicles, no "
    "advertorial or paid placement. 0 = the set is mostly that.\n"
    "  substance — 10 = the titles/snippets carry real reported information; "
    "0 = pure headlines with no content, or navigation junk.\n"
    "  overall   — your holistic 0-10 for how well this set answers the request.\n"
    'Reply ONLY with JSON: {"on_topic":n,"not_ad":n,"substance":n,'
    '"overall":n,"note":"<short>"}'
)


def _describe(items: list) -> str:
    if not items:
        return "(the set is EMPTY — no articles were returned)"
    out = []
    for i, it in enumerate(items[:8]):
        out.append(f"[{i}] {(it.get('title') or '')[:140]}\n"
                   f"    source: {it.get('meta') or ''}\n"
                   f"    snippet: {(it.get('snippet') or '')[:180]}")
    return "\n".join(out)


async def judge(request: str, subject_notes: str, items: list) -> dict:
    """Return {axis: float} + note. A failed judge call scores 0 so it counts as
    bad rather than silently vanishing from the average."""
    if not items:
        # Scored, not skipped — but scored as "answered nothing", which is a
        # real cost even when the alternative was a set of wrong articles.
        return {a: 0.0 for a in AXES} | {"note": "empty set"}
    data = await m.fast_llm_json(
        f"{_RUBRIC}\n\nUSER REQUEST: {request!r}\n"
        f"WHAT THE REQUEST MEANS: {subject_notes}\n\n"
        f"ARTICLES RETURNED:\n{_describe(items)}",
        max_tokens=400)
    if not isinstance(data, dict):
        return {a: 0.0 for a in AXES} | {"note": "judge failed"}
    out = {}
    for a in AXES:
        try:
            out[a] = max(0.0, min(10.0, float(data.get(a, 0))))
        except (TypeError, ValueError):
            out[a] = 0.0
    out["note"] = str(data.get("note", ""))[:90]
    return out
