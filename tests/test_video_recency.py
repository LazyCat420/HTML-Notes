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


def test_channel_name_match_subject_shorter_than_title():
    """LIVE BUG: 'newest paul barron video' → subject 'paul barron' was REJECTED
    by the bidirectional gate against the real channel 'Paul Barron Network'
    (bwd 0.67 < 0.7), so the feed path never ran. Users drop trailing words —
    forward containment is the signal, backward is only a sanity floor."""
    assert m._yt_channel_name_match("paul barron", "Paul Barron Network")
    assert m._yt_channel_name_match("mkbhd", "MKBHD")          # single word exact
    assert not m._yt_channel_name_match("bitcoin", "Bitcoin Magazine")
    # Sanity floor still rejects a subject buried in a long unrelated title.
    assert not m._yt_channel_name_match("paul barron", "Paul Barron Fan Clips Daily Show")


def test_resolve_channel_single_word_exact_top_result_only(patch_server):
    """A single-word subject binds ONLY when the TOP channel result matches it
    exactly — 'fireship' → channel 'Fireship' binds; 'bitcoin' whose top channel
    is 'Bitcoin Magazine' does not."""
    html = ('{"channelRenderer":{"channelId":"UCF","junk":1,'
            '"title":{"simpleText":"Fireship"}}'
            '{"channelRenderer":{"channelId":"UCX",'
            '"title":{"simpleText":"Fireship Clips"}}')

    async def fake_html(url, timeout=12.0, scraper_fallback=True):
        return html
    patch_server("_yt_fetch_html", fake_html)
    out = asyncio.run(m._resolve_youtube_channel("fireship"))
    assert out["channel_id"] == "UCF" and out["title"] == "Fireship"

    html2 = ('{"channelRenderer":{"channelId":"UCM",'
             '"title":{"simpleText":"Bitcoin Magazine"}}')

    async def fake_html2(url, timeout=12.0):
        return html2
    patch_server("_yt_fetch_html", fake_html2)
    assert asyncio.run(m._resolve_youtube_channel("bitcoin")) is None


# ── Strict-recency ordering bypasses the popularity scorer ───────────────────
def test_strict_recency_sorts_by_age_not_score(patch_server):
    # The older, high-view video would WIN the blended score; strict must still
    # put the 42-minute-old, low-view upload first.
    newest = Video(video_id="A", id="A", title="Paul Barron Network Live",
                   channel="Paul Barron Network", views=800, duration_sec=1200,
                   age_days=42 / 1440, verified=True, rank=1)
    older = Video(video_id="B", id="B", title="Paul Barron Network Analysis",
                  channel="Paul Barron Network", views=60000, duration_sec=1400,
                  age_days=3.0, verified=True, rank=0)

    async def fake_fetch(query, limit=10, order="relevance", lang="en", window_days=None):
        return [older, newest]  # deliberately newest-LAST

    patch_server("_yt_fetch_videos", fake_fetch)
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


def test_channel_uploads_parses_feed_newest_first(patch_server):
    async def fake_html(url, timeout=12.0, scraper_fallback=True):
        if "playlist_id=UUSH" in url:
            return ""          # this channel has never posted a Short
        assert "feeds/videos.xml?channel_id=UC123" in url
        return _SAMPLE_FEED
    patch_server("_yt_fetch_html", fake_html)
    feed = asyncio.run(m._youtube_channel_uploads("UC123", limit=6))
    assert [h["video_id"] for h in feed] == ["NEW1", "OLD1"]
    assert feed[0]["channel"] == "Paul Barron Network"
    assert feed[0]["thumbnail"] == "http://t/new.jpg"
    assert feed[0]["age_days"] is not None and feed[0]["age_days"] < 0  # future-dated sample


# ── Builder: newest + named channel → feed[0], deterministic (no variety) ────
def test_video_builder_uses_channel_feed_for_newest(patch_server):
    async def fake_ground(msg):
        return {"retrieval_query": "Paul Barron Network"}
    async def fake_resolve(name):
        assert "paul barron" in name.lower()
        return {"channel_id": "UC123", "title": "Paul Barron Network"}
    async def fake_uploads(cid, limit=8, form=None):
        return [{"video_id": "NEW1", "id": "NEW1", "title": "Newest Upload",
                 "channel": "Paul Barron Network", "age_days": 0.02},
                {"video_id": "OLD1", "id": "OLD1", "title": "Older",
                 "channel": "Paul Barron Network", "age_days": 5.0}]

    def boom_varied(*a, **k):
        raise AssertionError("a strict-newest ask must NOT go through the variety picker")

    patch_server("ground_query", fake_ground)
    patch_server("_resolve_youtube_channel", fake_resolve)
    async def _fake_resolve_many(name, limit=3, evidence="plain"):
        c = await fake_resolve(name)
        return [{**c, "match": 1.0, "rank_score": 1.0, "rank": 0,
                 "handle": "", "verified": True}] if c else []
    patch_server("_resolve_youtube_channels", _fake_resolve_many)
    patch_server("_youtube_channel_uploads", fake_uploads)
    patch_server("pick_varied_video", boom_varied)
    patch_server("_remember_current_video", lambda *a, **k: None)
    patch_server("_shown_video_ids", lambda sid: set())

    out = asyncio.run(m.build_router_widget(
        {"type": "video", "query": "newest Paul Barron Network video"},
        "sess-vid", "newest Paul Barron Network video"))
    assert out is not None
    wtype, prefix, cfg = out
    assert wtype == "youtube_player"
    assert cfg["video_id"] == "NEW1", "must serve the latest upload, not a varied pick"
    assert cfg["candidates"] == ["OLD1"]


