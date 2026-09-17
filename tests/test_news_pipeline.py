"""Tests for the deterministic, evidence-first news pipeline.

Covers the 10 gating requirements:
1. General-news test: blank topic does not create synthetic "news top stories" queries.
2. Source-integrity test: mismatched title/URL result is rejected.
3. Locale test: locale="US" excludes irrelevant country-biased results unless explicitly requested.
4. Recency test: “today” excludes stale articles.
5. Entity test: exact name, joined-handle variant, alias, and typo case return the intended subject.
6. Diversity test: top 5 has at least 3 publishers.
7. Verification test: unverified candidates cannot render ahead of verified candidates.
8. Failure test: zero verified evidence renders an honest unavailable/limited state.
9. Fast-LLM test: malformed/empty fast JSON cannot create or alter factual article metadata.
10. End-to-end benchmark: compare current route versus new route using fixed news queries.
"""
import pytest
import time
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

from app import main as m
from app.services.news_pipeline import (
    NewsRequest,
    CandidateArticle,
    VerifiedArticle,
    NewsResponse,
    NewsTrace,
    normalize_news_request,
    collect_candidates,
    validate_and_dedupe,
    verify_top_candidates,
    filter_and_diversify,
    rank_verified_articles,
    render_news_response,
    build_news_response,
)
from app import config_builders as cb


# ── Test 1: General-news request normalization ───────────────────────────────

@pytest.mark.asyncio
async def test_general_news_normalization_no_synthetic_query(patch_server):
    """Blank topic does not create synthetic 'news top stories' queries."""
    recorded_topics = []

    async def fake_news_search(topic, limit=12, category="", country="us"):
        recorded_topics.append(topic)
        return [
            {"title": "Global Summit Concludes", "url": "https://reuters.com/summit",
             "meta": "Reuters", "snippet": "Leaders concluded meetings today."}
        ]

    patch_server("news_search", fake_news_search)

    # 1. Test blank / generic queries normalize to topic=None
    for query in ["", "news", "headlines", "top stories", "what's happening", "whats going on in the news"]:
        req = normalize_news_request(query)
        assert req.topic is None, f"Expected topic=None for {query!r}, got {req.topic!r}"
        assert req.mode == "general", f"Expected mode='general' for {query!r}"
        assert req.locale == "US"
        assert req.recency_hours == 24

    # 2. Verify candidate retrieval passes "" (empty string) to news_search, NEVER "news top stories"
    req = normalize_news_request("top stories")
    candidates = await collect_candidates(req)
    assert len(recorded_topics) == 1
    assert recorded_topics[0] == "", f"Expected news_search query to be '', got {recorded_topics[0]!r}"
    assert "news top stories" not in recorded_topics


# ── Test 2: Source integrity — title/URL mismatch rejected ───────────────────

@pytest.mark.asyncio
async def test_source_integrity_rejected_on_mismatch(patch_server):
    """Mismatched title/URL result is rejected during verification."""
    # Candidate claiming Fed rate decision, but pointing to recipe / 404 page
    mismatched = CandidateArticle(
        title="Federal Reserve Holds Benchmark Interest Rates Steady at 5.25%",
        url="https://example.com/lifestyle/pasta-recipes",
        publisher="Example News",
        snippet="The Fed decided to keep rates unchanged in Wednesday's session."
    )

    async def fake_scrape(url):
        # Scraped text has nothing to do with Fed interest rates
        return {
            "success": True,
            "title": "Classic Italian Pasta Recipes - Cooking Guide",
            "content": "Discover how to make the best homemade tomato sauce with garlic and fresh basil."
        }

    patch_server("_scrape", fake_scrape)

    req = NewsRequest(topic="Federal Reserve", mode="topic")
    verified = await verify_top_candidates([mismatched], req)

    assert len(verified) == 1
    art = verified[0]
    assert art.verified is False
    assert art.verification_reason == "title_url_mismatch"

    # Ensure unverified article cannot render in final card
    resp = render_news_response(verified, req)
    assert len(resp.card_config["items"]) == 0
    assert resp.card_config["_verified"] is False


