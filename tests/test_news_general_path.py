"""The general news path: "top stories" must never become a keyword search.

Six defects lived on this path at once and each hid the next. The tests here
pin the ones that are invisible from a card: which query left the process, which
tiers were allowed to answer, and which URLs were fetched.
"""
import ast
import asyncio
import inspect
import pathlib
import re

import pytest

from app import config_builders as cb
from app import main as m
from app.services import search as S


def run(coro):
    """asyncio.run, not get_event_loop.

    `get_event_loop()` passed when this file ran alone and raised "There is no
    current event loop" once another module had run first — a harness failure
    that reads exactly like the code under test being broken.
    """
    return asyncio.run(coro)


# ── 1. a general ask never becomes a keyword search, at ANY tier ────────────

def test_no_tier_invents_a_query_for_an_empty_topic(monkeypatch):
    """The literal strings are gone, and the tiers that used them are skipped.

    `news_search("")` used to fall through to a scraper-service DuckDuckGo
    search for the words "news top stories" and then to a web search for "top
    news headlines". Both keyword-match roundup pages that CONTAIN the phrase —
    the exact defect that was fixed in the primary tier and left in place two
    tiers down, where it only fires in the degraded state that matters most.
    """
    called = []

    async def dead_shared(topic, limit, category="", country=""):
        called.append(("shared", topic))
        return []

    async def dead_google(topic, limit, category="", country=""):
        called.append(("google", topic))
        return []

    async def scraper(topic, limit=6):
        called.append(("scraper", topic))
        return []

    async def gdelt(topic, limit):
        called.append(("gdelt", topic))
        return []

    async def web(query, limit):
        called.append(("web", query))
        return []

    monkeypatch.setattr(S, "_shared_news_search", dead_shared)
    monkeypatch.setattr(S, "_google_news_rss", dead_google)
    monkeypatch.setattr(S, "_scraper_service_news", scraper)
    monkeypatch.setattr(S, "_gdelt_news", gdelt)
    monkeypatch.setattr(S, "web_search", web)

    assert run(S.news_search("", limit=8)) == []
    reached = [name for name, _ in called]
    assert "shared" in reached and "google" in reached
    assert "scraper" not in reached, "a general ask reached the DuckDuckGo tier"
    assert "gdelt" not in reached, "a general ask reached the GDELT keyword tier"
    assert "web" not in reached, "a general ask reached the generic web search"


def test_a_subject_ask_still_uses_every_tier(monkeypatch):
    """The skip above must be about the EMPTY topic, not about the fallbacks."""
    called = []

    async def none_shared(topic, limit, category="", country=""):
        return []

    async def none_google(topic, limit, category="", country=""):
        return []

    async def scraper(topic, limit=6):
        called.append(("scraper", topic))
        return []

    async def gdelt(topic, limit):
        called.append(("gdelt", topic))
        return []

    async def web(query, limit):
        called.append(("web", query))
        return []

    monkeypatch.setattr(S, "_shared_news_search", none_shared)
    monkeypatch.setattr(S, "_google_news_rss", none_google)
    monkeypatch.setattr(S, "_scraper_service_news", scraper)
    monkeypatch.setattr(S, "_gdelt_news", gdelt)
    monkeypatch.setattr(S, "web_search", web)

    run(S.news_search("israel hamas ceasefire", limit=6))
    assert [n for n, _ in called] == ["scraper", "gdelt", "web"]


def _search_source() -> str:
    """The real search.py text.

    NOT `inspect.getsource(S)`. app/services/search.py opens with
    `sys.modules[__name__].__dict__.update(main.__dict__)`, which copies main's
    `__file__` over its own — so inspect reads app/main.py, and this guard
    passed while both literal strings were still in the file it was meant to be
    guarding. The function's own code object knows where it really lives.
    """
    return pathlib.Path(S._scraper_service_news.__code__.co_filename).read_text()