def test_newest_is_idempotent_even_when_already_shown(patch_server):
    """LIVE BUG: asking 'newest paul barron video' twice returned DIFFERENT
    videos — the unseen-first filter rotated the feed. 'Newest' is a factual ask
    with one right answer: a repeat must return the SAME latest upload."""
    async def fake_ground(msg):
        return {"retrieval_query": "paul barron"}
    async def fake_resolve(name):
        return {"channel_id": "UC123", "title": "Paul Barron Network"}
    async def fake_uploads(cid, limit=8, form=None):
        return [{"video_id": "NEW1", "id": "NEW1", "title": "Newest",
                 "channel": "Paul Barron Network", "age_days": 0.02},
                {"video_id": "OLD1", "id": "OLD1", "title": "Older",
                 "channel": "Paul Barron Network", "age_days": 5.0}]

    patch_server("ground_query", fake_ground)
    patch_server("_resolve_youtube_channel", fake_resolve)
    async def _fake_resolve_many(name, limit=3, evidence="plain"):
        c = await fake_resolve(name)
        return [{**c, "match": 1.0, "rank_score": 1.0, "rank": 0,
                 "handle": "", "verified": True}] if c else []
    patch_server("_resolve_youtube_channels", _fake_resolve_many)
    patch_server("_youtube_channel_uploads", fake_uploads)
    patch_server("_remember_current_video", lambda *a, **k: None)
    # NEW1 was already shown this session — the old code rotated to OLD1 here.
    patch_server("_shown_video_ids", lambda sid: {"NEW1"})

    for _ in range(3):
        out = asyncio.run(m.build_router_widget(
            {"type": "video", "query": "newest paul barron video"},
            "sess-repeat", "newest paul barron video"))
        assert out[2]["video_id"] == "NEW1", "repeat ask must NOT rotate the feed"


def test_newest_fallback_sorts_relevance_hits_by_age(patch_server):
    """If the channel path doesn't bind and the date search is empty, the
    relevance fallback's hits must still be age-sorted for a 'newest' ask."""
    async def fake_ground(msg):
        return {"retrieval_query": "paul barron"}
    async def fake_resolve(name):
        return None                       # no channel bound
    calls = {"n": 0}
    async def fake_search(q, limit=10, order="relevance", rerank=False,
                          strict_recency=False, freshness=None, form=None):
        calls["n"] += 1
        if order == "date":
            return []                     # date search dead → relevance fallback
        return [{"video_id": "POP", "id": "POP", "title": "Popular", "age_days": 4.0},
                {"video_id": "FRESH", "id": "FRESH", "title": "Fresh", "age_days": 0.03}]

    patch_server("ground_query", fake_ground)
    patch_server("_resolve_youtube_channel", fake_resolve)
    async def _fake_resolve_many(name, limit=3, evidence="plain"):
        c = await fake_resolve(name)
        return [{**c, "match": 1.0, "rank_score": 1.0, "rank": 0,
                 "handle": "", "verified": True}] if c else []
    patch_server("_resolve_youtube_channels", _fake_resolve_many)
    patch_server("search_youtube_videos", fake_search)
    patch_server("_remember_current_video", lambda *a, **k: None)
    patch_server("_shown_video_ids", lambda sid: set())

    out = asyncio.run(m.build_router_widget(
        {"type": "video", "query": "newest paul barron video"},
        "sess-fb", "newest paul barron video"))
    assert out[2]["video_id"] == "FRESH", "age must beat relevance order on a newest ask"


# ── Channel + date VERIFICATION (the "fox news" junk-video fix) ──────────────
# LIVE BUG: "fox news video newest about the stock market" returned a 40-view
# clip from an unrelated channel. Two defects: RECENCY_RE stripped "news" out of
# the channel-name guess (FOX News could never bind), and the date-sorted search
# fallback had no channel check and no relevance floor.

@pytest.mark.parametrize("message,subject,topic", [
    ("fox news video newest about the stock market", "fox news", "stock market"),
    ("latest sky news update about ukraine", "sky news", "ukraine"),
    ("newest paul barron network video", "paul barron network", ""),
    ("fifa news video", "fifa news", ""),
    # all-filler subject must be EMPTY, not "video" (clean_video_query's raw
    # fallback), or we scrape a channel search for the word "video".
    ("newest video about the federal reserve", "", "federal reserve"),
])
def test_split_video_subject_topic(message, subject, topic):
    assert m._split_video_subject_topic(message) == (subject, topic)


def test_topic_gate_matches_half_the_content_words():
    assert m._topic_in_title("stock market", "Stock markets rally as Dow hits record")
    assert not m._topic_in_title("stock market", "Modi speech highlights today")
    assert m._topic_in_title("", "anything at all")


def test_recency_pick_binds_channel_with_news_in_name(patch_server):
    """The channel-name guess must KEEP the word 'news' so FOX News can bind,
    and the pick must come from the verified channel's feed, topic-filtered."""
    resolved = {}
    async def fake_resolve(name):
        resolved["name"] = name
        return {"channel_id": "UCFOX", "title": "Fox News"}
    async def fake_uploads(cid, limit=8, form=None):
        return [{"video_id": "OFF1", "id": "OFF1", "title": "Watters monologue",
                 "channel": "Fox News", "age_days": 0.01},
                {"video_id": "MKT1", "id": "MKT1",
                 "title": "Stock market surges on rate news",
                 "channel": "Fox News", "age_days": 0.2}]

    patch_server("_resolve_youtube_channel", fake_resolve)
    async def _fake_resolve_many(name, limit=3, evidence="plain"):
        c = await fake_resolve(name)
        return [{**c, "match": 1.0, "rank_score": 1.0, "rank": 0,
                 "handle": "", "verified": True}] if c else []
    patch_server("_resolve_youtube_channels", _fake_resolve_many)
    patch_server("_youtube_channel_uploads", fake_uploads)
    patch_server("_remember_current_video", lambda *a, **k: None)

    cfg = asyncio.run(m._recency_video_pick(
        "fox news video newest about the stock market", "sess-fox"))
    assert resolved["name"] == "fox news", "RECENCY_RE must not eat the channel name"
    assert cfg["video_id"] == "MKT1", "must serve the newest ON-TOPIC upload"
    assert cfg["channel"] == "Fox News"