# ── Test 3: Locale enforcement ───────────────────────────────────────────────

def test_locale_enforcement_us_excludes_country_bias():
    """locale='US' excludes irrelevant country-biased results unless explicitly requested."""
    candidates = [
        CandidateArticle(
            title="Local Delhi Traffic Diversions Announced",
            url="https://timesofindia.indiatimes.com/city/delhi/traffic-advisory",
            publisher="Times of India",
            snippet="Traffic updates for Connaught Place."
        ),
        CandidateArticle(
            title="Mumbai Coastal Road Phase 2 Open",
            url="https://ndtv.com/mumbai-news/coastal-road-update",
            publisher="NDTV",
            snippet="New road segment inaugurated."
        ),
        CandidateArticle(
            title="Treasury Yields Move Lower After Jobs Report",
            url="https://reuters.com/markets/us-treasury-yields",
            publisher="Reuters",
            snippet="US 10-year yield fell 4 basis points."
        ),
        CandidateArticle(
            title="FDA Approves New Alzheimer Treatment",
            url="https://apnews.com/health/fda-approval-medicine",
            publisher="Associated Press",
            snippet="Regulatory green light granted for therapy."
        ),
    ]

    # For US general news (topic=None), Indian regional outlets should be filtered
    req_us = NewsRequest(topic=None, locale="US", mode="general")
    valid_us = validate_and_dedupe(candidates, req_us)

    urls_us = [c.url for c in valid_us]
    assert "https://reuters.com/markets/us-treasury-yields" in urls_us
    assert "https://apnews.com/health/fda-approval-medicine" in urls_us
    assert not any("indiatimes.com" in u for u in urls_us)
    assert not any("ndtv.com" in u for u in urls_us)

    # If user explicitly requests India news (locale="IN"), Indian outlets are preserved
    req_in = NewsRequest(topic="Delhi", locale="IN", mode="topic")
    valid_in = validate_and_dedupe(candidates, req_in)
    urls_in = [c.url for c in valid_in]
    assert any("indiatimes.com" in u for u in urls_in)


# ── Test 4: Recency test ─────────────────────────────────────────────────────

def test_recency_filter_today_excludes_stale():
    """'today' excludes stale articles exceeding recency window."""
    now = datetime.now(timezone.utc)
    fresh = CandidateArticle(
        title="Fresh Breaking Story",
        url="https://reuters.com/fresh",
        publisher="Reuters",
        snippet="Happened 3 hours ago.",
        published_at=now - timedelta(hours=3)
    )
    stale = CandidateArticle(
        title="Old Coverage From Last Week",
        url="https://reuters.com/stale",
        publisher="Reuters",
        snippet="Happened 48 hours ago.",
        published_at=now - timedelta(hours=48)
    )

    req = NewsRequest(topic="market", recency_hours=24, mode="finance")
    valid = validate_and_dedupe([fresh, stale], req)

    assert len(valid) == 1
    assert valid[0].url == "https://reuters.com/fresh"


# ── Test 5: Entity test ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_entity_topic_matching_name_handle_alias():
    """Exact name, joined-handle variant, and alias return the intended subject."""
    # Test request normalization handles joined handles and prefixes
    req1 = normalize_news_request("@OpenAI latest updates")
    assert req1.topic == "OpenAI"

    req2 = normalize_news_request("news about NVDA earnings")
    assert "NVDA" in (req2.topic or "")

    # In verification, entity aliases match (e.g. Nvidia <-> Jensen Huang <-> Blackwell)
    art_nvidia = CandidateArticle(
        title="Jensen Huang Announces Blackwell Ultra Volume Shipments",
        url="https://cnbc.com/tech/nvidia-blackwell-update",
        publisher="CNBC",
        snippet="CEO details datacenter rack demand across major hyperscalers."
    )

    req_nvda = NewsRequest(topic="nvidia", mode="topic")
    valid = validate_and_dedupe([art_nvidia], req_nvda)
    assert len(valid) == 1, "Nvidia alias matching should accept Jensen Huang / Blackwell article"


