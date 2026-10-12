"""A live ask naming a news network plays THAT network, never a look-alike.

Live failures 2026-10-11 (nemotron35 selected; the path is model-free):
  "cnn live news" -> Times Now "Breaking News LIVE..." (keyword search under
                     the live filter is dominated by CNN-News18 and friends)
  "fox live news" -> "LIVE: WEBCAMS around the world | FOX 10 Phoenix"
The hit lists below are the ones the live search returned that day.
"""
import json

import app.main  # noqa: F401  — import main FIRST (submodules copy its dict)
from fastapi.testclient import TestClient

from app.main import app as fastapi_app


def H(vid, channel, title, live=True, age=0.1, short=False):
    return {"video_id": vid, "id": vid, "channel": channel, "title": title,
            "is_live": live, "age_days": age, "is_short": short}


CNN_LIVE = [
    H("hcRubBgrZ6U", "CNN-News18", "US Midterm Elections | US Judge Orders Trump Admin"),
    H("lTtwlP71YB8", "CNN-News18", "LIVE: Trump Claims Russia-Ukraine Energy Ceasefire"),
    H("kad4RFaOr6A", "Times Now", "Breaking News LIVE: Trump Accepts Iran's Offer"),
]
FOX_LIVE = [
    H("8ufvKsnGIFg", "LiveNOW from FOX", "LIVE: Trump White House latest"),
    H("SVKuiggXCdI", "FOX 10 Phoenix", "LIVE: WEBCAMS around the world | FOX 10 Phoenix"),
    H("1g0K3x5ZG6M", "MIRROR NOW", "Trump Speech LIVE"),
    H("bs4rhXlRGC4", "FOX One", "FOX NEWS Channel"),
]
CNN_UPLOADS = [
    H("old1", "CNN", "Older CNN report", live=False, age=2.0),
    H("new1", "CNN", "🔴 LIVE: Newest CNN report", live=False, age=0.2),
    H("short1", "CNN", "A CNN short", live=False, age=0.05, short=True),
    H("n18", "CNN-News18", "CNN-News18 upload", live=False, age=0.01),
]


def fake_search(table):
    calls = []

    async def search(query, limit=5, order="relevance", **kw):
        calls.append((query, order))
        q = query.lower()
        for key, hits in table.items():
            if key[0] in q and key[1] == order:
                return list(hits)
        return []
    search.calls = calls
    return search


def test_match_news_network():
    from app.services.youtube_helpers import match_news_network
    assert match_news_network("cnn live news")[0] == "CNN"
    assert match_news_network("cnn live")[0] == "CNN"
    assert match_news_network("fox live news")[0] == "Fox News"
    assert match_news_network("fox business live")[0] == "Fox Business"
    assert match_news_network("watch msnbc live")[0] == "MSNBC"
    # Ambiguous words need "news": not the animal, not a preschool ABC stream.
    assert match_news_network("fox livestream") is None
    assert match_news_network("abc song live") is None
    assert match_news_network("dunkey livestreams") is None


async def _no_stream(handle):
    return None


def test_cnn_never_plays_cnn_news18_or_times_now(patch_server):
    from app.services import youtube_helpers as yh
    patch_server("_channel_live_now", _no_stream)
    patch_server("search_youtube_videos", fake_search({
        ("cnn", "live"): CNN_LIVE, ("cnn", "date"): CNN_UPLOADS}))
    patch_server("filter_blocked_videos", lambda hits: hits)
    import asyncio
    cfg = asyncio.run(yh.pick_news_network_live("cnn live news"))
    assert cfg["video_id"] == "new1", "CNN is not live: its own newest long-form upload"
    assert cfg["not_live"] is True
    assert cfg["title"] == "CNN isn't live on YouTube right now — latest: Newest CNN report"
    assert "n18" not in cfg["candidates"] and "short1" not in cfg["candidates"]


def test_fox_plays_the_networks_own_stream_not_a_local_affiliate(patch_server):
    from app.services import youtube_helpers as yh
    patch_server("_channel_live_now", _no_stream)
    patch_server("search_youtube_videos", fake_search({("fox", "live"): FOX_LIVE}))
    patch_server("filter_blocked_videos", lambda hits: hits)
    import asyncio
    cfg = asyncio.run(yh.pick_news_network_live("fox live news"))
    # FOX One outranks LiveNOW in the official list; the webcam feed is out.
    assert cfg["video_id"] == "bs4rhXlRGC4"
    assert cfg["candidates"] == ["8ufvKsnGIFg"]
    assert "SVKuiggXCdI" not in cfg["candidates"]


def test_a_non_network_live_ask_is_left_to_the_generic_path(patch_server):
    from app.services import youtube_helpers as yh
    search = fake_search({})
    patch_server("search_youtube_videos", search)
    import asyncio
    assert asyncio.run(yh.pick_news_network_live("dunkey livestreams")) is None
    assert search.calls == [], "no search spent on an ask that names no network"


def test_the_routed_fast_path_uses_the_network_picker(patch_server):
    """End to end through /session/message: the user's exact words."""
    patch_server("_channel_live_now", _no_stream)
    patch_server("search_youtube_videos", fake_search({("fox", "live"): FOX_LIVE}))
    patch_server("filter_blocked_videos", lambda hits: hits)
    client = TestClient(fastapi_app)
    body = {"session_id": "live-net-test", "message": "fox live news",
            "current_canvas": '<div id="dashboard-grid"></div>', "canvas_version": 0,
            "provider": "vllm", "model": "nemotron35"}
    with client.stream("POST", "/session/message", json=body) as r:
        text = "".join(r.iter_text())
    events = [json.loads(l[6:]) for l in text.splitlines() if l.startswith("data: ")]
    debug = [e for e in events if e.get("type") == "debug"]
    assert debug and debug[0].get("id_prefix") == "live"
    html = "".join(e.get("content", "") for e in events if e.get("type") == "component")
    assert "bs4rhXlRGC4" in html
    assert "SVKuiggXCdI" not in html