def test_recency_pick_topic_miss_uses_channel_verified_search(patch_server):
    """Feed has nothing on-topic → date-search the topic but keep ONLY hits from
    the verified channel; junk channels never get through."""
    async def fake_resolve(name):
        return {"channel_id": "UCFOX", "title": "Fox News"}
    async def fake_uploads(cid, limit=8, form=None):
        return [{"video_id": "OFF1", "id": "OFF1", "title": "Watters monologue",
                 "channel": "Fox News", "age_days": 0.01}]
    async def fake_search(q, limit=10, order="relevance", rerank=False,
                          strict_recency=False, freshness=None, form=None):
        return [
            {"video_id": "JUNK", "id": "JUNK", "title": "stock market tips hindi",
             "channel": "Random Trading Guru", "age_days": 0.005},
            {"video_id": "FOXMKT", "id": "FOXMKT",
             "title": "Stock market rally continues", "channel": "Fox News",
             "age_days": 0.5},
        ]

    patch_server("_resolve_youtube_channel", fake_resolve)
    async def _fake_resolve_many(name, limit=3, evidence="plain"):
        c = await fake_resolve(name)
        return [{**c, "match": 1.0, "rank_score": 1.0, "rank": 0,
                 "handle": "", "verified": True}] if c else []
    patch_server("_resolve_youtube_channels", _fake_resolve_many)
    patch_server("_youtube_channel_uploads", fake_uploads)
    patch_server("search_youtube_videos", fake_search)
    patch_server("_remember_current_video", lambda *a, **k: None)

    cfg = asyncio.run(m._recency_video_pick(
        "fox news video newest about the stock market", "sess-fox2"))
    assert cfg["video_id"] == "FOXMKT", "fresher junk from the wrong channel must lose"


def test_strict_recency_floor_drops_offtopic_fresh_hit():
    """Date sort is blind to subject: a fresh unrelated upload must not win
    'newest X' when on-topic hits exist."""
    from app.youtube_search import Video as V
    vids = [
        V(video_id="JUNK1234567", id="JUNK1234567",
          title="wedding dance compilation", age_days=0.001),
        V(video_id="ONTOPIC1234", id="ONTOPIC1234",
          title="federal reserve rate decision explained", age_days=0.4),
    ]
    q = "federal reserve rate decision"
    strong = [v for v in vids if m._yt_token_overlap(q, v.title or "") >= 0.45]
    assert [v.video_id for v in strong] == ["ONTOPIC1234"]


# ── parse_freshness: the unified time-constraint parser ──────────────────────
import datetime as _dt
from app.youtube_search import (parse_freshness, filter_by_age, sp_token,
                                Freshness)

_NOW = _dt.date(2026, 7, 28)


@pytest.mark.parametrize("text,window", [
    ("ai news from this week", 7.0),
    ("videos from the past week", 7.0),
    ("anything from last month", 31.0),
    ("video from yesterday about the fed", 2.0),
    ("past 3 days of crypto coverage", 3.0),
    ("a video from 2 weeks ago", 14.0),
    ("what happened today", 1.0),
    ("uploads from the last 48 hours", 2.0),
])
def test_parse_freshness_windowed(text, window):
    f = parse_freshness(text, now=_NOW)
    assert f is not None and f.window_days == window, (text, f)


@pytest.mark.parametrize("text", [
    "show me a new video about spacex",     # bare "new" — the original bug
    "newest paul barron video",
    "latest update on the war",
    "recent macro analysis",
    "fifa news video",
])
def test_parse_freshness_strict_unbounded(text):
    f = parse_freshness(text, now=_NOW)
    assert f is not None and f.window_days is None, (text, f)


@pytest.mark.parametrize("text", [
    "cookie recipe video",
    "funny cat videos",
    "how to tie a tie",
    "something to watch",
])
def test_parse_freshness_none_for_evergreen(text):
    assert parse_freshness(text, now=_NOW) is None


def test_parse_freshness_since_month_and_date():
    f = parse_freshness("bitcoin coverage since june", now=_NOW)
    assert f.window_days == float((_NOW - _dt.date(2026, 6, 1)).days)  # 57
    # A month AFTER now's month means last year's occurrence.
    f2 = parse_freshness("since september", now=_NOW)
    assert f2.window_days == float((_NOW - _dt.date(2025, 9, 1)).days)
    f3 = parse_freshness("since 2026-07-20", now=_NOW)
    assert f3.window_days == 8.0


def test_parse_freshness_new_is_never_a_hard_window():
    # "the new dune trailer" must stay findable even when the trailer is
    # months old — strict newest-first among ON-TOPIC hits, no hard drop.
    f = parse_freshness("the new dune trailer", now=_NOW)
    assert f is not None and f.window_days is None


# ── filter_by_age ────────────────────────────────────────────────────────────
def test_filter_by_age_slack_live_and_unknown():
    hits = [
        {"video_id": "IN", "age_days": 6.0, "is_live": False},
        {"video_id": "SLACK", "age_days": 10.9, "is_live": False},   # 7*1.5+0.5=11
        {"video_id": "OUT", "age_days": 40.0, "is_live": False},
        {"video_id": "LIVE", "age_days": None, "is_live": True},
        {"video_id": "UNKNOWN", "age_days": None, "is_live": False},
    ]
    kept = [h["video_id"] for h in filter_by_age(hits, 7.0)]
    assert kept == ["IN", "SLACK", "LIVE"], kept
    # No window → passthrough.
    assert len(filter_by_age(hits, None)) == 5