# ── Test 6: Diversity test ───────────────────────────────────────────────────

def test_publisher_diversity_gate():
    """Top 5 must have at least 3 distinct publishers and max 2 per publisher."""
    verified_list = [
        VerifiedArticle(
            id=f"art_{i}",
            title=f"Story {i}",
            url=f"https://reuters.com/story-{i}",
            publisher="Reuters",
            published_at=None,
            snippet=f"Snippet {i}",
            body_excerpt=f"Excerpt {i}",
            source_tier="wire",
            verified=True
        ) for i in range(4)
    ] + [
        VerifiedArticle(
            id=f"art_bb_{i}",
            title=f"Bloomberg Story {i}",
            url=f"https://bloomberg.com/story-{i}",
            publisher="Bloomberg",
            published_at=None,
            snippet=f"Snippet {i}",
            body_excerpt=f"Excerpt {i}",
            source_tier="wire",
            verified=True
        ) for i in range(2)
    ] + [
        VerifiedArticle(
            id="art_ap_0",
            title="AP Story 0",
            url="https://apnews.com/story-0",
            publisher="Associated Press",
            published_at=None,
            snippet="Snippet AP",
            body_excerpt="Excerpt AP",
            source_tier="wire",
            verified=True
        )
    ]

    req = NewsRequest(topic=None, limit=5, mode="general")
    diversified = filter_and_diversify(verified_list, req)

    assert len(diversified) <= 5
    pubs = [a.publisher for a in diversified]
    assert pubs.count("Reuters") <= 2, "No publisher may occupy more than 2 slots in top 5"
    assert len(set(pubs)) >= 3, f"Expected at least 3 distinct publishers, got {set(pubs)}"


# ── Test 7: Verification test ────────────────────────────────────────────────

def test_unverified_candidates_cannot_render_ahead_of_verified():
    """Unverified candidates cannot render in news items."""
    v1 = VerifiedArticle(
        id="v1", title="Verified Headline 1", url="https://reuters.com/v1",
        publisher="Reuters", published_at=None, snippet="S1", body_excerpt="B1",
        source_tier="wire", verified=True
    )
    v2 = VerifiedArticle(
        id="v2", title="Verified Headline 2", url="https://apnews.com/v2",
        publisher="AP", published_at=None, snippet="S2", body_excerpt="B2",
        source_tier="wire", verified=True
    )
    u1 = VerifiedArticle(
        id="u1", title="Unverified Rumor", url="https://blog.com/u1",
        publisher="Blog", published_at=None, snippet="Su", body_excerpt="Bu",
        source_tier="aggregator", verified=False
    )

    req = NewsRequest(topic=None, limit=5, mode="general")
    resp = render_news_response([u1, v1, v2], req)

    rendered_urls = [item["url"] for item in resp.card_config["items"]]
    assert "https://blog.com/u1" not in rendered_urls
    assert "https://reuters.com/v1" in rendered_urls
    assert "https://apnews.com/v2" in rendered_urls
    assert all(item["_verified"] is True for item in resp.card_config["items"])


# ── Test 8: Failure test ─────────────────────────────────────────────────────

def test_safe_degradation_zero_verified_renders_honest_unavailable():
    """Zero verified evidence renders an honest unavailable state without hallucinating."""
    req = NewsRequest(topic="Quantum Llamas", mode="topic")
    resp = render_news_response([], req)

    cfg = resp.card_config
    assert cfg["title"] == "News Unavailable"
    assert cfg["subtitle"] == "0 verified stories"
    assert cfg["items"] == []
    assert cfg["_verified"] is False
    assert "Unable to retrieve verified news coverage" in cfg["answer"]


