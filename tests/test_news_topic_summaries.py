"""A topic news card summarises EVERY vetted story, from the article itself.

Live 2026-10-11 with nemotron35: "news about ai regulation" — the relevance
gate kept 3 of 8 stories, the editor wrote up 1, and the card showed one
story ("summarizes only 1 of the links"). Two model filters ran in a row and
the second one's omissions deleted stories the first had vetted. Topic cards
also summarised ~200-char provider snippets, never the article.
"""
import asyncio

from app import config_builders as cb
from app import main as m


def run(coro):
    return asyncio.run(coro)


STORIES = [{"title": f"AI rule story {i}", "url": f"https://ex.com/{i}", "image": "",
            "meta": "Reuters", "snippet": f"Provider snippet {i}", "date": ""}
           for i in range(8)]


def _wire(monkeypatch, patch_server, editor_reply, gate_keeps=(0, 1, 2)):
    calls = {"single": [], "bodies": 0}

    async def fake_news(topic, limit=6, category="", country=""):
        return [dict(s) for s in STORIES]

    async def gate(subject, negatives, items, min_keep=0, hyde=""):
        return [items[i] for i in gate_keeps if i < len(items)]

    async def bodies(items, max_items=8, max_chars=2500):
        calls["bodies"] += 1
        for it in items[:max_items]:
            it["body"] = f"Full article text for {it['title']}. " * 6
        return items

    async def llm(prompt, max_tokens=1024):
        if prompt.startswith("Summarise this news article"):
            head = prompt.split("HEADLINE: ", 1)[1].split("\n", 1)[0]
            calls["single"].append((head, "Full article text" in prompt))
            return {"summary": f"Own summary of {head}."}
        return editor_reply

    monkeypatch.setattr(m, "news_search", fake_news, raising=False)
    monkeypatch.setattr(m, "ground_query", None, raising=False)
    patch_server("filter_items_by_relevance", gate)
    patch_server("_attach_article_bodies", bodies)
    patch_server("fast_llm_json", llm)
    return calls


def test_the_editor_cannot_delete_stories_the_gate_vetted(monkeypatch, patch_server):
    lazy = {"overview": "AI rule story 0 happened.",
            "items": [{"index": 0, "title": "AI rule story 0", "summary": "Written by the editor."}]}
    calls = _wire(monkeypatch, patch_server, lazy)

    cfg = run(cb.build_news_card("news about ai regulation", finance=False, general=False))

    assert [it["title"] for it in cfg["items"]] == ["AI rule story 0", "AI rule story 1", "AI rule story 2"]
    assert cfg["items"][0]["description"] == "Written by the editor."
    # The two the editor skipped were summarised one by one, from the article.
    assert [d for d in (it["description"] for it in cfg["items"][1:])] == [
        "Own summary of AI rule story 1.", "Own summary of AI rule story 2."]
    assert all(from_body for _, from_body in calls["single"])


def test_topic_cards_read_the_articles(monkeypatch, patch_server):
    full = {"overview": "AI rule story 0 happened.",
            "items": [{"index": i, "title": f"AI rule story {i}", "summary": "w"} for i in range(3)]}
    calls = _wire(monkeypatch, patch_server, full)
    run(cb.build_news_card("news about ai regulation", finance=False, general=False))
    assert calls["bodies"] == 1
    assert calls["single"] == [], "nothing skipped, nothing to fill"


def test_an_empty_editor_pass_still_summarises_each_story(monkeypatch, patch_server):
    calls = _wire(monkeypatch, patch_server, None)
    cfg = run(cb.build_news_card("news about ai regulation", finance=False, general=False))
    assert len(cfg["items"]) == 3
    assert all(it["description"].startswith("Own summary of") for it in cfg["items"])
    assert len(calls["single"]) == 3