def _code_strings() -> list:
    """Every string literal search.py can actually SEND, docstrings excluded.

    A plain substring check over the file cannot tell a query from the comment
    explaining why that query was wrong — this guard failed on the docstring
    that documents the very fix it is guarding. (The mirror of the older
    own-goal here, where `"_drop_pr_spam" in source` was satisfied by a
    comment.) So: parse, drop docstrings, and look only at what is left.
    """
    tree = ast.parse(_search_source())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings:
            out.append(node.value)
    return out


def test_the_literal_queries_are_not_in_the_source():
    """A behavioural test can be satisfied by a branch that is never taken; this
    one fails if the strings come back at all."""
    strings = " | ".join(_code_strings())
    assert "news top stories" not in strings, "the DuckDuckGo tier still invents a query"
    assert "top news headlines" not in strings, "the web tier still invents a query"


def test_this_guard_is_reading_the_right_file_and_can_still_fail():
    """Positive controls for the guard above.

    Without them, a guard that reads the wrong file — or that parses out
    everything it was meant to inspect — reports success forever. This one DID
    read the wrong file: search.py opens by copying main's __dict__ over its
    own, taking __file__ with it, so inspect pointed at app/main.py and the
    guard passed with both literals still in place.
    """
    src = _search_source()
    assert "async def _scraper_service_news" in src
    assert "async def news_search" in src
    strings = _code_strings()
    # It can see real code strings...
    assert any("news.google.com" in x for x in strings)
    # ...and it is genuinely excluding docstrings, which is the only reason the
    # phrases above may still appear in this file's prose.
    assert "news top stories" in src


# ── 2. the section reaches the provider ─────────────────────────────────────

def test_shared_search_sends_the_section_and_the_country(monkeypatch):
    sent = {}

    class FakeResp:
        status_code = 200

        @staticmethod
        def json():
            return {"items": [], "source": "editorial"}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            sent.update(json or {})
            return FakeResp()

    monkeypatch.setattr(S.httpx, "AsyncClient", FakeClient)
    run(S._shared_news_search("", 8, category="world", country="us"))
    assert sent["topic"] == ""
    assert sent["category"] == "world"
    assert sent["country"] == "us"


def test_a_subject_search_sends_no_section(monkeypatch):
    """`category` is meaningless for a keyword search and must not be sent as
    an empty string that a provider might treat as a filter."""
    sent = {}

    class FakeResp:
        status_code = 200

        @staticmethod
        def json():
            return {"items": []}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            sent.update(json or {})
            return FakeResp()

    monkeypatch.setattr(S.httpx, "AsyncClient", FakeClient)
    run(S._shared_news_search("nvidia earnings", 6))
    assert "category" not in sent


# ── 3. a Google redirect stub must never be fetched ─────────────────────────

def test_enrich_skips_google_stubs_and_complete_items(monkeypatch):
    """Two wasted fetches per card, and one of them cannot work.

    A news.google.com/rss/articles/CBMi… link does not resolve server-side and
    its og:image is Google's own logo — the same picture for every story. An
    item that already has a photo and a summary has nothing to gain either, and
    the fetch is the dominant latency term on this path (a general ask measured
    31.2s, almost all of it here).
    """
    fetched = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            fetched.append(url)
            raise RuntimeError("no network in tests")

    monkeypatch.setattr(S.httpx, "AsyncClient", FakeClient)
    items = [
        {"title": "stub", "url": "https://news.google.com/rss/articles/CBMiabc",
         "image": "", "snippet": ""},
        {"title": "complete", "url": "https://www.nytimes.com/a.html",
         "image": "https://static01.nyt.com/a.jpg",
         "snippet": "A real summary long enough to be worth keeping as it is."},
        {"title": "needs work", "url": "https://www.bbc.com/news/b",
         "image": "", "snippet": ""},
    ]
    run(S._enrich_news(items))
    assert fetched == ["https://www.bbc.com/news/b"]


