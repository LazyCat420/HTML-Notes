"""build_news_card — the one news pipeline. Each test pins a property that one
of the six former builders got wrong somewhere."""
import pytest

from app import main as m
from app import config_builders as cb

SHARED = [  # the shared provider's spelling: meta/date/snippet
    {"title": "Fed holds rates steady", "url": "https://reuters.com/fed",
     "meta": "Reuters", "date": "2026-09-05", "snippet": "The Federal Reserve held rates at 5.25%.", "image": ""},
    {"title": "Nvidia falls 4% on guidance", "url": "https://cnbc.com/nvda",
     "meta": "CNBC", "date": "2026-09-05", "snippet": "Nvidia (NVDA) slid after guidance.", "image": ""},
    {"title": "5 Stocks To Buy Right Now", "url": "https://fool.com/x",
     "meta": "Motley Fool", "date": "2026-09-05", "snippet": "Our top picks.", "image": ""},
]
FINNEWS = [  # finnews' spelling: publisher/published/og_desc
    {"title": "Apple unveils new iPhone", "url": "https://bloomberg.com/aapl",
     "publisher": "Bloomberg", "published": "2026-09-05 14:00 UTC",
     "og_desc": "Apple (AAPL) introduced the iPhone 18.", "related_tickers": ["AAPL"]},
]


def _ground(subject):
    async def fake(message):
        return {"subject": subject, "intent": "informational", "retrieval_query": subject,
                "hyde": "", "negatives": [], "freshness": "", "ambiguous": False, "clarify": ""}
    return fake


def _llm(overview, n):
    async def fake(instruction, max_tokens=400):
        return {"overview": overview,
                "items": [{"index": i, "title": f"T{i}", "summary": f"S{i} says something specific."} for i in range(n)]}
    return fake


async def _none(*a, **k):
    return []


@pytest.mark.asyncio
async def test_normalises_every_provider_spelling_and_shows_the_publisher(patch_server):
    async def news(topic, limit=8, **kw): return list(SHARED[:2])
    async def fin(query="", tickers=None, limit=8): return list(FINNEWS)
    seen = {}
    async def llm(instruction, max_tokens=400):
        seen["prompt"] = instruction
        return {"overview": "Nvidia fell 4% and Apple unveiled the iPhone 18.",
                "items": [{"index": 0, "title": "Fed", "summary": "Held."},
                          {"index": 1, "title": "Nvidia", "summary": "Fell 4%."},
                          {"index": 2, "title": "Apple", "summary": "iPhone 18."}]}
    patch_server("ground_query", _ground("stock market"))
    patch_server("news_search", news); patch_server("_finnews_articles", fin)
    patch_server("fast_llm_json", llm)
    cfg = await cb.build_news_card("stock market news", finance=True, general=True)
    assert "(, )" not in seen["prompt"], "prompt headers rendered with no publisher/date"
    assert "(Bloomberg · 2026-09-05 14:00 UTC)" in seen["prompt"]
    metas = [it["meta"] for it in cfg["items"]]
    assert all(metas), f"every item must carry a publisher: {metas}"
    assert any(mt.startswith("Bloomberg") for mt in metas)


@pytest.mark.asyncio
async def test_ad_filter_runs_on_the_primary_tier(patch_server):
    """The old stock builder filtered only its Yahoo fallback."""
    async def news(topic, limit=8, **kw): return list(SHARED)   # includes the listicle
    patch_server("news_search", news); patch_server("_finnews_articles", _none)
    patch_server("fast_llm_json", _llm("The Fed held at 5.25% while Nvidia slid on guidance.", 2))
    cfg = await cb.build_news_card("stock market news", finance=True, general=True)
    assert not any("Buy Right Now" in it["title"] for it in cfg["items"])
    assert len(cfg["items"]) == 2


