"""Three ways to turn a news ask into a set of articles. A ⊆ B ⊆ C.

The point of the bake-off is to separate two questions that were previously
answered together by "the articles are bad":

  * is the QUERY wrong (A vs B)?
  * are the RESULTS unfiltered (B vs C)?
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import app.main as m


@dataclass
class Pick:
    strategy: str
    query: str
    items: list = field(default_factory=list)
    latency_ms: int = 0
    llm_calls: int = 0
    note: str = ""


async def _fetch(query: str, limit: int = 6) -> list:
    return await m.news_search(query, limit=limit)


# ── A: what shipped before 2026-09-05 ───────────────────────────────────────

_GENERAL_A = {"whats", "what", "s", "going", "on", "happening", "up", "new",
              "current", "events", "event", "anything", "something", "interesting",
              "any", "the", "is", "are", "in", "world", "lately", "now", "right",
              "hows", "how", "things", "there", "out", "cool", "hey", "so",
              "tell", "me", "show", "give", "whatsup", "sup", "good", "today",
              "todays", "day", "days", "daily", "for", "of", "all", "top", "global",
              "us", "usa", "america", "american", "morning", "evening", "tonight",
              "feed", "stories", "story", "summary", "brief"}


def legacy_topic(message: str) -> str:
    """The exact pre-fix derivation: extract_topic (which applies
    TOPIC_STOPWORDS = MUSIC_FILLER_WORDS | widget adjectives) then _NEWSY then
    the all-general blank-out. Kept verbatim so A is a real baseline and not a
    caricature of one."""
    raw = m.extract_topic(message)
    topic = " ".join(w for w in raw.split() if w not in m._NEWSY).strip()
    if topic and all(w in _GENERAL_A for w in topic.split()):
        topic = ""
    return topic


async def strategy_legacy(message: str, **_) -> Pick:
    t = time.time()
    q = legacy_topic(message)
    items = await _fetch(q)
    return Pick("A legacy", q, items, int((time.time() - t) * 1000), 0)


# ── B: grounded subject, no gate ─────────────────────────────────────────────

async def strategy_grounded(message: str, **_) -> Pick:
    t = time.time()
    g = await m.ground_query(message)
    q = (g.get("subject") or "").strip() or m._strip_news_scaffolding(message)
    if g.get("is_general_news"):
        q = ""
    items = await _fetch(q)
    return Pick("B grounded", q, items, int((time.time() - t) * 1000), 1)


# ── C: grounded subject + PR-spam drop + relevance gate ─────────────────────

async def strategy_gated(message: str, **_) -> Pick:
    t = time.time()
    g = await m.ground_query(message)
    subject = (g.get("subject") or "").strip()
    q = subject or m._strip_news_scaffolding(message)
    if g.get("is_general_news"):
        q, subject = "", ""
    items = await _fetch(q)
    calls = 1
    note = ""
    if items:
        items = m._drop_pr_spam(items)
    if items and subject:
        vetted = await m.filter_items_by_relevance(
            subject, g.get("negatives") or [], items, min_keep=0,
            hyde=g.get("hyde") or "")
        calls += 1
        if not vetted:
            note = "all off-subject -> web escalation"
            raw = await m.web_search(f"{subject} news", 6)
            retry = [{"title": r.get("title", ""), "url": r.get("url", ""),
                      "image": "", "meta": m._host_of(r.get("url", "")),
                      "snippet": r.get("snippet", ""), "date": ""}
                     for r in (raw or [])]
            retry = m._drop_pr_spam(retry)
            if retry:
                vetted = await m.filter_items_by_relevance(
                    subject, g.get("negatives") or [], retry, min_keep=0)
                calls += 1
        items = vetted
    return Pick("C gated", q, items, int((time.time() - t) * 1000), calls, note)


# ── D: what actually ships ───────────────────────────────────────────────────

async def strategy_production(message: str, finance: bool = False, general=None, **_) -> Pick:
    """The real builder, build_news_card. A, B and C are hand-rolled parallels;
    on 2026-09-06 the bench scored 4.80/10 for C while the app's market-news
    path — which C never touched — still shipped a generic overview over an
    analyst-promo piece. Only this row measures the product."""
    import app.config_builders as cb
    t = time.time()
    cfg = await cb.build_news_card(message, finance=finance, general=general)
    items = [{"title": it.get("title", ""), "url": it.get("url", ""),
              "snippet": it.get("description", ""), "meta": it.get("meta", "")}
             for it in (cfg.get("items") or [])]
    return Pick("D production", cfg.get("title", ""), items,
                int((time.time() - t) * 1000), 3,
                note=(cfg.get("answer") or "")[:80])


STRATEGIES = (strategy_legacy, strategy_grounded, strategy_gated, strategy_production)