# A trimmed /live page from 2026-10-11: @CNN resolved to its 24/7 stream while
# keyword search returned only CNN-News18 / Times Now.
CNN_LIVE_PAGE = ('<html><head><title>CNN Headlines: 24/7 Live News - YouTube</title>'
                 '<meta name="title" content="CNN Headlines: 24/7 Live News">'
                 '<link rel="canonical" href="https://www.youtube.com/watch?v=GotlA1KKWoo">'
                 '</head><script>var x={"isLiveNow":true,"isLiveContent":true};</script></html>')
FOX_CHANNEL_PAGE = ('<html><head><title>Fox News - YouTube</title>'
                    '<link rel="canonical" href="https://www.youtube.com/channel/UCXIJgqnII2ZOINSWNOGFThA">'
                    '</head></html>')


def test_parse_channel_live_page():
    from app.services.youtube_helpers import parse_channel_live_page
    assert parse_channel_live_page(CNN_LIVE_PAGE) == {
        "video_id": "GotlA1KKWoo", "title": "CNN Headlines: 24/7 Live News"}
    assert parse_channel_live_page(FOX_CHANNEL_PAGE) is None, "not live: channel page"
    assert parse_channel_live_page("") is None


def test_the_channel_live_page_wins_over_keyword_search(patch_server):
    from app.services import youtube_helpers as yh
    pages = {"CNN": CNN_LIVE_PAGE}

    async def fetch(url, timeout=12.0, scraper_fallback=True):
        return next((html for h, html in pages.items() if f"/@{h}/live" in url), "")
    patch_server("_yt_fetch_html", fetch)
    search = fake_search({("cnn", "live"): CNN_LIVE})
    patch_server("search_youtube_videos", search)
    patch_server("filter_blocked_videos", lambda hits: hits)
    import asyncio
    cfg = asyncio.run(yh.pick_news_network_live("cnn live news"))
    assert cfg["video_id"] == "GotlA1KKWoo"
    assert cfg["title"] == "CNN Headlines: 24/7 Live News"
    assert search.calls == [], "the authoritative page answered; no keyword search"


def test_newest_first_trusts_youtube_order_when_ages_are_missing():
    from app.services.youtube_helpers import _newest_first
    hits = [H("old", "X", "", age=510), H("a", "X", "", age=None), H("b", "X", "", age=None),
            H("fresh", "X", "", age=0.1)]
    assert [h["video_id"] for h in _newest_first(hits)] == ["a", "b", "fresh", "old"]


def test_tuning_in_to_a_network_is_a_live_ask():
    from app.services.youtube_helpers import is_network_live_ask
    for ask in ["cnn live news", "fox live news", "put on fox news", "watch cnn",
                "turn on msnbc", "tune in to bbc news", "switch to sky news"]:
        assert is_network_live_ask(ask), ask
    for ask in ["watch cnn video about the senate", "latest fox news clip",
                "what is cnn saying about the election", "fox livestream",
                "watch dunkey", "cnn headlines today"]:
        assert not is_network_live_ask(ask), ask


def _route(message):
    client = TestClient(fastapi_app)
    body = {"session_id": f"live-net-{abs(hash(message))}", "message": message,
            "current_canvas": '<div id="dashboard-grid"></div>', "canvas_version": 0,
            "provider": "vllm", "model": "nemotron35"}
    with client.stream("POST", "/session/message", json=body) as r:
        text = "".join(r.iter_text())
    return [json.loads(l[6:]) for l in text.splitlines() if l.startswith("data: ")]


def test_put_on_fox_news_plays_the_stream_not_a_headline_card(patch_server):
    patch_server("_channel_live_now", _no_stream)
    patch_server("search_youtube_videos", fake_search({("fox", "live"): FOX_LIVE}))
    patch_server("filter_blocked_videos", lambda hits: hits)
    events = _route("put on fox news")
    debug = [e for e in events if e.get("type") == "debug"]
    assert debug and debug[0].get("id_prefix") == "live", debug
    html = "".join(e.get("content", "") for e in events if e.get("type") == "component")
    assert "bs4rhXlRGC4" in html


def test_the_router_built_live_widget_uses_the_network_picker_too(patch_server):
    """The tier-2/agent route builds the widget in config_builders — the twin
    path took the top keyword hit as well."""
    import asyncio
    from app.config_builders import build_router_widget
    patch_server("_channel_live_now", _no_stream)
    patch_server("search_youtube_videos", fake_search({("cnn", "live"): CNN_LIVE,
                                                       ("cnn", "date"): CNN_UPLOADS}))
    patch_server("filter_blocked_videos", lambda hits: hits)
    got = asyncio.run(build_router_widget(
        {"type": "video", "query": "cnn live news", "modifiers": {"live": True}},
        "router-live-test", "cnn live news"))
    wtype, prefix, cfg = got
    assert (wtype, prefix) == ("youtube_player", "live")
    assert cfg["video_id"] == "new1", "CNN's own upload, never CNN-News18 / Times Now"