# ── sp_token: window → YouTube upload-date facet ─────────────────────────────
def test_sp_token_window_mapping():
    assert sp_token("date", 0.02) == "CAISAggB"        # ≤1h
    assert sp_token("date", 1.0) == "CAISAggC"         # ≤1d
    assert sp_token("date", 5.0) == "CAISAggD"         # ≤1w
    assert sp_token("date", 20.0) == "CAISAggE"        # ≤1mo
    assert sp_token("date", 200.0) == "CAISAggF"       # ≤1y
    assert sp_token("relevance", 5.0) == "EgIIAw%3D%3D"
    assert sp_token("date", None) == "CAI%253D"        # legacy bare date sort
    assert sp_token("date", 400.0) == "CAI%253D"       # beyond a year: no facet
    assert sp_token("relevance", None) is None
    assert sp_token("live", 5.0) == "EgJAAQ%253D%253D" # live ignores windows


# ── pick_best_video: deterministic + exclusion rotation ──────────────────────
def test_pick_best_video_deterministic_and_rotates():
    hits = [{"video_id": v, "id": v, "title": v} for v in ("A", "B", "C")]
    top, others = m.pick_best_video(hits, exclude_ids=set())
    assert top["video_id"] == "A" and others == ["B", "C"]
    # Same call again: deterministic, no randomness.
    assert m.pick_best_video(hits, exclude_ids=set())[0]["video_id"] == "A"
    # A was shown → newest unseen is B.
    assert m.pick_best_video(hits, exclude_ids={"A"})[0]["video_id"] == "B"
    # Everything shown → exhaustion rule: ignore exclusion, serve the best.
    assert m.pick_best_video(hits, exclude_ids={"A", "B", "C"})[0]["video_id"] == "A"
    assert m.pick_best_video([], exclude_ids=set()) == (None, [])


# ── windowed search: hard filter + stale fallback tagging ────────────────────
def test_windowed_search_filters_and_tags_stale_fallback(patch_server):
    fresh_hit = Video(video_id="FRESH123456", id="FRESH123456",
                      title="spacex starship update", age_days=2.0)
    old_hit = Video(video_id="OLD12345678", id="OLD12345678",
                    title="spacex starship update old", age_days=120.0)

    async def fake_fetch(query, limit=10, order="relevance", lang="en",
                         window_days=None):
        return [old_hit, fresh_hit]

    patch_server("_yt_fetch_videos", fake_fetch)
    out = asyncio.run(m._search_youtube_scrape(
        "spacex starship update", limit=5,
        freshness=Freshness(window_days=7.0, matched="this week")))
    assert [h["video_id"] for h in out] == ["FRESH123456"], \
        "out-of-window hit must be filtered"
    assert not out[0].get("stale_fallback")

    async def fake_fetch_all_old(query, limit=10, order="relevance", lang="en",
                                 window_days=None):
        return [old_hit]

    patch_server("_yt_fetch_videos", fake_fetch_all_old)
    out2 = asyncio.run(m._search_youtube_scrape(
        "spacex starship update", limit=5,
        freshness=Freshness(window_days=7.0, matched="this week")))
    assert out2 and all(h.get("stale_fallback") for h in out2), \
        "empty window must degrade to tagged newest-available, never to nothing"


def test_bare_new_is_strict_newest_first(patch_server):
    """The original bug: 'a new video about X' surfaced a months-old popular
    hit. Any freshness intent now means newest-first among on-topic hits."""
    newest = Video(video_id="NEWEST12345", id="NEWEST12345",
                   title="spacex launch coverage", views=500, age_days=0.5)
    popular = Video(video_id="POPULAR1234", id="POPULAR1234",
                    title="spacex launch coverage classic", views=9_000_000,
                    verified=True, age_days=120.0)

    async def fake_fetch(query, limit=10, order="relevance", lang="en",
                         window_days=None):
        return [popular, newest]

    patch_server("_yt_fetch_videos", fake_fetch)
    out = asyncio.run(m._search_youtube_scrape("new spacex launch coverage", limit=5))
    assert out[0]["video_id"] == "NEWEST12345", \
        "a 'new' ask must lead with the newest on-topic upload, not the popular one"


# ── Channel IDENTITY (the primeagen rebuild) ────────────────────────────────
# LIVE FAILURE these lock down: "newest primeagen video" served a 5-day-old clip
# from the unrelated 'The PrimeTime' while ThePrimeagen's own upload sat unseen,
# and asking for primeagen once returned a Fox News video ("prime time").
# Root causes: token-set matching gave 'primeagen' vs 'ThePrimeagen' an
# intersection of ZERO (unreachable at ANY threshold), and a single-word subject
# only ever examined the TOP candidate.
@pytest.mark.parametrize("subject,title,handle", [
    ("primeagen", "ThePrimeagen", "@ThePrimeagen"),          # the exact live failure
    ("primeagen", "ThePrimeagenHighlights", "@ThePrimeagenHighlights"),
    ("primeagen", "The PrimeTime", "@ThePrimeTimeagen"),     # sibling: handle is the ONLY link
    ("mkbhd", "Marques Brownlee", "@mkbhd"),                 # handle ≠ display name
    ("fox news", "FOX News", "@FoxNews"),
    ("paul barron", "Paul Barron Network", "@PaulBarronNetwork"),
    ("fireship", "Fireship", "@Fireship"),
])
def test_channel_match_binds_real_creator(subject, title, handle):
    assert m._yt_channel_match_score(subject, title, handle) >= m._YT_CHANNEL_MATCH_FLOOR


@pytest.mark.parametrize("subject,title,handle", [
    ("bitcoin", "Bitcoin Magazine", "@BitcoinMagazine"),     # topic word, not a creator
    ("bitcoin", "Bitcoin News Today", "@BitcoinNewsToday"),
    ("linus", "Linus Tech Tips", "@LinusTechTips"),          # prefix ≠ identity
    ("top gun maverick", "Top Gun", "@TopGunFan"),
    ("theo", "Theo - t3.gg", "@t3dotgg"),
    ("primeagen", "TheStandupPod", "@TheStandupPod"),
    ("paul barron", "Paul Barron Fan Clips Daily Show", "@PBFanClips"),  # impersonator
])
def test_channel_match_rejects_impostors_and_topics(subject, title, handle):
    assert m._yt_channel_match_score(subject, title, handle) < m._YT_CHANNEL_MATCH_FLOOR


