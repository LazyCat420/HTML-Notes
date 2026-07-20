"""Agent-written prose must have clickable links, and items must carry pictures.

Two separate regressions, both of which rendered as "the card looks fine but is
less useful than it should be", which is exactly the kind that survives a green
suite:

1. **Bare URLs were inert text.** `_md_inline` linkified `[label](url)` but had no
   autolinker, and agents write bare URLs constantly ("source: https://x.com/y").
   Markdown proper doesn't autolink either, so this looked like correct behaviour
   right up until you tried to click one.

2. **Images were a side effect of description repair.** `_ensure_data_card_quality`
   returned early unless a description gap fired, so a card with good prose and
   zero pictures was "fine" by the floor and rendered as a column of monogram
   letters.
"""
import asyncio
import re

import pytest

from app.widgets.factory import _md_inline, _render_markdown


def _hrefs(html: str) -> list:
    return re.findall(r'href="([^"]*)"', html)


def _visible(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


class TestAutolink:
    def test_bare_url_becomes_a_link(self):
        out = _md_inline("See https://example.com/page for details.")
        assert _hrefs(out) == ["https://example.com/page"]
        assert 'target="_blank"' in out and 'rel="noopener"' in out

    def test_markdown_link_still_works_and_is_not_double_linked(self):
        out = _md_inline("A [labelled](https://rei.com/hat) link")
        assert _hrefs(out) == ["https://rei.com/hat"]
        # The label, not the URL, is the anchor text.
        assert ">labelled<" in out
        assert out.count("<a ") == 1

    def test_markdown_and_bare_url_together(self):
        out = _md_inline("[lbl](https://a.com/1) then https://b.com/2")
        assert _hrefs(out) == ["https://a.com/1", "https://b.com/2"]

    def test_href_of_a_generated_anchor_is_never_relinkified(self):
        """The autolinker runs after link-building; without parking the anchors it
        would match the URL inside the href it just wrote and nest an <a> in an
        attribute."""
        out = _md_inline("[x](https://a.com/1)")
        assert "<a" not in out[out.index("href=") + 5:out.index(">", out.index("href="))]
        assert out.count("<a ") == 1

    def test_trailing_sentence_punctuation_is_not_swallowed(self):
        out = _md_inline("Go to https://a.com/b.")
        assert _hrefs(out) == ["https://a.com/b"]
        assert _visible(out).endswith(".")

    def test_balanced_parens_stay_in_the_url(self):
        """Wikipedia's ..._(genus) must survive; a paren that only closes the prose
        must not."""
        keep = _md_inline("See https://en.wikipedia.org/wiki/Capsicum_(genus) now")
        assert _hrefs(keep) == ["https://en.wikipedia.org/wiki/Capsicum_(genus)"]
        give_back = _md_inline("(see https://y.com/b).")
        assert _hrefs(give_back) == ["https://y.com/b"]

    def test_escaped_quote_entity_is_not_glued_to_the_href(self):
        """esc() turns a closing quote into &quot;. Stripping ';' as punctuation
        first would leave a mangled '&quot' in the href."""
        out = _md_inline('Quote "https://x.com/a" end')
        assert _hrefs(out) == ["https://x.com/a"]

    def test_query_string_ampersand_is_not_double_escaped(self):
        out = _md_inline("https://a.com/x?p=1&q=2")
        assert _hrefs(out) == ["https://a.com/x?p=1&amp;q=2"]
        assert "&amp;amp;" not in out

    @pytest.mark.parametrize("text", ["javascript:alert(1)", "ftp://x.com/f",
                                      "data:text/html,<b>x</b>"])
    def test_non_http_schemes_are_never_linked(self, text):
        assert "<a " not in _md_inline(text)

    def test_links_work_inside_lists_and_tables(self):
        assert _hrefs(_render_markdown("- item https://a.com/1")) == ["https://a.com/1"]
        table = _render_markdown("| a | b |\n|---|---|\n| https://c.com/2 | x |")
        assert _hrefs(table) == ["https://c.com/2"]


class TestItemImageBackfill:
    def test_favicon_is_derived_from_any_url(self):
        from app.main import _favicon_for
        assert _favicon_for("https://www.rei.com/product/1") == (
            "https://www.google.com/s2/favicons?domain=www.rei.com&sz=128")
        assert _favicon_for("not a url") == ""

    def test_items_missing_images_ignores_unlinked_and_already_imaged(self):
        from app.main import _items_missing_images
        cfg = {"items": [
            {"title": "has image", "url": "https://a.com", "image": "https://a.com/i.jpg"},
            {"title": "has thumbnail", "url": "https://b.com", "thumbnail": "https://b.com/t.jpg"},
            {"title": "needs one", "url": "https://c.com"},
            {"title": "no link at all"},
        ]}
        assert [i["title"] for i in _items_missing_images(cfg)] == ["needs one"]

    def test_backfill_falls_back_to_favicon_when_no_og_image(self, monkeypatch):
        import app.main as m

        async def fake_enrich(items, timeout=5.0):
            return None  # site blocked us: no og:image for anyone

        monkeypatch.setattr(m, "_enrich_news", fake_enrich)
        items = [{"title": "T", "url": "https://www.bbc.com/news"}]
        n = asyncio.run(m._backfill_item_images(items))
        assert n == 1
        assert items[0]["image"] == (
            "https://www.google.com/s2/favicons?domain=www.bbc.com&sz=128")

    def test_backfill_prefers_a_real_og_image_over_the_favicon(self, monkeypatch):
        import app.main as m

        async def fake_enrich(items, timeout=5.0):
            for it in items:
                it["image"] = "https://cdn.example.com/real.jpg"

        monkeypatch.setattr(m, "_enrich_news", fake_enrich)
        items = [{"title": "T", "url": "https://www.bbc.com/news"}]
        asyncio.run(m._backfill_item_images(items))
        assert items[0]["image"] == "https://cdn.example.com/real.jpg"

    def test_a_card_with_no_quality_gap_still_gets_images(self, monkeypatch):
        """The regression: good descriptions + zero pictures returned early from
        _ensure_data_card_quality and never gained an image."""
        import app.main as m

        async def fake_enrich(items, timeout=5.0):
            return None

        monkeypatch.setattr(m, "_enrich_news", fake_enrich)
        cfg = {"title": "Fine card", "answer": "Prose.", "items": [
            {"title": "A", "description": "a real summary", "url": "https://www.rei.com/"}]}
        assert m._data_card_quality_gap(cfg) == ""  # no gap: the old early-return path
        out = asyncio.run(m._ensure_data_card_quality(cfg))
        assert out["items"][0]["image"].startswith("https://www.google.com/s2/favicons")

    def test_backfill_never_overwrites_an_existing_description(self, monkeypatch):
        import app.main as m

        async def fake_enrich(items, timeout=5.0):
            for it in items:
                # _enrich_news only fills falsy fields; the probe pre-fills snippet
                # precisely so this pass can't clobber real descriptions.
                assert it["snippet"], "probe must pre-fill snippet to stay image-only"

        monkeypatch.setattr(m, "_enrich_news", fake_enrich)
        items = [{"title": "T", "description": "keep me", "url": "https://a.com"}]
        asyncio.run(m._backfill_item_images(items))
        assert items[0]["description"] == "keep me"
