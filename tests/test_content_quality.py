"""Tests for the content quality and scoring engine (Wallgarden-inspired).

Tests cover:
- Source reputation tracking and voting (+1, -1)
- Wallgarden-style parole burn system (6 months * 2^(strikes-1))
- Multi-axis item scoring (reputation, heuristics, all-caps, clickbait, tabloids)
- Explicit user rating overrides (+25 / -50)
- Auto-burn triggers on repeated downvotes
- Quality classification (GENUINE, TABLOID, GOSSIP, PR_SPAM, CLICKBAIT)
- Quality profile aggregation
- Integration with news item sorting and filtering
"""
import time
import pytest
from app import content_quality


@pytest.fixture(autouse=True)
def clean_quality_state(tmp_path, monkeypatch):
    """Use an isolated in-memory or temporary SQLite database for each test."""
    db_file = str(tmp_path / "test_quality.db")
    monkeypatch.setattr(content_quality, "DATABASE_URL", db_file)
    content_quality.invalidate_cache()
    content_quality.init_content_quality_tables()
    yield
    content_quality.invalidate_cache()


def test_classify_genuine_article():
    item = {
        "title": "NASA James Webb Telescope Discovers Distant Galaxy",
        "url": "https://www.nasa.gov/missions/webb/galaxy-discovery",
        "description": "Astronomers using NASA's James Webb Space Telescope have identified the oldest known galaxy.",
        "meta": "NASA",
    }
    classification = content_quality.classify_content_quality(item)
    assert classification["quality_class"] == "GENUINE"
    assert classification["penalty"] == 0.0
    assert len(classification["flags"]) == 0


def test_classify_tabloid_domain():
    item = {
        "title": "Star Spotted Looking Unrecognizable on Beach Stroll",
        "url": "https://www.dailymail.co.uk/tvshowbiz/article-12345/star-beach.html",
        "description": "The celebrity turned heads as they stepped out in Malibu.",
    }
    classification = content_quality.classify_content_quality(item)
    assert classification["quality_class"] == "TABLOID"
    assert "tabloid_source" in classification["flags"]
    assert classification["penalty"] >= 20.0


def test_classify_gossip_patterns():
    item = {
        "title": "Celebrity Romance Rumors: Cheating Scandal Erupts in Hollywood",
        "url": "https://news.example.com/entertainment/rumors",
        "description": "Drama erupts as insider reveals shocking details.",
    }
    classification = content_quality.classify_content_quality(item)
    assert "gossip_content" in classification["flags"]
    assert classification["penalty"] >= 15.0


def test_classify_all_caps_and_clickbait():
    item = {
        "title": "YOU WON'T BELIEVE WHAT HAPPENED NEXT!!!",
        "url": "https://clicky.example.com/slop",
        "description": "This shocking reveal leaves viewers stunned.",
    }
    classification = content_quality.classify_content_quality(item)
    assert "all_caps" in classification["flags"]
    assert "excessive_punctuation" in classification["flags"]
    assert "clickbait_phrasing" in classification["flags"]
    assert classification["penalty"] >= 20.0


def test_record_vote_and_reputation():
    url = "https://www.reuters.com/business/tech-merger-2026"
    title = "Tech Giant Announces Strategic Acquisition"
    publisher = "Reuters"

    # Initial score should be neutral
    rep = content_quality.get_source_reputation("reuters.com")
    assert rep["upvotes"] == 0
    assert rep["downvotes"] == 0

    # Upvote
    res = content_quality.record_vote(url=url, title=title, publisher=publisher, vote=1)
    assert res["domain"] == "reuters.com"
    assert res["upvotes"] == 1
    assert res["downvotes"] == 0
    assert res["score"] > 0

    # Upvote again
    res2 = content_quality.record_vote(url=url + "-2", title="Second Story", publisher=publisher, vote=1)
    assert res2["upvotes"] == 2

    # Scoring an item from reuters should get an affinity boost
    test_item = {"url": url, "title": title, "meta": "Reuters"}
    score = content_quality.score_content_item(test_item)
    assert score > 5.0
    assert "liked_source" in test_item.get("_quality_flags", [])


def test_burn_parole_formula():
    domain = "rumorslop.com"
    now = time.time()
    base_sentence = content_quality.BURN_PAROLE_BASE_SECONDS  # 180 days

    # Strike 1: 180 days
    content_quality.burn_source(domain, strikes=1, start_time=now)
    assert content_quality.is_source_burned(domain, now=now + 100) is True
    # After 181 days, parole expires
    assert content_quality.is_source_burned(domain, now=now + base_sentence + 86400) is False

    # Strike 2: sentence doubles (360 days)
    content_quality.burn_source(domain, strikes=2, start_time=now)
    assert content_quality.is_source_burned(domain, now=now + base_sentence + 86400) is True
    assert content_quality.is_source_burned(domain, now=now + (base_sentence * 2) + 86400) is False


