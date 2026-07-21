"""Video-by-time: 'newest Paul Barron Network video' must return the LATEST
upload, not the most-watched one. Covers the three bugs that defeated recency —
'newest' not triggering date mode, the blended scorer overriding date order, and
the variety picker randomising the result — plus the named-channel uploads feed.
"""
import os
os.environ.setdefault("DATABASE_URL", "data/test_notes.db")
import asyncio

import pytest

import app.main as m
from app.youtube_search import Video


# ── Recency intent vocabulary ────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "newest Paul Barron Network video",
    "latest Paul Barron video",
    "most recent bitcoin video",
    "Paul Barron video from an hour ago",
    "that clip from 42 minutes ago",
    "just posted MKBHD video",
    "what did they upload this morning",
    "any video in the past hour",
])
def test_newest_re_matches_strict_recency(text):
    assert m.NEWEST_RE.search(text.lower())


@pytest.mark.parametrize("text", [
    "cookie recipe video",
    "funny cat videos",
    "how to tie a tie",
    "lofi hip hop stream",
])
def test_newest_re_ignores_evergreen_asks(text):
    assert not m.NEWEST_RE.search(text.lower())


def test_recency_re_now_includes_newest():
    # want_dated must also fire so the softer freshness path is on even if the
    # strict channel path finds nothing.
    assert m.RECENCY_RE.search("newest paul barron network video")


# ── Channel name-match gate (precision) ──────────────────────────────────────
def test_channel_name_match_exact_and_rejects_movie():
    assert m._yt_channel_name_match("Paul Barron Network", "Paul Barron Network")
    assert m._yt_channel_name_match("linus tech tips", "Linus Tech Tips")
    # A movie subject must NOT bind to a fan channel that merely shares a word.
    assert not m._yt_channel_name_match("top gun maverick", "Top Gun")
    assert not m._yt_channel_name_match("bitcoin price today", "Bitcoin")


def test_resolve_channel_skips_single_word_subject(monkeypatch):
    """Single-word subjects are too ambiguous — no network call, straight None."""
    async def boom(*a, **k):
        raise AssertionError("must not fetch for a single-word subject")
    monkeypatch.setattr(m, "_yt_fetch_html", boom)
    assert asyncio.run(m._resolve_youtube_channel("bitcoin")) is None


# ── Strict-recency ordering bypasses the popularity scorer ───────────────────
def test_strict_recency_sorts_by_age_not_score(monkeypatch):
    # The older, high-view video would WIN the blended score; strict must still
    # put the 42-minute-old, low-view upload first.
    newest = Video(video_id="A", id="A", title="Paul Barron Network Live",
                   channel="Paul Barron Network", views=800, duration_sec=1200,
                   age_days=42 / 1440, verified=True, rank=1)
    older = Video(video_id="B", id="B", title="Paul Barron Network Analysis",
                  channel="Paul Barron Network", views=60000, duration_sec=1400,
                  age_days=3.0, verified=True, rank=0)

    async def fake_fetch(query, limit=10, order="relevance", lang="en"):
        return [older, newest]  # deliberately newest-LAST

    monkeypatch.setattr(m, "_yt_fetch_videos", fake_fetch)
    out = asyncio.run(m._search_youtube_scrape("paul barron network", limit=5,
                                               strict_recency=True))
    assert [h["video_id"] for h in out] == ["A", "B"], "newest upload must lead"


_SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <title>Paul Barron Network</title>
  <entry>
    <yt:videoId>NEW1</yt:videoId>
    <title>Newest Upload</title>
    <published>2999-01-01T00:00:00+00:00</published>
    <media:group><media:thumbnail url="http://t/new.jpg"/></media:group>
  </entry>
  <entry>
    <yt:videoId>OLD1</yt:videoId>
    <title>Older Upload</title>
    <published>2000-01-01T00:00:00+00:00</published>
    <media:group><media:thumbnail url="http://t/old.jpg"/></media:group>
  </entry>
</feed>"""


def test_channel_uploads_parses_feed_newest_first(monkeypatch):
    async def fake_html(url, timeout=12.0):
        assert "feeds/videos.xml?channel_id=UC123" in url
        return _SAMPLE_FEED
    monkeypatch.setattr(m, "_yt_fetch_html", fake_html)
    feed = asyncio.run(m._youtube_channel_uploads("UC123", limit=6))
    assert [h["video_id"] for h in feed] == ["NEW1", "OLD1"]
    assert feed[0]["channel"] == "Paul Barron Network"
    assert feed[0]["thumbnail"] == "http://t/new.jpg"
    assert feed[0]["age_days"] is not None and feed[0]["age_days"] < 0  # future-dated sample


# ── Builder: newest + named channel → feed[0], deterministic (no variety) ────
def test_video_builder_uses_channel_feed_for_newest(monkeypatch):
    async def fake_ground(msg):
        return {"retrieval_query": "Paul Barron Network"}
    async def fake_resolve(name):
        assert "paul barron" in name.lower()
        return {"channel_id": "UC123", "title": "Paul Barron Network"}
    async def fake_uploads(cid, limit=8):
        return [{"video_id": "NEW1", "id": "NEW1", "title": "Newest Upload",
                 "channel": "Paul Barron Network", "age_days": 0.02},
                {"video_id": "OLD1", "id": "OLD1", "title": "Older",
                 "channel": "Paul Barron Network", "age_days": 5.0}]

    def boom_varied(*a, **k):
        raise AssertionError("a strict-newest ask must NOT go through the variety picker")

    monkeypatch.setattr(m, "ground_query", fake_ground)
    monkeypatch.setattr(m, "_resolve_youtube_channel", fake_resolve)
    monkeypatch.setattr(m, "_youtube_channel_uploads", fake_uploads)
    monkeypatch.setattr(m, "pick_varied_video", boom_varied)
    monkeypatch.setattr(m, "_remember_current_video", lambda *a, **k: None)
    monkeypatch.setattr(m, "_shown_video_ids", lambda sid: set())

    out = asyncio.run(m.build_router_widget(
        {"type": "video", "query": "newest Paul Barron Network video"},
        "sess-vid", "newest Paul Barron Network video"))
    assert out is not None
    wtype, prefix, cfg = out
    assert wtype == "youtube_player"
    assert cfg["video_id"] == "NEW1", "must serve the latest upload, not a varied pick"
    assert cfg["candidates"] == ["OLD1"]
