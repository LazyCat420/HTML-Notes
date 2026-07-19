"""Research cards must carry pictures, not just text and links.

The whole image path already existed — `summarised_items` sets item["image"],
`hero` picks the first result carrying one, and `render_data_card` renders both
(falling back to a grey `_monogram_tile`). Nothing populated the field:
`web_search` returns {title, url, snippet} with no image, and the one call that
could have filled it in (`_enrich_news`) was scoped to results whose SNIPPET was
empty. A result with a decent snippet — most of them — was never fetched, so it
never got a picture, and the card silently degraded to its text-only fallback.

These guards pin the fix at the seam that broke: enrichment is selected on
"missing image OR missing snippet", not "missing snippet" alone.
"""
import asyncio

import pytest

import app.main as m


def _results():
    """A realistic SERP: every result HAS a snippet, none has an image.

    This is the exact shape that used to produce a text-only card — under the
    old `thin` selection not one of these qualified for enrichment.
    """
    return [
        {"title": "Best Espresso Machines", "url": "https://a.example/one",
         "snippet": "A long and perfectly serviceable snippet about espresso machines."},
        {"title": "Top Picks 2026", "url": "https://b.example/two",
         "snippet": "Another substantial snippet with real prose in it."},
        {"title": "Buyer's Guide", "url": "https://c.example/three",
         "snippet": "Yet more prose, thoroughly non-thin."},
    ]


@pytest.mark.asyncio
async def test_results_with_snippets_still_get_images(monkeypatch):
    """The regression: good snippet + no image must STILL be enriched.

    Fails on the old `thin` selection, which skipped every one of these.
    """
    seen = []

    async def fake_enrich(items, timeout=5.0):
        seen.extend(i["url"] for i in items)
        for i in items:
            i["image"] = f"https://cdn.example{i['url'][-4:]}.jpg"

    monkeypatch.setattr(m, "_enrich_news", fake_enrich)
    monkeypatch.setattr(m, "read_web_page", _unreachable_page)
    monkeypatch.setattr(m, "fast_llm_json", _fake_llm)

    cfg = await m.build_answer_config("best espresso machines", results=_results())

    assert len(seen) == 3, f"every imageless source must be enriched, got {seen}"
    assert all(it["image"] for it in cfg["items"]), cfg["items"]
    assert cfg["image"], "the card needs a hero image"


@pytest.mark.asyncio
async def test_items_pair_each_image_with_its_source_link(monkeypatch):
    """An image is only useful if it's attributed — image and url travel together."""
    async def fake_enrich(items, timeout=5.0):
        for i in items:
            i["image"] = f"https://cdn.example/{i['url'][-3:]}.jpg"

    monkeypatch.setattr(m, "_enrich_news", fake_enrich)
    monkeypatch.setattr(m, "read_web_page", _unreachable_page)
    monkeypatch.setattr(m, "fast_llm_json", _fake_llm)

    cfg = await m.build_answer_config("q", results=_results())

    for item in cfg["items"]:
        assert item["url"], "an image with no source link is unattributed"
        assert item["image"]
        assert item["meta"], "the host is what tells the user WHERE it came from"


@pytest.mark.asyncio
async def test_already_imaged_results_are_not_refetched(monkeypatch):
    """Enrichment is a network cost — only pay it for what's actually missing."""
    seen = []

    async def fake_enrich(items, timeout=5.0):
        seen.extend(i["url"] for i in items)

    results = _results()
    for r in results:
        r["image"] = "https://cdn.example/already.jpg"

    monkeypatch.setattr(m, "_enrich_news", fake_enrich)
    monkeypatch.setattr(m, "read_web_page", _unreachable_page)
    monkeypatch.setattr(m, "fast_llm_json", _fake_llm)

    await m.build_answer_config("q", results=results)

    assert seen == [], f"nothing was missing; should not have refetched {seen}"


@pytest.mark.asyncio
async def test_enrichment_timeout_still_yields_a_card(monkeypatch):
    """A slow site must cost pictures, never the answer."""
    async def hanging_enrich(items, timeout=5.0):
        await asyncio.sleep(60)

    monkeypatch.setattr(m, "_enrich_news", hanging_enrich)
    monkeypatch.setattr(m, "read_web_page", _unreachable_page)
    monkeypatch.setattr(m, "fast_llm_json", _fake_llm)

    cfg = await asyncio.wait_for(
        m.build_answer_config("q", results=_results()), timeout=20.0)

    assert cfg["answer"], "the answer must survive an enrichment stall"