def test_handle_outranks_a_title_squatter():
    """A squatter can title itself 'primeagen' exactly; only the handle is
    unique. The real channel must still win, or 'newest X' serves the squatter."""
    real = m._yt_channel_match_score("primeagen", "ThePrimeagen", "@ThePrimeagen")
    squat = m._yt_channel_match_score("primeagen", "primeagen", "@AgenW.")
    assert real > squat


def test_parse_channel_candidates_keeps_fields_together():
    """A flat regex across the document paired one channel's id with a LATER
    channel's title. Blocks must be split on the renderer boundary first."""
    html = ('{"channelRenderer":{"channelId":"UC_A","title":{"simpleText":"Alpha"},'
            '"canonicalBaseUrl":"/@alpha","ownerBadges":["BADGE_STYLE_TYPE_VERIFIED"]}'
            '{"channelRenderer":{"channelId":"UC_B","title":{"simpleText":"Beta"},'
            '"canonicalBaseUrl":"/@beta"}')
    cands = m._parse_channel_candidates(html)
    assert [(c["channel_id"], c["title"], c["handle"]) for c in cands] == [
        ("UC_A", "Alpha", "@alpha"), ("UC_B", "Beta", "@beta")]
    assert cands[0]["verified"] and not cands[1]["verified"]
    assert [c["rank"] for c in cands] == [0, 1]


def test_resolver_scores_every_candidate_not_just_the_first(patch_server):
    """The [:1] slice for single-word subjects made the correct channel
    unreachable: YouTube ranks 'The PrimeTime' ABOVE 'ThePrimeagen' for
    'primeagen', so the real one was never even considered."""
    html = ('{"channelRenderer":{"channelId":"UC_WRONG","title":{"simpleText":"The PrimeTime"},'
            '"canonicalBaseUrl":"/@SomethingElse"}'
            '{"channelRenderer":{"channelId":"UC_REAL","title":{"simpleText":"ThePrimeagen"},'
            '"canonicalBaseUrl":"/@ThePrimeagen","ownerBadges":["BADGE_STYLE_TYPE_VERIFIED"]}')

    async def fake_html(url, timeout=12.0, scraper_fallback=True):
        return html
    patch_server("_yt_fetch_html", fake_html)
    out = asyncio.run(m._resolve_youtube_channels("primeagen"))
    assert out and out[0]["channel_id"] == "UC_REAL", \
        "the real channel must win even when YouTube ranks an impostor above it"


def test_creator_evidence_separates_topics_from_names():
    assert m._creator_evidence("cookie recipe", "a new cookie recipe video") == "weak"
    assert m._creator_evidence("stock market", "new stock market video") == "weak"
    assert m._creator_evidence("primeagen", "newest primeagen video") == "plain"
    assert m._creator_evidence("primeagen", "a video from primeagen") == "explicit"
    assert m._creator_evidence("mkbhd", "mkbhd's latest") == "explicit"


def test_multi_channel_merge_picks_newest_across_siblings(patch_server):
    """THE bug: the creator's newest upload was on a SIBLING channel. Binding one
    channel served an 11-day-old video while a 3-hour-old one existed."""
    async def fake_resolve_many(name, limit=3, evidence="plain"):
        return [{"channel_id": "UC_MAIN", "title": "ThePrimeagen", "handle": "@ThePrimeagen",
                 "match": 0.9, "rank_score": 1.2, "rank": 0, "verified": True},
                {"channel_id": "UC_SIB", "title": "The PrimeTime", "handle": "@ThePrimeTimeagen",
                 "match": 0.65, "rank_score": 0.98, "rank": 1, "verified": True}]

    async def fake_uploads(cid, limit=8, form=None):
        if cid == "UC_MAIN":
            return [{"video_id": "OLD11DAYS", "id": "OLD11DAYS", "title": "I like Game Programming",
                     "channel": "ThePrimeagen", "age_days": 11.2}]
        return [{"video_id": "FRESH3HRS", "id": "FRESH3HRS", "title": "Dont Smile",
                 "channel": "The PrimeTime", "age_days": 0.13}]

    patch_server("_resolve_youtube_channels", fake_resolve_many)
    patch_server("_youtube_channel_uploads", fake_uploads)
    patch_server("_remember_current_video", lambda *a, **k: None)
    patch_server("_shown_video_ids", lambda sid: set())

    cfg = asyncio.run(m._recency_video_pick("newest primeagen video", "sess-multi"))
    assert cfg["video_id"] == "FRESH3HRS", \
        "newest must span ALL of the creator's channels, not just the best-matching one"


def test_new_prefix_kept_in_subject_and_peeled_as_a_variant():
    """'a new primeagen video' must reach the creator, but 'new rockstars video'
    must KEEP its 'new' — New Rockstars is the channel's actual name. So the
    subject is no longer peeled destructively; the caller tries the full subject
    AND a peeled variant, keeping whichever binds more strongly."""
    assert m._split_video_subject_topic("a new primeagen video")[0] == "new primeagen"
    assert m._split_video_subject_topic("new rockstars video")[0] == "new rockstars"
    assert m._split_video_subject_topic("fox news video newest")[0] == "fox news"


def test_conversational_filler_stripped_from_channel_subject():
    """'i want to watch primeagen' produced the guess 'i to primeagen', which
    bound nothing and dropped the ask to keyword search."""
    assert m._split_video_subject_topic("i want to watch primeagen")[0] == "primeagen"
    assert m._split_video_subject_topic("can you put on primeagen")[0] == "primeagen"