# ── 4. the card ────────────────────────────────────────────────────────────

def _stub_llm(monkeypatch, payload):
    async def fake(prompt, **kw):
        return payload
    monkeypatch.setattr(m, "fast_llm_json", fake, raising=False)


def test_build_news_card_forwards_the_section_and_keeps_more_stories(monkeypatch):
    seen = {}

    async def fake_news(topic, limit=6, category="", country=""):
        seen.update(topic=topic, limit=limit, category=category, country=country)
        return [{"title": f"Story {i}", "url": f"https://ex.com/{i}", "image": "",
                 "meta": "Example", "snippet": "body", "date": "",
                 "category": "world", "consensus": 3}
                for i in range(12)]

    monkeypatch.setattr(m, "news_search", fake_news, raising=False)
    _stub_llm(monkeypatch, {"overview": "Story 0 and Story 1 happened.",
                            "items": [{"index": i, "title": f"Story {i}",
                                       "summary": "s"} for i in range(12)]})

    cfg = run(cb.build_news_card("world news", finance=False, general=True,
                                 category="world"))
    assert seen["topic"] == "", "a general ask must not send a query string"
    assert seen["category"] == "world"
    assert seen["country"] == "us"
    # The cap was 6 for every ask. A front page that shows six stories cannot
    # span the day, and the owner's complaint was a card of FOUR.
    assert len(cfg["items"]) == 10
    assert "World" in cfg["title"]


def test_a_subject_card_is_unchanged(monkeypatch):
    seen = {}

    async def fake_news(topic, limit=6, category="", country=""):
        seen.update(topic=topic, limit=limit, category=category)
        return [{"title": "NVIDIA beats", "url": "https://ex.com/1", "image": "",
                 "meta": "Reuters", "snippet": "b", "date": ""}]

    monkeypatch.setattr(m, "news_search", fake_news, raising=False)
    monkeypatch.setattr(m, "ground_query", None, raising=False)
    _stub_llm(monkeypatch, {"overview": "NVIDIA beats.",
                            "items": [{"index": 0, "title": "NVIDIA beats", "summary": "s"}]})
    run(cb.build_news_card("news about nvidia earnings", finance=True, general=False))
    assert seen["topic"], "a subject ask must still send its subject"
    assert not seen["category"]


def test_the_empty_card_does_not_say_specifically_about_top_stories(monkeypatch):
    async def none(topic, limit=6, category="", country=""):
        return []

    monkeypatch.setattr(m, "news_search", none, raising=False)
    cfg = run(cb.build_news_card("top stories", finance=False, general=True))
    answer = (cfg.get("answer") or "") + (cfg.get("subtitle") or "")
    assert "specifically about top stories" not in answer.lower()
    assert cfg["items"] == []


def test_the_editor_writes_but_does_not_select_on_a_general_ask(monkeypatch):
    """Ten sources in, ten stories out — whatever the model chose to write up.

    Measured live: the same request produced 10 items, then 6, then 4, because
    the editor silently omitted sources. A card of four standing for a whole
    day's news is the complaint this work started from, arriving from the
    summariser instead of from the provider.
    """
    async def fake_news(topic, limit=6, category="", country=""):
        return [{"title": f"Story {i}", "url": f"https://ex.com/{i}", "image": "",
                 "meta": "Reuters", "snippet": f"Provider snippet {i}", "date": "",
                 "category": "us", "consensus": 3} for i in range(10)]

    async def lazy_editor(prompt, **kw):
        # Wrote up only three of the ten.
        return {"overview": "Story 0 and Story 1 happened.",
                "items": [{"index": i, "title": f"Story {i}", "summary": "written"}
                          for i in (0, 1, 2)]}

    monkeypatch.setattr(m, "news_search", fake_news, raising=False)
    monkeypatch.setattr(m, "fast_llm_json", lazy_editor, raising=False)

    cfg = run(cb.build_news_card("top stories", finance=False, general=True))
    assert len(cfg["items"]) == 10, "the editor's omissions silently shrank the card"
    # The three it wrote keep their prose; the rest fall back to the provider's.
    assert cfg["items"][0]["description"] == "written"
    assert cfg["items"][9]["description"] == "Provider snippet 9"