@pytest.mark.asyncio
async def test_all_rejected_escalates_to_web_search_instead_of_reinstating(patch_server):
    calls = {"gate": 0, "web": 0}
    async def news(topic, limit=8, **kw): return list(SHARED[:2])
    async def gate(subject, negatives, items, **kw):
        calls["gate"] += 1
        return [] if calls["gate"] == 1 else items          # first set junk, retry good
    async def web(q, n=6):
        calls["web"] += 1
        return [{"title": "Trade talks resume in Geneva", "url": "https://ap.org/t",
                 "snippet": "US and China negotiators met in Geneva."}]
    patch_server("ground_query", _ground("US China trade talks"))
    patch_server("news_search", news); patch_server("_finnews_articles", _none)
    patch_server("filter_items_by_relevance", gate); patch_server("web_search", web)
    patch_server("fast_llm_json", _llm("Negotiators met in Geneva to resume US-China trade talks.", 1))
    cfg = await cb.build_news_card("latest news on us china trade talks")
    assert calls["web"] == 1, "an all-rejected verdict must escalate, not reinstate"
    assert calls["gate"] == 2
    # The editor pass rewrites titles, so pin provenance by URL: the surviving
    # story is the web-search hit, not the rejected provider set.
    assert cfg["items"] and "ap.org" in cfg["items"][0]["url"]


@pytest.mark.asyncio
async def test_general_ask_never_grounds(patch_server):
    async def boom(message):
        raise AssertionError("ground_query ran for a general ask")
    async def news(topic, limit=8, **kw):
        assert topic == "", f"general news must fetch top headlines, got {topic!r}"
        return list(SHARED[:2])
    patch_server("ground_query", boom); patch_server("news_search", news)
    patch_server("fast_llm_json", _llm("The Fed held rates at 5.25%; Nvidia fell 4%.", 2))
    cfg = await cb.build_news_card("whats going on in the news")
    # "News: Top Stories" said the same thing twice. A section ask still gets
    # the prefix that distinguishes it ("News: World"); the front page does not
    # need one.
    assert cfg["title"] == "Top Stories"


@pytest.mark.asyncio
async def test_generic_overview_is_replaced_with_a_grounded_sentence(patch_server):
    async def news(topic, limit=8, **kw): return list(SHARED[:2])
    patch_server("news_search", news); patch_server("_finnews_articles", _none)
    patch_server("fast_llm_json", _llm(
        "Market focus centers on biotech catalysts and semiconductor rotations as earnings season progresses.", 2))
    cfg = await cb.build_news_card("stock market news", finance=True, general=True)
    assert "biotech catalysts" not in cfg["answer"], "generic filler must not ship"
    assert cfg["answer"] == "S0 says something specific."


@pytest.mark.asyncio
async def test_subtitle_is_provenance_not_the_overview(patch_server):
    async def news(topic, limit=8, **kw): return list(SHARED[:2])
    patch_server("news_search", news); patch_server("_finnews_articles", _none)
    patch_server("fast_llm_json", _llm("The Fed held at 5.25% and Nvidia fell 4%.", 2))
    cfg = await cb.build_news_card("stock market news", finance=True, general=True)
    assert cfg["subtitle"] != cfg["answer"]
    assert cfg["subtitle"].startswith("2 stories · Reuters, CNBC")


@pytest.mark.asyncio
async def test_fails_open_when_the_editor_pass_dies(patch_server):
    async def news(topic, limit=8, **kw): return list(SHARED[:2])
    patch_server("news_search", news); patch_server("_finnews_articles", _none)
    patch_server("fast_llm_json", lambda *a, **k: _none())
    cfg = await cb.build_news_card("stock market news", finance=True, general=True)
    assert len(cfg["items"]) == 2 and all(it["description"] for it in cfg["items"])
    assert cfg["answer"]


@pytest.mark.asyncio
async def test_honest_empty_card_when_nothing_survives(patch_server):
    async def gate(subject, negatives, items, **kw): return []
    async def news(topic, limit=8, **kw): return list(SHARED[:2])
    patch_server("ground_query", _ground("quantum llamas"))
    patch_server("news_search", news); patch_server("_finnews_articles", _none)
    patch_server("filter_items_by_relevance", gate); patch_server("web_search", _none)
    cfg = await cb.build_news_card("news about quantum llamas")
    assert cfg["items"] == [] and "No recent coverage" in cfg["answer"]