def test_creator_evidence_flags_plural_topic_phrases():
    """'funny cat videos' bound a channel literally called 'Funnycats' and
    served a 19-day-old clip. A pluralised description is a topic."""
    assert m._creator_evidence("funny cat", "funny cat videos") == "weak"
    # Real creators whose names contain otherwise-generic words must survive.
    assert m._creator_evidence("linus tech tips", "linus tech tips video") == "plain"
    assert m._creator_evidence("fox news", "fox news video newest") == "plain"
    assert m._creator_evidence("new rockstars", "new rockstars video") == "plain"


async def _no_secret(_name):
    return ""


def test_mcp_recency_uses_channel_feed_not_keyword_search(patch_server):
    """The MCP tool (the AGENT's path) called search_youtube_videos directly, so
    the channel logic never ran there: 'primeagen' + freshness returned a
    5-day-old keyword hit while the creator's 3-hour-old upload existed. The
    chat path being fixed did not fix the agent path."""
    async def fake_resolve_many(name, limit=3, evidence="plain"):
        return [{"channel_id": "UC_MAIN", "title": "ThePrimeagen", "handle": "@ThePrimeagen",
                 "match": 0.9, "rank_score": 1.2, "rank": 0, "verified": True}]

    async def fake_uploads(cid, limit=8, form=None):
        return [{"video_id": "FRESH3HRS", "id": "FRESH3HRS", "title": "Dont Smile",
                 "channel": "ThePrimeagen", "age_days": 0.13}]

    async def fake_search(*a, **k):
        return [{"video_id": "STALE5DAY", "id": "STALE5DAY", "title": "Worst Advice Ever",
                 "channel": "The PrimeTime", "age_days": 5.0}]

    patch_server("_resolve_youtube_channels", fake_resolve_many)
    patch_server("_youtube_channel_uploads", fake_uploads)
    patch_server("search_youtube_videos", fake_search)

    patch_server("_fetch_secret", _no_secret)
    out = asyncio.run(m.internal_tool_execute(m.InternalToolRequest(
        tool="html_notes_youtube_search",
        args={"query": "primeagen", "freshness": "newest", "limit": 4})))
    assert out["results"][0]["video_id"] == "FRESH3HRS", \
        "the agent path must use the creator's feed, not keyword search"


def test_mcp_non_creator_query_still_uses_search(patch_server):
    """A topic ask must NOT be hijacked into some channel's feed."""
    async def boom(*a, **k):
        raise AssertionError("channel path must not run for a topic query")

    async def fake_search(*a, **k):
        return [{"video_id": "TOPIC12345", "id": "TOPIC12345",
                 "title": "cookie recipe", "age_days": 1.0}]

    patch_server("_youtube_channel_uploads", boom)
    patch_server("search_youtube_videos", fake_search)
    async def no_chan(name, limit=3, evidence="plain"):
        return []
    patch_server("_resolve_youtube_channels", no_chan)
    patch_server("_fetch_secret", _no_secret)
    out = asyncio.run(m.internal_tool_execute(m.InternalToolRequest(
        tool="html_notes_youtube_search",
        args={"query": "cookie recipe", "freshness": "new", "limit": 3})))
    assert out["results"][0]["video_id"] == "TOPIC12345"


def test_unbounded_new_probes_recent_windows(patch_server):
    """LIVE BUG: 'a new cookie recipe video' returned 270- and 365-day-old
    uploads. Date sort only REORDERS the fetched pool, and with no upload-date
    facet that pool was entirely old. An unbounded recency ask must bound the
    FETCH, not just sort the result."""
    calls = []

    async def fake_fetch(query, limit=10, order="relevance", lang="en", window_days=None):
        calls.append(window_days)
        if window_days in (7.0, 31.0):
            return [Video(video_id="FRESH1234567", id="FRESH1234567",
                          title="cookie recipe quick", age_days=2.0)]
        return [Video(video_id="ANCIENT12345", id="ANCIENT12345",
                      title="cookie recipe classic", age_days=365.0)]

    patch_server("_yt_fetch_videos", fake_fetch)
    out = asyncio.run(m._search_youtube_scrape(
        "cookie recipe", limit=3, freshness=Freshness(matched="new")))
    assert 7.0 in calls and 31.0 in calls, "must probe recent upload-date facets"
    assert out[0]["video_id"] == "FRESH1234567", "the fresh upload must lead"


def test_no_recency_ask_does_not_probe_windows(patch_server):
    """An evergreen ask keeps relevance ranking — no freshness, no probing."""
    calls = []

    async def fake_fetch(query, limit=10, order="relevance", lang="en", window_days=None):
        calls.append(window_days)
        return [Video(video_id="EVERGREEN123", id="EVERGREEN123",
                      title="cookie recipe classic", views=900000, age_days=365.0)]

    patch_server("_yt_fetch_videos", fake_fetch)
    asyncio.run(m._search_youtube_scrape("cookie recipe", limit=3))
    assert all(w is None for w in calls), "a non-recency ask must not window the fetch"


def test_channel_ask_without_a_recency_word_still_serves_newest(patch_server):
    """THE reported failure. 'primeagen video' carries NO recency word, so both
    router lanes skipped the channel picker entirely and fell to keyword search,
    which returns whatever is popular — 5, 11 and 14 days old — while a 3-hour
    upload sat in the creator's feed. Naming a channel IS the request for its
    latest; the picker must run regardless of recency vocabulary."""
    async def fake_resolve_many(name, limit=3, evidence="plain"):
        return [{"channel_id": "UC_M", "title": "ThePrimeagen", "handle": "@ThePrimeagen",
                 "match": 0.9, "rank_score": 1.2, "rank": 0, "verified": True}]

    async def fake_uploads(cid, limit=8, form=None):
        return [{"video_id": "NEWEST12345", "id": "NEWEST12345", "title": "Dont Smile",
                 "channel": "ThePrimeagen", "age_days": 0.15},
                {"video_id": "OLDER1234567", "id": "OLDER1234567", "title": "old one",
                 "channel": "ThePrimeagen", "age_days": 11.2}]

    patch_server("_resolve_youtube_channels", fake_resolve_many)
    patch_server("_youtube_channel_uploads", fake_uploads)
    patch_server("_remember_current_video", lambda *a, **k: None)
    patch_server("_shown_video_ids", lambda sid: set())

    cfg = asyncio.run(m._recency_video_pick("primeagen video", "sess-norecency"))
    assert cfg and cfg["video_id"] == "NEWEST12345"