def test_auto_burn_on_repeated_downvotes():
    domain = "spamtimes.net"
    url_base = "https://spamtimes.net/article"

    # Downvote 3 times
    content_quality.record_vote(url=f"{url_base}1", title="Clickbait 1", publisher="SpamTimes", vote=-1)
    content_quality.record_vote(url=f"{url_base}2", title="Clickbait 2", publisher="SpamTimes", vote=-1)
    res = content_quality.record_vote(url=f"{url_base}3", title="Clickbait 3", publisher="SpamTimes", vote=-1)

    assert res["is_burned"] is True
    assert res["burn_strikes"] == 1
    assert content_quality.is_source_burned(domain) is True


def test_direct_item_rating_override():
    url = "https://example.com/unique-article"
    item = {"url": url, "title": "Good Article on Mixed Domain"}

    # Neutral initially
    neutral_score = content_quality.score_content_item(item)

    # Vote up specifically
    content_quality.record_vote(url=url, title=item["title"], publisher="Example", vote=1)
    up_score = content_quality.score_content_item(item)
    assert up_score >= neutral_score + 25.0

    # Downvote specifically
    url2 = "https://example.com/bad-article"
    item2 = {"url": url2, "title": "Bad Article"}
    content_quality.record_vote(url=url2, title=item2["title"], publisher="Example", vote=-1)
    down_score = content_quality.score_content_item(item2)
    assert down_score <= neutral_score - 20.0


def test_rerank_and_filter_items():
    items = [
        {
            "title": "SHOCKING CELEBRITY GOSSIP YOU WON'T BELIEVE!!!",
            "url": "https://www.thesun.co.uk/gossip/123",
            "meta": "The Sun",
        },
        {
            "title": "Breakthrough in Quantum Computing Research Published",
            "url": "https://arstechnica.com/science/2026/quantum",
            "meta": "Ars Technica",
        },
        {
            "title": "Generic Technology Industry Update",
            "url": "https://techcrunch.com/2026/update",
            "meta": "TechCrunch",
        },
    ]

    # Pre-vote Ars Technica
    content_quality.record_vote(url=items[1]["url"], title=items[1]["title"], publisher="Ars Technica", vote=1)

    scored = content_quality.rank_and_filter_content_items(items)
    # Highest quality item should be first
    assert scored[0]["url"] == items[1]["url"]
    # Lowest quality item should be last
    assert scored[-1]["url"] == items[0]["url"]


def test_quality_profile():
    content_quality.record_vote("https://reuters.com/a", "Reuters A", "Reuters", 1)
    content_quality.record_vote("https://bloomberg.com/b", "Bloomberg B", "Bloomberg", 1)
    content_quality.burn_source("badtabloid.com")

    profile = content_quality.get_quality_profile()
    assert profile["total_votes"] == 2
    trusted_domains = [s["domain"] for s in profile["trusted_sources"]]
    assert "reuters.com" in trusted_domains or "bloomberg.com" in trusted_domains
    burned_domains = [s["domain"] for s in profile["burned_sources"]]
    assert "badtabloid.com" in burned_domains


def test_synthesize_multi_widget_takeaway():
    # Single or zero widgets returns None
    assert content_quality.synthesize_multi_widget_takeaway([]) is None
    assert content_quality.synthesize_multi_widget_takeaway([("w-1", "news_card", {"title": "Single"})]) is None

    # Multi-widget with news items
    placed = [
        ("w-1", "news_card", {
            "title": "US Tech News",
            "items": [
                {
                    "title": "Tech Giant Announces Open-Source Weights",
                    "description": "The release enables local deployment on consumer GPUs.",
                    "_quality_score": 12.5,
                }
            ]
        }),
        ("w-2", "news_card", {
            "title": "Global Markets",
            "items": [
                {
                    "title": "Central Bank Holds Benchmark Interest Rate Steady",
                    "description": "Policymakers cite steady inflation data across the board.",
                    "_quality_score": 25.0,
                }
            ]
        })
    ]

    takeaway = content_quality.synthesize_multi_widget_takeaway(placed, "news on tech and central bank")
    assert takeaway is not None
    assert takeaway.startswith("Here's the data. From what we pulled, this is what I think you should be focusing on:")
    # The higher quality item (25.0) should be prioritized
    assert "Central Bank Holds Benchmark Interest Rate Steady" in takeaway

    # Multi-widget with answer card overview fallback
    placed_answers = [
        ("ans-1", "answer_card", {
            "title": "Battery Breakthrough",
            "answer": "Solid-state batteries demonstrated 800-mile range in preliminary track testing.",
        }),
        ("ans-2", "answer_card", {
            "title": "EV Market Outlook",
            "answer": "Sales exceeded initial quarterly projections by twelve percent.",
        })
    ]
    ans_takeaway = content_quality.synthesize_multi_widget_takeaway(placed_answers)
    assert ans_takeaway is not None
    assert "Solid-state batteries demonstrated 800-mile range" in ans_takeaway