def test_a_subject_ask_still_lets_the_editor_drop_off_topic_sources(monkeypatch):
    """The rule above must not disarm the one gate that needs a model.

    On a subject ask the editor's omission IS the relevance judgement — only it
    can tell a story about the subject from a story that merely mentions it.
    """
    async def fake_news(topic, limit=6, category="", country=""):
        return [{"title": f"Story {i}", "url": f"https://ex.com/{i}", "image": "",
                 "meta": "Reuters", "snippet": "s", "date": ""} for i in range(6)]

    async def picky_editor(prompt, **kw):
        return {"overview": "Story 0 happened.",
                "items": [{"index": 0, "title": "Story 0", "summary": "written"}]}

    monkeypatch.setattr(m, "news_search", fake_news, raising=False)
    monkeypatch.setattr(m, "fast_llm_json", picky_editor, raising=False)
    monkeypatch.setattr(m, "ground_query", None, raising=False)
    monkeypatch.setattr(m, "filter_items_by_relevance", None, raising=False)

    cfg = run(cb.build_news_card("news about the apple vision pro",
                                 finance=False, general=False))
    assert len(cfg["items"]) == 1


def test_the_section_survives_normalisation_and_reaches_the_badge(monkeypatch):
    """The badge is the only thing that makes a mixed front page read as one.

    Live, every badge on every card said "Top Stories" — including on the world
    and business cards. The section reached the fetch and died one function
    short of the card, in _normalise_news_item, which rebuilds the item dict
    field by field and simply had no line for it.
    """
    async def fake_news(topic, limit=6, category="", country=""):
        return [
            {"title": "A world story", "url": "https://ex.com/1", "image": "",
             "meta": "Reuters", "snippet": "s", "date": "", "category": "world"},
            {"title": "A business story", "url": "https://ex.com/2", "image": "",
             "meta": "FT", "snippet": "s", "date": "", "category": "business"},
            {"title": "A US story", "url": "https://ex.com/3", "image": "",
             "meta": "NPR", "snippet": "s", "date": "", "category": "us"},
        ]

    async def editor(prompt, **kw):
        return {"overview": "A world story and a business story happened.",
                "items": [{"index": i, "title": f"t{i}", "summary": "s"} for i in range(3)]}

    monkeypatch.setattr(m, "news_search", fake_news, raising=False)
    monkeypatch.setattr(m, "fast_llm_json", editor, raising=False)

    cfg = run(cb.build_news_card("top stories", finance=False, general=True))
    assert [it["badge"] for it in cfg["items"]] == ["World", "Business", "US"]


def test_a_subject_card_still_badges_plainly(monkeypatch):
    async def fake_news(topic, limit=6, category="", country=""):
        return [{"title": "NVIDIA beats", "url": "https://ex.com/1", "image": "",
                 "meta": "Reuters", "snippet": "s", "date": "", "category": "business"}]

    async def editor(prompt, **kw):
        return {"overview": "NVIDIA beats.",
                "items": [{"index": 0, "title": "NVIDIA beats", "summary": "s"}]}

    monkeypatch.setattr(m, "news_search", fake_news, raising=False)
    monkeypatch.setattr(m, "fast_llm_json", editor, raising=False)
    monkeypatch.setattr(m, "ground_query", None, raising=False)
    monkeypatch.setattr(m, "filter_items_by_relevance", None, raising=False)

    cfg = run(cb.build_news_card("news about the apple vision pro",
                                 finance=False, general=False))
    assert cfg["items"][0]["badge"] == "News"