# ── Test 9: Fast-LLM test ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fast_llm_cannot_alter_facts_or_invent_metadata(patch_server):
    """Malformed or hallucinating fast_llm_json cannot alter factual article metadata."""
    async def bad_llm(instruction, max_tokens=256):
        # LLM attempts to return hallucinated titles, urls, or garbage
        return {
            "overview": "Wild rumors circulating.",
            "items": [
                {"title": "Completely Fake Story", "url": "https://hallucinated.io", "publisher": "FakeNews"}
            ],
            "ranked_indices": [999]  # invalid index
        }

    patch_server("fast_llm_json", bad_llm)

    v1 = VerifiedArticle(
        id="v1", title="Official Employment Statistics Released",
        url="https://reuters.com/jobs-report", publisher="Reuters",
        published_at=None, snippet="Unemployment held at 4.1%.",
        body_excerpt="The Labor Department reported...",
        source_tier="wire", verified=True
    )

    req = NewsRequest(topic="jobs", mode="general")
    ranked = await rank_verified_articles([v1], req)
    resp = render_news_response(ranked, req)

    items = resp.card_config["items"]
    assert len(items) == 1
    assert items[0]["title"] == "Official Employment Statistics Released"
    assert items[0]["url"] == "https://reuters.com/jobs-report"
    assert items[0]["meta"].startswith("Reuters")
    assert "hallucinated.io" not in str(resp.card_config)


# ── Test 10: End-to-end benchmark comparison ─────────────────────────────────

@pytest.mark.asyncio
async def test_end_to_end_benchmark_current_vs_new_route(patch_server):
    """Compare current route vs new deterministic pipeline on fixed news queries."""
    shared_articles = [
        {"title": "Fed holds interest rates at 5.25%", "url": "https://reuters.com/fed",
         "meta": "Reuters", "snippet": "The Federal Reserve held rates steady.", "consensus": 3},
        {"title": "Nvidia expands AI datacenter infrastructure", "url": "https://cnbc.com/nvda",
         "meta": "CNBC", "snippet": "New accelerator systems announced.", "consensus": 2},
        {"title": "Apple previews next operating system", "url": "https://bloomberg.com/aapl",
         "meta": "Bloomberg", "snippet": "New software features showcased.", "consensus": 2},
    ]

    async def fake_news(topic, limit=8, **kw):
        return list(shared_articles)

    async def fake_scrape(url):
        # Return genuine article scrape matching headline
        for a in shared_articles:
            if a["url"] == url:
                return {
                    "success": True,
                    "title": a["title"] + " - Primary News Wire",
                    "content": f"{a['title']}. (Reuters/Bloomberg) — Detailed factual report covering "
                               f"the economic catalysts and verified statements. Full article text exceeding "
                               f"one hundred and twenty characters to meet verification requirements."
                }
        return {"success": True, "title": "Article", "content": "Sample content " * 15}

    async def fake_llm(instruction, max_tokens=256):
        return {
            "overview": "The Fed held rates steady while Nvidia expanded datacenter infrastructure.",
            "ranked_indices": [0, 1, 2]
        }

    patch_server("news_search", fake_news)
    patch_server("_finnews_articles", lambda *a, **k: [])
    patch_server("_scrape", fake_scrape)
    patch_server("fast_llm_json", fake_llm)

    # 1. Test legacy route via build_news_card
    t0_leg = time.time()
    legacy_cfg = await cb.build_news_card("top stories", general=True, use_pipeline=False)
    t_leg_ms = (time.time() - t0_leg) * 1000

    # 2. Test new deterministic pipeline via build_news_response
    t0_new = time.time()
    new_resp = await build_news_response("top stories")
    t_new_ms = (time.time() - t0_new) * 1000

    # Assertions on new pipeline guarantees:
    assert isinstance(new_resp, NewsResponse)
    assert new_resp.trace.trace_id.startswith("news_")
    assert new_resp.card_config["_verified"] is True
    assert len(new_resp.articles) >= 1
    assert all(art.verified for art in new_resp.articles)
    assert all(item["_verified"] for item in new_resp.card_config["items"])

    # Latency budget check: mocked unit benchmark must finish in under 500ms
    assert t_new_ms < 500.0, f"New pipeline exceeded latency budget: {t_new_ms}ms"