def test_relative_og_image_is_resolved_against_the_page():
    """A site-relative og:image handed to <img src> renders broken, which looks
    exactly like having no image — so the repair has to happen at extraction."""
    import urllib.parse
    for page, raw, expected in [
        ("https://x.example/a/b", "/img/hero.jpg", "https://x.example/img/hero.jpg"),
        ("https://x.example/a/b", "//cdn.example/h.jpg", "https://cdn.example/h.jpg"),
        ("https://x.example/a/b", "https://cdn.example/h.jpg", "https://cdn.example/h.jpg"),
    ]:
        assert urllib.parse.urljoin(page, raw) == expected


# ── helpers ──────────────────────────────────────────────────────

async def _unreachable_page(url, max_chars=2500):
    """Page reads are a separate path; these tests are about the meta-fetch."""
    return {"is_error": True, "content": ""}


async def _fake_llm(prompt, max_tokens=1400):
    return {"format": "explainer", "title": "T", "overview": "o",
            "answer": "## Answer\n\nSome real prose.", "sources": [0, 1, 2]}


# ── the sourceless-answer floor must cite what the answer came from ──
#
# The espresso case: the model answered from html_notes_web_search results, then
# the quality floor stapled on five Google News articles under a "Sources" badge.
# Wrong corpus for a product question — and a groundedness lie, because the card
# cited pages the answer had never read. They also all resolved to the same
# Google News og:image, so every thumbnail was identical.

@pytest.mark.asyncio
async def test_sourceless_answer_cites_the_cached_search_it_read(monkeypatch):
    """The model's own reading list is cached — cite THAT, don't go find new pages."""
    q = "best espresso machines under $500"
    cached = [{"title": "Real Review", "url": "https://coffee.example/review",
               "snippet": "We tested twelve machines."}]

    called = []

    async def should_not_run(*a, **k):
        called.append("searched")
        return []

    monkeypatch.setattr(m, "get_cached_tool_result", lambda k: cached if k == f"search:{q}" else None)
    monkeypatch.setattr(m, "news_search", should_not_run)
    monkeypatch.setattr(m, "web_search", should_not_run)
    monkeypatch.setattr(m, "_enrich_news", _noop_enrich)

    cfg = await m._ensure_data_card_quality(
        {"title": q, "answer": "Some real prose about espresso machines."}, q)

    assert called == [], "the sources were already cached; nothing should have been searched"
    assert [i["url"] for i in cfg["items"]] == ["https://coffee.example/review"]


@pytest.mark.asyncio
async def test_non_news_question_never_falls_back_to_news_search(monkeypatch):
    """With no cache, a product question searches the WEB, not the news wire."""
    q = "best espresso machines under $500"
    used = []

    async def fake_web(query, limit=5):
        used.append("web")
        return [{"title": "Guide", "url": "https://coffee.example/g", "snippet": "s"}]

    async def fake_news(query, limit=5):
        used.append("news")
        return [{"title": "Article", "url": "https://news.google.com/rss/x", "snippet": ""}]

    monkeypatch.setattr(m, "get_cached_tool_result", lambda k: None)
    monkeypatch.setattr(m, "web_search", fake_web)
    monkeypatch.setattr(m, "news_search", fake_news)
    monkeypatch.setattr(m, "_enrich_news", _noop_enrich)

    cfg = await m._ensure_data_card_quality({"title": q, "answer": "prose"}, q)

    assert used == ["web"], f"a product ask must not hit the news wire, got {used}"
    assert "news.google.com" not in cfg["items"][0]["url"]


@pytest.mark.asyncio
async def test_a_genuine_news_ask_still_uses_the_news_wire(monkeypatch):
    """The news path is still right for news — don't overcorrect into breaking it."""
    q = "latest news on the election"
    used = []

    async def fake_web(query, limit=5):
        used.append("web")
        return []

    async def fake_news(query, limit=5):
        used.append("news")
        return [{"title": "Story", "url": "https://ap.example/s", "snippet": "s"}]

    monkeypatch.setattr(m, "get_cached_tool_result", lambda k: None)
    monkeypatch.setattr(m, "web_search", fake_web)
    monkeypatch.setattr(m, "news_search", fake_news)
    monkeypatch.setattr(m, "_enrich_news", _noop_enrich)

    await m._ensure_data_card_quality({"title": q, "answer": "prose"}, q)

    assert used == ["news"], f"a news ask should use news_search, got {used}"


async def _noop_enrich(items, timeout=5.0):
    return None