def test_topic_ask_without_recency_returns_none_for_search(patch_server):
    """A topic ask with no channel bound and no recency intent must hand back
    None so the caller keeps RELEVANCE ranking — date-sorting an evergreen ask
    returns junk."""
    async def no_chan(name, limit=3, evidence="plain"):
        return []

    async def boom(*a, **k):
        raise AssertionError("must not run a date search for a plain topic ask")

    patch_server("_resolve_youtube_channels", no_chan)
    patch_server("search_youtube_videos", boom)
    assert asyncio.run(m._recency_video_pick("a cookie recipe video", "s-t")) is None


# ── Shorts vs videos ────────────────────────────────────────────────────────
# LIVE BUG: "newest <channel> video" served the channel's newest SHORT. The
# uploads feed interleaves both and creators post Shorts far more often, so the
# feed head was almost always a 30-second clip. Format is now its own axis:
# default = no Shorts, explicit ask = Shorts only.
from app.youtube_search import parse_video_form, filter_by_form


@pytest.mark.parametrize("text", [
    "newest mkbhd short",
    "show me a short from mkbhd",
    "mkbhd shorts",
    "play some youtube shorts",
    "a short about cats",
    "short-form content from veritasium",
])
def test_parse_video_form_detects_a_shorts_ask(text):
    assert parse_video_form(text) == "short"


@pytest.mark.parametrize("text", [
    "newest mkbhd video",
    "pull up a video about spacex",
    "a cookie recipe video",
    # "short" as an adjective or as somebody else's noun — NOT the format.
    "best short films of 2026",
    "how to sew shorts",
    "cargo shorts review",
    "i am short on time",
])
def test_parse_video_form_ignores_non_format_uses(text):
    assert parse_video_form(text) != "short"


@pytest.mark.parametrize("text", [
    "newest linus video, not a short",
    "give me the full video",
    "long-form primeagen",
])
def test_parse_video_form_explicit_long(text):
    assert parse_video_form(text) == "long"


def test_filter_by_form_fails_open_rather_than_returning_nothing():
    shorts = [{"video_id": "S1", "is_short": True}]
    assert filter_by_form(shorts, None) == shorts, \
        "a channel that posts only Shorts must still return something"
    assert filter_by_form(shorts, "any") == shorts


_SHORTS_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <title>Shorts</title>
  <author><name>Paul Barron Network</name></author>
  <entry>
    <yt:videoId>SHORT1</yt:videoId>
    <title>60 Second Take</title>
    <published>2999-06-01T00:00:00+00:00</published>
    <media:group><media:thumbnail url="http://t/s.jpg"/></media:group>
  </entry>
