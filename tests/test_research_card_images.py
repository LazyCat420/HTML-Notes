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