</feed>"""

_MIXED_FEED = _SAMPLE_FEED.replace("""  <entry>
    <yt:videoId>NEW1</yt:videoId>""", """  <entry>
    <yt:videoId>SHORT1</yt:videoId>
    <title>60 Second Take</title>
    <published>2999-06-01T00:00:00+00:00</published>
    <media:group><media:thumbnail url="http://t/s.jpg"/></media:group>
  </entry>
  <entry>
    <yt:videoId>NEW1</yt:videoId>""")


def _feed_server(patch_server):
    """The uploads feed leads with a Short; the Shorts playlist feed names it."""
    seen = []

    async def fake_html(url, timeout=12.0, scraper_fallback=True):
        seen.append(url)
        if "playlist_id=UUSH" in url:
            return _SHORTS_FEED
        return _MIXED_FEED

    patch_server("_yt_fetch_html", fake_html)
    return seen


def test_channel_feed_drops_shorts_by_default(patch_server):
    """The head of the uploads feed is a Short posted AFTER the real upload —
    the exact shape of the live failure. Newest VIDEO is NEW1, not SHORT1."""
    _feed_server(patch_server)
    feed = asyncio.run(m._youtube_channel_uploads("UC123", limit=6))
    assert [h["video_id"] for h in feed] == ["NEW1", "OLD1"]
    assert all(h["is_short"] is False for h in feed)


def test_channel_feed_serves_shorts_when_asked(patch_server):
    seen = _feed_server(patch_server)
    feed = asyncio.run(m._youtube_channel_uploads("UC123", limit=6, form="short"))
    assert [h["video_id"] for h in feed] == ["SHORT1"]
    assert feed[0]["is_short"] is True
    # Attribution survives: the playlist feed's <title> is "Shorts", so the
    # channel name has to come from the author element.
    assert feed[0]["channel"] == "Paul Barron Network"
    # A Shorts ask reads ONE feed — no point paying for the uploads feed too.
    assert all("playlist_id=UUSH" in u for u in seen)


def test_shorts_playlist_id_derives_from_the_channel_id():
    assert m._yt_auto_playlist("UCtI0Hodo5o5dUb67FeUjDeA", "short") == \
        "UUSHtI0Hodo5o5dUb67FeUjDeA"
    assert m._yt_auto_playlist("UCtI0Hodo5o5dUb67FeUjDeA", "all") == \
        "UUtI0Hodo5o5dUb67FeUjDeA"


def test_channel_feed_falls_open_when_the_channel_posts_only_shorts(patch_server):
    async def fake_html(url, timeout=12.0, scraper_fallback=True):
        return _SHORTS_FEED       # every upload is also in the Shorts feed
    patch_server("_yt_fetch_html", fake_html)
    feed = asyncio.run(m._youtube_channel_uploads("UC123", limit=6))
    assert [h["video_id"] for h in feed] == ["SHORT1"], \
        "an empty player is worse than a Short from the right channel"


def test_search_drops_shorts_before_the_age_sort(patch_server):
    """A Short uploaded an hour ago must not beat this morning's real upload on
    a strict-recency ask — the format filter runs BEFORE the date sort."""
    fresh_short = Video(video_id="S1", id="S1", title="paul barron network take",
                        channel="Paul Barron Network", duration_sec=45,
                        age_days=0.04, rank=0)
    real = Video(video_id="V1", id="V1", title="paul barron network market update",
                 channel="Paul Barron Network", duration_sec=1400,
                 age_days=0.3, rank=1)

    async def fake_fetch(query, limit=10, order="relevance", lang="en", window_days=None):
        return [fresh_short, real]

    patch_server("_yt_fetch_videos", fake_fetch)
    out = asyncio.run(m._search_youtube_scrape("paul barron network", limit=5,
                                               strict_recency=True))
    assert [h["video_id"] for h in out] == ["V1"]
    # ...and the same query asking for a Short gets the Short.
    out = asyncio.run(m._search_youtube_scrape("paul barron network", limit=5,
                                               strict_recency=True, form="short"))
    assert [h["video_id"] for h in out] == ["S1"]


def test_recency_pick_asks_the_feed_for_shorts(patch_server):
    """'newest mkbhd short' must reach the feed with form='short' — and the word
    must not leak into the channel guess or the topic filter."""
    got = {}

    async def fake_resolve_many(name, limit=3, evidence="plain"):
        got["subject"] = name
        return [{"channel_id": "UC_MKBHD", "title": "Marques Brownlee", "handle": "@mkbhd",
                 "match": 0.95, "rank_score": 1.2, "rank": 0, "verified": True}]

    async def fake_uploads(cid, limit=8, form=None):
        got["form"] = form
        return [{"video_id": "SHORT9", "id": "SHORT9", "title": "quick take",
                 "channel": "Marques Brownlee", "age_days": 0.05, "is_short": True}]

    patch_server("_resolve_youtube_channels", fake_resolve_many)
    patch_server("_youtube_channel_uploads", fake_uploads)
    patch_server("_remember_current_video", lambda *a, **k: None)
    patch_server("_shown_video_ids", lambda sid: set())

    cfg = asyncio.run(m._recency_video_pick("newest mkbhd short", "sess-short"))
    assert cfg and cfg["video_id"] == "SHORT9"
    assert got["form"] == "short"
    assert "short" not in got["subject"].lower(), \
        "the format word must not become part of the channel-name guess"


def test_mcp_format_arg_reaches_the_channel_feed(patch_server):
    """The agent's rewrite drops the word 'short' as readily as it drops time
    words, so the tool takes an explicit format arg."""
    got = {}

    async def fake_resolve_many(name, limit=3, evidence="plain"):
        return [{"channel_id": "UC_MKBHD", "title": "Marques Brownlee", "handle": "@mkbhd",
                 "match": 0.95, "rank_score": 1.2, "rank": 0, "verified": True}]

    async def fake_uploads(cid, limit=8, form=None):
        got["form"] = form
        return [{"video_id": "SHORT9", "id": "SHORT9", "title": "quick take",
                 "channel": "Marques Brownlee", "age_days": 0.05, "is_short": True}]

    async def fake_search(*a, **k):
        raise AssertionError("a Shorts ask with a bound channel must use the feed")

    patch_server("_resolve_youtube_channels", fake_resolve_many)
    patch_server("_youtube_channel_uploads", fake_uploads)
    patch_server("search_youtube_videos", fake_search)
    patch_server("_fetch_secret", _no_secret)

    out = asyncio.run(m.internal_tool_execute(m.InternalToolRequest(
        tool="html_notes_youtube_search",
        args={"query": "mkbhd", "format": "short", "limit": 4})))
    assert got["form"] == "short"
    assert out["results"][0]["video_id"] == "SHORT9"


def test_mcp_format_video_means_no_shorts(patch_server):
    """format='video' is the schema's word for long-form; the server speaks
    'long'. A silent mismatch here would re-open the bug."""
    got = {}

    async def fake_resolve_many(name, limit=3, evidence="plain"):
        return [{"channel_id": "UC1", "title": "Marques Brownlee", "handle": "@mkbhd",
                 "match": 0.95, "rank_score": 1.2, "rank": 0, "verified": True}]

    async def fake_uploads(cid, limit=8, form=None):
        got["form"] = form
        return [{"video_id": "VID1", "id": "VID1", "title": "review",
                 "channel": "Marques Brownlee", "age_days": 0.4, "is_short": False}]

    patch_server("_resolve_youtube_channels", fake_resolve_many)
    patch_server("_youtube_channel_uploads", fake_uploads)
    patch_server("_fetch_secret", _no_secret)
    out = asyncio.run(m.internal_tool_execute(m.InternalToolRequest(
        tool="html_notes_youtube_search",
        args={"query": "mkbhd", "freshness": "newest", "format": "video"})))
    assert got["form"] == "long"
    assert out["results"][0]["video_id"] == "VID1"


def test_mcp_flags_a_shorts_ask_that_fell_back_to_videos(patch_server):
    """Fail-open must be VISIBLE — the model may not call a 20-minute review a
    Short just because the tool had nothing better."""
    async def no_chan(name, limit=3, evidence="plain"):
        return []

    async def fake_search(*a, **k):
        return [{"video_id": "LONG1", "id": "LONG1", "title": "full review",
                 "age_days": 1.0, "is_short": False}]

    patch_server("_resolve_youtube_channels", no_chan)
    patch_server("search_youtube_videos", fake_search)
    patch_server("_fetch_secret", _no_secret)
    out = asyncio.run(m.internal_tool_execute(m.InternalToolRequest(
        tool="html_notes_youtube_search",
        args={"query": "cat compilation", "format": "short"})))
    assert "note" in out and "Shorts" in out["note"]
